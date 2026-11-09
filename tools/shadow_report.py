"""Summarize the hook's shadow mode to calibrate the threshold.

    .venv\\Scripts\\python.exe -I tools\\shadow_report.py [--since 2026-11-04] [--log PATH]

Reads shadow.log (and its rotations .1 .2 .3), counts decisions, latencies and,
for the prompts that got as far as a search, how many would have been injected
at each candidate threshold. The log does not keep the prompt text (only a
hash), so false positives are reviewed through the paths that would have been
injected.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import Counter
from pathlib import Path

# 0.50-0.62 covers gemma-300m; 0.70-0.82 covers embeddinggemma-2 (threshold 0.75).
THRESHOLDS = (0.50, 0.52, 0.54, 0.56, 0.58, 0.60, 0.62, 0.70, 0.72, 0.74, 0.75, 0.76, 0.78, 0.80, 0.82)


def default_log() -> Path:
    base = os.environ.get("RECALL_HOME") or str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "recall")
    return Path(base) / "shadow.log"


def read_rows(log: Path, since: str | None = None) -> list[dict]:
    rows = []
    for p in [log.with_name(log.name + f".{i}") for i in (3, 2, 1)] + [log]:
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict) and (not since or r.get("ts", "") >= since):
                rows.append(r)
    return rows


def summarize(rows: list[dict]) -> dict:
    scored = [r for r in rows if isinstance(r.get("best"), (int, float))]
    ms = [r["ms"] for r in rows if isinstance(r.get("ms"), (int, float)) and r.get("best") is not None]
    best = sorted(r["best"] for r in scored)
    return {
        "total": len(rows),
        "decisions": dict(Counter(r.get("decision", "?") for r in rows).most_common()),
        "searched": len(scored),
        "latency_ms": {"p50": statistics.median(ms), "p95": sorted(ms)[int(0.95 * (len(ms) - 1))], "max": max(ms)}
        if ms
        else None,
        "best_quantiles": {q: best[int(q / 100 * (len(best) - 1))] for q in (10, 25, 50, 75, 90)} if best else None,
        "would_inject_at": {t: sum(b >= t for b in best) for t in THRESHOLDS},
        "top_paths": Counter(p for r in scored for p in (r.get("paths") or [])).most_common(10),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Summarize the hook shadow log to calibrate the threshold.")
    ap.add_argument("--log", type=Path, default=default_log(), help="shadow.log path (rotations .1-.3 are read too)")
    ap.add_argument("--since", help="only entries on/after this ISO date, e.g. 2026-11-07")
    a = ap.parse_args(argv)
    s = summarize(read_rows(a.log, a.since))
    print(json.dumps(s, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
