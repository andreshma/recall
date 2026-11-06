# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security
- Fixes from the [2026-11-06 audit](audits/report-recall-2026-11-06.md) and
  the [adversarial review](docs/ADVERSARIAL_REVIEW.md):
  - Every field taken from the index has `<`/`>` neutralized in the hook and
    MCP output, so book content cannot close the data block (AR-01–03).
  - The hook's 1.5 s budget is now a wall-clock deadline (AR-04).
  - Non-finite scores are rejected in search, hook and MCP (AR-07).
  - The shadow-log prompt hash is an HMAC with a per-install key (AR-12).
  - MCP errors return only the exception type (AR-16); `pip-audit` pinned in CI.
  - The service rejects `Infinity`/`NaN`/overflowing numbers with 400 and
    closes idle connections after 10 s (AR-10, AR-11).

### Fixed
- `doc_max_tokens` is part of the index identity when not the default;
  existing indexes are accepted and upgraded on the next write (AR-05).
- A stale `service.json` whose PID was reused no longer keeps recall from
  starting, and concurrent starts no longer leave an orphaned service
  (AR-08, AR-09).
- MCP score guidance follows the configured threshold (AR-13).
- `tools/shadow_report.py` covers the embeddinggemma-2 threshold range.

### Changed
- Privacy wording: retrieval is local, but injected passages reach the
  assistant's provider; the hook cites library-relative paths (AR-06).

### Added
- `config.example.json` with the recommended E5 configuration.
- `audits/` (360° report, summary, SBOM) and `docs/ADVERSARIAL_REVIEW.md`.
- Repository standards: Apache-2.0 license, contributing guide, security
  policy, code of conduct, issue/PR templates, CI on Windows with the locked
  environment, Dependabot, architecture docs, threat model and ADRs.

### Changed
- Documentation in English; installation moved from `INSTALL.md` to
  [docs/USAGE.md](docs/USAGE.md).
- Default paths derive from the user's home directory instead of fixed paths.
- Experiment configs reference model snapshots under `~/.cache`.

## [0.2.0] - 2026-11-05

### Added
- Chunking and model experiments (`experiments/`), the 80-query evaluation set
  and the study in [docs/paper.md](docs/paper.md).
- `tools/verify_index.py`: read-only integrity verifier (SQLite check, chunk
  accounting, vector sanity, text–vector correspondence, FTS rows, library
  coverage, re-embedded sample).
- `tools/shadow_report.py`: summarizes the shadow log and shows how many real
  prompts would be injected at each candidate threshold.
- Passage trimming: the service returns the window of each chunk that shares
  the most terms with the question (`snippet_chars`, `snippet_top`).

### Changed
- Production configuration E5: embeddinggemma-2 with ~3,500-character chunks
  (34,721 chunks, R@5 0.988 on 80 queries); hook threshold 0.75.

### Found
- The quantized ONNX export of Qwen3-Embedding-0.6B is not padding-invariant
  (cosine 0.92–0.96 against the unpadded vector); not used.

## [0.1.1] - 2026-11-03

### Added
- Topic-domain and publication-year filters (`data/books_meta.json`).
- Page citations for PDFs via `tools/build_pages.py` (96.1 % exact).
- Configurable model and chunking, part of the index identity.

## [0.1.0] - 2026-10-27

### Added
- Hybrid index (SQLite, float32 vectors + FTS5, RRF), incremental indexing with
  atomic batch writes and model identity guard.
- Local service (`recall serve`, 127.0.0.1 + Bearer token), fail-open
  `UserPromptSubmit` hook with shadow mode, and a dependency-free MCP server
  (`recall_books_search`).
