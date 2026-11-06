# 0007. Production configuration E5: embeddinggemma-2 with ~3,500-character chunks

- Status: Accepted (supersedes the baseline model and chunking of [0006](0006-local-onnx-directml-offline.md))
- Date: 2026-11-05

## Context and problem statement

The baseline (embeddinggemma-300m, ~900-character chunks) ranks the correct
passage in the top five for 97.5 % of 80 labeled queries, but its positive and
negative cosines overlap (separation 0.944), which limits a clean automatic
threshold. Five experiments varied chunk size and model. The full study is in
[docs/paper.md](../paper.md).

## Decision drivers

- Retrieval quality, especially Spanish → English.
- Full separation of relevant and unrelated prompts for the hook threshold.
- Index size and latency.
- Vectors must be reproducible (batch- and padding-invariant) on the production device.

## Considered options

- **Baseline:** embeddinggemma-300m, ~900 chars (R@5 0.975, separation 0.944).
- **E1:** embeddinggemma-300m, ~2,000 chars (R@5 0.988, separation 0.800).
- **E2:** embeddinggemma-300m, ~3,500 chars (R@5 1.000, separation 0.911).
- **E3:** embeddinggemma-2, ~900 chars (R@5 0.975, separation 1.000).
- **E4:** Qwen3-Embedding-0.6B, ~900 chars (not padding-invariant on DirectML; ~46 h on CPU).
- **E5:** embeddinggemma-2, ~3,500 chars (R@5 0.988, separation 1.000).

## Decision

**E5.** embeddinggemma-2 with chunks of target 3,200 / max 3,500 / min 1,500
characters and 400 overlap (`experiments/configs/e5-gemma2-chunk3500.json`),
34,721 chunks. The hook threshold starts at **0.75** in shadow mode: 96.7 % of
positives and 0 of 28 negatives pass. 0.72 was rejected after a real prompt
about a CI pipeline scored 0.73. Because large chunks often hide the relevant
paragraph beyond the 700-character injection limit, the service trims each top
hit to the sentence window that shares the most query terms (lexical selection;
re-embedding windows cost ~2 s per hit on DirectML).

## Consequences

- Positive: R@5 0.988 (EN→EN 1.000, ES→EN 0.975), MRR 0.898, index one third of
  the baseline size; these figures are the floors of the regression tests.
- Positive: hook latency 0.4–0.6 s end to end.
- Negative: the margin between the highest negative (0.7258) and the lowest
  positive (0.7289) is 0.003; the threshold is to be recalibrated from the shadow log.
- Negative: shadow logs from the previous model were archived; their cosines
  are on a different scale.
- Rollback: the previous index is kept as a backup; restoring it and removing
  the production `config.json` returns to the baseline.
