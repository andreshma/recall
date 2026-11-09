# Security Policy

recall runs a local search service and a hook that adds text to every prompt a
coding assistant receives. This document explains the security model and how to
report a vulnerability.

## Supported versions

| Version | Security fixes |
|---|---|
| 0.3.x | Yes |
| < 0.3 | No — upgrade to the latest 0.3 release |

recall is pre-1.0: fixes land on `main` and in the next 0.3.x release.

## Reporting a vulnerability

**Please do not open a public issue for security vulnerabilities.**

Use GitHub's private vulnerability reporting:
<https://github.com/andreshma/recall/security/advisories/new> ("Report a
vulnerability" under the repository's Security tab). Please include the
affected version or commit, a description of the issue and its impact, and
reproduction steps (describe the *class* of problem; do not include a
weaponized exploit).

The maintainer (GitHub: andreshma) aims to acknowledge within **5 business
days**, agrees a disclosure date with you, and credits you in the advisory
unless you prefer otherwise.

## Security model

- **Local retrieval.** The service binds to `127.0.0.1` on an ephemeral port
  and requires a random Bearer token, stored with the port and pid in
  `%LOCALAPPDATA%\recall\service.json` (user-only ACL). Before sending the
  token or a prompt, the hook, the client and `start-recall.ps1` make the
  server prove it knows the token (`GET /proof`, an HMAC bound to its port),
  so a process that took over the port of a dead service learns neither.
  Nothing makes network calls at run time; the model loads from a local
  snapshot and `huggingface-hub` is not installed.
- **What does leave the machine.** Injected passages — and, from the MCP tool,
  the absolute path of each chapter file — become part of the prompt, so they
  reach whatever provider runs your assistant. Index only documents you are
  willing to share with it.
- **Fail-open hook.** The hook can only *add* context; on any error, timeout or
  low score it adds nothing, within a 1.5 s wall-clock budget. It starts in
  shadow mode, logging an HMAC of each prompt (never its text).
- **Read-only search.** The service and the MCP server open the index read-only.
- **Model identity.** The index records the model and chunking it was built
  with; searching with anything else is an explicit error.
- **Pinned supply chain.** `requirements.lock` and `requirements-dev.lock` pin
  every package by hash; CI actions are pinned by commit SHA.

**Known limitation:** injected passages come from the indexed books. Text in a
book could, in principle, try to instruct the assistant (indirect prompt
injection). The hook and the MCP tool wrap passages in a block that tells the
assistant they are reference *data*, not instructions; in every field taken
from the index, `<`, `>` and their look-alikes become `&lt;`/`&gt;` and line
breaks are collapsed, so no field can close that block or forge an entry, and
each passage's length is capped. Still, only index content you trust. See
[docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) for the full analysis.
