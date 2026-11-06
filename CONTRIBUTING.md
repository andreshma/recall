# Contributing to recall

Thanks for helping! recall is a local-first retrieval system for a personal
technical library, used by a coding assistant on every prompt. A few principles
are non-negotiable — everything else is open to discussion.

## Ground rules

1. **Retrieval stays local.** No network calls, no telemetry. The model is
   loaded from a local snapshot, `HF_HUB_OFFLINE=1` is set on import, and
   `huggingface-hub` is excluded from the lockfile on purpose
   (`requirements.excludes`). Don't add a dependency that phones home. (What
   the hook injects does reach the assistant's provider — that is the point —
   so keep injected text short and paths library-relative.)
2. **The hook never gets in the way.** `recall_hook.py` fails open: on any error
   or delay the prompt passes through unchanged, within a 1.5 s budget. It uses
   only the standard library. Keep it that way.
3. **Scores must stay absolute.** Automatic injection depends on a cosine
   threshold. Changes to ranking must keep exposing the raw cosine, and any
   change to the model or chunking must re-calibrate the threshold
   ([ADR 0002](docs/adr/README.md)).
4. **The index knows its model.** Chunking, prefixes and model files are part of
   the index identity. If you change them, the index must refuse to mix — don't
   bypass `ModelMismatchError`.
5. **No book content in the repo.** Only metadata and hand-written queries.

## Good first contributions

See "Decision and future work" in [docs/paper.md](docs/paper.md):

- **More negatives**: grow the 28 negative prompts toward ~100 for a tighter
  threshold estimate.
- **Batch-invariance check**: make "same text alone vs. in a padded batch gives
  the same vector" an automatic check before indexing with a new model.
- **Second labeler** for part of the 80-query set.

## Dev setup

```powershell
uv venv --python 3.12
uv pip sync requirements.lock
.venv\Scripts\python.exe -I -m pytest          # unit tests, no model needed
.venv\Scripts\python.exe -I -m pytest -m model # regression against your real index
```

`requirements.in` is the source of truth for direct dependencies;
`requirements.lock` pins the exact versions with hashes for win/amd64.
Regenerate it with the command at the top of `requirements.in`.

**Dependency updates.** Dependabot opens PRs that bump `requirements.in` and
`pyproject.toml` but cannot regenerate the hash-pinned lock, so their CI fails
on `test_lock_pins_every_input_with_hashes` — on purpose. To take an update:
check out the PR branch, run the `uv pip compile` command from
`requirements.in` (drop `--offline` if the new wheels are not cached),
`uv pip sync requirements.lock`, run `pytest` **and** `pytest -m model`
against your index, and push the new lock to the PR.

## Experiments

To try another model or chunking, add a file under `experiments/configs/`, run
`experiments\run.ps1 -Only <name>` (it indexes into its own directory and never
touches production) and compare with `experiments\evaluate.py`. Report R@5,
MRR@10 and separation as in [docs/paper.md](docs/paper.md), and run
`tools\verify_index.py <dir> --sample 30` on the new index.

## Branching and commits

`main` is always releasable; work on a `feature/<name>` branch and open a PR.
Use Conventional Commits (`feat:`, `fix:`, `docs:`, `exp:`…). Add a test for
anything that touches the hook, the service auth, or the model identity.
