"""CLI: ``recall index | search | serve | mcp | status | eval``."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from . import config


def _utf8_stdio() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def cmd_index(a) -> int:
    """Indexa (incremental por defecto)."""
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
    print(f"recall index: proveedores {emb.providers}, db {config.db_path()}", file=sys.stderr)
    rep = Indexer(config.db_path(), emb, chunking=cfg.get("chunking")).run(
        colls, rebuild=a.rebuild, limit_files=a.limit_files)
    print(json.dumps(asdict(rep), ensure_ascii=False, indent=1))
    return 1 if rep.errors else 0


def _searcher(cfg):
    from .search import make_searcher

    return make_searcher(cfg)


def cmd_search(a) -> int:
    """Busca e imprime resultados (servicio si esta vivo; si no, en proceso)."""
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
    """Servicio HTTP local (bloquea)."""
    from .service import serve

    return serve()


def cmd_mcp(a) -> int:
    """Servidor MCP stdio."""
    from .mcp_server import run_stdio

    return run_stdio()


def cmd_status(a) -> int:
    """Estado del indice y del servicio."""
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
        out["service"] = f"no disponible ({e})"
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


def cmd_eval(a) -> int:
    """Regresion con las consultas de libros."""
    from .evaluation import evaluate, load_queries

    res = evaluate(_searcher(config.load()), load_queries(Path(a.queries)))
    for g, v in res["groups"].items():
        print(f"{g:16s} n={v['n']:2d} R@5={v['r5']:.2f} MRR={v['mrr']:.2f}")
    c = res["calibration"]
    print(f"latencia p50={res['latency_s']['p50']:.3f}s max={res['latency_s']['max']:.3f}s")
    print(f"umbral: positivos min={c['pos_min']:.3f}  negativos max={c['neg_max']:.3f}")
    if a.out:
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada."""
    _utf8_stdio()
    ap = argparse.ArgumentParser(prog="recall", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("index", help="indexa los libros (incremental)")
    p.add_argument("--rebuild", action="store_true", help="borra el indice y lo reconstruye")
    p.add_argument("--root", action="append", help="raiz de libros (repetible, en orden de precedencia)")
    p.add_argument("--limit-files", type=int, help="procesa como maximo N archivos nuevos/cambiados")
    p.add_argument("--dml-device", type=int, help="adaptador DirectML (0, 1...) para embeber")
    p.add_argument("--cpu", action="store_true", help="embeber solo en CPU")
    p.set_defaults(fn=cmd_index)
    p = sub.add_parser("search", help="busca")
    p.add_argument("query", nargs="+", help="una o varias formulaciones (ES y EN)")
    p.add_argument("-k", type=int, default=5)
    p.add_argument("--text", action="store_true")
    p.set_defaults(fn=cmd_search)
    sub.add_parser("serve", help="servicio local de busqueda").set_defaults(fn=cmd_serve)
    sub.add_parser("mcp", help="servidor MCP stdio").set_defaults(fn=cmd_mcp)
    sub.add_parser("status", help="estado del indice y del servicio").set_defaults(fn=cmd_status)
    p = sub.add_parser("eval", help="regresion R@5/MRR")
    p.add_argument("--queries", default=str(Path(__file__).resolve().parents[2] / "tests" / "data" / "queries_books.json"))
    p.add_argument("--out")
    p.set_defaults(fn=cmd_eval)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
