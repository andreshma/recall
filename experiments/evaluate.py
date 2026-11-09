"""Compare the production index with the experiments from experiments/run.ps1.

    .venv\\Scripts\\python.exe -I experiments\\evaluate.py [--queries tests\\data\\queries_books_80.json]

For each index (production = %LOCALAPPDATA%\\recall; experiments =
%LOCALAPPDATA%\\recall-exp\\<name>, with its own config.json) it measures:

* **hook**: one query exactly as the user types it (what the hook does).
  R@5 and MRR@10 total, EN->EN and ES->EN.
* **mcp**: the question in Spanish AND English in the same call, as the MCP
  tool asks. Only for ``XNNes``/``XNNen`` pairs.
* **threshold**: the share of positives at or above the hook threshold, and the
  maximum cosine of negative prompts that pass the hook filters. With another
  model the cosines change scale: the threshold is recalibrated per model.
* **latency** p50/max of one query (with the model already loaded).

Writes one JSON per index to %LOCALAPPDATA%\\recall-exp\\eval-<name>.json and
a table to stdout.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from recall import config  # noqa: E402
from recall.evaluation import evaluate, is_relevant, load_queries  # noqa: E402
from recall.search import make_searcher  # noqa: E402

LOCAL = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))


def homes(only: list[str] | None) -> list[tuple[str, Path]]:
    out = [("base", LOCAL / "recall")]
    exp = LOCAL / "recall-exp"
    for d in sorted(exp.glob("e*")):
        if (d / "index.sqlite3").is_file() and (d / "report.json").stat().st_size if (d / "report.json").exists() else False:
            out.append((d.name, d))
    return [x for x in out if not only or x[0] in only]


def mcp_mode(searcher, queries: list[dict]) -> dict:
    by = {q["id"]: q for q in queries}
    rows = []
    for qid, q in by.items():
        if not qid.endswith("es") or qid[:-2] + "en" not in by:
            continue
        en = by[qid[:-2] + "en"]
        hits = searcher.search([q["q"], en["q"]], k=10)
        rr, r5 = 0.0, 0
        for i, h in enumerate(hits[:10]):
            if is_relevant(q, h):
                rr = rr or 1.0 / (i + 1)
                r5 = r5 or int(i < 5)
        rows.append((qid[:-2], r5, rr))
    n = len(rows)
    return {"n": n, "r5": sum(r[1] for r in rows) / n, "mrr": sum(r[2] for r in rows) / n,
            "misses": [r[0] for r in rows if not r[1]]}


def run_one(name: str, home: Path, queries: list[dict]) -> dict:
    os.environ["RECALL_HOME"] = str(home)
    cfg = config.load()
    # all share the same page map and metadata (they do not affect ranking)
    t = time.perf_counter()
    s = make_searcher(cfg, db=home / "index.sqlite3")
    load_s = time.perf_counter() - t
    res = evaluate(s, queries)
    thr = cfg["hook"]["threshold"]
    cal = res["calibration"]
    neg_max = cal["neg_max"]
    out = {
        "name": name, "model": s.emb.identity.get("model"), "chunking": cfg.get("chunking"),
        "chunks": s.size, "load_s": round(load_s, 1),
        "hook": res["groups"], "mcp": mcp_mode(s, queries),
        "latency_s": res["latency_s"],
        "threshold": {"current": thr, "pos_share_at_current": sum(x >= thr for x in cal["pos"]) / len(cal["pos"]),
                      "neg_max": neg_max,
                      "pos_share_above_neg_max": sum(x > neg_max for x in cal["pos"]) / len(cal["pos"]),
                      "pos_min": cal["pos_min"]},
        "misses": [r["id"] for r in res["rows"] if not r["r5"]],
    }
    (LOCAL / "recall-exp").mkdir(exist_ok=True)
    (LOCAL / "recall-exp" / f"eval-{name}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", default=str(ROOT / "tests" / "data" / "queries_books_80.json"))
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args()
    queries = load_queries(Path(a.queries))
    results = []
    for name, home in homes(a.only):
        print(f"evaluating {name} ...", file=sys.stderr, flush=True)
        results.append(run_one(name, home, queries))
    hdr = f"{'index':16} {'chunks':>7} {'R@5':>5} {'EN':>5} {'ES>EN':>5} {'MRR':>5} {'mcpR@5':>6} {'mcpMRR':>6} {'p50 s':>6} {'pos>neg':>7}"
    print(hdr)
    for r in results:
        h = r["hook"]
        print(f"{r['name']:16} {r['chunks']:>7} {h['TOTAL']['r5']:>5.2f} {h['EN->EN']['r5']:>5.2f} "
              f"{h['ES->EN (cross)']['r5']:>5.2f} {h['TOTAL']['mrr']:>5.2f} {r['mcp']['r5']:>6.2f} "
              f"{r['mcp']['mrr']:>6.2f} {r['latency_s']['p50']:>6.2f} {r['threshold']['pos_share_above_neg_max']:>7.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
