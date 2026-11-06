# Audit 360 report: recall

| Field | Value |
|---|---|
| Project | `recall` v0.2.0 (alpha), Apache-2.0 |
| Audit date | 2026-11-06 |
| Commit audited | `557bff4` |
| Type | Advisory, pre-release (never blocks) |
| Tier assumed | Single-user tool / open-source alpha |
| RAG | **AMBER** (no Critical or High; 6 Medium, all fixable locally) |
| Findings | 0 Critical · 0 High · 6 Medium · 9 Low · 4 Info (19) |

> **Scope note.** recall is a single-user, local tool. An attacker already
> running as the same Windows user (who can read the token, edit `config.json`
> or replace the index) is out of scope, as in the
> [threat model](../docs/THREAT_MODEL.md). Findings that need that access are
> rated Low and kept for robustness.

## Remediation status (post-audit follow-up)

The Medium findings and most Lows were fixed in the commit that follows this
report (137 unit tests and the 10-test model regression suite pass). The
findings further down are recorded **as found** at `557bff4`.

| ID | Finding | Status |
|---|---|---|
| M1 | Data-block markers closable by headings / overlapping tags (AR-01–03) | **Fixed** — every field from the index (text, book, chapter, section, page, path, lines) has `<`/`>` replaced in both the hook and the MCP output; regression tests with the probe strings |
| M2 | Hook budget not a deadline (AR-04) | **Fixed** — request runs in a daemon thread joined with the remaining budget; response capped at 4 MiB; slow-drip test |
| M3 | `doc_max_tokens` outside the identity (AR-05) | **Fixed** — part of the identity when not the default; indexes without it are accepted and upgraded on the next write; any later change is an explicit error |
| M4 | "Nothing leaves your machine" overstated (AR-06) | **Fixed** — README, CONTRIBUTING, SECURITY and MCP instructions reworded; the hook cites library-relative paths. The MCP tool still returns absolute paths so the assistant can open the file (documented) |
| M5 | Defaults lag the documented configuration | **Fixed** — `config.example.json` (E5, tested against the experiment config) and a USAGE procedure; code defaults stay baseline on purpose |
| M6 | Stale score scale in MCP instructions (AR-13) | **Fixed** — guidance derived from the configured threshold |
| L1 | Non-finite scores pass (AR-07) | **Fixed** — `isfinite` in the hook and the MCP formatter; search drops non-finite scores |
| L2 | Start races (AR-08, AR-09) | **Fixed** — PID must belong to a Python process (service and start script); exclusive start lock before the model load |
| L3 | Service resource limits (AR-10, AR-11) | **Partially fixed** — 10 s connection timeout, `Infinity`/`NaN`/overflow rejected with 400; no thread cap yet |
| L4 | Unsalted shadow hash (AR-12) | **Fixed** — HMAC-SHA256 with a per-install key (`log.key`); hashes logged before the change are not comparable with later ones |
| L5 | Digest cache keyed by size + mtime (AR-14) | Open |
| L6 | Static quality not gated | **Partially fixed** — unused imports removed; no ruff/mypy CI step yet |
| L7 | No coverage floor | Open |
| L8 | Dependency updates via Dependabot | **Fixed** — re-lock procedure documented in CONTRIBUTING |
| L9 | Spanish-only code strings | Open |
| I2 | MCP errors echo `str(e)` (AR-16) | **Fixed** — exception type only; detail to stderr |
| I3 | Unpinned `pip-audit` (AR-17) | **Fixed** — pinned version |
| I1, I4 | Truncated blob aborts load; bare `icacls` | Open / no action needed |

---

# Executive summary

recall is small (≈3,200 lines of Python), clearly layered (indexer → SQLite
index → service → hook / MCP) and unusually well measured for an alpha: a
labeled 80-query evaluation, regression floors tied to production numbers, an
integrity verifier, ADRs, a threat model and a written study. Dependency and
secret scans are clean, every dependency is hash-pinned, CI is least-privilege
with SHA-pinned actions, and the core security controls (token, `Host`
check, loopback-only, read-only index, fail-open hook) held under probing.

The weak spots are at the **boundary where book text meets the assistant**:
the markers that tell the assistant "this is data, not instructions" can be
closed by crafted headings or overlapping tags, and the hook's 1.5 s budget is
not a real deadline. One quality gap matters for the large-chunk production
setup: a token limit that changes vectors is not part of the index identity.
Finally, the docs over-promise privacy ("nothing leaves your machine") and the
code defaults lag behind the documented production configuration.

### Top 5 (as found)

1. **M1** Book headings and overlapping tags can close the `<recall_libros>`
   (hook) and `<<<datos>>>` (MCP) data blocks — indirect prompt injection.
2. **M2** The hook's 1.5 s budget is a per-socket timeout, not a deadline
   (8.3 s observed).
3. **M3** `doc_max_tokens` / `query_max_tokens` change vectors but are not in
   the model identity — silent mixing of truncated and full vectors.
