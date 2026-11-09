# 0002. Raw cosine with an absolute threshold for automatic injection

- Status: Accepted
- Date: 2026-10-27

## Context and problem statement

The hook must decide, with no human in the loop, whether the best result for a
prompt is relevant enough to inject. Ranking scores (RRF) are relative to the
other candidates of the same query and are not comparable across queries.

## Decision drivers

- An irrelevant injected passage is worse than none.
- The decision must be a single comparison that can be calibrated and tested.
- Ranking quality for explicit search must not suffer.

## Considered options

- **A. Rank with RRF, decide with the raw cosine** of the best hit against a fixed threshold.
- **B. Decide with the RRF score.**
- **C. Always inject the top-k.**

## Decision

Option **A**. Search fuses vector and FTS5 results with RRF for ordering, but
every hit also carries its raw cosine with the query, and the hook injects only
hits at or above `hook.threshold`. Mechanical prompts (git, files, editing) are
filtered before searching, because with a large library they reach cosines
above legitimate questions.

## Consequences

- Positive: one number to calibrate; regression tests assert that no negative
  prompt reaches the threshold and that at least 80 % of positives do.
- Negative: cosine scale is model-specific; the threshold must be recalibrated
  whenever the model changes (0.58 for embeddinggemma-300m, 0.75 for
  embeddinggemma-2; see [0007](0007-e5-gemma2-large-chunks.md)).
- Negative: the margin between the highest negative and the lowest positive can
  be small; shadow mode ([0004](0004-shadow-mode-before-activation.md)) exists to
  validate it on real prompts.
