# recall — User Manual

A step-by-step guide to installing recall, indexing a library of technical
books, and connecting it to Claude Code and Claude Desktop. For the design
rationale see [architecture.md](architecture.md); for the evaluation study see
[paper.md](paper.md); for the test suite see [TESTING.md](TESTING.md).

Paths below use `C:\Users\<user>\source\repos\recall` as the repository root
(`ROOT`). Replace `<user>` with your Windows user name. Clone into that folder
name so the paths match:

```powershell
git clone https://github.com/andreshma/recall.git C:\Users\<user>\source\repos\recall
```

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

Retrieval is local. The model is loaded from disk, `HF_HUB_OFFLINE=1` is set
when the package is imported, and `huggingface-hub` is not even installed
(`requirements.excludes`). The service listens only on 127.0.0.1 and requires a
token. All state lives in `%LOCALAPPDATA%\recall` (or `RECALL_HOME`), never in
the repository. What recall *returns* — passages, citations and, from the MCP
tool, absolute file paths — becomes part of your conversation and so reaches
whatever provider runs your assistant.

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
| Windows PowerShell 5.1 or PowerShell 7 (`pwsh`) | `start-recall.ps1` supports and is tested on both (the tests run it with every edition installed). `experiments\run.ps1` is run with `pwsh`. |
| Claude Code and/or Claude Desktop | To use the hook and the MCP server. |

Runtime dependencies (pinned with hashes in `requirements.lock`): `numpy`,
`onnxruntime-directml`, `tokenizers`; plus `pytest`, `hatchling` and
`editables` for the tests and the editable install. Dev tools (ruff, mypy,
coverage, pip-audit) are hash-locked in `requirements-dev.lock`.

---

## 3. Install

### 3.1 Virtual environment

```powershell
cd C:\Users\<user>\source\repos\recall
uv venv --python 3.12 .venv
uv pip sync --require-hashes --python .venv\Scripts\python.exe requirements.lock
uv pip install --no-deps --no-build-isolation --python .venv\Scripts\python.exe -e .
.venv\Scripts\python.exe -I -m recall --help
```

The last install is an **editable** install of recall itself: it makes
`python -I -m recall` work (isolated mode ignores `PYTHONPATH` and the current
directory), which the hook entries, the MCP entries and the service launched by
`start-recall.ps1` all rely on. `--no-deps --no-build-isolation` builds it with
the `hatchling` and `editables` pinned in the lock, so nothing unhashed is
installed. `uv pip sync` removes packages that are not in the lock, recall
included: run the editable install again after every sync.

Offline, from uv's cache:

```powershell
uv venv --offline --python 3.12.13 .venv
uv pip sync --offline --no-verify-hashes --python .venv\Scripts\python.exe requirements.lock
uv pip install --offline --no-deps --no-build-isolation --python .venv\Scripts\python.exe -e .
```

`--no-verify-hashes` is needed only offline, because uv's cache does not keep
the wheel-file hash. With network access, `--require-hashes` verifies every
hash in the lock. To regenerate the lock (also offline):

```powershell
uv pip compile --offline --python-version 3.12 --python-platform x86_64-pc-windows-msvc --generate-hashes --excludes requirements.excludes requirements.in -o requirements.lock
```

**Dependency updates.** Dependabot edits `pyproject.toml` and
`requirements.in` but not `requirements.lock`, so its PRs fail
`test_lock_pins_every_input_with_hashes`.
Apply the bump by hand instead: change both files, regenerate the lock with the
command above, re-run the editable install, run the unit and `model` suites,
and close the Dependabot PR. Keep `onnxruntime` (the CPU runtime that a
non-Windows install of `pyproject.toml` gets; recall itself runs on Windows)
on the same version as `onnxruntime-directml`: Dependabot proposes moving only
the non-Windows one, and DirectML releases lag behind, so the two would drift.

### 3.2 Embedding model

recall loads a quantized ONNX model from a local snapshot directory. The code
default is `onnx-community/embeddinggemma-300m-ONNX` in the Hugging Face cache
(`%USERPROFILE%\.cache\huggingface\hub\models--onnx-community--embeddinggemma-300m-ONNX\snapshots\<revision>`).
The production configuration uses `onnx-community/embeddinggemma-2-ONNX` (§11,
§13).

**recall never downloads a model** (and `huggingface-hub` is deliberately not
installed in its environment). Get the snapshot once, by any means you prefer
and outside recall's venv, for example:

```powershell
# Git LFS required; accept the model's license on its Hugging Face page first
# (the Gemma models are gated).
git lfs install
git clone https://huggingface.co/onnx-community/embeddinggemma-300m-ONNX "$env:USERPROFILE\.cache\recall-models\embeddinggemma-300m-ONNX"
```

