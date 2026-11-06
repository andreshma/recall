"""Resume el modo sombra del hook para calibrar el umbral.

    .venv\\Scripts\\python.exe -I tools\\shadow_report.py [--since 2026-11-04] [--log RUTA]

Lee shadow.log (y sus rotaciones .1 .2 .3), cuenta decisiones, latencias y,
para los prompts que llegaron a buscar, cuantos se habrian inyectado con cada
umbral candidato. El log no guarda el texto del prompt (solo un hash), asi que
la revision de falsos positivos se hace con las rutas que se habrian inyectado.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import Counter
from pathlib import Path

THRESHOLDS = (0.50, 0.52, 0.54, 0.56, 0.58, 0.60, 0.62)


def default_log() -> Path:
    base = os.environ.get("RECALL_HOME") or str(
        Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "recall")
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
        "latency_ms": {"p50": statistics.median(ms), "p95": sorted(ms)[int(0.95 * (len(ms) - 1))],
                       "max": max(ms)} if ms else None,
        "best_quantiles": {q: best[int(q / 100 * (len(best) - 1))] for q in (10, 25, 50, 75, 90)} if best else None,
        "would_inject_at": {t: sum(b >= t for b in best) for t in THRESHOLDS},
        "top_paths": Counter(p for r in scored for p in (r.get("paths") or [])).most_common(10),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", type=Path, default=default_log())
    ap.add_argument("--since")
    a = ap.parse_args(argv)
    s = summarize(read_rows(a.log, a.since))
    print(json.dumps(s, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
