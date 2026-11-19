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
| [0008](0008-query-windows-for-long-prompts.md) | Split long prompts into ≤32-token query windows | Accepted |
| [0009](0009-queries-on-cpu.md) | Embed queries on CPU, index on DirectML | Superseded by 0010 |
| [0010](0010-directml-one-query-shape.md) | Queries on DirectML at one fixed input shape | Accepted (supersedes 0009) |
| [0011](0011-hub-prior-for-the-hook-threshold.md) | Subtract a per-chunk hub prior before the hook threshold | Accepted (refines 0002) |

To add one, copy [`template.md`](template.md), take the next number, and add a
row above.
