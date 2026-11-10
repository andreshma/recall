# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the
version is 0.x, a minor release may contain breaking changes; they are marked
**Breaking** and come with an upgrade note.

## [Unreleased]

## [0.3.1] - 2026-11-09

### Changed

- The CPU fallback no longer inherits the DirectML-tuned session options. The
  memory pattern (a CPU speed-up ONNX Runtime enables by default, but which
  DirectML does not support) now stays on when inference runs on CPU, and the
  Run lock that serializes DirectML — which cannot run concurrent calls — is
  skipped on CPU, where ONNX Runtime allows concurrent `Run`, so the
  multithreaded service parallelizes embeddings under load. Embedding output is
  unchanged; no re-index is needed.

### Fixed

- The shared tokenizer is now guarded while its truncation/padding is mutated,
  so concurrent requests cannot corrupt each other's encoding.

## [0.3.0] - 2026-11-09

First public release. It gathers everything since 0.2.0: the fixes from the
[2026-11-06 audit](audits/report-recall-2026-11-06.md) (AR-xx, M/L/I-x), the
translation of the code to English, and the fixes from the
[2026-11-09 audit and adversarial review](audits/report-recall-2026-11-09.md)
(ADV-x, F-xx).

### Upgrading from 0.2

1. Stop the running service once (`Stop-Process` on the pid in
   `%LOCALAPPDATA%\recall\service.json`). A 0.2 service has no `/proof`
   endpoint, so new clients do not talk to it and the launcher starts a second
   one next to it.
2. Reinstall: `uv pip sync --require-hashes ... requirements.lock`, then the
   new editable install `uv pip install --no-deps --no-build-isolation ... -e .`
   (README quickstart). It replaces the old `recall_src.pth` step.
3. If you relied on the old default library root, or on DirectML adapter 1
   being picked first, set `collections.books.roots` and
   `model.index_providers` / `model.query_providers` in `config.json`.
4. If you edited `data/books_meta.json` or query sets with the Spanish topic
   keys, rename them (see Changed). `recall index` needs no rebuild for this
   release; chunk text changes only for files that are re-chunked (see F-11).

### Added

- **Server proof** (ADV-2, F-07): `GET /proof?nonce=<n>` answers
  `HMAC-SHA256(token, "recall-proof:<n>:<port>")`; the hook, the client and
  `start-recall.ps1` verify it before sending the token or a prompt.
- `/health` reports `providers`, `device_id`, `meta_error` and `load_error`.
- 22 data and data-science books (Fundamentals of Data Engineering, Data
  Contracts, Python for Data Analysis 3e and others) that were in the library
  folder but missing from the conversion manifest; `data/books_meta.json`
  covers all 148 books, and ships inside the package (F-10).
- Model suite: recovery from a lost DirectML device with the real model, and
  a retrieval floor for the new data books (`queries_books_data.json`).
- Engine guard: when the ONNX Runtime session dies (DirectML device lost after
  the laptop resumes from sleep) the service recreates it and retries; if that
  fails it answers `/health` with 503 and exits, and `start-recall.ps1`
  replaces a 503 service. Errors go to `serve.log`.
- Query windows for long prompts (`search.query_window_tokens`, ADR 0008); the
  hook sends `budget_ms` so the service stops embedding windows in time.
- `config.example.json` with the recommended E5 configuration;
  `tests/data/prompts_long_20.json` (20 long prompts for the hook path).
- Docs: the Markdown input contract (USAGE §4), the `/proof` protocol and
  `service.json` rules, an upgrade note, model download instructions.
- Open-source readiness: Apache-2.0 license and NOTICE, `CITATION.cff`,
  contributing guide, security policy (supported versions, private
  vulnerability reporting), code of conduct with a private contact path,
  issue/PR templates, architecture docs, threat model, ADRs, `audits/` and
  `docs/ADVERSARIAL_REVIEW.md`.
- CI: unit tests on Windows with the locked environment and an 80 % coverage
  floor (L7), a `lint` job (ruff format and check, mypy; L6), an advisory
  `pip-audit` job, a `quickstart` job that runs the README install block
  verbatim, a CodeQL workflow (Python and Actions), and Dependabot.
- `requirements-dev.in` / `requirements-dev.lock`: the dev and CI tools
  (ruff, mypy, coverage, pip-audit), hash-locked; `hatchling` and `editables`
  in the runtime lock for a hash-only editable install.
- Tests: `test_server_proof.py`, `test_providers.py`, `test_publication.py`,
  `test_engine_guard.py`, CLI tests with the hash embedder, and regression
  tests for every finding below.

### Changed

