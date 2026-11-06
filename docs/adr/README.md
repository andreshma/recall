# Architecture Decision Records

We record significant, hard-to-reverse decisions as ADRs in the
[MADR](https://adr.github.io/madr/) format. Each file is immutable once
`Accepted`; a later decision supersedes an earlier one rather than editing it.

| # | Title | Status |
|---|---|---|
| [0001](0001-sqlite-brute-force-vectors.md) | SQLite with brute-force vectors and FTS5, no vector database | Accepted |
| [0002](0002-cosine-absolute-threshold.md) | Raw cosine with an absolute threshold for automatic injection | Accepted |
| [0003](0003-fail-open-hook-budget.md) | Fail-open hook with a 1.5 s time budget | Accepted |
| [0004](0004-shadow-mode-before-activation.md) | Shadow mode before activating injection | Accepted |
| [0005](0005-model-identity-guard.md) | Model identity guard in the index | Accepted |
| [0006](0006-local-onnx-directml-offline.md) | Local ONNX model on DirectML, fully offline | Accepted |
| [0007](0007-e5-gemma2-large-chunks.md) | Production configuration E5: embeddinggemma-2 with ~3,500-character chunks | Accepted (supersedes the baseline configuration in 0006) |

To add one, copy [`template.md`](template.md), take the next number, and add a
row above.
