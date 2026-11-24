# Runbook: operations and recovery

Task-oriented entry point for running recall day to day. It adds no procedure
of its own: every step lives in the [User Manual](USAGE.md), and this page says
where to look first and which file tells you what happened. `$py` is
`.venv\Scripts\python.exe`, as in the manual.

## Contents

1. [Where things are](#1-where-things-are)
2. [Is it healthy?](#2-is-it-healthy)
3. [Service](#3-service)
4. [Hook](#4-hook)
5. [Index](#5-index)
6. [MCP server](#6-mcp-server)
7. [Recovery](#7-recovery)

## 1. Where things are

State lives in `%LOCALAPPDATA%\recall` (or `RECALL_HOME`), never in the
repository.

| File | Written by | What it tells you |
|---|---|---|
| `index.sqlite3` (+ `-wal`, `-shm`) | `recall index` | The index: files, chunks, vectors, FTS5, model identity. |
| `config.json` | you (optional) | Overrides of the code defaults ([USAGE §11](USAGE.md#11-configuration-configjson)). |
| `service.json` | `recall serve` | Port, token and pid of the running service. Never paste it anywhere. |
| `start.log` | `start-recall.ps1` | Why the service was or was not started at session start. |
| `serve.log` | `recall serve` | Search errors by exception type (never the query), engine rebuilds. |
| `shadow.log` (+ `.1`…) | `recall_hook.py` | One line per prompt: hash, scores, decision. Never the prompt text. |
| `pages.sqlite3` | `tools/build_pages.py` | Optional line → printed page map. |
| `hub_ref.npz` | `recall serve` | Cached reference vectors of the hub prior (ADR 0011). |

## 2. Is it healthy?

```powershell
& $py -I -m recall status                      # index counts, model identity, service health
& $py -I tools\verify_index.py --sample 30     # read-only integrity check; exits 1 on any failure
Get-Content "$env:LOCALAPPDATA\recall\shadow.log" -Tail 5
```

The full end-to-end check (start script, status, one hook call, last log
line) is at the end of [USAGE §14](USAGE.md#14-troubleshooting).

## 3. Service

| Situation | Go to |
|---|---|
| Start it by hand, endpoints, limits, what the launcher does | [USAGE §6](USAGE.md#6-the-local-service) |
| `recall status` shows the service as unavailable | `start.log`, then [USAGE §14](USAGE.md#14-troubleshooting) |
| `start.log` says "did not prove the token" | A service from before 0.3.0 is still running: stop it once ([USAGE §6](USAGE.md#6-the-local-service)) |
| `/health` answers 503 with `load_error`, or searches answer `index identity changed` | The index was rebuilt under the running service: [USAGE §14](USAGE.md#14-troubleshooting) |
| `shadow.log` shows `HTTPError` on every prompt | `serve.log` (`rebuild` / `give-up` lines): [USAGE §14](USAGE.md#14-troubleshooting) |
| Searches or indexing are slow | Check the provider in use: [USAGE §14](USAGE.md#14-troubleshooting), [USAGE §5](USAGE.md#5-index) |

Stopping the service is always the same: read the pid from `service.json` and
stop that process ([USAGE §6](USAGE.md#6-the-local-service)). The next session
start launches a fresh one.

## 4. Hook

The hook cannot break a prompt: on any error, timeout or low score it exits 0
with no output. What it decided is in `shadow.log`:

| `decision` | Meaning |
|---|---|
| `skip-trivial`, `skip-mechanical` | The prompt was not searched (short, a command, an acknowledgement, or a mechanical request). |
| `service-unavailable` | The service was down, slow, failed the `/proof` check or answered an error; `err` holds the exception type. |
| `hub-pending` | The service has not loaded the hub prior yet; nothing is injected until it has. |
| `below-threshold` | No hit cleared `hook.threshold`. |
| `over-budget` | Hits arrived after the time budget. |
| `would-inject` | Shadow mode: these passages would have been injected. |
| `inject` | Active mode: passages were added to the prompt. |

| Situation | Go to |
|---|---|
| The hook never injects | [USAGE §14](USAGE.md#14-troubleshooting) |
| Review what it would inject, pick a threshold | `tools\shadow_report.py`: [USAGE §9](USAGE.md#9-shadow-mode--active) |
| Switch between `shadow`, `active` and `off` | [USAGE §9](USAGE.md#9-shadow-mode--active) |
| What the hook does and how it is wired | [USAGE §7.3–7.4](USAGE.md#74-what-the-hook-does) |

## 5. Index

| Situation | Go to |
|---|---|
| Add, change or remove books | `recall index` (incremental): [USAGE §5](USAGE.md#5-index) |
| `recall index` exits 1 with `root not found` | [USAGE §14](USAGE.md#14-troubleshooting) |
| `ModelMismatchError` | [USAGE §14](USAGE.md#14-troubleshooting) |
| Check integrity after an interrupted run or a disk problem | [USAGE §12.1](USAGE.md#121-verify-an-index--toolsverify_indexpy) |
| Change the model or the chunking | [USAGE §13](USAGE.md#13-changing-the-model-or-chunking) — always followed by a threshold recalibration |
| Topic or year filters return nothing | [USAGE §14](USAGE.md#14-troubleshooting) |
| Moving or deleting the index fails ("file in use") | [USAGE §14](USAGE.md#14-troubleshooting) |
| Page numbers are missing from citations | Build the page map: [USAGE §12.2](USAGE.md#122-page-map--toolsbuild_pagespy) |

## 6. MCP server

| Situation | Go to |
|---|---|
| `recall` does not appear in `/mcp` | [USAGE §14](USAGE.md#14-troubleshooting), [USAGE §7.2](USAGE.md#72-mcp-server-user-scope) |
| Claude Desktop does not show the tool | [USAGE §14](USAGE.md#14-troubleshooting), [USAGE §8](USAGE.md#8-connect-claude-desktop) |
| The tool answers `recall unavailable: <ErrorType>` | The detail is on the MCP server's stderr; check the index with `recall status`. |

With the service down the MCP server still answers: it loads an in-process
searcher on the first call.

## 7. Recovery

| Situation | Go to |
|---|---|
| Roll back a model or chunking change | [USAGE §15](USAGE.md#15-rollback-and-uninstall) |
| Undo the Claude Code wiring | Restore the backup of [USAGE §7.1](USAGE.md#71-backup-first), or [USAGE §15](USAGE.md#15-rollback-and-uninstall) |
| Uninstall and delete all state | [USAGE §15](USAGE.md#15-rollback-and-uninstall) |
| Report a vulnerability | Privately: [SECURITY.md](../SECURITY.md) |
| Ask for help | [SUPPORT.md](../SUPPORT.md) — never paste prompts, `service.json`, its token, or book text |
