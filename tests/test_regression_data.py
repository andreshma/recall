"""Regression over the 22 data books added on 2026-11-06 (marker ``model``).

Same mechanism as ``test_regression.py`` with ``queries_books_data.json``:
10 EN and 10 ES queries over chapters of those books. Floors = what was measured
on 2026-11-07 with the E5 production index (148 books, 41 741 chunks),
rounded down.
"""

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.model

FLOORS = {
    "TOTAL": {"r5": 1.00, "mrr": 0.92},
    "EN->EN": {"r5": 1.00, "mrr": 1.00},
    "ES->EN (cross)": {"r5": 1.00, "mrr": 0.85},
}
QUERIES = Path(__file__).resolve().parent / "data" / "queries_books_data.json"


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
        from recall.search import make_searcher

        con = store.open_ro(db)
        books = {r[0] for r in con.execute("select distinct book_id from files")}
        con.close()
        if "fundamentals-data-engineering-reis" not in books:
            pytest.skip("the index does not have the data books yet; run `recall index`")
        yield evaluate(make_searcher(config.load(), db), load_queries(QUERIES))
    finally:
        if old is None:
            os.environ.pop("RECALL_HOME", None)
        else:
            os.environ["RECALL_HOME"] = old


@pytest.mark.parametrize("group", list(FLOORS))
def test_data_books_floors(real, group):
    g = real["groups"][group]
    assert g["r5"] >= FLOORS[group]["r5"], (group, g, [r for r in real["rows"] if not r["r5"]])
    assert g["mrr"] >= FLOORS[group]["mrr"], (group, g)
