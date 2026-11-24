# Diagrams

Every diagram of the repository in one place. GitHub renders them automatically (Mermaid). They complement the
prose in [architecture.md](architecture.md).

Each diagram is repeated unchanged from the document that explains it: the context, components and flow
diagrams from [architecture.md](architecture.md), the overview from the [README](../README.md#how-it-works),
the trust boundaries from [THREAT_MODEL.md](THREAT_MODEL.md#trust-boundaries) and Figure 1 from the
[paper](paper.md#figure-1-data-flow). Keep both copies identical when one changes.

## Contents

- [System context (C4 level 1)](#system-context-c4-level-1)
- [Components (C4 level 2)](#components-c4-level-2)
- [Sequences](#sequences)
- [State and lifecycle](#state-and-lifecycle)
- [Trust boundaries](#trust-boundaries)

## System context (C4 level 1)

The diagram shows who uses recall and what it depends on; everything inside the box runs on one machine.

```mermaid
flowchart TB
    user(["User<br/>writes prompts in Spanish or English"])
    subgraph host["This machine"]
        assistant["Coding assistant<br/>Claude Code / Claude Desktop"]
        recall["recall<br/>service · hook · MCP · index"]
        library[("Technical library<br/>Markdown per chapter · 148 books")]
        gpu["Local GPU (DirectML)<br/>CPU fallback"]
    end

    user -- "prompt (ES / EN)" --> assistant
    assistant -- "UserPromptSubmit hook" --> recall
    assistant -- "MCP tool call" --> recall
    recall -- "passages with citations" --> assistant
    recall -- "reads and indexes" --> library
    recall -- "embeds on" --> gpu
```

## Components (C4 level 2)

The diagram shows the processes of recall and the files they share.

```mermaid
flowchart TB
    subgraph procs["recall processes"]
        start["start-recall.ps1<br/>SessionStart · always exit 0"]
        hook["Hook · recall_hook.py<br/>UserPromptSubmit · stdlib only<br/>filters · threshold · shadow/active"]
        mcp["MCP server · recall mcp<br/>mcp_server.py · stdio JSON-RPC<br/>recall_books_search"]
        svc["Service · recall serve<br/>service.py · search.py<br/>Searcher + warm model<br/>127.0.0.1:ephemeral + Bearer token"]
        idx["Indexer · recall index<br/>indexer.py · chunking.py<br/>incremental"]
    end
    subgraph files["State directory · %LOCALAPPDATA%\\recall"]
        db[("index.sqlite3 (WAL)<br/>files · chunks (f32 vectors) · FTS5 · meta (identity)")]
        pages[("pages.sqlite3<br/>line → printed page (optional)")]
        state[("service.json · config.json · shadow.log")]
    end

    start -- "launch detached if unhealthy" --> svc
    hook -- "token from service.json" --> state
    hook -- "HTTP POST /search" --> svc
    mcp -- "HTTP POST /search" --> svc
    mcp -. "fallback: in-process searcher" .-> db
    svc -- "read-only (mode=ro)" --> db
    svc -- "read-only" --> pages
    idx -- "read-write" --> db
    hook -- "log" --> state
```

Solid arrows are the normal path; the dotted arrow is the fallback the MCP server uses when the service is
down.

## Sequences

### A book becomes passages for the assistant (overview)

The flowchart follows a book from its Markdown chapters to the passages the assistant receives.

```mermaid
flowchart LR
    L["PDF/EPUB books"] --> M["Markdown per chapter"]
    M --> I["recall index<br/>incremental, atomic batches"]
    I --> DB[("SQLite WAL<br/>float32 vectors + FTS5")]
    P[("Page map")] --> S
    DB --> S["recall serve<br/>127.0.0.1 + Bearer token<br/>model kept warm"]
    U["User prompt"] --> H["Hook<br/>threshold · 1.5 s budget · fail-open"]
    H -->|HTTP| S
    H -->|passages + citation| A["Assistant"]
    A -->|recall_books_search| MCP["MCP server · stdio"]
    MCP --> S
```

### The indexer adds changed chapters (incremental indexing)

The sequence shows one `recall index` run: only changed files are chunked, and identical inputs reuse their
vector.

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

### The service answers one search

The flowchart shows what `recall serve` does with one `POST /search`, whoever sent it (hook, MCP server or
`recall search`).

```mermaid
flowchart TB
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

The ranking uses the fused score; `cos` is the raw cosine returned next to it, and `hub` the per-chunk prior
of [ADR 0011](adr/0011-hub-prior-for-the-hook-threshold.md).

### The hook injects passages into a prompt (automatic injection)

The sequence shows what `recall_hook.py` does with one prompt; with `hook.mode` set to `off` the hook exits
before any of this.

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

The threshold applies to `cos - hub_weight * hub`
([ADR 0011](adr/0011-hub-prior-for-the-hook-threshold.md)); with
`hook.hub_weight: 0` it is the raw cosine.

### The assistant searches through the MCP tool (explicit search)

The sequence shows one `recall_books_search` call, including the fallback when the service is not running.

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

### Data flow at the time of the study (paper, Figure 1)

The flowchart is Figure 1 of the [paper](paper.md#figure-1-data-flow), a dated snapshot of 2026-11-05.

```mermaid
flowchart LR
    L["PDF/EPUB books"] --> M["Markdown per chapter"]
    M --> I["Incremental indexer"]
    I --> DB[("SQLite: vectors + FTS5")]
    P["Page map"] --> S
    DB --> S["Local service<br/>127.0.0.1 + token"]
    U["User prompt"] --> H["Hook<br/>threshold + 1.5 s budget"]
    H -->|HTTP| S
    H -->|context with citation| A["Assistant"]
    A -->|recall_books_search| MCP["MCP server"]
    MCP --> DB
```

## State and lifecycle

Not applicable: the repository has no state diagram. The hook mode (`off`, `shadow`, `active`) is a
configuration value that you set, not a lifecycle the system drives; see
[USAGE §9](USAGE.md#9-from-shadow-mode-to-active).

## Trust boundaries

The diagram shows where untrusted input meets recall and which check guards each crossing.

```mermaid
flowchart LR
    subgraph user_session["User's assistant session"]
        prompt["Prompt"]
        hook["Hook"]
        mcp["MCP server (stdio)"]
        ctx["Assistant context"]
    end
    subgraph loopback["Loopback boundary · 127.0.0.1 only + Host check · /proof before Bearer · 64 KiB cap"]
        svc["recall serve"]
    end
    subgraph data["Data at rest · %LOCALAPPDATA%\\recall (user ACL)"]
        db[("index.sqlite3")]
        tok[("service.json · token")]
    end
    books[("Book text<br/>UNTRUSTED content")]
    other(["Other local process / browser"])

    prompt --> hook
    hook -- "Bearer token" --> svc
    mcp -- "Bearer token" --> svc
    svc -- "mode=ro" --> db
    books -- "indexed as data" --> db
    svc -- "passages" --> hook
    svc -- "passages" --> mcp
    hook -- "wrapped as DATA" --> ctx
    mcp -- "wrapped as DATA" --> ctx
    hook -. "reads token" .-> tok
    other -. "no token / wrong Host: 401/403" .-> svc

    classDef untrusted stroke-dasharray: 5 5
    class books,other untrusted
```

Dashed outlines mark what recall does not trust. Book text is treated as untrusted content: it is retrieved
and shown, never executed or obeyed.
