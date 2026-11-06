# 0004. Shadow mode before activating injection

- Status: Accepted
- Date: 2026-11-04

## Context and problem statement

The threshold and the prompt filters were calibrated on a labeled set of
queries and negative prompts. Real prompts are more varied, and a wrong
threshold either injects noise or never helps.

## Decision drivers

- Validate behavior on real usage before it changes any prompt.
- Do not store prompt text.
- Make calibration reproducible.

## Considered options

- **A. Shadow mode by default**, logging what would have been injected.
- **B. Activate immediately** and adjust on complaints.
- **C. Keep the hook off** and rely only on the MCP tool.

## Decision

Option **A**. `hook.mode` defaults to `shadow`: the hook runs the full pipeline
and appends to `%LOCALAPPDATA%\recall\shadow.log` the prompt hash (never its
text), latency, scores, paths and the decision (`skip-trivial`,
`skip-mechanical`, `service-unavailable`, `below-threshold`, `over-budget`,
`would-inject`), but injects nothing. `tools/shadow_report.py` summarizes the
log and how many prompts each candidate threshold would inject. `active`
enables injection; `off` disables the hook.

## Consequences

- Positive: the threshold is set from real prompts before anything is injected.
- Positive: switching modes is a configuration change, no code change.
- Negative: no benefit from automatic injection during the shadow period.
- Negative: logs from different models are on different cosine scales and must
  be archived separately when the model changes.
