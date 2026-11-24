# Diagrams

Rendered by GitHub automatically (Mermaid). They complement the prose in
[architecture.md](architecture.md).

The indexing, search, hook and MCP diagrams are repeated in the
[README](../README.md#how-it-works); keep both copies identical when one
changes.

## System context (C4 level 1)

```mermaid
flowchart TB
    user(["User"])
    subgraph host["This machine"]
        assistant["Claude Code / Claude Desktop"]
        recall["recall<br/>service · hook · MCP · index"]
        library[("Markdown library<br/>148 books")]
        gpu["GPU (DirectML)<br/>CPU fallback"]
    end

    user -- "prompt (ES / EN)" --> assistant
    assistant -- "UserPromptSubmit hook" --> recall
    assistant -- "MCP tool call" --> recall
    recall -- "cited passages" --> assistant
    recall -- "index" --> library
    recall -- "embeddings" --> gpu
```

## Components (C4 level 2)

```mermaid
flowchart TB
    start["start-recall.ps1<br/>SessionStart · always exit 0"]
    hook["recall_hook.py<br/>filters · threshold · shadow/active"]
    mcp["recall mcp<br/>recall_books_search"]
    svc["recall serve<br/>Searcher + warm model<br/>127.0.0.1 · token"]
    idx["recall index<br/>incremental"]
    db[("index.sqlite3<br/>vectors + FTS5 + identity")]
    pages[("pages.sqlite3")]
    state[("%LOCALAPPDATA%\\recall<br/>service.json · config.json · shadow.log")]

    start --> svc
    hook -- "token from service.json" --> state
    hook -- "POST /search" --> svc
    mcp -- "POST /search" --> svc
    mcp -. "fallback" .-> db
    svc -- "mode=ro" --> db
    svc --> pages
    idx -- "read-write" --> db
    hook -- "log" --> state
```

## Search inside the service

What `recall serve` does with one `POST /search`, whoever sent it (hook, MCP
server or `recall search`). The ranking uses the fused score; `cos` is the raw
cosine returned next to it, and `hub` the per-chunk prior of
[ADR 0011](adr/0011-hub-prior-for-the-hook-threshold.md).

```mermaid
flowchart TD
    req["POST /search<br/>queries, k, collections, filters,<br/>snippet_chars, budget_ms"]
    gate{"Loopback peer, valid Host,<br/>Bearer token?"}
    deny["403 / 401"]
    parse{"Body within 64 KiB<br/>and well-formed?"}
    bad["413 / 400"]
    win["Split long queries into windows<br/>(search.query_window_tokens)"]
    emb["Embed each window with the query prefix<br/>warm model, stops at the budget_ms deadline"]
    vec["Cosine against every vector<br/>in-memory float32 matrix"]
    fts["FTS5 (BM25) candidates<br/>index.sqlite3, mode=ro"]
    rrf["Reciprocal rank fusion<br/>FTS weight 0.25 same language, 0.02 across"]
    rows["Read each hit by id, check input_sha,<br/>deduplicate by file + section, keep k"]
    meta["Attach cos, hub, book metadata<br/>and page (pages.sqlite3)"]
    snip["Optional snippet: the window sharing<br/>the most terms with the query"]
    ok["200 hits"]
    err["503 index identity changed<br/>500 any other error"]

    req --> gate
    gate -- no --> deny
    gate -- yes --> parse
    parse -- no --> bad
    parse -- yes --> win
    win --> emb
    emb --> vec
    win --> fts
    vec --> rrf
    fts --> rrf
    rrf --> rows
    rows --> meta
    meta --> snip
    snip --> ok
    win -. "exception while searching" .-> err
```

## Sequence — automatic injection (hook)

With `hook.mode` set to `off` the hook exits before any of this. The
threshold applies to `cos - hub_weight * hub`
([ADR 0011](adr/0011-hub-prior-for-the-hook-threshold.md)); with
`hook.hub_weight: 0` it is the raw cosine.

```mermaid
sequenceDiagram
    autonumber
    participant CC as Claude Code
    participant H as recall_hook.py
    participant S as recall serve
    participant L as shadow.log

    CC->>H: stdin JSON {prompt, cwd, ...}
    H->>H: skip trivial / command / mechanical?
    alt skipped
        H->>L: {hash, decision: skip-*}
        H-->>CC: exit 0, no output
    else candidate
        H->>S: GET /proof?nonce=n (no token)
        S-->>H: HMAC(token, n:port)
        H->>H: proof matches service.json token?
        H->>S: POST /search (Bearer, snippet_chars) within 1.5 s budget
        alt unavailable / bad proof / timeout / error
            H->>L: {hash, decision: service-unavailable}
            H-->>CC: exit 0, no output (fail-open)
        else hits
            S-->>H: hits with cosine and hub prior
            H->>H: keep cos - hub_weight * hub ≥ threshold, ≤ 3, one per file
            alt hub prior not loaded / nothing clears the threshold / budget exceeded
                H->>L: {hash, scores, decision: hub-pending / below-threshold / over-budget}
                H-->>CC: exit 0, no output
            else shadow mode
                H->>L: {hash, scores, paths, decision: would-inject}
                H-->>CC: exit 0, no output
            else active mode
                H->>L: {hash, decision: inject}
                H-->>CC: additionalContext (ASCII JSON, cited passages)
            end
        end
    end
```

## Sequence — explicit search (MCP)

```mermaid
sequenceDiagram
    autonumber
    participant A as Assistant
    participant M as recall mcp (stdio)
    participant S as recall serve
    participant DB as index.sqlite3

    A->>M: tools/call recall_books_search {queries: [ES, EN], k, domains?, year_min?}
    M->>M: validate filters and queries
    M->>S: GET /proof, then POST /search (Bearer)
    alt service up
        S-->>M: hits
    else service unavailable
        M->>DB: load in-process searcher (first call only)
        DB-->>M: hits
    end
    M-->>A: formatted passages with book, chapter, page, path:lines
```

## Sequence — incremental indexing

```mermaid
sequenceDiagram
    autonumber
    participant CLI as recall index
    participant FS as Library roots
    participant E as Embedder
    participant DB as index.sqlite3

    CLI->>DB: open_rw, check model identity
    alt identity differs
        DB-->>CLI: ModelMismatchError (use --rebuild)
    end
    CLI->>FS: discover chapters
    loop each file
        CLI->>CLI: size+mtime equal? skip : compare sha256
    end
    loop each group (~512 chunks)
        CLI->>CLI: chunk changed files by section (book, chapter, section, lines, language)
        CLI->>DB: reuse vectors for identical inputs
        CLI->>E: embed the rest
        CLI->>DB: one transaction: files + chunks + FTS
    end
    CLI->>DB: apply deletions, bump generation
```

## Trust boundaries

```mermaid
flowchart LR
    subgraph user_session["User's assistant session"]
        prompt["Prompt"]
        ctx["Assistant context"]
    end
    subgraph loopback["Loopback boundary · 127.0.0.1 + Host check · /proof before Bearer"]
        svc["recall serve"]
    end
    subgraph data["Data at rest · %LOCALAPPDATA%\\recall (user ACL)"]
        db[("index.sqlite3")]
        tok[("service.json · token")]
    end
    books[("Book text<br/>UNTRUSTED content")]

    prompt --> hook["hook"] --> svc
    svc --> db
    books -- "indexed as data" --> db
    svc -- "passages wrapped as DATA" --> hook --> ctx
    hook -. "reads token" .-> tok
```
