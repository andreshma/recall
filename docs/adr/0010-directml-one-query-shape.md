# 0010. Queries on DirectML at one fixed input shape

- Status: Accepted
- Date: 2026-11-17

## Context and problem statement

[0009](0009-queries-on-cpu.md) moved queries to CPU because about one
DirectML call in ten stalled for seconds. In the shadow log afterwards
(2026-11-14 → 2026-11-17) timeouts fell from ~28 % to ~16 % of searched
prompts, not to zero: on CPU each 32-token window cost 0.5–0.8 s, and
1–1.4 s when other work loaded the laptop.

A benchmark that separated the possible causes (discrete GPU, one window per
call, 30 calls per scenario) found what the stalls actually were:

| Scenario | p50 | max | calls > 1 s |
|---|---|---|---|
| Back-to-back, width 32 | 280 ms | 373 ms | 0 |
| 2–15 s idle between calls | 300 ms | 432 ms | 0 |
| Idle gaps plus a keep-alive call every 1 s | 244 ms | 356 ms | 0 |
| Idle gaps, every call padded to width 64 | 2,485 ms | 3,412 ms | 24 |

Idle time and GPU power states had nothing to do with it. The input shape
did. Measured on fresh sessions:

- **A DirectML session is fast only for the first shape it runs.** First
  shape (1, 32): 50–70 ms per call. After it had seen (1, 64), (2, 32) or
  (4, 32), each of those took 1.1–3.6 s, and (1, 32) itself slowed to
  ~350 ms. Starting with (1, 64) instead made (1, 64) the fast one and
  (1, 32) the slow one. CPU cost does not depend on the shape this way.
- **The service caused it itself.** `warm_up` embedded seven query lengths at
  start, i.e. seven widths (32 … 224), to "compile each shape", which
  degraded the session before the first real query. Earlier the windows of
  one prompt were batched together, a second shape on the batch axis.
  The "~150 ms up to 32 tokens and 1.2–5.5 s from 33 on" measured for
  [0008](0008-query-windows-for-long-prompts.md) was this same effect.
- **Pinning the dimensions in the model** (ONNX Runtime free dimension
  overrides, `batch_size=1`, `sequence_length=32`) was even faster (33 ms
  p50) but returned wrong vectors (max abs difference 0.25 against CPU), so
  it is not used.

## Decision drivers

- Fit the hook's 1.5 s budget ([0003](0003-fail-open-hook-budget.md)) at the
  tail, also when the CPU is busy.
- No change in retrieval quality and no reindex.

## Considered options

- **Stay on CPU** (0009). Rejected: 10× slower per window and sensitive to
  CPU load.
- **Race DirectML against CPU.** Rejected: unnecessary once the cause is gone.
- **Free dimension overrides.** Rejected: wrong vectors.
- **DirectML, every query call at one shape.**

## Decision

Default `model.query_providers` is DirectML first again. When the query
session runs on DirectML, `OnnxEmbedder.query_shape` is `(1, W)`: each text
is embedded in its own call, padded (masked) to exactly `W` tokens, where `W`
is `search.query_window_tokens` if windows are on and `model.query_max_tokens`
otherwise. Longer texts are truncated to `W`, which windows never need. The
warm-up and the session rebuilt by the engine guard go through the same path,
so the session never sees a second shape. On CPU nothing changes: all windows
in one call (0009). Indexing is unchanged.

Measured, the 61 windows of `tests/data/prompts_long_20.json`, 3 passes each:

| Query session | p50 | p90 | max | calls > 1 s | max abs diff vs CPU |
|---|---|---|---|---|---|
| CPU (0009) | 818 ms | 1,359 ms | 2,760 ms | 43 / 183 | — |
| DirectML, one shape | 88 ms | 139 ms | 182 ms | 0 / 183 | 2.2e-7 |

`tests/test_model_query_shape.py` asserts that every call, warm-up included,
has the pinned shape and that the vectors match CPU.

## Consequences

- Positive: the model is no longer the bottleneck. A long prompt (2–4
  windows) costs ~0.2–0.4 s of embedding instead of 1–3 s.
- Negative / trade-offs: with windows off, every DirectML query is padded to
  `query_max_tokens` (256); not measured, and windows are on in the
  recommended configuration. The rest of the search (SQLite FTS, the vector
  scan) still runs on the CPU and still swings with machine load.
- Indexing on DirectML sees many shapes too; whether it gains from the same
  treatment is not measured yet.

### Related decisions

Supersedes [0009](0009-queries-on-cpu.md).
