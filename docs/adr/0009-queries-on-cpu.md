# 0009. Embed queries on CPU, index on DirectML

- Status: Superseded by [0010](0010-directml-one-query-shape.md): the stalls came from the session seeing several input shapes, not from DirectML itself
- Date: 2026-11-13

## Context and problem statement

A week of shadow-mode logs (2026-11-07 → 2026-11-13, 1,805 prompts) showed the
hook still missing its 1.5 s budget ([0003](0003-fail-open-hook-budget.md)):
of 1,135 prompts that reached the search, 275 (24 %) were
`service-unavailable`, all timeouts, and the hook's median was 0.9–1.2 s on
most days. Breaking one prompt down (20 long Spanish prompts from
`tests/data/prompts_long_20.json`, live service, development laptop with an
integrated GPU and a discrete NVIDIA GPU):

- **Hook process start.** `python -I` itself took ~60–120 ms, but importing
  `urllib.request` (http.client, email, ssl) added ~200 ms on every prompt.
- **Query embedding on DirectML.** Each prompt becomes 2–4 windows of ≤32
  tokens ([0008](0008-query-windows-for-long-prompts.md)). The same window
  batches measured on the same machine:

  | Query providers | p50 | p90 | max |
  |---|---|---|---|
  | DirectML, discrete GPU | 384 ms | 3,137 ms | 3,498 ms |
  | DirectML, integrated GPU | 383 ms | 1,190 ms | 3,465 ms |
  | CPU | 541 ms | 894 ms | 1,143 ms |

  DirectML's median is lower, but about one call in ten stalls for seconds
  even with nothing else queued; the hook's budget is decided by that tail.
  Vectors agree across providers to ≤1.5e-6 (max abs difference).
- On CPU, embedding all windows in **one** call was 2–3× faster than one call
  per window (identical vectors). On DirectML the opposite holds: cost follows
  the total tokens per call (0008), and batching made the live service slower.

## Decision drivers

- Fit the hook's 1.5 s budget at the tail, not just the median.
- No change in retrieval quality and no reindex.
- Keep DirectML where throughput matters: indexing.

## Considered options

- **Keep DirectML for queries** and raise the hook budget. Rejected: delays
  every prompt and does not remove the stalls.
- **Race DirectML and CPU** per query and take the first answer. Rejected for
  now: two sessions in memory and more moving parts for a tail CPU already fixes.
- **Queries on CPU, windows batched in one call; indexing stays on DirectML.**

## Decision

Default `model.query_providers` is `["CPUExecutionProvider"]`;
`model.index_providers` stays DirectML-first. `Searcher.search` embeds all
query windows in one call when the query session runs on CPU, and keeps one
call per window (with the deadline checked between windows) on DirectML.
The hook talks to the service over a plain loopback socket instead of
`urllib.request`.

Measured after the change, same 20 long prompts, live service:

| | before (DirectML queries) | after (CPU queries) |
|---|---|---|
| service time p50 / p90 | 1.6–2.5 s / 2.4–4.2 s | 0.68–0.76 s / 1.16–1.22 s |
| whole hook p50 / p90 | 1.73–1.89 s / 1.9–2.2 s | 0.90–0.94 s / 1.28–1.40 s |
| hook start with imports | ~530 ms | ~145 ms |

Retrieval is unchanged: 80-query set R@5 1.00 / MRR 0.91, data-books set
R@5 1.00 / MRR 0.93, max negative cosine 0.743 (threshold 0.75).

## Consequences

- Positive: the long-prompt worst case fits the budget; latency no longer
  depends on the GPU's state; the CPU path serves concurrent requests in
  parallel (0.3.1).
- Negative / trade-offs: short queries are slower on CPU than a warm GPU's
  median (~0.5 s vs ~0.1–0.4 s), and CPU load from other work now competes
  with queries. Machines with a GPU that does not stall can opt back in with
  `"query_providers": ["DmlExecutionProvider:N", "CPUExecutionProvider"]`.
- The shadow log after this change is the data for calibrating the threshold
  (the earlier week under-counts prompts that timed out).
