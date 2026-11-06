# Diagrams

Rendered by GitHub automatically (Mermaid). They complement the prose in
[ARCHITECTURE.md](ARCHITECTURE.md).

## System context (C4 level 1)

```mermaid
flowchart TB
    user(["User"])
    subgraph host["This machine"]
        assistant["Claude Code / Claude Desktop"]
        recall["recall<br/>service · hook · MCP · index"]
        library[("Markdown library<br/>126 books")]
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

## Sequence — automatic injection (hook)

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
        H->>S: POST /search (Bearer, snippet_chars) within 1.5 s budget
        alt unavailable / timeout / error
            H->>L: {hash, decision: service-unavailable}
            H-->>CC: exit 0, no output (fail-open)
        else hits
            S-->>H: hits with cosine
            H->>H: keep cos ≥ threshold, ≤ 3, one per file
            alt shadow mode
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
    M->>M: validate filters
    M->>S: POST /search
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
    subgraph loopback["Loopback boundary · 127.0.0.1 + Bearer + Host check"]
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