- **Breaking — default library root** (F-05): `~\books\md`
  (`%USERPROFILE%\books\md`) instead of the previous, machine-specific default.
  A configured root that does not exist keeps its indexed files and is
  reported as an error (`recall index` exits 1) instead of emptying the index.
- **Breaking — default DirectML providers** (F-09): `DmlExecutionProvider`
  without a fixed adapter (was `DmlExecutionProvider:1`). `:N` still picks a
  DXGI adapter; if it cannot start, or comes up without DirectML, adapters
  0–3 are tried and then CPU, and the choice is logged and shown in `/health`.
- **Breaking — `books_meta.json` fields removed** (F-01): `source_file` and
  `year_evidence` are no longer in the repository file (title, author, format,
  year, year_source, edition, domains, lang remain).
- **Breaking — `tools/build_pages.py` CLI** (F-01): PDF locations come from a
  local, git-ignored map (`--sources`, default
  `data/books_sources.local.json`) with `--pdf-dir` and `--md-root`, instead
  of `source_file` in the metadata.
- **Breaking — English hook tag and MCP delimiters**: the hook block is
  `<recall_books source="local index of technical books">` (was
  `<recall_libros …>`), and MCP results use `<<<data source=…>>>`,
  `<<<passage n>>>` and `source:` (were `<<<datos …>>>`, `<<<fragmento n>>>`,
  `fuente:`).
- **Breaking — topic keys renamed to English**: `arquitectura` →
  `architecture`, `datos` → `data`, `seguridad` → `security`,
  `sre-observabilidad` → `sre-observability`, `agile-gestion` →
  `agile-management`, `producto` → `product`, `ia-ml` → `ai-ml`,
  `programacion` → `programming`, `requisitos-analisis` →
  `requirements-analysis`, `liderazgo-organizacion` →
  `leadership-organization` (MCP `domains` filter and `books_meta.json`). The
  eval query type is `book` (was `libro`) and the cross-language group is
  `ES->EN (cross)`.
- Translation to English: comments, docstrings, the MCP instructions and tool
  description, the hook's injected header, CLI help and output, user-facing
  errors (L9), tool help and test prose, and all documentation (installation
  moved from `INSTALL.md` to [docs/USAGE.md](docs/USAGE.md)). Spanish remains
  only where it is data: stopword lists, the regexes that recognize Spanish
  prompts, and the Spanish test queries and prompts.
- Installation: an editable install (`uv pip install --no-deps
  --no-build-isolation -e .`) replaces the hand-written `recall_src.pth`;
  README quickstart, CONTRIBUTING, Makefile and CI use it (F-04).
- `start-recall.ps1` launches `pythonw -I -c "...main(['serve'])" <statedir>`
  and so honors `RECALL_HOME` (F-06).
- Privacy wording: retrieval is local, but injected passages reach the
  assistant's provider; the hook cites library-relative paths, the MCP tool
  absolute ones (AR-06, M4, F-16).
- `recall eval` needs `--queries` outside a source checkout (exits 2 with a
  message) (F-10); `recall serve` exits 1 when the engine guard gives up.
- Lower cognitive complexity (SonarQube S3776 and related) in the HTTP
  handler, `verify_index`, `evaluate`, `query_windows` and the indexer, with no
  behavior change; `verify_index --sample` is deterministic.
- Chunking (F-11): a closing `#` run is stripped from a heading only when
  whitespace precedes it ("C#", "F#" keep their hash); a short final section
  is attached to the previous chunk instead of being dropped; front-matter
  patterns are anchored ("contents-of-a-container-image" is a chapter). Chunk
  text changes only for files that are re-chunked.
- Dependencies: numpy 2.5.4, tokenizers 0.23.3, pytest 9.2.0, with the lock
  regenerated and onnxruntime kept on the same version on every platform;
  token ids and embeddings unchanged (300 sampled chunks, max abs difference
  0.0). Code defaults stay the baseline configuration; paths derive from the
  user's home directory; experiment configs reference snapshots under
  `~/.cache`.
- `doc_max_tokens` is part of the index identity when not the default;
  existing indexes are accepted and upgraded on the next write (AR-05, M3).
- MCP score guidance follows the configured threshold (AR-13, M6).

### Removed

- `source_file` / `year_evidence` from `data/books_meta.json` (see Changed).
- The `recall_src.pth` install step (replaced by the editable install).

### Fixed

- **ADV-1** A query that fails the same way every time (e.g. a lone
  surrogate) could exhaust the engine guard and shut the service down: such
  queries get 400 / `isError`, and a non-engine error rebuilds the session at
  most once per run of failures and never counts toward giving up; replaced
  sessions are released (F-18).
