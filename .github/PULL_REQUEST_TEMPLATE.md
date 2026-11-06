<!-- Thanks for contributing! Keep PRs focused and small where possible. -->

## What & why

<!-- What does this change and why? Link any issue: Closes #123 -->

## Type

- [ ] feat
- [ ] fix
- [ ] docs
- [ ] refactor / chore
- [ ] experiment (new chunking/model configuration)

## Safety checklist (required)

- [ ] Nothing leaves the machine: no new network calls, no telemetry, and
      `huggingface-hub` stays excluded.
- [ ] The hook still fails open and stays within its time budget.
- [ ] The service still binds to 127.0.0.1 and requires the Bearer token.
- [ ] No book content, personal paths or private data are committed.
- [ ] Changes to chunking, prefixes or model are reflected in the model
      identity (they force `recall index --rebuild`).

## Tests

- [ ] `python -I -m pytest` passes.
- [ ] If retrieval changed: `python -I -m pytest -m model` passes against a full
      local index, and new numbers are recorded in the PR.
