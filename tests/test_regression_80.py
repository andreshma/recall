"""Regresion con 80 consultas (20 originales + 60 de tests/data/queries_books_80.json).

Marca ``model``: usa el modelo y el indice reales (``pytest -m model``). Las 60
nuevas se armaron leyendo los libros (grep, encabezados), sin ningun buscador,
para no sesgar el set hacia recall ni hacia otro motor.

Pisos medidos el 2026-11-05 sobre el indice de produccion E5 (126 libros,
34 721 chunks, embeddinggemma-2, trozos de ~3500): TOTAL 0.9875/0.898, EN->EN
1.00/0.895, ES->EN 0.975/0.902. Los del 2026-11-03 (gemma-300m, ~900) eran
0.96/0.87, 0.95/0.91, 0.95/0.84. Bajar de ellos es regresion.
"""
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.model

QUERIES = Path(__file__).resolve().parent / "data" / "queries_books_80.json"
FLOORS = {
    "TOTAL": {"r5": 0.98, "mrr": 0.89},
    "EN->EN": {"r5": 1.00, "mrr": 0.89},
    "ES->EN (cruce)": {"r5": 0.97, "mrr": 0.90},
}


@pytest.fixture(scope="module")
def real80():
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    real_home = Path(base) / "recall"
    if not (real_home / "index.sqlite3").is_file():
        pytest.skip("sin indice real")
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
