# 0003. Fail-open hook with a 1.5 s time budget

- Status: Accepted
- Date: 2026-10-27

## Context and problem statement

The `UserPromptSubmit` hook runs before every prompt. If it is slow or crashes,
it degrades every interaction with the assistant, including those that have
nothing to do with books.

## Decision drivers

- Retrieval is a convenience; the prompt is the user's work.
- Loading the embedding model takes seconds.
- Windows consoles may not accept non-ASCII output.

## Considered options

- **A. Thin stdlib client with a total budget, failing open**, querying a warm service.
- **B. Load the model inside the hook.**
- **C. Block until the service answers.**

## Decision

Option **A**. The hook never loads the model; it queries `recall serve` over
loopback with the token from `service.json`, within a total budget of 1.5 s
(`hook.budget_s`). A missing service, timeout, invalid token, malformed JSON or
any exception ends in exit code 0 with no output. Output is ASCII-only JSON
written as bytes, so a code-page mismatch cannot silently drop it. The
SessionStart script that launches the service also always exits 0.

## Consequences

- Positive: the worst case is "no context", never a blocked or broken prompt.
- Positive: measured end-to-end latency is 0.4–0.6 s, including interpreter startup.
- Negative: the first prompt of a session may get no context while the service
  is still loading the model.
