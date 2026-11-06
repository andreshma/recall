# 0006. Local ONNX model on DirectML, fully offline

- Status: Accepted (baseline model and chunking superseded by [0007](0007-e5-gemma2-large-chunks.md))
- Date: 2026-10-27

## Context and problem statement

Neither book text nor prompts may leave the machine. Embedding a ~100k-chunk
library and answering queries within the hook budget needs acceleration on a
Windows laptop.

## Decision drivers

- Local first: no external service at runtime.
- Query latency well under 1.5 s.
- Minimal dependencies.

## Considered options

- **A. Quantized ONNX model with onnxruntime-directml**, CPU fallback.
- **B. CPU-only inference.**
- **C. A hosted embeddings API.**

## Decision

Option **A**, starting with `embeddinggemma-300m` (8-bit ONNX, 768 dimensions)
and ~900-character chunks. Retrieval prefixes are applied to queries and
documents. The model loads from a local snapshot; `HF_HUB_OFFLINE=1` is set when
the package is imported and `huggingface-hub` is excluded from the lock file, so
there is no code path to download anything. The DirectML adapter is selectable
(`DmlExecutionProvider:<n>`); if DirectML fails to start, the session falls back
to CPU. DirectML and CPU vectors agree (cosine ≥ 0.999, covered by a test), so
mixing providers does not change the identity.

## Consequences

- Positive: a query embeds in ~0.1 s on DirectML versus seconds on a loaded CPU.
- Positive: fully offline operation.
- Negative: tied to Windows and DirectML for acceleration; other platforms run on CPU.
- Negative: some ONNX exports behave differently on DirectML (see the
  Qwen3-Embedding finding in [the paper](../paper.md)).
