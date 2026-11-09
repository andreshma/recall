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

- [ ] Retrieval stays local: no new network calls, no telemetry, and
      `huggingface-hub` stays excluded.
- [ ] The hook still fails open and stays within its time budget.
- [ ] The service still binds to 127.0.0.1 and requires the Bearer token;
      clients still verify `/proof` before sending the token or a prompt.
- [ ] Every index field rendered by the hook or the MCP tool still goes
      through the sanitizer (`field`/`defang` in `recall_hook.py`,
      `textutil.block_field` in the MCP server): one line, `<`/`>` and their
      look-alikes as entities.
- [ ] No book content, personal paths or private data are committed.
- [ ] Changes to chunking, prefixes or model are reflected in the model
      identity (they force `recall index --rebuild`).

## Tests

- [ ] `python -I -m pytest` passes.
- [ ] `ruff format --check .`, `ruff check .` and `mypy --platform win32` pass.
- [ ] User-visible changes are under `[Unreleased]` in `CHANGELOG.md`, and
      docs (README, `docs/USAGE.md`) match the new behavior.
- [ ] If retrieval changed: `python -I -m pytest -m model` passes against a full
      local index, and new numbers are recorded in the PR.
