# Testing

recall has two suites: a fast **unit suite** that never loads a model or touches
real state, and a **model suite** (marker `model`) that runs against the real
model and the real production index. On top of that, two tools check an index
directly: `tools/verify_index.py` (integrity) and `experiments/evaluate.py`
(retrieval quality across configurations).

```powershell
.venv\Scripts\python.exe -I -m pytest            # unit suite (default; excludes `model`)
.venv\Scripts\python.exe -I -m pytest -m model   # model suite (real model + real index)
```

`pyproject.toml` sets `addopts = "-m 'not model' -p no:cacheprovider"`, so a
plain `pytest` never runs the slow tests.

The unit suite has 238 tests and the model suite 14 (measured with
`pytest --collect-only`, Windows, all PowerShell editions installed; a few
tests skip elsewhere). Both need recall importable under `python -I` (the
editable install of [USAGE §3.1](USAGE.md#31-virtual-environment)).

CI also runs these; locally they need the dev tools, hash-locked in
`requirements-dev.lock` (`uv pip install --require-hashes --no-deps --python .venv\Scripts\python.exe -r requirements-dev.lock`, or `make dev`):

```powershell
.venv\Scripts\python.exe -m ruff format --check .        # formatting
.venv\Scripts\python.exe -m ruff check .                 # lint (rules in pyproject.toml)
.venv\Scripts\python.exe -m mypy --platform win32        # types
.venv\Scripts\python.exe -I -m coverage run -m pytest -q  # unit suite with coverage
.venv\Scripts\python.exe -m coverage report --fail-under=80
```

## Unit suite

The fixtures in `tests/conftest.py` keep every unit test hermetic:

- **`RECALL_HOME` in a temp directory.** No unit test reads or writes
  `%LOCALAPPDATA%\recall`.
- **`HashEmbedder`** (`src/recall/embedder.py`): a deterministic, model-free
  embedder (hashed bag of words). Texts with words in common get a high cosine,
  which is enough to exercise indexing, incremental updates, deduplication and
  the threshold without loading hundreds of megabytes of weights.
- **A minimal library** of Markdown chapters written to the temp directory.
- **The service in a thread**, on an ephemeral loopback port with a real token,
  so the hook and client are tested over real HTTP.

| File | What it covers |
|---|---|
| `test_chunking.py` | Chunk sizes, sections, overlap, line ranges, skipped front-matter files (anchored names), the linear heading parser (time-bounded ReDoS regression), `C#`/`F#` headings, short tails kept. |
| `test_indexer.py` | Incremental add / change / delete, vector reuse, root precedence (a v2 root over the original), `--rebuild`, model-identity refusal, resuming an interrupted run, links/junctions outside the root ignored, a missing root keeps its files. |
| `test_search.py` | Cosine and provenance, dedup by file and section, collection filter, multi-query merge, near-zero lexical weight across languages, read-only searcher, background reload, rows checked against the snapshot after a rebuild, identity change surfaced as `load_error`. |
| `test_metadata.py` | Topic and year filters, books without a verified year, hostile `books_meta.json` normalized, a missing file reported, reload on change. |
| `test_snippets.py` | Sentence windows, picking the window that matches the query, snippet only on request and exactly for the first hit of the first files, hook rendering, service parameters. |
| `test_service.py` | Token required, loopback-only bind, `Host` check, request limits, ASCII JSON, single instance, warm-up, the absolute request-read deadline. |
| `test_server_proof.py` | `/proof` HMAC bound to the port; the hook and the client send no token or prompt to a server that fails it; malformed `service.json` is "no service". |
| `test_hook.py` | Fail-open (no service, garbage stdin, corrupt files, slow and byte-dripping services within budget), shadow is the default and never injects, prompt HMAC not text, active mode output, threshold, trivial and mechanical prompts skipped, log rotation, cp1252 console, stdlib-only imports, every header field squashed and defanged. |
| `test_mcp.py` | Handshake, `instructions` within Claude Code's 4096-character limit, tool schema, results marked as data, validation, lone surrogates and deep nesting rejected without stopping the stdio loop. |
| `test_hardening.py` | Fixes from the 2026-11-06 audit and review: non-finite scores, start lock and reused pids, connection cap, digest cache, truncated vectors. |
| `test_providers.py` | DirectML adapter choice: no hard-coded adapter, fallback across adapters then CPU, the choice logged. |
| `test_windows.py` | Query windows for long prompts and the `budget_ms` deadline. |
| `test_publication.py` | No private terms (checked by digest) in tracked text files, no source file names in `books_meta.json`, the local PDF map is git-ignored. |
| `test_start_script.py` | `start-recall.ps1` exits 0 silently without an index or venv, rotates `start.log`, leaves a healthy service alone, verifies `/proof`, never kills an unproven pid. Runs under every installed PowerShell (pwsh 7, Windows PowerShell 5.1); skipped if neither is installed. |
| `test_packaging.py` | Both locks pin every input with hashes, the dev group matches `requirements-dev.in`, one version everywhere (pyproject, package, CITATION.cff, CHANGELOG), `huggingface-hub` excluded, installed versions match the lock, offline env forced on import, config merge, hook defaults match package defaults, install docs' hook blocks are valid JSON and merge without reordering existing hooks. |
| `test_shadow_report.py` | `tools/shadow_report.py` summary over a synthetic log. |
| `test_verify_index.py` | `tools/verify_index.py` passes a good index and flags corrupted ones. |
| `test_engine_guard.py` | The service recreates the model session on an ONNX Runtime error (or two failures in a row), retries once, rebuilds once under concurrent failures, gives up (503 on `/health`) when the rebuild fails or the model identity changes, never gives up on a request that fails deterministically, releases replaced sessions, and logs to `serve.log` without query text. |
| `test_cli.py` | `recall index` (incremental, `--cpu`, `--dml-device`, `--limit-files`), `status`, `search` without the service and `eval` with an empty language group, all with the hash embedder. |

## Model suite (`-m model`)

These tests use the model configured in `%LOCALAPPDATA%\recall\config.json` and
the real index at `%LOCALAPPDATA%\recall\index.sqlite3` (they ignore the unit
tests' `RECALL_HOME`). They **skip** when there is no index or when more than
5 % of the library files are missing from it, because a half-built index would
give a false failure.

| Test | What it asserts |
|---|---|
| `test_regression.py::test_recall_floors` | R@5 and MRR@10 on the original 20 queries stay at or above the floors (TOTAL 0.95 / 0.86, EN→EN 1.00 / 0.94, ES→EN 0.90 / 0.78). |
| `test_regression_80.py` | Same on the 80-query set (TOTAL 0.98 / 0.89, EN→EN 1.00 / 0.89, ES→EN 0.97 / 0.90). |
| `test_threshold_separates_good_from_trivial` | With the production `hook.threshold`, no negative prompt that passes the hook's filters reaches the threshold, at least 80 % of positives do, and no positive is skipped by the filters. |
| `test_latency` | Search p50 ≤ 1.0 s with the model loaded. |
| `test_regression_data.py` | Retrieval on 20 queries (10 EN, 10 ES) about the data books added on 2026-11-06 stays at or above its floors. |
| `test_model_resilience.py` | A real DirectML session that starts failing (simulated lost device) is recreated by the service's guard and returns the same vector. |
| `test_dml_and_cpu_vectors_agree` | Embedding the same documents on DirectML and on CPU gives cosine ≥ 0.999, so mixing providers between indexing and querying does not change results. |

The floors are the measured values rounded down. The model, index and queries
are deterministic, so any drop is a regression, not noise. When the production
model or chunking changes, re-measure and update the floors in the same commit.

## Index integrity — `tools/verify_index.py`

```powershell
.venv\Scripts\python.exe -I tools\verify_index.py [HOME ...] [--sample 30] [--sample-cpu] [--no-source]
```

Read-only. Runs SQLite `quick_check`, chunk accounting, vector checks (length,
finite, unit norm, non-zero, same input → same vector), text ↔ embedded-input
sha256, one FTS row per chunk, and coverage of the library (`--no-source`
skips it). `--sample N` re-embeds N random chunks with the index's model and
requires cosine ≥ 0.99 with the stored vector; this is what catches a model
whose output depends on the batch it is embedded in. Exits 1 on any failure.
Run it after every full index and before promoting an experiment.

## Retrieval comparison — `experiments/evaluate.py`

```powershell
.venv\Scripts\python.exe -I experiments\evaluate.py [--queries FILE] [--only NAME ...]
```

Evaluates production and each experiment under `%LOCALAPPDATA%\recall-exp` on
the 80-query set (`tests/data/queries_books_80.json`): R@5 and MRR@10 overall
and by language pair, R@5 in MCP mode (Spanish + English in one call),
threshold separation against the negative prompts in
`src/recall/evaluation.py`, and latency. `recall eval --queries FILE` gives the
same metrics for the production index alone.

## Continuous integration

CI (`.github/workflows/ci.yml`) runs on every push to `main` and every pull
request:

- **test** (`windows-latest`, Python 3.12): installs exactly what the lock
  pins, then recall itself and the dev tools, and runs the unit suite with an
  80 % coverage floor:

  ```powershell
  python -m pip install --require-hashes --no-deps -r requirements.lock
  python -m pip install --no-deps --no-build-isolation -e .
  python -m pip install --require-hashes --no-deps -r requirements-dev.lock
  python -I -m coverage run -m pytest -q
  python -I -m coverage report --fail-under=80
  ```

- **quickstart** (`windows-latest`): runs the README's install block verbatim
  (extracted by `.github/scripts/readme_block.py`) and then
  `.venv\Scripts\python.exe -I -m recall --help`.
- **lint** (`ubuntu-latest`): `ruff format --check`, `ruff check`,
  `mypy --platform win32`.
- **security** (advisory): `pip-audit` over both locks.

`.github/workflows/codeql.yml` runs CodeQL for Python and GitHub Actions on
pushes, pull requests and weekly.

What CI **cannot** cover: anything under the `model` marker. It has no model
snapshot, no GPU and no library, so retrieval quality, the threshold,
DirectML/CPU agreement and real latency are checked only locally with
`pytest -m model`, `tools/verify_index.py` and `experiments/evaluate.py`.

## Manual check

After installing or changing the setup, follow the end-to-end check in
[USAGE.md §14](USAGE.md#14-troubleshooting): start the service, run
`recall status`, pipe a sample prompt to `recall_hook.py`, and confirm a new
`shadow.log` line.
