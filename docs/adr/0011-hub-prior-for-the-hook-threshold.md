# 0011. Subtract a per-chunk hub prior before the hook threshold

- Status: Accepted (refines [0002](0002-cosine-absolute-threshold.md))
- Date: 2026-11-19

## Context and problem statement

[0002](0002-cosine-absolute-threshold.md) chose an absolute cosine threshold
for automatic injection, and [0004](0004-shadow-mode-before-activation.md)
required shadow mode before switching it on. Two weeks of shadow log
(2026-11-07 → 11-19, 1,768 distinct real prompts after dropping benchmark
repeats) showed that the threshold alone cannot be calibrated:

- The highest cosine per prompt is squeezed into a narrow band: p25 0.692,
  p50 0.724, p75 0.751, p99 0.782. At 0.75 the hook would inject on 26 % of
  prompts; at 0.80 on 0.3 %.
- What it would inject is mostly the same few chapters. At 0.75 about 300 of
  453 injections were three chapters (Google ADK hands-on code and
  inter-agent communication in *Agentic Design Patterns*, the system
  management example in *Enterprise Integration Patterns*); from 0.76 up,
  34 of 46 were *Zero Trust Networks* "context-aware agents" (network agents,
  not AI agents). Prompts about hooks, agents and services, which is most of
  what a coding assistant sees, are close to them regardless of topic.

These are hubs: chunks close to the region of embedding space where generic
prompts land. Raising the threshold removes the useful injections before it
removes them.

## Decision drivers

- Fewer injections of passages unrelated to the prompt, without losing the
  ones that answer it.
- No change to the explicit search (`recall search`, MCP): its ranking and
  quality floors stay as they are.
- No reindex and no extra cost per query inside the hook's 1.5 s budget.
- Prompts are never stored (only an HMAC of them is logged), so the fix cannot
  be learned from real prompt text.

## Considered options

- **Higher threshold plus a hand-kept list of excluded paths.** Rejected:
  every new hub needs an edit, and the list says nothing about why.
- **Margin of the first hit over the others.** Rejected for now: the eight
  logged scores are usually within 0.03 of each other, and it cannot be
  validated without labeled negatives.
- **Hubness correction with a reference set of generic prompts** (in the
  spirit of CSLS, Conneau et al. 2018): measure, per chunk, how close it is to
  prompts that should never inject, and subtract that.

## Decision

`recall.hub` computes, per chunk `d`,

    hub[d] = mean of the 5 highest cos(prompt, d) over the reference prompts
             − median of that value over all chunks

where the cosine of a prompt is computed as the search computes it (maximum
over its query windows). The reference is `src/recall/hub_prompts.json`: 90
synthetic operational prompts (ES/EN, short and long, about a repo, its CLI,
hook, service, CI, git, cloud resources and agents as tools) with no book
topic. It ships with the package; no real prompt is used.

- The service embeds the reference once per model and window settings
  (~15 s on CPU, cached in `hub_ref.npz` in the state directory, keyed by the
  model identity, the prompts and the window parameters) in a background
  thread after start, one prompt per call so that hook queries interleave.
  The prior is recomputed from the in-memory vectors on every index reload
  (~1.4 s for 41,741 chunks, outside the search lock).
- Every hit carries `hub`. The hook compares `cos − hook.hub_weight × hub`
  (default weight 1) with its threshold. Until the prior is loaded the hook
  logs `hub-pending` and does not inject. The ranking never uses `hub`.
- The shadow log now records the chunk ids and the adjusted scores (`adj`)
  of the eight hits, so a later prior or threshold can be replayed over it.
  `tools/shadow_report.py` reads `adj` first.
- `search.hub_prior: false` turns it off; `hook.hub_weight: 0` makes the hook
  compare the raw cosine again.

Measured 2026-11-19 on the production index (E5, CPU), with 60 of the
prompts as reference and the other 30 held out as negatives; positives are the
120 labeled queries (80 short, 20 data, 20 long prompts):

| Score | Threshold | Held-out negatives injected | Positives injected | Positives with the right passage first |
|---|---|---|---|---|
| Raw cosine | 0.77 | 7 % | 86 % | 72 % |
| Adjusted (top-5, weight 1) | 0.74 | 7 % | 93 % | 77 % |

Without being told, the prior placed the chapters that dominated the real
log in the top 2 % of all chunks (percentiles 98.1–100). Top-10, top-20 and
the plain mean separated almost the same as top-5. With all 90 prompts as
reference, threshold 0.74 injects on 93 % of the positives, 76 % with the
right passage first.

## Consequences

- Positive: the hubs are penalized by a measured property instead of a list;
  a new generic chunk is handled the same way. Explicit search is unchanged.
- Positive: the log can now be replayed (ids + adjusted scores), so the next
  calibration does not depend on what the hook happened to compare.
- Negative / trade-offs: the threshold scale changes. Entries before this ADR
  are raw cosines; calibrate with `tools/shadow_report.py --since` the date
  the prior went in. The starting threshold for E5 is 0.74, to be recalibrated
  from the shadow log before `hook.mode` becomes `active`.
- Negative: the evidence is small (30 held-out negatives written for the purpose, 120
  positives) and the reference set reflects one kind of user. Editing
  `hub_prompts.json` changes the prior and invalidates the calibration.
- Negative: the first start with a new model spends ~15 s of model time in
  the background, during which the hook does not inject.

## Calibration (2026-11-23)

Shadow log with the prior, 2026-11-19 18:22 → 11-23 (467 distinct searched
prompts):

| Adjusted threshold | Prompts that would inject | Distinct chapters | Hubs from the context section among them |
|---|---|---|---|
| 0.72 | 12.8 % | 44 of 60 | 10 |
| 0.73 | 7.5 % | 27 of 35 | 4 |
| 0.74 | 3.9 % | 16 of 18 | 0 |
| 0.75 | 1.7 % | 8 of 8 | 0 |

The passages that enter only between 0.74 and 0.75 were mostly generic (Git
server setup, IPython shortcuts, an introduction chapter, a book's front
matter). The hook was switched to `active` at **0.75**. The prior also lifts
chunks below the median (negative `hub`), so several injections at 0.74 had
a raw cosine of 0.715–0.73; a penalty-only variant (`max(hub, 0)`) is not
measured yet.
