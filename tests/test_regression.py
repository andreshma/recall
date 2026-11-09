"""Retrieval regression with the REAL model and index (marker ``model``).

Run with ``pytest -m model``. Uses ``%LOCALAPPDATA%\\recall\\index.sqlite3``
(ignores the tests' RECALL_HOME) and is skipped if the index does not exist or
does not cover every book yet, because a partial index would give a false red.

Floors set from what was measured on 2026-10-27 over the full index (see the
README, "Metrics"): dropping below them is a real regression, not noise, because
the model, the index and the queries are deterministic.
"""

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.model

# Measured 2026-11-05 on the E5 production index (embeddinggemma-2, chunks
# of ~3500, 34 721 chunks): TOTAL 0.95/0.868, EN->EN 1.00/0.95, ES->EN
# 0.90/0.786. Before (gemma-300m, ~900): 0.95/0.86, 1.00/0.93, 0.90/0.78. The
# floors are the measurements rounded down: any drop is a regression.
FLOORS = {
    "TOTAL": {"r5": 0.95, "mrr": 0.86},
    "EN->EN": {"r5": 1.00, "mrr": 0.94},
    "ES->EN (cross)": {"r5": 0.90, "mrr": 0.78},
}
# The hook threshold is the one in the production config (cfg["hook"]["threshold"]:
# 0.75 with E5; 0.58 was gemma-300m's, cosine scales differ per model).
# Measured with E5: negatives <= 0.726; positives >= 0.75 in 29 of 30.
MIN_POSITIVE_SHARE = 0.80
LATENCY_P50_S = 1.0

QUERIES = Path(__file__).resolve().parent / "data" / "queries_books.json"


@pytest.fixture(scope="module")
def real():
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    real_home = Path(base) / "recall"
    db = real_home / "index.sqlite3"
    if not db.is_file():
        pytest.skip("no real index; run `recall index`")
    old = os.environ.get("RECALL_HOME")
    os.environ["RECALL_HOME"] = str(real_home)
    try:
        from recall import config, store
        from recall.evaluation import evaluate, load_queries
        from recall.indexer import discover
        from recall.search import make_searcher

        cfg = config.load()
        found, _ = discover(cfg["collections"])
        con = store.open_ro(db)
        indexed = {r[0] for r in con.execute("select path from files")}
        con.close()
        # A partial index (first indexing run) gives a false red; a few files just
        # reconverted in _md, pending the next incremental run, do not.
        missing = len(set(found) - indexed)
        if missing > 0.05 * len(found):
            pytest.skip(f"incomplete index ({missing}/{len(found)} files missing)")
        res = evaluate(make_searcher(cfg, db), load_queries(QUERIES))
        res["threshold"] = cfg["hook"]["threshold"]
        yield res
    finally:
        if old is None:
            os.environ.pop("RECALL_HOME", None)
        else:
            os.environ["RECALL_HOME"] = old


@pytest.mark.parametrize("group", list(FLOORS))
def test_recall_floors(real, group):
    g = real["groups"][group]
    assert g["r5"] >= FLOORS[group]["r5"], (group, g, [r for r in real["rows"] if not r["r5"]])
    assert g["mrr"] >= FLOORS[group]["mrr"], (group, g)


def test_threshold_separates_good_from_trivial(real):
    """No negative that passes the hook filters reaches the threshold, while at
    least 80 % of the positives do; no positive is skipped by the filter."""
    thr = real["threshold"]
    c = real["calibration"]
    assert c["neg_max"] < thr, [n for n in c["neg_detail"] if not n["skipped"]]
    share = sum(x >= thr for x in c["pos"]) / len(c["pos"])
    assert share >= MIN_POSITIVE_SHARE, c["pos"]
    assert c["pos_skipped"] == []


def test_latency(real):
    assert real["latency_s"]["p50"] <= LATENCY_P50_S, real["latency_s"]


def test_dml_and_cpu_vectors_agree():
    """Indexing on DirectML and querying on CPU (or vice versa) does not change the vectors."""
    from recall import config
    from recall.embedder import doc_input, from_config

    cfg = config.load()
    docs = [
        doc_input("Learning DDD", "Chapter 3", "A bounded context is a boundary for a model."),
        doc_input("SRE", "Chapter 3", "Alert on error budget burn rate over two windows."),
    ]
    cfg["model"]["index_providers"] = ["CPUExecutionProvider"]
    cpu = from_config(cfg, "index").embed(docs, "doc")
    cfg["model"]["index_providers"] = ["DmlExecutionProvider:1", "CPUExecutionProvider"]
    dml = from_config(cfg, "index").embed(docs, "doc")
    assert float((cpu * dml).sum(axis=1).min()) >= 0.999
