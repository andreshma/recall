# Security Policy

recall runs a local search service and a hook that adds text to every prompt a
coding assistant receives. This document explains the security model and how to
report a vulnerability.

## Security model

- **Local only.** The service binds to `127.0.0.1` on an ephemeral port and
  requires a random Bearer token, stored with the port in
  `%LOCALAPPDATA%\recall\service.json`. No component makes network calls; the
  model loads from a local snapshot and `huggingface-hub` is not installed.
  Injected passages do reach the assistant's provider as part of the prompt.
- **Fail-open hook.** The hook can only *add* context; on any error, timeout or
  low score it adds nothing. It starts in shadow mode, logging a hash of each
  prompt (never its text).
- **Read-only search.** The service and the MCP server open the index read-only.
- **Model identity.** The index records the model and chunking it was built
  with; searching with anything else is an explicit error.
- **Pinned supply chain.** `requirements.lock` pins every dependency by hash.

**Known limitation:** injected passages come from the indexed books. Text in a
book could, in principle, try to instruct the assistant (indirect prompt
injection). The hook and the MCP tool wrap passages in a block that tells
the assistant they are reference *data*, not instructions; every field taken
from the index has its `<`/`>` replaced so it cannot close that block, and each
passage's length is capped. Still, only index content you trust. See
[docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) for the full analysis.

## Reporting a vulnerability

**Please do not open a public issue for security vulnerabilities.**

Use GitHub's **private vulnerability reporting** ("Report a vulnerability" under
the Security tab). Please include the affected commit, a description of the
issue and its impact, and reproduction steps (describe the *class* of problem;
do not include a weaponized exploit).

We aim to acknowledge within **5 business days** and support coordinated
disclosure.

## Supported versions

While pre-1.0, only the latest `main` receives security fixes.