(or `hf download <repo> --local-dir <dir>` from a separate Python environment
that has the Hugging Face CLI). Then point `model.snapshot` in
`%LOCALAPPDATA%\recall\config.json` at that folder:

```json
{"model": {"snapshot": "~/.cache/recall-models/embeddinggemma-300m-ONNX"}}
```

The folder must contain `onnx/model_quantized.onnx` (and its `.onnx_data`, if
any) and `tokenizer.json`; otherwise `recall index` stops with "Model not found
in the local cache". The folder's **name** is recorded as the model revision
in the index identity, so keep it stable (renaming it later makes the index
refuse to mix vectors). `model.snapshot` accepts `~` (expanded to your profile
directory).

### 3.3 Run the tests

```powershell
.venv\Scripts\python.exe -I -m pytest            # unit tests (no model)
.venv\Scripts\python.exe -I -m pytest -m model   # regression against the real model and index
```

See [TESTING.md](TESTING.md).

---

## 4. Prepare the library

recall does not convert PDF or EPUB: any converter will do, as long as its
output follows this **Markdown input contract**.

**Layout.** One directory per book under a library root, one UTF-8 `.md` file
per chapter:

```
<root>\<book-id>\NN-<slug>.md
```

- `<book-id>` is the folder name and the key of the book in
  `data/books_meta.json` (year, edition, format, topics). Folders whose name
  starts with `_` or `.` are ignored.
- Chapters are read in file-name order, so a numeric prefix (`01-`, `02-`…)
  keeps them in book order. Only `.md` files directly inside the book folder
  are read (no subfolders).
- Directory links and junctions (`mklink /J`) and symlinked files are **not
  followed**, and nothing whose real path is outside the root is indexed.
- The default root is `%USERPROFILE%\books\md` (`~\books\md`).

**File contents.**

- **First line:** `# <Book> — <Chapter>` (an em dash with a space on each
  side). Without that separator the folder name is used as the book and the
  line as the chapter.
- An optional `> Source: ...` line is dropped.
- ATX headings `#` to `#####` open a new section, which is what citations
  show. `######` headings (converters emit callouts such as *Tip* or *Note*
  that way) and captions such as "Figure 3", "Table 2" or "Listing 4" stay
  inline in the text.
- A closing run of `#` on a heading is removed only when whitespace precedes
  it (CommonMark), so "C#" and "F#" keep their hash.
- Paragraphs are separated by blank lines.

**What is skipped.** Front and back matter are skipped by **exact** file stem
(after the numeric prefix): `cover`, `front-matter`, `contents`,
`table-of-contents`, `copyright-page`, `acknowledgments`, `dedication`,
`colophon`, `index`, `list-of-figures` and similar (`SKIP_STEM` in
`src/recall/chunking.py`). A chapter called "disaster-recovery" or
"contents-of-a-container-image" is therefore not mistaken for a cover or a
table of contents. A file with less than 100 characters of text (a part
divider, a title page) is not indexed; a short section inside a chapter is
attached to the previous chunk instead of being dropped.

**Several roots.** Roots are configured per collection in `config.json`, in
**precedence order**: if the same book directory exists under two roots, the
first wins and the other is treated as deleted. That lets a re-converted copy
of the library replace the old one book by book, without `--rebuild`:

```json
{"collections": {"books": {"roots": [
  "C:\Users\<user>\books\md_v2",
  "C:\Users\<user>\books\md"]}}}
```

The next `recall index` removes the old book and adds the new one, reusing the
vectors of any unchanged text. Do not put a half-converted root first: a book
partially present there hides the complete one in the next root. If you
overwrite files in place instead, the content hash detects it. A configured
root that **does not exist** (an unplugged drive, a typo) is not treated as
"every book was deleted": its indexed files are kept, and `recall index`
reports `root not found` and exits 1. Removing a root from the config does
delete its books on the next run.

**Metadata.** Per-book metadata (year, edition, format, topics) lives in
`data/books_meta.json`, which also ships inside the package. Topics come from
a closed taxonomy (`recall.metadata.DOMAINS`). A book without a verified year
is left as `null` and never passes a year filter. The file may be edited by
hand: entries that are not objects are dropped and every field is coerced to
the type search expects (a bad entry cannot break searches). If the file is
missing or invalid, any filter returns nothing and the cause is reported on
stderr and as `meta_error` in `/health`, instead of failing silently. Another
file can be used with `metadata_file` in `config.json`. The file carries no
source file names: the PDF location of each book, needed only for page
numbers (§12.2), lives in the git-ignored `data/books_sources.local.json`.

