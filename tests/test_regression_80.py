"""Regression with 80 queries (20 original + 60 from tests/data/queries_books_80.json).

Marker ``model``: uses the real model and index (``pytest -m model``). The 60
new ones were written by reading the books (grep, headings), without any search
engine, so the set is not biased toward recall or any other engine.

Floors measured on 2026-11-05 over the E5 production index (126 books,
34 721 chunks, embeddinggemma-2, chunks of ~3500): TOTAL 0.9875/0.898, EN->EN
1.00/0.895, ES->EN 0.975/0.902. The 2026-11-03 ones (gemma-300m, ~900) were
0.96/0.87, 0.95/0.91, 0.95/0.84. Dropping below them is a regression.
"""

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.model

QUERIES = Path(__file__).resolve().parent / "data" / "queries_books_80.json"
FLOORS = {
    "TOTAL": {"r5": 0.98, "mrr": 0.89},
    "EN->EN": {"r5": 1.00, "mrr": 0.89},
    "ES->EN (cross)": {"r5": 0.97, "mrr": 0.90},
}


@pytest.fixture(scope="module")
def real80():
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    real_home = Path(base) / "recall"
    if not (real_home / "index.sqlite3").is_file():
        pytest.skip("no real index")
    old = os.environ.get("RECALL_HOME")
    os.environ["RECALL_HOME"] = str(real_home)
    try:
        from recall import config
        from recall.evaluation import evaluate, load_queries
        from recall.search import make_searcher

        yield evaluate(make_searcher(config.load()), load_queries(QUERIES))
    finally:
        if old is None:
            os.environ.pop("RECALL_HOME", None)
        else:
            os.environ["RECALL_HOME"] = old


@pytest.mark.parametrize("group", list(FLOORS))
def test_recall_floors_80(real80, group):
    g = real80["groups"][group]
    assert g["r5"] >= FLOORS[group]["r5"], (group, g, [r["id"] for r in real80["rows"] if not r["r5"]])
    assert g["mrr"] >= FLOORS[group]["mrr"], (group, g)


def test_query_set_shape():
    import json

    qs = json.loads(QUERIES.read_text(encoding="utf-8"))
    assert len(qs) == 80 and len({q["id"] for q in qs}) == 80
    assert sum(q["qlang"] == "es" for q in qs) == 40
