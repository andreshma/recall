# 0008. Split long prompts into ≤32-token query windows

- Status: Accepted
- Date: 2026-11-06

## Context and problem statement

After E5 ([0007](0007-e5-gemma2-large-chunks.md)) the hook almost never
answered within its 1.5 s budget ([0003](0003-fail-open-hook-budget.md)): of
289 shadow-log entries, 227 were `service-unavailable`, nearly all timeouts at
~1.55 s. Measured on 2026-11-06:

- embeddinggemma-2 (int8 quantized) on DirectML takes 100–200 ms when a call
  holds ≤32 tokens and 1.2–5.5 s from 33 tokens on, on both GPUs of the laptop.
  The limit is total tokens per call: a batch of two 26-token windows is just
  as slow. The query prefix already uses ~8 tokens.
- Real prompts are longer than that; MCP queries (short concepts) are not.

Two side effects made it worse. The service process, launched hidden, was
throttled by Windows as background work (EcoQoS): 1.4–1.7 s per long prompt
versus 0.26–0.53 s for the same process in the foreground. Abandoned requests
also kept running, and concurrent `Run` calls on the DirectML session crashed
the service inside the NVIDIA driver (`nvwgf2umx.dll`, `0xc0000005`).

## Decision drivers

- Fit the hook's 1.5 s budget on the production device.
- Keep the whole prompt in play; the topic is often not at the start.
- No reindex, and no change for short queries.

## Considered options

- **Truncate the query to 32 tokens** (`query_max_tokens: 32`).
- **Split the prompt into ≤32-token windows**, embed each in its own call and fuse.
- **Reduce the prompt to key terms** (stop-word removal) before embedding.
- **Raise the hook budget.** Rejected: it delays every prompt.

## Decision

Split into windows (`search.query_window_tokens: 32`). Each window
repeats the last 5 words of the previous one so a key phrase is not cut
in two. Up to 6 windows; a word that alone exceeds the limit (a long URL) is
dropped. Each window contributes its RRF (vector and FTS) to its original
query and the contributions are **summed**, so passages that match several
windows win. Across original queries (ES and EN in MCP) each passage keeps
its best score, as before. The hook sends `budget_ms` and the service stops
embedding windows when the deadline passes; the first window is always
embedded.

Measured on the 80 labeled queries plus 20 new long prompts
(`tests/data/prompts_long_20.json`, labels taken from the 80):

| Variant | Short R@5 / MRR | Long R@5 / MRR |
|---|---|---|
| Whole prompt (before) | 0.988 / 0.898 | 1.000 / 0.831, over budget |
| Truncate to 32 | 0.988 / 0.888 | 0.450 / 0.300 |
| Windows, max fusion, no overlap | 0.975 / 0.886 | 0.900 / 0.814 |
| **Windows, sum fusion, overlap 5** | **0.988 / 0.898** | **1.000 / 0.750** |

Alongside, and for the same root cause:

- The service declares itself HighQoS with
  `SetProcessInformation(ProcessPowerThrottling)` on start.
- The embedder serializes `Run` with a lock and disables `mem_pattern` with
  sequential execution, as the ONNX Runtime DirectML EP documentation requires.

## Consequences

- Positive: end to end, 18–19 of 20 long prompts answered through the real hook
  (p50 ~770 ms) instead of 5–6; the service no longer crashes under concurrent
  or abandoned requests; short queries and MCP are unchanged.
- Negative / trade-offs: on long prompts the reciprocal rank is lower than
  embedding the whole prompt (0.75 vs 0.83) — the correct book is still in the top 5,
  sometimes lower in it. Each long prompt costs 3–6 model calls. The 20 long
  prompts are synthetic; the shadow log, now reaching the service, is the real
  check, and the hook threshold should be recalibrated from it before
  activation ([0004](0004-shadow-mode-before-activation.md)).
- The window limit is specific to this model and DirectML; code defaults keep
  windows off (`query_window_tokens: 0`) and `config.example.json` turns them on.
