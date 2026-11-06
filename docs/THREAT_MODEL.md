# Threat Model

A STRIDE-oriented look at recall. The system indexes a local library of
technical books, serves searches over loopback, and inserts retrieved passages
into a coding assistant's context, either automatically (hook) or on request
(MCP tool).

## Assets

| Asset | Why it matters |
|---|---|
| The user's prompts | May contain private work context; must not leave the machine or be logged in clear. |
| The assistant's context | Injected text influences what the assistant does. |
| The index (`index.sqlite3`) | Tampering changes what is retrieved and injected. |
| The service token (`service.json`) | Grants access to the search endpoint. |
| The host machine | Runs the service, the model and the hook on every prompt. |

## Trust boundaries

```mermaid
flowchart TD
    prompt([Prompt]) --> hook[Hook]
    hook -- "Bearer token" --> svc{{"Service<br/>127.0.0.1 only · Host check · 64 KiB cap"}}
    mcp[MCP stdio] --> svc
    svc -- "mode=ro" --> db[(Index)]
    books[(Book text)] -. "UNTRUSTED content" .-> db
    svc -- "passages" --> hook
    hook -- "wrapped as DATA" --> ctx([Assistant context])
    other([Other local process / browser]) -. "no token / wrong Host → 401/403" .-> svc
```

Book text is treated as untrusted content: it is retrieved and shown, never
executed or obeyed.

## STRIDE

### Spoofing
- *Another local process or a web page calls the service.* Mitigation: the
  service binds 127.0.0.1 on an ephemeral port, rejects non-loopback peers,
  validates the `Host` header (127.0.0.1/localhost with its own port) against
  DNS rebinding, and requires a Bearer token compared with
  `hmac.compare_digest`.
- *A stale or foreign process impersonates the service.* Mitigation: the token
  is random per start (`secrets.token_urlsafe(32)`) and lives in
  `service.json`, written atomically and restricted to the current user's ACL;
  the hook only talks to the port and token recorded there.

### Tampering
- *Index modified to inject chosen text.* Mitigation: the index lives under
  the user's `%LOCALAPPDATA%\recall`; the service opens it read-only; the
  injected block is labeled as data. Residual: anyone who can write the user's
  profile can change what is retrieved — out of scope (same privilege as the
  user).
- *Index silently built with a different model.* Mitigation: the model
  identity (sha256 of weights and tokenizer, revision, prefixes, chunking) is
  stored and checked; mismatches raise `ModelMismatchError`.
- *Index integrity drift.* Mitigation: `tools/verify_index.py` checks SQLite
  integrity, chunk accounting, vector validity, text-to-vector correspondence,
  FTS rows, coverage, and re-embeds a sample.

### Repudiation
- Not a goal for a single-user local tool. The shadow log records decisions
  (hash, scores, paths, latency) for calibration, not for audit.

### Information disclosure
- *Prompts logged in clear.* Mitigation: `shadow.log` stores a hash of the
  prompt, never its text; the service writes no access log.
- *Data leaving the machine.* Mitigation: no runtime network calls;
  `HF_HUB_OFFLINE=1` is set on import; `huggingface-hub` is excluded from the
  lock file so there is no code path to the Hub; the model loads from a local
  snapshot.
- *Unrelated private data reaching the context.* Mitigation: the hook and MCP
  query only the `books` collection, which contains only book text.

### Denial of service
- *Slow or crashed service blocks prompts.* Mitigation: the hook has a 1.5 s
  total budget and fails open; the startup script always exits 0; the MCP
  server falls back to an in-process searcher.
- *Oversized requests.* Mitigation: 64 KiB body cap and bounded `k`.

### Elevation of privilege
- *Prompt injection from book content.* A passage could contain text that reads
  like instructions. Mitigation: passages are wrapped in a `<recall_libros>`
  block that states they are reference data and must not be obeyed; any
  `<recall_libros>` tag inside a passage is stripped so content cannot close
  the block early; passages are capped (3 × 700 characters); injection only
  happens above the cosine threshold and only in active mode. Residual: the
  assistant may still be influenced by data; the library is the user's own
  collection, which limits who can plant content.
- *Code execution through the service.* Mitigation: the service only returns
  text; it executes nothing from the index or the request.

## Supply chain

- `requirements.lock` pins every dependency with hashes (`uv pip compile
  --generate-hashes`), installed offline from a local cache.
- Few runtime dependencies: `numpy`, `onnxruntime-directml`, `tokenizers`.
  The hook is standard-library only.
- `huggingface-hub` is deliberately excluded (`requirements.excludes`).
- The MCP subset (initialize, tools/list, tools/call, ping) is implemented by
  hand instead of pulling the SDK and its transitive dependencies.
- Model weights are identified by sha256 in the index identity.

## Out of scope

- An attacker with write access to the user's profile or to the library.
- Multi-user machines where another user shares the same account.
- Correctness of the assistant's use of retrieved passages.