- **ADV-3** ReDoS in the heading parser (a heading with 4,000 spaces took
  minutes): the parser is now linear.
- **ADV-4** A hostile `books_meta.json` broke searches: entries are
  normalized on load.
- **ADV-6** Slow-loris: an absolute 3 s request-read deadline per connection.
- **ADV-8** A directory junction inside a library root made outside content
  indexable: links and junctions are ignored and real paths must stay under
  the root.
- **ADV-9 / ADV-10** Deeply nested JSON stopped the MCP stdio server and
  dropped service connections: -32700 / 400, and both keep serving.
- **F-03** After `--rebuild`, a running service could return the wrong
  passage (reused row ids), and after an identity change keep old vectors
  joined to new rows: rows are verified against the snapshot's `input_sha`,
  and an identity change makes `/health` and `/search` answer 503.
- **F-06** `start-recall.ps1` failed every health check under Windows
  PowerShell 5.1 (`Invoke-RestMethod -NoProxy`): it now works in 5.1 and
  pwsh 7.
- **F-10** Filters and `recall eval` broke from a wheel: metadata is package
  data, and a missing meta file is reported (`meta_error`), not silent.
- **F-12** A malformed `service.json` crashed `recall serve` or surfaced as
  "recall unavailable: KeyError": one validating reader everywhere.
- **F-17** Tests: vacuous assertions in `test_snippets.py` and the lock-hash
  check made strict; timing-sensitive asserts use bounded polling or generous
  bounds around the operation only.
- The hook timed out on almost every real prompt with E5: DirectML takes
  seconds once a call exceeds 32 tokens (query windows, ADR 0008).
- The service crashed in the GPU driver under concurrent requests: DirectML
  `Run` calls are serialized, with `mem_pattern` off and sequential execution.
- The hidden service ran throttled as background work (EcoQoS); it now opts
  out of power throttling on start.
- After the laptop resumed from sleep the service stayed up with `/health` ok
  while every search failed (engine guard, see Added).
- Book metadata was read once at start; the searcher now re-reads the file
  when it changes and keeps the old data if the new file is unreadable.
- Queries B04en/B04es also accept *Data Management at Scale* ch. 2.
- `recall eval` crashed when a query set had no queries for a language group;
  `recall index` assumed every embedder has `providers`.
- `docs/architecture.md` and `.github/PULL_REQUEST_TEMPLATE.md` had
  upper-case duplicates that clashed on case-insensitive checkouts; one copy
  of each remains.
- A stale `service.json` whose PID was reused no longer keeps recall from
  starting, and concurrent starts no longer leave an orphaned service
  (AR-08, AR-09).
- `tools/shadow_report.py` covers the embeddinggemma-2 threshold range.
- A row with a wrong-length vector is skipped with a warning instead of
  aborting the index load (AR-15).

### Security

- **ADV-2 / F-07** Clients sent the bearer token and up to 1,000 characters
  of the prompt to whatever process held the port of a stale `service.json`,
  and the launcher could kill an unrelated pid: fixed by the server proof
  (see Added); the launcher only stops the pid a proven server reports.
- **ADV-5 / ADV-7 / F-08** The hook did not defang `year`, and newlines in
  book/chapter/section could forge an entry line (hook) or a `source:` line
  (MCP): every header field is collapsed to one line and defanged in both
  renderers.
- **ADV-11** Fullwidth and other look-alike angle brackets: `<`, `>` and
  their look-alikes become `&lt;`/`&gt;` in every field.
- **F-01 / F-05** Publication hygiene: no source file names or private paths
  in the repository; `tests/test_publication.py` guards it.
- **F-13** CI: checkouts without persisted credentials, CodeQL, hash-locked
  dev tools.
- From the 2026-11-06 audit: every field taken from the index has `<`/`>`
  neutralized in the hook and MCP output (AR-01–03, M1); the hook's 1.5 s
  budget is a wall-clock deadline (AR-04, M2); non-finite scores are rejected
  (AR-07, L1); the shadow-log prompt hash is an HMAC with a per-install key
  (AR-12, L4); MCP errors return only the exception type (AR-16); `pip-audit`
  pinned (AR-17); the service rejects `Infinity`/`NaN`/overflowing numbers
  with 400 (AR-11), caps concurrent connections at 32 and closes idle ones
  (AR-10), and re-hashes the model files on start (AR-14).

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

<!-- Versions before 0.3.0 predate the public repository and have no tags. -->
[Unreleased]: https://github.com/andreshma/recall/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/andreshma/recall/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/andreshma/recall/releases/tag/v0.3.0