---

## 5. Index

```powershell
$py = 'C:\Users\<user>\source\repos\recall\.venv\Scripts\python.exe'
& $py -I -m recall index                 # incremental
& $py -I -m recall index --rebuild       # drop and rebuild
& $py -I -m recall index --dml-device 1  # pick the DirectML adapter
& $py -I -m recall index --cpu           # CPU only
& $py -I -m recall index --root D:\books\md --limit-files 3    # quick trial
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
- **DirectML adapter.** The default providers are `DmlExecutionProvider`
  (no adapter number: DXGI adapter 0 is tried first) then
  `CPUExecutionProvider`. `DmlExecutionProvider:N` in `model.index_providers`
  / `model.query_providers` (or `--dml-device N` for one run) puts adapter N
  first — useful on machines with an integrated and a discrete GPU. If that
  adapter cannot start, or the session silently comes up without DirectML, the
  other adapters 0–3 are tried and then CPU. The choice is printed to stderr
  (`recall: ONNX Runtime on DirectML adapter 1`) and shown in `/health`
  (`providers`, `device_id`). **Upgrading from 0.2:** the old default was
  `DmlExecutionProvider:1`; a machine that relied on adapter 1 being first
  must now set it in `config.json`.
- **Missing root.** A configured root that does not exist keeps its indexed
  files, is reported as `root not found`, and makes `recall index` exit 1
  (§4).

The first full index of a ~3,000-chapter library takes hours on a GPU; later
runs are incremental (a no-change pass takes well under a second of work).

---

## 6. The local service

```powershell
& $py -I -m recall serve     # blocks; for debugging
& $py -I -m recall status    # index counts, model identity and service health
```

`recall serve` keeps the model loaded and listens on 127.0.0.1 on an ephemeral
port. It writes the port, token and pid to `%LOCALAPPDATA%\recall\service.json`
(user-only ACL). Endpoints:

| Endpoint | Token | What it does |
|---|---|---|
| `GET /proof?nonce=<n>` | no | Server proof. `<n>` is 16–128 characters of `[A-Za-z0-9_-]`; the answer is `{"proof": "<hex HMAC-SHA256(token, "recall-proof:<n>:<port>")>"}`. Loopback and `Host` checks still apply. |
| `GET /health` | yes | `ok`, `chunks`, `generation`, `pid`, `engine_rebuilds`, `providers`, `device_id`, `meta_error`, `load_error`, plus model information. **503** when the engine guard gave up or the index identity changed under the running service (`load_error`). |
| `POST /search` | yes | `queries[]`, `k`, `collections`, optional filters, `snippet_chars`, `snippet_top`, `budget_ms`. 400 for malformed input (including queries that are not valid UTF-8 text, such as a lone surrogate, non-finite numbers and deeply nested JSON), 413 over 64 KiB, **503** `index identity changed` if the index was rebuilt with another model or chunking. |

Clients (the hook, the `recall search`/MCP client and `start-recall.ps1`)
first call `/proof` with a random nonce and verify the answer with the token
from `service.json`; only a server that proves it knows the token receives the
token or a prompt. Binding the port into the HMAC stops a process on another
port from relaying the real server's answer. `service.json` is accepted only if
`port` is an integer 1–65535, `pid` an integer > 0 and `token` printable ASCII
without spaces, at most 512 characters; anything else counts as "no service".

Limits: at most 32 concurrent connections, and each connection must deliver
its whole request within 3 s (an absolute deadline, so trickled bytes cannot
hold a slot).

In normal use you don't start it by hand: `start-recall.ps1` does it from the
Claude Code `SessionStart` hook (§7.3). The script:

1. Does nothing if `service.json` points to a live process that passes the
   `/proof` check and whose `/health` answers with the token. The token is
   never sent to a process that has not proved it.
2. Otherwise launches `.venv\Scripts\pythonw.exe -I -c "...main(['serve'])" <statedir>`
   hidden and **detached** from the session (via `Win32_Process.Create`), so
   Claude Code does not kill it when the hook ends. The state directory is
   passed on the command line, so a `RECALL_HOME` set for the script reaches
   the service. It does not wait for the model to load: the first prompt of a
   session may get no context (the hook fails open), later ones will.
3. Starts nothing if there is no index, and notes it in `start.log`.

It always exits 0 and writes nothing to stdout. Details go to
`%LOCALAPPDATA%\recall\start.log` (rotated at 1 MiB). A proven service that
answers `/health` with 503 is stopped and replaced — only the pid that the
proven server itself reports; an unproven pid is never killed. The script runs
under Windows PowerShell 5.1 and pwsh 7 alike.

**After upgrading recall, stop the old service once.** A service started by a
version before 0.3.0 has no `/proof` endpoint: the hook and the client then
fail open (no context), and the launcher logs "did not prove the token",
discards `service.json` and starts a new service while the old one keeps
running (and holding the GPU and the index). Before the first session on the
new version, stop it once:

```powershell
$svc = Get-Content "$env:LOCALAPPDATA\recall\service.json" -Raw | ConvertFrom-Json
Stop-Process -Id $svc.pid
```

If `service.json` is already gone, stop the `pythonw.exe` process whose
command line contains `recall` (Task Manager → Details, or
`Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" | Select-Object ProcessId, CommandLine`).