4. **M4** "Nothing leaves your machine" omits that passages and absolute paths
   reach the hosted assistant.
5. **M5** Code defaults are the baseline (embeddinggemma-300m, threshold 0.58)
   while docs present E5 as the configuration; a fresh install silently gets
   the older, worse-separating setup.

### Scanners

| Tool | Result |
|---|---|
| pip-audit (lockfile, hashes) | 0 known vulnerabilities |
| Trivy fs (vuln on lockfile, secrets, misconfig) | 0 vulnerabilities · 0 secrets · 0 misconfigurations |
| Semgrep (`p/python`, `p/secrets`) | 0 findings on 33 files |
| Bandit | 1 Medium (B608, constant SQL with placeholders — false positive) · 4 Low |
| Ruff (`E,F,I,UP,B,S`, non-test code) | 40 issues: 19 E501, 2 F401 unused imports, 3 B905, 3 B007, misc. UP; S603/S607/S310 reviewed |
| mypy (`src/recall`, hook) | 7 errors (Optional connection access, one wrong-arity call path) |
| Lizard | avg CCN 5.4; 10 functions above CCN 15 (max 32, `verify_index.verify`) |
| pytest + coverage | 122 passed; 73 % line+branch (unit suite only) |
| Adversarial review | 17 findings, 6 Medium — [docs/ADVERSARIAL_REVIEW.md](../docs/ADVERSARIAL_REVIEW.md) |

Not run: gitleaks, trufflehog, osv-scanner, SonarQube (not installed). The
SBOM was generated with Trivy from the lockfile:
[`sbom/recall-2026-11-06.cdx.json`](sbom/recall-2026-11-06.cdx.json).

## Findings

Each finding: what, evidence, impact, fix. `AR-xx` refers to the
[adversarial review](../docs/ADVERSARIAL_REVIEW.md), which holds the probes.

### MEDIUM

**M1 · Security — Data-block markers can be closed by book content** (AR-01, AR-02, AR-03)
- *What:* the hook strips `</recall_libros>` from passage text in one regex
  pass and does not sanitize the header (book, chapter, section, path); the
  MCP formatter sanitizes text but not the header, and misses `<<< /` with a
  space.
- *Evidence:* `recall_hook.py:203,210`, `mcp_server.py:87,112`; probes produced
  3 closing tags instead of 1 (hook) and 2 instead of 1 (MCP).
- *Impact:* a crafted heading or passage can end the "this is data" block and
  address the assistant as if outside it. Requires hostile content in the
  library, so the realistic risk is books from untrusted sources.
- *Fix:* one sanitizer applied to every field that reaches the assistant,
  repeated until stable, that neutralizes `<`/`>` runs.

**M2 · Reliability — The hook's time budget is not a deadline** (AR-04)
- *What:* the remaining budget is passed as the urllib socket timeout, which
  applies per read.
- *Evidence:* `recall_hook.py:167`; a slow-drip fake service held the hook
  8.32 s against a 1.5 s budget.
- *Impact:* a stalled service (e.g. GPU hang after headers) delays every
  prompt until Claude Code's own 5 s hook timeout.
- *Fix:* wall-clock deadline: run the request in a daemon thread joined with
  the remaining budget.

**M3 · Data quality — Token limits are not part of the model identity** (AR-05)
- *What:* the identity covers model, revision, files, dim, prefixes, pooling,
  but not `doc_max_tokens` / `query_max_tokens`.
- *Evidence:* `embedder.py:212`; identical identities with 320 vs 1,100 tokens
  give cosine 0.9335 for the same chunk.
- *Impact:* with ~3,500-character chunks, dropping or changing that key makes
  incremental indexing mix truncated and full vectors without error — the
  exact failure the identity guard exists to prevent.
- *Fix:* add `doc_max_tokens` to the identity (it shapes stored vectors), and
  migrate the existing identity once without re-embedding.

**M4 · Privacy / docs — "Nothing leaves your machine" is overstated** (AR-06)
- *What:* retrieval is local, but injected passages (up to 3 × 700 chars per
  prompt) and absolute chapter paths, which contain the OS username, are sent
  to the assistant's provider with the prompt.
- *Evidence:* README "Local first" banner, CONTRIBUTING rule 1,
  `recall_hook.py:211`.
- *Impact:* a user could index confidential documents believing they stay
  local.
- *Fix:* reword to "retrieval is local; passages go to your assistant as part
  of the prompt"; cite library-relative paths.

**M5 · Configuration — Code defaults lag the documented configuration**
- *What:* `config.DEFAULTS` is the baseline (embeddinggemma-300m, ~900-char
  chunks, threshold 0.58). E5 only exists in a local `config.json` and
  `experiments/configs/e5-gemma2-chunk3500.json`. The README "Results" and the
  paper call E5 "production".
