# recall — User Manual

A step-by-step guide to installing recall, indexing a library of technical
books, and connecting it to Claude Code and Claude Desktop. For the design
rationale see [ARCHITECTURE.md](ARCHITECTURE.md); for the evaluation study see
[paper.md](paper.md); for the test suite see [TESTING.md](TESTING.md).

Paths below use `C:\Users\<user>\source\repos\recall` as the repository root
(`ROOT`). Replace `<user>` with your Windows user name.

## Contents

1. [What it does](#1-what-it-does)
2. [Requirements](#2-requirements)
3. [Install](#3-install)
4. [Prepare the library](#4-prepare-the-library)
5. [Index](#5-index)
6. [The local service](#6-the-local-service)
7. [Connect Claude Code](#7-connect-claude-code)
8. [Connect Claude Desktop](#8-connect-claude-desktop)
9. [Shadow mode → active](#9-shadow-mode--active)
10. [Command reference](#10-command-reference)
11. [Configuration (`config.json`)](#11-configuration-configjson)
12. [Maintenance tools](#12-maintenance-tools)
13. [Changing the model or chunking](#13-changing-the-model-or-chunking)
14. [Troubleshooting](#14-troubleshooting)
15. [Rollback and uninstall](#15-rollback-and-uninstall)

---

## 1. What it does

recall indexes a folder of Markdown books (one file per chapter) into a local
SQLite file with vectors and full text, and makes it searchable from Claude in
two ways:

| Piece | What it does |
|---|---|
| `recall index` | Chunks the `.md` files and stores text + vector in SQLite (incremental). |
| `recall serve` | Local service with the model loaded (127.0.0.1, Bearer token). |
| `recall_hook.py` | Before every Claude Code prompt, searches and injects ≤ 3 passages (shadow mode by default). |
| `recall mcp` | MCP tool `recall_books_search` for Claude Code and Desktop, with topic and year filters. |
| `data/books_meta.json` | Year (from the copyright page), edition, format and topics of each book. |
| `tools/build_pages.py` | Markdown line → printed PDF page map, to cite "p. 199". |
| `experiments/` | Index with another model or chunking without touching production, and compare. |

Everything is local. The model is loaded from disk, `HF_HUB_OFFLINE=1` is set
when the package is imported, and `huggingface-hub` is not even installed
(`requirements.excludes`). The service listens only on 127.0.0.1 and requires a
token. All state lives in `%LOCALAPPDATA%\recall` (or `RECALL_HOME`), never in
the repository.

### 1.1 What it does not do

- It does not convert PDF/EPUB to Markdown. It expects the library already
  converted, one file per chapter (§4).
- It does not download models at run time. The ONNX snapshot must already be on
  disk (§3.2).
- The hook does not inject anything until you switch it to `active` (§9).

---

## 2. Requirements

| Requirement | Notes |
|---|---|
| Windows 10/11 x64 | The lock file is resolved for `x86_64-pc-windows-msvc`. |
| Python 3.12 | `requires-python = ">=3.12,<3.13"`. The reference setup uses CPython 3.12.13 managed by [uv](https://docs.astral.sh/uv/). |
| uv | To create the venv and install from the lock. |
| A DirectX 12 GPU (optional) | `onnxruntime-directml` runs on the GPU; without one it falls back to CPU (slower indexing and queries). |
| PowerShell 7 (`pwsh`) | For `start-recall.ps1` and `experiments\run.ps1`. |
| Claude Code and/or Claude Desktop | To use the hook and the MCP server. |

Runtime dependencies (pinned with hashes in `requirements.lock`): `numpy`,
`onnxruntime-directml`, `tokenizers`. Dev: `pytest`.

---

## 3. Install

### 3.1 Virtual environment

```powershell
cd C:\Users\<user>\source\repos\recall
uv venv --python 3.12 .venv
uv pip sync --require-hashes --python .venv\Scripts\python.exe requirements.lock
Set-Content .venv\Lib\site-packages\recall_src.pth 'C:\Users\<user>\source\repos\recall\src'
```

The `.pth` file puts `src\` on the venv's path, so `python -I -m recall` works
without installing the package. Offline, from uv's cache:

```powershell
uv venv --offline --python 3.12.13 .venv
uv pip sync --offline --no-verify-hashes --python .venv\Scripts\python.exe requirements.lock
```

`--no-verify-hashes` is needed only offline, because uv's cache does not keep
the wheel-file hash. With network access, `--require-hashes` verifies every
hash in the lock. To regenerate the lock (also offline):

```powershell
uv pip compile --offline --python-version 3.12 --python-platform x86_64-pc-windows-msvc --generate-hashes --excludes requirements.excludes requirements.in -o requirements.lock
```

### 3.2 Embedding model

recall loads a quantized ONNX model from a local snapshot directory. The code
default is `onnx-community/embeddinggemma-300m-ONNX` in the Hugging Face cache
(`%USERPROFILE%\.cache\huggingface\hub\models--onnx-community--embeddinggemma-300m-ONNX\snapshots\<revision>`).
The production configuration uses `onnx-community/embeddinggemma-2-ONNX` (§11,
§13). Download the snapshot once by any means you prefer and point
`model.snapshot` in `config.json` at it. The snapshot must contain
`onnx/model_quantized.onnx` (and its `.onnx_data`, if any) and `tokenizer.json`.

`model.snapshot` accepts `~` (expanded to your profile directory).

### 3.3 Run the tests

```powershell
.venv\Scripts\python.exe -I -m pytest            # unit tests (no model)
.venv\Scripts\python.exe -I -m pytest -m model   # regression against the real model and index
```

See [TESTING.md](TESTING.md).

---

## 4. Prepare the library

recall reads Markdown, one file per chapter, under one directory per book:

```
<root>\<book-slug>\NN-chapter-title.md
```

The default root is `%USERPROFILE%\Downloads\Books TI\_md`. Front-matter files
(cover, copyright, table of contents…) are skipped by **exact** name match, so
a chapter called "disaster-recovery" is not mistaken for a "cover".

Roots are configured per collection in `config.json`, in **precedence order**:
if the same book directory exists under two roots, the first wins and the other
is treated as deleted. That lets a re-converted copy of the library replace the
old one book by book, without `--rebuild`:

```json
{"collections": {"books": {"roots": [
  "C:\\Users\\<user>\\Downloads\\Books TI\\_md_v2",
  "C:\\Users\\<user>\\Downloads\\Books TI\\_md"]}}}
```

The next `recall index` removes the old book and adds the new one, reusing the
vectors of any unchanged text. Do not put a half-converted root first: a book
partially present there hides the complete one in the next root. If you
overwrite files in place instead, the content hash detects it.

Per-book metadata (year, edition, format, topics) lives in
`data/books_meta.json`. Topics come from a closed taxonomy
(`recall.metadata.DOMAINS`). A book without a verified year is left as `null`
and never passes a year filter.

---

## 5. Index

```powershell
$py = 'C:\Users\<user>\source\repos\recall\.venv\Scripts\python.exe'
& $py -I -m recall index                 # incremental
& $py -I -m recall index --rebuild       # drop and rebuild
& $py -I -m recall index --dml-device 1  # pick the DirectML adapter
& $py -I -m recall index --cpu           # CPU only
& $py -I -m recall index --root D:\books\_md --limit-files 3   # quick trial
```

- **Incremental.** Same size and mtime → the file is not read. Otherwise its
  sha256 is compared, and only changed content is re-chunked. Each chunk reuses
  the vector of any previous chunk with the same embedded input. Deletions are
  applied last so their vectors can be reused.
- **Atomic batches.** Files and chunks are committed in groups; an interruption
  loses at most the group in progress. Re-running resumes.
- **Model identity.** The index records the model (repository, snapshot
  revision, sha256 of the ONNX and tokenizer files, dimension, prefixes,
  chunking). Indexing or searching with a different identity fails with
  `ModelMismatchError` instead of returning wrong results; changing model or
  chunking requires `--rebuild` (or a separate home, §13).
- **DirectML adapter.** On machines with several GPUs, `--dml-device N` picks
  the DXGI adapter. The default providers are `DmlExecutionProvider:1` then
  `CPUExecutionProvider`; adjust `model.index_providers` and
  `model.query_providers` in `config.json` for your machine.

The first full index of a ~3,000-chapter library takes hours on a GPU; later
runs are incremental (a no-change pass takes well under a second of work).

---

## 6. The local service

```powershell
& $py -I -m recall serve     # blocks; for debugging
& $py -I -m recall status    # index counts, model identity and service health
```

`recall serve` keeps the model loaded and listens on 127.0.0.1 on an ephemeral
port. It writes the port, token and pid to `%LOCALAPPDATA%\recall\service.json`.
Endpoints: `GET /health` and `POST /search` (`queries[]`, `k`, `collections`,
optional `snippet_chars` and `snippet_top`), both requiring the Bearer token.

In normal use you don't start it by hand: `start-recall.ps1` does it from the
Claude Code `SessionStart` hook (§7.3). The script:

1. Does nothing if `service.json` points to a live process whose `/health`
   answers with the token.
2. Otherwise launches `pythonw -I -m recall serve` hidden and **detached** from
   the session, so Claude Code does not kill it when the hook ends. It does not
   wait for the model to load: the first prompt of a session may get no
   context (the hook fails open), later ones will.
3. Starts nothing if there is no index, and notes it in `start.log`.

It always exits 0 and writes nothing to stdout. Details go to
`%LOCALAPPDATA%\recall\start.log` (rotated at 1 MiB).

If the service is down, `recall search` and the MCP server fall back to an
in-process searcher.

---

## 7. Connect Claude Code

### 7.1 Backup first

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
Copy-Item "$env:USERPROFILE\.claude\settings.json" "$env:USERPROFILE\.claude\settings.json.bak-recall-$stamp"
Copy-Item "$env:USERPROFILE\.claude.json"          "$env:USERPROFILE\.claude.json.bak-recall-$stamp"
```

### 7.2 MCP server (user scope)

Recommended, with `alwaysLoad` so `recall_books_search` is not deferred behind
tool search:

```powershell
claude mcp add-json recall --scope user '{"type":"stdio","command":"C:\\Users\\<user>\\source\\repos\\recall\\.venv\\Scripts\\python.exe","args":["-I","-m","recall","mcp"],"env":{"HF_HUB_OFFLINE":"1"},"alwaysLoad":true}'
```

Alternative without `alwaysLoad` (the name goes before `--env`, otherwise it is
read as another `KEY=VALUE` pair):

```powershell
claude mcp add recall --scope user --transport stdio --env HF_HUB_OFFLINE=1 -- "C:\Users\<user>\source\repos\recall\.venv\Scripts\python.exe" -I -m recall mcp
```

User scope is stored in `~\.claude.json`. The server's `instructions` are
truncated at 4096 characters by Claude Code; recall's are shorter (a test
checks it). Verify:

```powershell
claude mcp get recall
claude mcp list            # should show "✔ Connected" for recall
```

### 7.3 Hooks in `~\.claude\settings.json`

recall's hook entries are **appended** to each array; existing hooks are not
removed or reordered.

**SessionStart** — append this object as the **last** element of the
`"SessionStart"` array (create it if missing):

<!-- bloque-sessionstart -->
```json
{
  "hooks": [
    {
      "type": "command",
      "command": "pwsh",
      "args": ["-NoProfile", "-File", "C:\\Users\\<usuario>\\source\\repos\\recall\\start-recall.ps1"],
      "timeout": 10,
      "statusMessage": "Starting recall..."
    }
  ]
}
```

**UserPromptSubmit** — add the `"UserPromptSubmit"` key inside `"hooks"` (if
missing), with an array containing this object:

<!-- bloque-userpromptsubmit -->
```json
{
  "hooks": [
    {
      "type": "command",
      "command": "C:\\Users\\<usuario>\\source\\repos\\recall\\.venv\\Scripts\\python.exe",
      "args": ["-I", "C:\\Users\\<usuario>\\source\\repos\\recall\\recall_hook.py"],
      "timeout": 5
    }
  ]
}
```

The result looks like this (`...` = what was already there, untouched):

```json
"hooks": {
  "SessionStart": [ ...existing entries..., { <SessionStart block> } ],
  ...other events, untouched...,
  "UserPromptSubmit": [ { <UserPromptSubmit block> } ]
}
```

Why this shape: with `args`, `command` is executed directly, without a shell.
`timeout` is in seconds; the hook enforces its own 1.5 s budget and fails open,
so 5 s is only a safety net for interpreter startup. `UserPromptSubmit` takes
no `matcher` (it always fires). Output goes through
`hookSpecificOutput.additionalContext`, limited to 10,000 characters; recall
injects at most 3 × 700 characters plus a header.

Validate the JSON after editing:

```powershell
Get-Content "$env:USERPROFILE\.claude\settings.json" -Raw | ConvertFrom-Json | Out-Null; "JSON OK"
```

### 7.4 What the hook does

- Thin client: it does not load the model; it asks the local service with the
  token from `service.json`.
- Skips trivial prompts (short, `/`, `!`, `#` commands, confirmations like "ok"
  or "continue") and mechanical requests (git, file operations, edits; see
  `MECHANICAL` in `recall_hook.py`).
- Injects up to `max_snippets` passages of `max_chars`, one per file, only when
  the best cosine clears `threshold`. Each passage is the window of the chunk
  that shares the most terms with the prompt, with its citation (book — chapter
  — section, path:lines, page when known).
- Fails open on any error or delay: exit 0, no output, the prompt is untouched.
- Writes ASCII-only JSON as bytes, so a non-UTF-8 Windows console cannot drop
  the injection.

---

## 8. Connect Claude Desktop

The config file depends on the install type:

- Standard installer: `%APPDATA%\Claude\claude_desktop_config.json`.
- Microsoft Store (MSIX) install:
  `%LOCALAPPDATA%\Claude-Data\<package-id>\claude_desktop_config.json`.

Settings → Developer → **Edit Config** in Desktop always opens the right file.
Back it up, then **add** this entry inside `"mcpServers"` (create the key if
missing) without removing the others:

<!-- bloque-desktop -->
```json
{
  "recall": {
    "command": "C:\\Users\\<usuario>\\source\\repos\\recall\\.venv\\Scripts\\python.exe",
    "args": ["-I", "-m", "recall", "mcp"],
    "env": { "HF_HUB_OFFLINE": "1" }
  }
}
```

Quit Desktop completely (also from the system tray) and reopen it. Logs:
`%APPDATA%\Claude\logs\mcp-server-recall.log`. Desktop has no hooks, so it uses
only the MCP tool; the tool description carries the essentials (query in both
Spanish and English, results are data, cite the source).

---

## 9. Shadow mode → active

Without `config.json`, the hook runs in **shadow** mode: it searches, decides,
and logs to `%LOCALAPPDATA%\recall\shadow.log` (prompt hash, cosines, the paths
it would have injected; never the prompt text) and **injects nothing**. The log
rotates at 5 MiB to `shadow.log.1`, keeping 3 copies.

Review it after a few days:

```powershell
.venv\Scripts\python.exe -I tools\shadow_report.py
.venv\Scripts\python.exe -I tools\shadow_report.py --since 2026-11-05
```

The report reads `shadow.log` and its rotations and prints decision counts,
latency (p50/p95/max), quantiles of the best cosine, how many searched prompts
would have been injected at each candidate threshold, and the most frequent
paths. Since only a hash of the prompt is logged, false positives are reviewed
through those paths.

Switch to **active** (the next hook invocation reads it; nothing to restart):

```powershell
Set-Content "$env:LOCALAPPDATA\recall\config.json" '{"hook": {"mode": "active"}}' -Encoding utf8
```

If `config.json` already exists, edit its `"hook"` section instead of
overwriting the file. Back to shadow: `"mode": "shadow"`. Turn it off without
uninstalling: `"mode": "off"`.

---

## 10. Command reference

| Command | What it does |
|---|---|
| `recall index [--rebuild] [--root DIR]... [--limit-files N] [--dml-device N] [--cpu]` | Index the library (incremental by default). |
| `recall search QUERY... [-k 5] [--text]` | Search with one or more phrasings (e.g. Spanish and English); uses the service if alive, else in-process. |
| `recall serve` | Run the local search service (blocks). |
| `recall mcp` | Run the stdio MCP server. |
| `recall status` | Index counts, generation, model identity and service health. |
| `recall eval [--queries FILE] [--out FILE]` | R@5 / MRR, latency and threshold calibration over a query set. |

All are run as `.venv\Scripts\python.exe -I -m recall <command>`. Examples:

```powershell
& $py -I -m recall search "tasa de consumo del presupuesto de errores" "error budget burn rate" --text
& $py -I -m recall eval --queries tests\data\queries_books_80.json
```

---

## 11. Configuration (`config.json`)

`%LOCALAPPDATA%\recall\config.json` (or `$RECALL_HOME\config.json`) is optional
and merged section by section over the code defaults, so you only write what
changes. An unreadable file is ignored (the hook and service fail open).

| Section | Key | Default | Purpose |
|---|---|---|---|
| `collections` | `books.roots` | `[%USERPROFILE%\Downloads\Books TI\_md]` | Library roots, in precedence order (§4). |
| `chunking` | `target`, `max_chars`, `min_chars`, `overlap` | 900, 1000, 450, 150 | Chunk sizes in characters. Part of the index identity. |
| `model` | `snapshot`, `name` | embeddinggemma-300m snapshot | Model directory and name. Part of the identity. |
| `model` | `onnx_file` | `onnx/model_quantized.onnx` | ONNX file inside the snapshot. |
| `model` | `dim` | 768 | Embedding dimension. |
| `model` | `doc_max_tokens`, `query_max_tokens` | 320, 256 | Truncation. Raise `doc_max_tokens` with larger chunks (~1 token per 3–4 characters). |
| `model` | `index_providers`, `query_providers` | `DmlExecutionProvider:1`, CPU | ONNX Runtime providers, in order. |
| `model` | `batch_size` | 32 | Indexing batch. |
| `search` | `fts_weight_same_lang`, `fts_weight_cross_lang` | 0.25, 0.02 | Lexical weight in RRF when query and chunk share a language / don't. |
| `search` | `rrf_k`, `vec_weight`, `candidates`, `min_reload_s` | 60, 1.0, 100, 30 | Fusion and reload tuning. |
| `hook` | `mode` | `shadow` | `shadow`, `active` or `off`. |
| `hook` | `threshold` | 0.58 | Minimum cosine to inject. Model-specific (§13). |
| `hook` | `max_snippets`, `max_chars` | 3, 700 | Passages injected and characters per passage. |
| `hook` | `budget_s` | 1.5 | Total time budget of the hook. |
| `hook` | `min_prompt_chars` | 20 | Shorter prompts are skipped. |

The production configuration (embeddinggemma-2, ~3,500-character chunks,
threshold 0.75) is `experiments/configs/e5-gemma2-chunk3500.json` plus a
`hook` section:

```json
{"chunking": {"target": 3200, "max_chars": 3500, "min_chars": 1500, "overlap": 400},
 "model": {"snapshot": "~/.cache/recall-models/onnx-community--embeddinggemma-2-ONNX/<revision>",
           "name": "onnx-community/embeddinggemma-2-ONNX", "doc_max_tokens": 1100, "batch_size": 8},
 "hook": {"mode": "shadow", "threshold": 0.75}}
```

Environment variables:

| Variable | Purpose |
|---|---|
| `RECALL_HOME` | State directory instead of `%LOCALAPPDATA%\recall` (the tests use it). |
| `HF_HUB_OFFLINE` | Set to `1` by the package on import; also set in the MCP entries. |

---

## 12. Maintenance tools

### 12.1 Verify an index — `tools/verify_index.py`

Read-only integrity check. `HOME` is a directory with `index.sqlite3` and,
optionally, `config.json`; the default is `%LOCALAPPDATA%\recall`.

```powershell
.venv\Scripts\python.exe -I tools\verify_index.py
.venv\Scripts\python.exe -I tools\verify_index.py $env:LOCALAPPDATA\recall --sample 30
.venv\Scripts\python.exe -I tools\verify_index.py <home> --sample 30 --sample-cpu --no-source
```

Checks: SQLite `quick_check`; chunk accounting per file (no orphans); vectors
(length, finite, unit norm, none zero; same input → same vector); stored text
matches the embedded input (sha256); one FTS row per chunk; every library file
indexed and unchanged (skip with `--no-source`); and with `--sample N`,
re-embeds N random chunks and requires cosine ≥ 0.99 with the stored vector
(`--sample-cpu` forces CPU). Exits 1 if any check fails.

### 12.2 Page map — `tools/build_pages.py`

Builds `pages.sqlite3` (default in `%LOCALAPPDATA%\recall`), a map from each
Markdown line to its PDF page, used to cite the printed page number. It needs
PyMuPDF, which recall itself does not depend on, so run it with a separate
Python environment that has it:

```powershell
& '<tools-venv>\Scripts\python.exe' -I tools\build_pages.py [--out pages.sqlite3] [--only <book-slug> ...]
```

For each PDF it searches each paragraph's normalized text in the PDF, advancing
a cursor through the book; a paragraph not found inherits the previous page and
is cited as approximate (`p. ~N`). The printed page label is cited when the PDF
defines one. EPUBs have no fixed pages and are cited by chapter. The map is
independent of the index (any model or chunking) and optional. Regenerate it
after re-converting books.

### 12.3 Experiments — `experiments/`

```powershell
pwsh -File experiments\run.ps1                                # every config, in series
pwsh -File experiments\run.ps1 -Only e5-gemma2-chunk3500      # one
pwsh -File experiments\run.ps1 -LimitFiles 3 -Tag smoke       # short trial
.venv\Scripts\python.exe -I experiments\evaluate.py           # compare production and experiments
.venv\Scripts\python.exe -I experiments\evaluate.py --only e5-gemma2-chunk3500
```

Each `experiments\configs\*.json` is indexed into
`%LOCALAPPDATA%\recall-exp\<name>` with its own `config.json` and index; the
production index is never touched. Runs are serial because they share the GPU.
Progress goes to `<home>\index.log`, a summary to
`%LOCALAPPDATA%\recall-exp\runs.log`. `evaluate.py` reports R@5, MRR, MCP-mode
R@5, threshold separation and latency over the 80-query set
(`--queries` to use another).

---

## 13. Changing the model or chunking

`config.json` can change `model` (name, snapshot, prefixes, pooling
`sentence_embedding`/`mean`/`last`, padding side, EOS) and `chunking`. Both are
part of the index identity, so changing them requires `recall index --rebuild`.

Recommended procedure:

1. Add a config under `experiments\configs\`, index it with
   `experiments\run.ps1 -Only <name>` and verify it with
   `tools\verify_index.py %LOCALAPPDATA%\recall-exp\<name> --sample 30`. The
   sample re-embedding catches models whose vectors depend on the batch (see
   [paper.md §5](paper.md)).
2. Compare with `experiments\evaluate.py`.
3. To promote it, stop the service, move the current index (`index.sqlite3`,
   `-wal`, `-shm`, `model_digest.json`) and `shadow.log` to a backup directory,
   copy the experiment's index and `config.json` into `%LOCALAPPDATA%\recall`,
   and add the `hook` section.
4. **Recalibrate the threshold.** Cosines change scale between models (about
   0.5 for embeddinggemma-300m, about 0.7 for embeddinggemma-2). Use the
   separation reported by `evaluate.py` for a starting value, then the shadow
   log. Archive the old `shadow.log`: its cosines are on another scale.

---

## 14. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| The first prompt of a session gets no context | Expected: the service is still loading the model. Later prompts work. |
| The hook never injects | It is in `shadow` mode (the default), the threshold is too high for the model, or the prompt was skipped as trivial/mechanical. Check `shadow.log` decisions. |
| `recall status` shows the service as unavailable | Run `start-recall.ps1` and look at `%LOCALAPPDATA%\recall\start.log`; with no index, nothing starts. |
| `ModelMismatchError` | The configured model or chunking differs from the index. Restore the matching `config.json`, or `recall index --rebuild`. |
| Indexing is slow | DirectML is not in use or the wrong adapter is selected. Try `--dml-device 0`/`1`; `recall index` prints the providers it uses. |
| Moving or deleting the index fails ("file in use") | A `recall serve` or `recall mcp` process holds it open. Stop the service (pid in `service.json`) and reconnect MCP afterwards (`/mcp` in Claude Code). |
| `recall` does not appear in `/mcp` | `claude mcp list`; check that the `python.exe` path in the entry exists. |
| Desktop does not show the tool | Wrong config file (MSIX vs standard install, §8); fully quit Desktop; read `mcp-server-recall.log`. |

Manual end-to-end check:

```powershell
$py = 'C:\Users\<user>\source\repos\recall\.venv\Scripts\python.exe'
pwsh -NoProfile -File C:\Users\<user>\source\repos\recall\start-recall.ps1
Start-Sleep 20; & $py -I -m recall status
'{"hook_event_name":"UserPromptSubmit","prompt":"explain the cone of uncertainty in estimation","cwd":"C:\\"}' |
  & $py -I C:\Users\<user>\source\repos\recall\recall_hook.py
Get-Content "$env:LOCALAPPDATA\recall\shadow.log" -Tail 1
```

In a new Claude Code session, `/mcp` should list `recall`, and a question such
as *"what is a bounded context according to the books?"* should cite book and
chapter. In shadow mode, `shadow.log` gains a `would-inject` line per relevant
prompt.

---

## 15. Rollback and uninstall

**Roll back a model change:** stop the service, move the backed-up index files
and `shadow.log` back into `%LOCALAPPDATA%\recall`, and restore (or delete) the
previous `config.json`.

**Uninstall:**

```powershell
claude mcp remove --scope user recall
```

- Desktop: delete the `"recall"` entry from `mcpServers` in
  `claude_desktop_config.json` and restart Desktop.
- `settings.json`: remove the `"SessionStart"` element that calls
  `start-recall.ps1` and the whole `"UserPromptSubmit"` entry for recall, or
  restore the backup from §7.1.
- Stop the service and delete all state (including the index):

```powershell
$svc = Get-Content "$env:LOCALAPPDATA\recall\service.json" -Raw | ConvertFrom-Json
Stop-Process -Id $svc.pid -Force
Remove-Item "$env:LOCALAPPDATA\recall" -Recurse -Force
```