The service itself logs search errors (exception type, never the query) to
`%LOCALAPPDATA%\recall\serve.log`, rotated at 1 MiB. If the model session stops
working, typically after sleep when DirectML loses the GPU, the service
recreates it (about 7 s) and retries; the replaced session is released. An
ONNX Runtime error triggers that at once; any other error only on the second
in a row and at most once per run of failures, and it never counts toward
giving up, so a request that fails the same way every time cannot exhaust the
rebuild budget. If rebuilding fails, or engine errors persist after
`max_rebuilds` rebuilds, the service reports 503 and shuts down so the next
session starts a clean one.

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

Use absolute paths here (the MCP server is started from whatever directory
the session runs in). Search results from this tool cite each chapter by its
**absolute** local path so the assistant can open the file; the hook, in
contrast, cites library-relative paths. Both reach the assistant's provider
as part of the conversation.

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

<!-- block-sessionstart -->
```json
{
  "hooks": [
    {
      "type": "command",
      "command": "pwsh",
      "args": ["-NoProfile", "-File", "C:\\Users\\<user>\\source\\repos\\recall\\start-recall.ps1"],
      "timeout": 10,
      "statusMessage": "Starting recall..."
    }
  ]
}
```

**UserPromptSubmit** — add the `"UserPromptSubmit"` key inside `"hooks"` (if
missing), with an array containing this object:

<!-- block-userpromptsubmit -->
```json
{
  "hooks": [
    {
      "type": "command",
      "command": "C:\\Users\\<user>\\source\\repos\\recall\\.venv\\Scripts\\python.exe",
      "args": ["-I", "C:\\Users\\<user>\\source\\repos\\recall\\recall_hook.py"],
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

With **Windows PowerShell 5.1** instead of pwsh 7, use `"command": "powershell"`
in the SessionStart block; the script works the same under both.

Paths: the hook entries use **absolute** paths (Claude Code runs them from the
session's working directory, so a relative path would point into whatever
project is open). The same applies to the MCP entries (§7.2, §8).

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
  token from `service.json`, after the service has passed the `/proof` check
  (§6).
- Skips trivial prompts (short, `/`, `!`, `#` commands, confirmations like "ok"
  or "continue") and mechanical requests (git, file operations, edits; see
  `MECHANICAL` in `recall_hook.py`).
- Injects up to `max_snippets` passages of `max_chars`, one per file, only when
  the best cosine clears `threshold`. Each passage is the window of the chunk
  that shares the most terms with the prompt, with its citation (book — chapter
  — section, library-relative path `book-id/file.md`:lines, page when known).
  Passages are wrapped in a `<recall_books>` block marked as data; in every
  field `<`, `>` and their look-alikes become `&lt;`/`&gt;`, and header fields
  are collapsed to one line, so book content cannot close the block or forge
  an entry.
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

