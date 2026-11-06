"""Regresion de recuperacion con el modelo y el indice REALES (marca ``model``).

Corre con ``pytest -m model``. Usa ``%LOCALAPPDATA%\\recall\\index.sqlite3``
(ignora el RECALL_HOME de los tests) y se salta si el indice no existe o aun
no cubre todos los libros, porque un indice a medias daria un falso rojo.

Pisos fijados con lo medido el 2026-10-27 sobre el indice completo (ver
README, "Metricas"): bajar de ellos es una regresion real, no ruido, porque
el modelo, el indice y las consultas son deterministas.
"""
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.model

# Medido 2026-11-05 con el indice de produccion E5 (embeddinggemma-2, trozos
# de ~3500, 34 721 chunks): TOTAL 0.95/0.868, EN->EN 1.00/0.95, ES->EN
# 0.90/0.786. Antes (gemma-300m, ~900): 0.95/0.86, 1.00/0.93, 0.90/0.78. Los
# pisos son lo medido redondeado hacia abajo: cualquier baja es regresion.
FLOORS = {
    "TOTAL": {"r5": 0.95, "mrr": 0.86},
    "EN->EN": {"r5": 1.00, "mrr": 0.94},
    "ES->EN (cruce)": {"r5": 0.90, "mrr": 0.78},
}
# El umbral del hook es el de la config de produccion (cfg["hook"]["threshold"]:
# 0.75 con E5; 0.58 era el de gemma-300m, los cosenos cambian de escala por
# modelo). Medido con E5: negativos <= 0.726; positivos >= 0.75 en 29 de 30.
MIN_POSITIVE_SHARE = 0.80
LATENCY_P50_S = 1.0

QUERIES = Path(__file__).resolve().parent / "data" / "queries_books.json"


@pytest.fixture(scope="module")
def real():
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    real_home = Path(base) / "recall"
    db = real_home / "index.sqlite3"
    if not db.is_file():
        pytest.skip("sin indice real; corre `recall index`")
    old = os.environ.get("RECALL_HOME")
    os.environ["RECALL_HOME"] = str(real_home)
    try:
        from recall import config, store
        from recall.embedder import from_config
        from recall.evaluation import evaluate, load_queries
        from recall.indexer import discover
        from recall.search import make_searcher

        cfg = config.load()
        found, _ = discover(cfg["collections"])
        con = store.open_ro(db)
        indexed = {r[0] for r in con.execute("select path from files")}
        con.close()
        # Un indice a medias (primer indexado) da falso rojo; unos pocos archivos
        # recien reconvertidos en _md, pendientes del siguiente incremental, no.
        missing = len(set(found) - indexed)
        if missing > 0.05 * len(found):
            pytest.skip(f"indice incompleto (faltan {missing}/{len(found)} archivos)")
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
    """Ningun negativo que pase los filtros del hook llega al umbral, y al
    menos el 80 % de los positivos si; ningun positivo lo salta el filtro."""
    thr = real["threshold"]
    c = real["calibration"]
    assert c["neg_max"] < thr, [n for n in c["neg_detail"] if not n["skipped"]]
    share = sum(x >= thr for x in c["pos"]) / len(c["pos"])
    assert share >= MIN_POSITIVE_SHARE, c["pos"]
    assert c["pos_skipped"] == []


def test_latency(real):
    assert real["latency_s"]["p50"] <= LATENCY_P50_S, real["latency_s"]


def test_dml_and_cpu_vectors_agree():
    """Indexar en DirectML y consultar en CPU (o al reves) no cambia los vectores."""
    from recall import config
    from recall.embedder import doc_input, from_config

    cfg = config.load()
    docs = [doc_input("Learning DDD", "Chapter 3", "A bounded context is a boundary for a model."),
            doc_input("SRE", "Chapter 3", "Alert on error budget burn rate over two windows.")]
    cfg["model"]["index_providers"] = ["CPUExecutionProvider"]
    cpu = from_config(cfg, "index").embed(docs, "doc")
    cfg["model"]["index_providers"] = ["DmlExecutionProvider:1", "CPUExecutionProvider"]
    dml = from_config(cfg, "index").embed(docs, "doc")
    assert float((cpu * dml).sum(axis=1).min()) >= 0.999
