# 0005. Model identity guard in the index

- Status: Accepted
- Date: 2026-10-27

## Context and problem statement

Vectors from different models, revisions, prefixes or chunking settings are
not comparable. Mixing them, or querying an index with the wrong model, gives
results that look plausible but are silently wrong.

## Decision drivers

- Fail loudly instead of degrading silently.
- Allow experiments with other models and chunk sizes without risk to
  production.

## Considered options

- **A. Store a model identity in the index and check it on every open.**
- **B. Trust the configuration.**
- **C. Store only the model name.**

## Decision

Option **A**. The index `meta` table records the identity: model name, snapshot
revision, sha256 of the ONNX weights, external data and `tokenizer.json`,
dimension, query prefix and document format, pooling and padding settings, and
chunking parameters. Indexing or searching with a different identity raises
`ModelMismatchError`; an index that has chunks but no identity also fails.
Changing model or chunking requires `recall index --rebuild` or a separate
`RECALL_HOME`, which is how `experiments/run.ps1` builds each configuration.

## Consequences

- Positive: an index can never mix vectors from two models.
- Positive: experiments run in their own directories without touching production.
- Negative: any change to prefixes, pooling or chunking forces a full re-index.
