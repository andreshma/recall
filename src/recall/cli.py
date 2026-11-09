"""CLI: ``recall index | search | serve | mcp | status | eval``."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from . import config

#: Labeled query set of a source checkout; a wheel install has no tests/ tree.
DEFAULT_QUERIES = Path(__file__).resolve().parents[2] / "tests" / "data" / "queries_books.json"


def _utf8_stdio() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def cmd_index(a) -> int:
    """Index (incremental by default)."""
    from .embedder import from_config
    from .indexer import Indexer

    cfg = config.load()
    colls = cfg["collections"]
    if a.root:
        colls = {"books": {"roots": a.root}}
    if a.dml_device is not None:
        cfg["model"]["index_providers"] = [f"DmlExecutionProvider:{a.dml_device}", "CPUExecutionProvider"]
    if a.cpu:
        cfg["model"]["index_providers"] = ["CPUExecutionProvider"]
    config.ensure_home()
    emb = from_config(cfg, "index")
    provs = getattr(emb, "providers", None)  # the Embedder protocol does not require it
    print(f"recall index: providers {provs}, db {config.db_path()}", file=sys.stderr)
    rep = Indexer(config.db_path(), emb, chunking=cfg.get("chunking")).run(
        colls, rebuild=a.rebuild, limit_files=a.limit_files
    )
    print(json.dumps(asdict(rep), ensure_ascii=False, indent=1))
    return 1 if rep.errors else 0


def _searcher(cfg):
    from .search import make_searcher

    return make_searcher(cfg)


def cmd_search(a) -> int:
    """Search and print results (via the service if alive; otherwise in-process)."""
    from . import client

    try:
        hits = client.search(a.query, k=a.k)
    except client.ServiceUnavailable:
        hits = _searcher(config.load()).search(a.query, k=a.k)
    for i, h in enumerate(hits, 1):
        print(f"[{i}] cos={h['cos']:.3f} {h['book']} — {h['chapter']} — {h['section']}")
        print(f"    {h['path']}:{h['line_start']}-{h['line_end']} via={','.join(h['via'])}")
        if a.text:
            print("    " + h["text"][:400].replace("\n", " "))
    return 0


def cmd_serve(a) -> int:
    """Local HTTP service (blocks)."""
    from .service import serve

    return serve()


def cmd_mcp(a) -> int:
    """Stdio MCP server."""
    from .mcp_server import run_stdio

    return run_stdio()


def cmd_status(a) -> int:
    """Index and service status."""
    from . import client, store

    out: dict = {"home": str(config.home()), "db": str(config.db_path())}
    p = config.db_path()
    if p.is_file():
        con = store.open_ro(p)
        out["chunks"] = con.execute("select count(*) from chunks").fetchone()[0]
        out["files"] = con.execute("select count(*) from files").fetchone()[0]
        out["books"] = con.execute("select count(distinct book_id) from files").fetchone()[0]
        out["generation"] = store.generation(con)
        ident = store.get_meta(con, "model_identity")
        out["model"] = json.loads(ident) if ident else None
        con.close()
    try:
        out["service"] = client.health()
    except client.ServiceUnavailable as e:
        out["service"] = f"unavailable ({e})"
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


def cmd_eval(a) -> int:
    """Regression over the book queries."""
    from .evaluation import evaluate, load_queries

    queries = Path(a.queries) if a.queries else DEFAULT_QUERIES
    if not queries.is_file():
        # The labeled sets live in the source checkout (tests/data), not in the wheel.
        print(f"recall eval: query set not found: {queries}; pass --queries FILE", file=sys.stderr)
        return 2
    res = evaluate(_searcher(config.load()), load_queries(queries))
    for g, v in res["groups"].items():
        if v is None:  # group with no queries in this set
            print(f"{g:16s} n= 0")
            continue
        print(f"{g:16s} n={v['n']:2d} R@5={v['r5']:.2f} MRR={v['mrr']:.2f}")
    c = res["calibration"]
    print(f"latency p50={res['latency_s']['p50']:.3f}s max={res['latency_s']['max']:.3f}s")
    print(f"threshold: positives min={c['pos_min']:.3f}  negatives max={c['neg_max']:.3f}")
    if a.out:
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    _utf8_stdio()
    ap = argparse.ArgumentParser(prog="recall", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("index", help="index the books (incremental)")
    p.add_argument("--rebuild", action="store_true", help="delete the index and rebuild it")
    p.add_argument("--root", action="append", help="books root (repeatable, in precedence order)")
    p.add_argument("--limit-files", type=int, help="process at most N new/changed files")
    p.add_argument("--dml-device", type=int, help="DirectML adapter (0, 1...) used for embedding")
    p.add_argument("--cpu", action="store_true", help="embed on CPU only")
    p.set_defaults(fn=cmd_index)
    p = sub.add_parser("search", help="search the index")
    p.add_argument("query", nargs="+", help="one or more phrasings of the query (ES and EN)")
    p.add_argument("-k", type=int, default=5, help="number of results (default 5)")
    p.add_argument("--text", action="store_true", help="also print the start of each fragment")
    p.set_defaults(fn=cmd_search)
    sub.add_parser("serve", help="local search service (blocks)").set_defaults(fn=cmd_serve)
    sub.add_parser("mcp", help="MCP server over stdio").set_defaults(fn=cmd_mcp)
    sub.add_parser("status", help="index and service status").set_defaults(fn=cmd_status)
    p = sub.add_parser("eval", help="R@5/MRR regression over labeled queries")
    p.add_argument("--queries", help="labeled query set (default: tests/data/queries_books.json of the checkout)")
    p.add_argument("--out", help="write the full result as JSON to this file")
    p.set_defaults(fn=cmd_eval)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