- *Evidence:* `src/recall/config.py:39-75`; README "Configuration" table.
- *Impact:* a new user gets the configuration the study shows is worse at
  separating relevant from unrelated prompts, and has no documented step to
  adopt E5. Moving the defaults would, conversely, break existing baseline
  indexes via the identity guard (by design).
- *Fix:* keep code defaults (to avoid breaking existing indexes) but document
  and ship E5 as the recommended `config.json` (a ready file plus a USAGE
  step), and state plainly in the README that defaults are the baseline.

**M6 · Assistant guidance — MCP instructions carry a stale score scale** (AR-13)
- *What:* the tool instructions tell the assistant `cos >= 0.58` is "almost
  always relevant" and 0.50–0.58 is "judge yourself".
- *Evidence:* `mcp_server.py:41`.
- *Impact:* under E5 every negative scores up to 0.726, so the assistant is
  told to trust unrelated passages. Rated Low in the adversarial pass;
  raised to Medium here because it degrades answers on the production config
  every time the tool is used.
- *Fix:* derive the guidance from the configured threshold.

### LOW

**L1 · Non-finite scores pass the threshold** (AR-07) — a NaN/inf `cos`
passes `cos < threshold` checks. *Fix:* `math.isfinite` in the hook; drop
non-finite scores in search.

**L2 · Service start races** (AR-08, AR-09) — a reused PID in a stale
`service.json` keeps recall down; concurrent starts leave an orphaned
`serve` (observed during the production switch). *Fix:* require `/health` to
answer before treating an instance as alive; take a lock before loading the
model.

**L3 · Service resource limits** (AR-10, AR-11) — no per-connection timeout
or thread cap; `k = 1e400` raises an uncaught `OverflowError`. *Fix:*
handler timeout, catch `OverflowError`.

**L4 · Shadow-log hash is unsalted** (AR-12) — a guessed prompt can be
confirmed. *Fix:* HMAC with a per-install key.

**L5 · Model digest cache keyed by size + mtime** (AR-14) — robustness only.

**L6 · Static quality is not gated** — ruff reports 40 issues in non-test
code (2 unused imports, `zip()` without `strict=`, unused loop variables) and
mypy 7 errors; CI runs neither. *Fix:* fix the real ones, add ruff + mypy
config and a CI step.

**L7 · Coverage has no floor; CLI and evaluation untested in the unit suite** —
73 % overall; `cli.py` and `evaluation.py` at 0 % (exercised only by the
`model` suite); `embedder.py` 28 % (the ONNX path needs the model). *Fix:*
CLI smoke tests with the hash embedder; a coverage floor in CI.

**L8 · Dependency updates cannot flow through Dependabot** — the lock is
hash-pinned and compiled offline for win/amd64; Dependabot edits
`requirements.in`/`pyproject.toml` but not the lock, so its PR fails CI
(PR #1: `test_lock_pins_every_input_with_hashes`). The guard works; the
process is undocumented. *Fix:* document the re-lock procedure for maintainers
(and keep the test).

**L9 · Supporting docs and comments are Spanish-only in code** — public docs
are English, but docstrings, CLI help and error messages are Spanish. Not a
defect for the current user; a barrier for contributors. *Fix:* translate
CLI help and user-facing errors first.

### INFO

- **I1** Truncated vector blob aborts the index load (AR-15); fail-open holds.
- **I2** MCP tool errors echo `str(e)`, which can contain paths (AR-16).
- **I3** The advisory `pip-audit` CI job installs an unpinned tool (AR-17).
- **I4** `icacls` is invoked by bare name (Ruff S607). Probed: with a planted
  `icacls.exe` in the working directory, Windows still resolved the system
  binary, and the service is launched with the repo as its working
  directory. No action needed; an absolute `%SystemRoot%\System32` path would
  make it explicit.

## Deterministic results

| Check | Result |
|---|---|
| Unit tests | 122 passed, 10 deselected (`model`) |
| Model regression (local, real index) | 10 passed (2026-11-06) |
| Coverage (unit) | 73 % — hook 90 %, search 92 %, indexer 93 %, chunking 93 %, service 74 %, embedder 28 %, cli 0 %, evaluation 0 % |
| pip-audit | 0 vulnerabilities (18 packages) |
| Trivy | 0 / 0 / 0 |
| Semgrep | 0 |
| Bandit | 1 Medium (false positive), 4 Low |
| Ruff (non-test) | 40 |
| mypy | 7 |
| Lizard | avg CCN 5.4, max 32 |

Summary JSON: [`summary/recall-2026-11-06.json`](summary/recall-2026-11-06.json).

## Method and limits

Judgment layer across architecture, security, reliability, performance, data
quality, privacy, testing, maintainability, documentation, supply chain and
licensing, plus the deterministic scanners above and the adversarial review's
probes. Run by the project's own maintainer tooling, not an independent party.
The `model` suite and performance figures depend on one machine (Windows 11,
RTX 3070 Ti). Accessibility is not applicable (no UI).