<!-- block-desktop -->
```json
{
  "recall": {
    "command": "C:\\Users\\<user>\\source\\repos\\recall\\.venv\\Scripts\\python.exe",
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
| `recall eval [--queries FILE] [--out FILE]` | R@5 / MRR, latency and threshold calibration over a query set. Without `--queries` it uses `tests\data\queries_books.json` of the source checkout; elsewhere (an installed wheel) it exits 2 and asks for `--queries`. |

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

**Recommended configuration (E5).** The code defaults are the baseline
(embeddinggemma-300m, ~900-character chunks, threshold 0.58), kept so existing
indexes stay valid. The configuration measured as best in
[the paper](paper.md) is in [`config.example.json`](../config.example.json):

1. Download `onnx-community/embeddinggemma-2-ONNX` to a local folder.
2. Copy `config.example.json` to `%LOCALAPPDATA%\recall\config.json`; set
   `collections.books.roots` and `model.snapshot` to your paths.
3. Run `recall index --rebuild` (the identity changes, so a rebuild is
   required), then restart `recall serve`.
4. Keep the hook in `shadow` and calibrate with `tools\shadow_report.py`
   before switching to `active` (§9).

`doc_max_tokens` is part of the index identity when it is not the default
(320): changing it, or removing it from the config, makes the index refuse to
mix vectors. Indexes built before this rule are accepted with the configured
value and record it on the next `recall index`.

| Section | Key | Default | Purpose |
|---|---|---|---|
| `collections` | `books.roots` | `[%USERPROFILE%\books\md]` | Library roots, in precedence order (§4). A root that does not exist is reported and its books kept. |
| `chunking` | `target`, `max_chars`, `min_chars`, `overlap` | 900, 1000, 450, 150 | Chunk sizes in characters. Part of the index identity. |
| `model` | `snapshot`, `name` | embeddinggemma-300m snapshot | Model directory and name. Part of the identity. |
| `model` | `onnx_file` | `onnx/model_quantized.onnx` | ONNX file inside the snapshot. |
| `model` | `dim` | 768 | Embedding dimension. |
| `model` | `doc_max_tokens`, `query_max_tokens` | 320, 256 | Truncation. Raise `doc_max_tokens` with larger chunks (~1 token per 3–4 characters). |
| `model` | `index_providers`, `query_providers` | `DmlExecutionProvider`, `CPUExecutionProvider` | ONNX Runtime providers, in order. `DmlExecutionProvider:N` puts DXGI adapter N first; other adapters (0–3) and then CPU are tried if it cannot start (§5). |
| `model` | `batch_size` | 32 | Indexing batch. |
| `search` | `fts_weight_same_lang`, `fts_weight_cross_lang` | 0.25, 0.02 | Lexical weight in RRF when query and chunk share a language / don't. |
| `search` | `rrf_k`, `vec_weight`, `candidates`, `min_reload_s` | 60, 1.0, 100, 30 | Fusion and reload tuning. |
| `search` | `query_window_tokens`, `query_max_windows`, `query_window_overlap` | 0 (off), 6, 5 | Split a longer query into windows of at most N tokens, one model call each, fused by sum (ADR 0008). Use 32 with embeddinggemma-2 on DirectML. |
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
| `RECALL_HOME` | State directory instead of `%LOCALAPPDATA%\recall` (the tests use it). `start-recall.ps1` honors it and passes it to the service it starts. |
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
& '<tools-venv>\Scripts\python.exe' -I tools\build_pages.py [--sources data\books_sources.local.json] [--pdf-dir DIR] [--md-root DIR] [--out pages.sqlite3] [--only <book-id> ...]
```

Where each book's PDF lives is local knowledge and never goes in the
repository. It is read from `--sources` (default
`data/books_sources.local.json`, git-ignored), a JSON object mapping each book
id to its PDF:

```json
{
  "learning-ddd": "C:\Users\<user>\books\pdf\learning-ddd.pdf",
  "site-reliability-engineering": "sre/site-reliability-engineering.pdf"
}
```

Relative paths are resolved against `--pdf-dir` (default `~\books`); entries
that are not `.pdf` are ignored. The Markdown is read from
`<--md-root>\<book-id>\*.md` (default `~\books\md`, the default library root).

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
| `shadow.log` shows `HTTPError` on every prompt | Look at `serve.log`. `rebuild` lines mean the service recreated the model session; `give-up` means it shut down, and the next session start relaunches it. |
| `ModelMismatchError` | The configured model or chunking differs from the index. Restore the matching `config.json`, or `recall index --rebuild`. |
| `/health` 503 with `load_error` / searches answer `index identity changed` | The index was rebuilt with another model or chunking while the service ran. It refuses to mix vectors; the next session start replaces it (or stop it and run `start-recall.ps1`). |
| `start.log`: "did not prove the token" | `service.json` points at a process that is not the current recall service — typically a service started before 0.3.0 still running after an upgrade (§6). Stop that process once. |
| `recall index` exits 1 with `root not found` | A configured root does not exist. Its books were kept; fix the path in `config.json` (or remove the root to drop its books). |
| Topic or year filters return nothing | `/health` shows `meta_error`: `data/books_meta.json` (or `metadata_file`) is missing or invalid. |
| Indexing or the hook is slow | DirectML is not in use or the wrong adapter is selected. `recall index` (and `recall serve` run in a console) prints `recall: ONNX Runtime on ...` to stderr, and `/health` shows `providers` and `device_id`. Try `--dml-device 0`/`1`, then set `DmlExecutionProvider:N` in `config.json`. |
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
