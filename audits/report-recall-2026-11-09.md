# Audit report: recall (2026-11-09)

| Field | Value |
|---|---|
| Project | `recall` 0.3.0 (alpha), Apache-2.0 |
| Audit date | 2026-11-09 |
| Code audited | the tree after the English translation of 2026-11-09 (pre-public history) |
| Type | Advisory, pre-release (never blocks): 360° audit (`F-xx`) plus an adversarial review (`ADV-x`, details in [ADVERSARIAL_REVIEW.md](../docs/ADVERSARIAL_REVIEW.md#review-of-2026-11-09)) |
| Tier assumed | Single-user tool / open-source alpha, before its first public release |
| RAG as found | **RED** for publication (1 High: F-01), AMBER for the code |
| RAG after fixes | **GREEN** with accepted residuals (below) |
| Findings | 1 High · 5 Medium · 18 Low · 3 Info (27 findings; F-07 and F-08 are the audit pass's names for ADV-2 and ADV-5) |

> **Scope note.** As in the [threat model](../docs/THREAT_MODEL.md), an
> attacker already running as the same Windows user (who can read the token,
> edit `config.json` or replace the index) is out of scope; findings that need
> that access are kept for robustness. "Another local process" (no access to
> the user's profile) is in scope.

> **How to read the references.** The public repository starts from a single
> commit, so this report cites files and tests, not commit ids. Every fix
> listed as **Fixed** is guarded by the test(s) named next to it; run one with
> `python -I -m pytest -q "<file>::<test>"`.

## Summary

The adversarial pass found two ways for a *local, unprivileged* process or
input to hurt the service: a stale `service.json` let whatever process took
over the port receive the bearer token and prompt text (ADV-2), and a single
query that always fails the same way could make the engine guard shut the
service down (ADV-1). Indexing could stall for hours on one pathological
heading (ADV-3), and a running service could pair vectors with the wrong text
after a rebuild (F-03). For publication, the tracked data carried source file
names (F-01) and private paths and names (F-05). The rest were robustness,
documentation drift, CI hardening and open-source readiness.

All findings are fixed except the residuals listed under **Accepted**.

## Findings and status

### Service, client, hook, MCP, launcher

| ID | Sev. | Finding | Status | Guarded by |
|---|---|---|---|---|
| ADV-1 | Medium | A query that fails deterministically (a lone surrogate: valid JSON, not UTF-8 encodable) exhausted the engine guard's rebuild budget: 8 requests shut recall down, each reloading the model. | **Fixed** — rejected at the boundary (400 / `isError`); a non-engine error rebuilds at most once per run of failures and never counts toward giving up. | `tests/test_engine_guard.py::test_a_deterministic_request_error_never_gives_up`, `tests/test_service.py::test_lone_surrogate_is_a_400`, `tests/test_mcp.py::test_lone_surrogate_query_is_a_tool_error` |
| F-18 | Low | Non-engine faults (e.g. SQLite) rebuilt the GPU session repeatedly; the replaced session was not released. | **Fixed** (with ADV-1). | `tests/test_engine_guard.py::test_replaced_session_is_released`, `::test_a_deterministic_request_error_never_gives_up` |
| ADV-2 / F-07 | Medium | Clients never authenticated the server: with a stale `service.json` the hook and client sent the token and up to 1,000 characters of the prompt to any process on that port, which could also answer with forged hits; the launcher could kill that pid on a 503. | **Fixed** — `GET /proof` handshake (HMAC of a client nonce and the port) before any token or prompt is sent, in the hook, the client and `start-recall.ps1`; the launcher only stops the pid a proven server reports. | `tests/test_server_proof.py` (`test_hook_sends_no_token_or_prompt_to_an_impostor`, `test_client_sends_no_token_to_an_impostor`, `test_relayed_proof_from_the_genuine_service_is_rejected`, `test_genuine_service_proves_itself`, `test_proof_endpoint`), `tests/test_start_script.py::test_impostor_on_the_port_gets_no_token_and_is_not_killed` |
| ADV-6 | Low | Pre-auth slot exhaustion: the 10 s timeout was per read, so trickling a header byte every few seconds held a slot forever; 32 sockets denied service. | **Fixed** — absolute 3 s request-read deadline per connection. | `tests/test_service.py::test_trickled_request_is_cut_at_the_deadline` |
| ADV-5 / F-08 | Low | The hook did not defang `year`, so metadata could close `</recall_books>`. | **Fixed** — every header field squashed and defanged. | `tests/test_hook.py::test_no_header_field_can_forge_an_entry_or_close_the_block`, `::test_every_metadata_field_is_squashed_and_defanged` |
| ADV-7 | Low | Newlines in book/chapter/section could forge an extra `[n] ...` entry (hook) or `source:` line (MCP). | **Fixed** — header fields collapsed to one line in both renderers. | `tests/test_hook.py::test_no_header_field_can_forge_an_entry_or_close_the_block`, `tests/test_mcp.py::test_no_header_field_can_forge_a_source_line`, `::test_every_metadata_field_is_squashed_and_defanged` |
| ADV-11 | Info | Fullwidth/confusable brackets and the old `‹ ›` defang output looked like tags. | **Fixed** — `<`, `>` and their look-alikes become `&lt;` / `&gt;`. | `tests/test_hook.py::test_lookalike_tags_are_neutralized`, `tests/test_server_proof.py::test_hook_and_package_share_the_proof_and_tag_rules` |
| ADV-9 | Low | The MCP stdio server exited on deeply nested JSON (`RecursionError`). | **Fixed** — -32700, the loop keeps serving. | `tests/test_mcp.py::test_stdio_survives_deeply_nested_json` |
| ADV-10 | Info | The service dropped the connection on a nested-array body under 64 KiB. | **Fixed** — 400. | `tests/test_service.py::test_deeply_nested_body_is_a_400` |
| F-12 | Low | `service.json` was not validated: a list or string pid crashed `recall serve`; the client surfaced `KeyError` as "recall unavailable" instead of falling back. | **Fixed** — one validation rule set for service, client, hook and launcher. | `tests/test_server_proof.py::test_malformed_service_file_is_no_service`, `::test_hook_and_package_validate_service_files_alike` |
| F-06 | Low | `start-recall.ps1` used `Invoke-RestMethod -NoProxy`, missing in Windows PowerShell 5.1, so every health check threw and a degraded service was never restarted; `RECALL_HOME` did not reach the service. | **Fixed** — HTTP without a proxy that works in 5.1 and 7; the state directory is passed on the command line. | `tests/test_start_script.py` (runs under every installed edition): `test_healthy_service_is_left_alone`, `test_degraded_service_is_restarted_with_the_state_dir` |

### Index, search, data, configuration, packaging

| ID | Sev. | Finding | Status | Guarded by |
|---|---|---|---|---|
| ADV-3 | Medium | ReDoS in the heading regex: a heading with a long run of spaces took 0.18 s (500 spaces), 10.5 s (2,000), more than 90 s (4,000); converters emit such runs. | **Fixed** — linear hand-written parser. | `tests/test_chunking.py::test_heading_parse_is_linear_on_long_space_runs` (time-bounded) |
| F-11 | Low | Closing-hash stripping ate "C#"/"F#"; chunks under 100 characters were dropped (short last sections lost); `contents-.*` / `copyright.*` skipped real chapters. | **Fixed** — CommonMark rule for closing hashes, short tails attached to the previous chunk, anchored skip patterns. | `tests/test_chunking.py::test_closing_hashes_need_whitespace_before_them`, `::test_short_last_section_is_attached_not_dropped`, `::test_skip_rules_do_not_swallow_chapters_named_contents_or_copyright` |
| ADV-8 | Low | A directory junction (`mklink /J`, no admin) inside a root made outside content indexable. | **Fixed** — links and junctions ignored; real paths must stay under the root. | `tests/test_indexer.py::test_discover_ignores_directory_junctions`, `::test_discover_ignores_symlinked_books_and_files`, `::test_root_that_is_a_junction_still_works` |
| ADV-4 | Low | A hostile `books_meta.json` (non-object entries, non-iterable `domains`) broke every search returning that book (500s that also fed ADV-1). | **Fixed** — entries normalized on load. | `tests/test_metadata.py::test_hostile_entries_are_normalized_on_load`, `::test_hostile_meta_file_does_not_break_search` |
| F-03 | Medium | Stale snapshot and rowid reuse: after `--rebuild` the running searcher returned the wrong passage with a confident cosine; after an identity change it kept old vectors joined to new rows while `/health` said ok. | **Fixed** — rows verified against the snapshot's `input_sha` (a mismatch is never returned and triggers a reload); an identity change sets `load_error` and makes `/health` and `/search` answer 503. | `tests/test_search.py::test_stale_snapshot_never_pairs_a_vector_with_another_chunks_text`, `::test_identity_change_seen_by_background_reload_is_visible`, `::test_existing_index_without_new_columns_still_opens`, `tests/test_engine_guard.py::test_load_error_makes_health_503_and_search_503`, `::test_index_mismatch_is_not_an_engine_fault` |
| F-09 | Low | The default provider `DmlExecutionProvider:1` was the development machine's adapter; elsewhere the session fell back to CPU silently and the hook timed out. | **Fixed** — no adapter by default, fallback across adapters then CPU, the choice logged and shown in `/health`. **Breaking** default (CHANGELOG). | `tests/test_providers.py` (all six tests), `tests/test_service.py::test_health_reports_engine_and_metadata_state` |
| F-10 | Low | From a wheel, metadata resolved outside site-packages and `recall eval` used a source-tree path: filters silently returned nothing. | **Fixed** — metadata is package data; `recall eval` asks for `--queries` (exit 2); a missing file is reported (`meta_error`). | `tests/test_packaging.py::test_wheel_ships_the_book_metadata`, `tests/test_cli.py::test_eval_without_query_set_fails_loudly`, `tests/test_metadata.py::test_missing_meta_file_is_reported` |
| F-01 | **High** (publication) | Tracked data carried the local file names of the source documents, which revealed where some copies had been obtained, plus evidence notes in Spanish. | **Fixed** — `source_file` and `year_evidence` removed; PDF locations come from a git-ignored local map. **Breaking** for `tools/build_pages.py` (CHANGELOG). | `tests/test_publication.py::test_no_private_terms_in_tracked_text_files`, `::test_books_meta_has_no_source_files_or_evidence_prose`, `::test_local_sources_map_is_git_ignored`, `::test_build_pages_reads_the_local_sources_map` |
| F-05 | Low (publication) | A private default library root and converter path in code and docs; a personal first name in an evaluation prompt. | **Fixed** — neutral default `~\books\md`, the Markdown input contract documented instead of a converter, the prompt made generic. **Breaking** default (CHANGELOG). | `tests/test_publication.py::test_no_private_terms_in_tracked_text_files` (terms checked by digest), `tests/test_indexer.py::test_missing_root_does_not_empty_the_index` |
| F-19 | Info | `textutil.squash` was unused; the test embedder lives in `embedder.py`. | **Fixed** (squash is used by the renderers) / **Accepted** (`HashEmbedder` stays in `embedder.py`, see below). | `tests/test_mcp.py::test_every_metadata_field_is_squashed_and_defanged` |

### Documentation, CI, open source

Severities in this group were assigned in this report (the audit pass listed
these findings without one).

| ID | Sev. | Finding | Status | Guarded by |
|---|---|---|---|---|
| F-04 | Medium | The quickstart did not work from a fresh clone: no step made the package importable under `python -I`, obtained the model or set the library root. | **Fixed** — README, CONTRIBUTING and Makefile use an editable install with the locked `hatchling`/`editables` (no build isolation), document the model snapshot (recall never downloads) and the library root; a CI job runs the README install block verbatim and then `python -I -m recall --help`. | CI job `quickstart` (`.github/workflows/ci.yml`), `tests/test_packaging.py::test_readme_quickstart_installs_the_package` |
| F-13 | Low | CI: checkouts persisted credentials; no CodeQL; dev tools installed by version only. | **Fixed** — `persist-credentials: false` everywhere, `codeql.yml` (Python and Actions, SHA-pinned), dev tools from the hash-locked `requirements-dev.lock`. Two pins need online verification (Accepted). | `tests/test_packaging.py::test_lock_pins_every_input_with_hashes`, `::test_dev_group_matches_dev_requirements` |
| F-14 | Low | No CITATION.cff, no release tag, duplicate CHANGELOG subsections and no compare links, no Code of Conduct contact, legacy license metadata, no Repository/Changelog URLs. | **Fixed** — `CITATION.cff` (CFF 1.2.0), PEP 639 `license` / `license-files`, project URLs, classifiers for the supported platforms, CHANGELOG 0.3.0 with one subsection per type and links, a private Code of Conduct contact, supported versions in SECURITY. The `v0.3.0` tag is created at publication. | `tests/test_packaging.py::test_version_is_the_same_everywhere` |
| F-15 | Low | Doc drift: test counts, book and chunk counts, "nothing crosses the machine boundary", "stripped" vs entities, threat-model spoofing, the L9 note, "onnxruntime (Linux CI)", a stale SBOM. | **Fixed** — counts measured and written (README, TESTING); the paper labeled as a dated snapshot with its numbers kept; privacy and entity wording; THREAT_MODEL updated for spoofing, DoS, elevation, containment and publication hygiene; the 2026-11-06 report's L9 note corrected; SBOMs regenerated. | Docs review; relative links checked by script |
| F-16 | Low | The README said paths are cited relative to the library; true for the hook only — the MCP tool returns absolute paths. | **Fixed** — README, USAGE and THREAT_MODEL state both. | `tests/test_hook.py::test_render_cites_library_relative_paths` |
| F-17 | Low | Vacuous assertions (`snippet_hits >= 0`, `with_snip or not long_`, a lock-hash check that passed whenever any hash existed) and timing-sensitive asserts (tight wall-clock bounds, fixed sleeps). | **Fixed** — exact expectations in `test_snippets.py` and the lock test; fixed sleeps replaced with bounded polling; wall-clock bounds measured around the operation only, against fake services that stall far longer than the bound. | `tests/test_snippets.py::test_search_adds_snippet_only_when_asked`, `tests/test_hook.py::test_slow_service_respects_budget`, `::test_budget_is_a_wall_clock_deadline`, `tests/test_hardening.py::test_connections_beyond_the_cap_are_closed_and_slots_come_back`, `tests/test_packaging.py::test_lock_pins_every_input_with_hashes` |

## Accepted (residuals)

| Item | Why accepted |
|---|---|
| Proof-to-request gap (ADV-2) | A few milliseconds between `/proof` and the request (HTTP/1.0, one connection per request), in which the real service would have to die and another process bind the same port. Closing it needs a persistent authenticated channel; the window is too small to aim at. |
| `/proof` is unauthenticated (ADV-2) | By design: it must work before the client sends any secret. It is loopback-only and `Host`-validated, and returns only an HMAC of a client-chosen nonce. |
| Upgrade from 0.2 (ADV-2) | A still-running 0.2 service has no `/proof`: new clients fail open and the launcher starts a second service. Documented (USAGE §6, CHANGELOG): stop the old service once. |
| MCP returns absolute paths (F-16) | So that the assistant can open the file. Documented as reaching the assistant's provider. |
| `HashEmbedder` in `embedder.py` (F-19) | A test double kept next to the real embedder so the tests and the probes share it; documented as the test embedder and never built by `from_config`. |
| Paper numbers (F-15) | `docs/paper.md` is a dated study (2026-11-05, 126 books); it is labeled as a snapshot rather than rewritten. |

## Deterministic results

| Check | Result |
|---|---|
| Unit suite (`python -I -m pytest -q`) | 238 tests collected; 237 pass and 1 skips (creating symlinks needs a privilege the run did not have) on Windows with both PowerShell editions installed |
| Model suite (`-m model`) | 14 tests; run locally against the real index |
| ruff format / ruff check / mypy `--platform win32` | clean |
| Trivy (lockfiles, DB of 2026-11-08) | runtime lock: 0 vulnerabilities (18 packages); dev lock: 0 (after re-locking `msgpack` 1.2.1 -> 1.2.4 for CVE-2026-114613) |
| SBOM (CycloneDX, Trivy 0.67.2) | [`sbom/recall-2026-11-09.cdx.json`](sbom/recall-2026-11-09.cdx.json) (runtime lock) and [`sbom/recall-dev-2026-11-09.cdx.json`](sbom/recall-dev-2026-11-09.cdx.json) (dev and CI tools) |

To regenerate the SBOMs (offline once Trivy's database is cached), copy each
lock to a `requirements.txt` in a folder of its own (named `recall` and
`recall-dev`, which become the SBOM's component names) and run:

```powershell
trivy fs --offline-scan --skip-db-update --scanners vuln --format cyclonedx --output audits\sbom\recall-<date>.cdx.json <dir>\recall
trivy fs --offline-scan --skip-db-update --scanners vuln --format cyclonedx --output audits\sbom\recall-dev-<date>.cdx.json <dir>\recall-dev
```

Summary JSON: [`summary/recall-2026-11-09.json`](summary/recall-2026-11-09.json).

## Method and limits

The audit pass read the code, docs and CI across security, reliability, data
quality, privacy, testing, maintainability, documentation, supply chain and
publication. The adversarial pass reproduced each `ADV-x` finding with a
proof of concept against a throwaway `RECALL_HOME` built from the unit-test
fixtures (never the production index or settings, never the network). Both
were run by the project's own maintainer tooling, not by an independent party.

## Resolved online before publication

- F-13: `github/codeql-action` pinned to the commit of v4.39.1 (`9b3254085494b55a0f7c4c2b3d2d52cc712ce370`), verified against the release tag.
- F-13: the `quickstart` job installs uv with `astral-sh/setup-uv` pinned to the commit of v10.3.0 (`1c37ad07a6a961277cf70c0d37d6f313000f5884`) and `version: 0.11.7`.
- Dev lock re-locked online: `msgpack` 1.2.1 -> 1.2.4 (CVE-2026-114613, High, CI tools only); the dev SBOM was regenerated and Trivy reports 0 vulnerabilities in both locks.
