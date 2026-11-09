"""Verify the integrity of a recall index (read-only).

    .venv\\Scripts\\python.exe -I tools\\verify_index.py [HOME ...] [--sample 30] [--no-source]

HOME = folder with index.sqlite3 and (optionally) config.json; defaults to
%LOCALAPPDATA%\\recall. Checks, without writing anything:

1. SQLite ``quick_check``.
2. Accounting: sum of ``files.n_chunks`` = rows in ``chunks``; no orphan
   chunks and no files with a different count.
3. Vectors: length = dim x 4 bytes, finite, unit norm, none zero; the same
   ``input_sha`` always with the same vector.
4. Text: ``input_sha`` = sha256 of the embedded text (catches misaligned text
   and vector).
5. FTS: one text row per chunk.
6. Sources: every library ``.md`` is indexed and its sha256 matches.
7. ``--sample N``: re-embeds N chunks spread evenly over the whole index (always
   the same ones for the same index) with the index's model and compares
   with the stored vector (cosine >= 0.99), using the indexing providers
   (``--sample-cpu`` to force CPU).

Exits with 1 if any check fails.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from recall.embedder import DOC_FORMAT, doc_input  # noqa: E402
from recall.textutil import sha256_file, sha256_text  # noqa: E402


def _cfg(home: Path) -> dict:
    os.environ["RECALL_HOME"] = str(home)
    from recall import config

    return config.load()


def _check_accounting(con) -> tuple:
    n_chunks = con.execute("select count(*) from chunks").fetchone()[0]
    _n_files, declared = con.execute("select count(*), coalesce(sum(n_chunks),0) from files").fetchone()
    bad_files = con.execute(
        "select f.path, f.n_chunks, count(c.id) from files f left join chunks c on c.path=f.path "
        "group by f.path having f.n_chunks != count(c.id)"
    ).fetchall()
    orphans = con.execute("select count(*) from chunks where path not in (select path from files)").fetchone()[0]
    return (
        "chunk_accounting",
        declared == n_chunks and not bad_files and not orphans,
        {"declared": declared, "rows": n_chunks, "mismatched_files": len(bad_files), "orphans": orphans},
    )


def _check_vectors(con, dim: int, fmt: str, n_chunks: int) -> list[tuple]:
    bad_len = bad_val = bad_norm = zero = bad_sha = 0
    sha_vec: dict[str, bytes] = {}
    sha_conflict = 0
    norms_min, norms_max = 9.0, 0.0
    for _cid, book, chapter, text, h, vec in con.execute("select id, book, chapter, text, input_sha, vec from chunks"):
        if len(vec) != dim * 4:
            bad_len += 1
            continue
        v = np.frombuffer(vec, dtype=np.float32)
        if not np.isfinite(v).all():
            bad_val += 1
            continue
        n = float(np.linalg.norm(v))
        norms_min, norms_max = min(norms_min, n), max(norms_max, n)
        if n < 1e-6:
            zero += 1
        elif abs(n - 1.0) > 1e-3:
            bad_norm += 1
        if sha256_text(doc_input(book, chapter, text, fmt)) != h:
            bad_sha += 1
        prev = sha_vec.setdefault(h, vec)
        if prev is not vec and prev != vec:
            sha_conflict += 1
    return [
        (
            "vectors",
            not (bad_len or bad_val or bad_norm or zero),
            {
                "wrong_length": bad_len,
                "non_finite": bad_val,
                "not_unit": bad_norm,
                "zero": zero,
                "norm_range": [round(norms_min, 5), round(norms_max, 5)] if n_chunks else None,
            },
        ),
        ("text_matches_embedded_input", bad_sha == 0, {"mismatch": bad_sha}),
        ("same_input_same_vector", sha_conflict == 0, {"conflicts": sha_conflict}),
    ]


def _check_sources(con, home: Path) -> tuple:
    from recall.indexer import discover

    cfg = _cfg(home)
    found, _ = discover(cfg["collections"])
    known = dict(con.execute("select path, sha256 from files"))
    missing = sorted(set(found) - set(known))
    extra = sorted(set(known) - set(found))
    stale = [p for p in sorted(set(found) & set(known)) if sha256_file(p) != known[p]]
    return (
        "source_coverage",
        not missing and not extra and not stale,
        {
            "library": len(found),
            "missing": len(missing),
            "extra": len(extra),
            "stale": len(stale),
            "missing_books": sorted({Path(p).parent.name for p in missing})[:10],
        },
    )


def _check_sample(con, home: Path, sample: int, sample_cpu: bool) -> tuple:
    from recall import embedder as E

    cfg = _cfg(home)
    # Defaults to the indexing providers: that is what must match.
    # --sample-cpu avoids competing for the GPU; with embeddinggemma CPU and GPU
    # give cosine 1.0, but with quantized Qwen3 they give ~0.94 (see README).
    if sample_cpu:
        cfg["model"]["index_providers"] = ["CPUExecutionProvider"]
    emb = E.from_config(cfg, "index")
    # Deterministic, even sample (no ``random``): covers every book and does not
    # depend on the ids being contiguous.
    all_ids = [r[0] for r in con.execute("select id from chunks order by id")]
    m = min(sample, len(all_ids))
    ids = [all_ids[i * len(all_ids) // m] for i in range(m)]
    rows = con.execute(
        f"select book, chapter, text, vec from chunks where id in ({','.join('?' * len(ids))})", ids
    ).fetchall()
    fresh = emb.embed([E.format_doc(emb, b, c, t) for b, c, t, _ in rows], "doc")
    cos = [float(np.dot(f, np.frombuffer(v, dtype=np.float32))) for f, (*_, v) in zip(fresh, rows, strict=True)]
    return (
        "reembed_sample",
        min(cos) >= 0.99,
        {"n": len(cos), "min_cos": round(min(cos), 5), "mean_cos": round(sum(cos) / len(cos), 5)},
    )


def verify(home: Path, sample: int = 0, source: bool = True, sample_cpu: bool = False) -> dict:
    db = home / "index.sqlite3"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    res: dict = {"home": str(home), "checks": {}, "ok": True}

    def check(name, ok, detail=None):
        res["checks"][name] = {"ok": bool(ok), **({"detail": detail} if detail is not None else {})}
        res["ok"] &= bool(ok)

    qc = con.execute("pragma quick_check").fetchall()
    check("sqlite_quick_check", qc == [("ok",)], None if qc == [("ok",)] else qc[:5])

    row = con.execute("select value from meta where key='model_identity'").fetchone()
    ident = json.loads(row[0]) if row else {}
    dim = int(ident.get("dim") or 0)
    fmt = ident.get("doc_format") or DOC_FORMAT
    res["model"] = ident.get("model")
    check("model_identity", bool(ident and dim), ident.get("model"))

    n_chunks = con.execute("select count(*) from chunks").fetchone()[0]
    res["files"] = con.execute("select count(*) from files").fetchone()[0]
    res["chunks"] = n_chunks
    check(*_check_accounting(con))
    for c in _check_vectors(con, dim, fmt, n_chunks):
        check(*c)
    fts = con.execute("select count(*) from fts_docsize").fetchone()[0]
    check("fts_rows", fts == n_chunks, {"fts": fts, "chunks": n_chunks})
    if source:
        check(*_check_sources(con, home))
    if sample and n_chunks:
        check(*_check_sample(con, home, sample, sample_cpu))
    con.close()
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Check the integrity of a recall index (read-only).")
    ap.add_argument(
        "homes", nargs="*", type=Path, help="folders holding index.sqlite3 (default: %%LOCALAPPDATA%%\\recall)"
    )
    ap.add_argument(
        "--sample", type=int, default=0, help="re-embed N chunks spread over the index and require cosine >= 0.99"
    )
    ap.add_argument("--sample-cpu", action="store_true", help="re-embed the sample on CPU")
    ap.add_argument(
        "--no-source",
        action="store_true",
        help="skip checking that every library .md is indexed with a matching sha256",
    )
    a = ap.parse_args(argv)
    homes = a.homes or [Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "recall"]
    ok = True
    for h in homes:
        r = verify(h, a.sample, not a.no_source, a.sample_cpu)
        ok &= r["ok"]
        print(json.dumps(r, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
