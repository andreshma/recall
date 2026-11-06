"""Busqueda: dedup, filtros, coseno, peso de FTS por idioma, recarga."""
import sqlite3

import numpy as np
import pytest

from recall import store
from recall.embedder import HashEmbedder
from recall.indexer import Indexer
from recall.search import Searcher
from recall.textutil import detect_lang, fts_query

from conftest import para


def test_returns_cosine_and_provenance(indexed):
    db, _, emb = indexed
    hits = Searcher(db, emb).search("bounded context ubiquitous language model", k=5)
    assert hits and hits[0]["book_id"] == "learning-ddd"
    for h in hits:
        assert -1.0 <= h["cos"] <= 1.0001
        assert h["line_start"] <= h["line_end"] and h["path"].endswith(".md")
        assert {"book", "chapter", "section", "via"} <= set(h)
    assert hits == sorted(hits, key=lambda h: -h["score"])


def test_dedup_by_file_and_section(indexed):
    db, _, emb = indexed
    hits = Searcher(db, emb).search("bounded context boundary model", k=20)
    keys = [(h["path"].lower(), h["section"]) for h in hits]
    assert len(keys) == len(set(keys))


def test_collection_filter(indexed):
    db, _, emb = indexed
    s = Searcher(db, emb)
    assert s.search("error budget", collections=["books"])
    assert s.search("error budget", collections=["clientes-x"]) == []


def test_multi_query_merges_best_cos(indexed):
    db, _, emb = indexed
    s = Searcher(db, emb)
    a = s.search("error budget burn rate", k=3)
    both = s.search(["consulta sin relacion alguna", "error budget burn rate"], k=3)
    assert both[0]["path"] == a[0]["path"] and both[0]["cos"] == pytest.approx(a[0]["cos"], abs=1e-4)


def test_cross_language_fts_weight_is_near_zero(home, tmp_path):
    """Un doc que solo casa por FTS en otro idioma no supera a uno vectorial."""
    from recall import config

    md = tmp_path / "_md"
    (md / "b").mkdir(parents=True)
    (md / "b" / "01-a.md").write_text("# B — A\n\n# S\n\n" + para("alpha beta gamma delta epsilon zeta", 600), encoding="utf-8")
    (md / "b" / "02-b.md").write_text(
        "# B — B\n\n# T\n\n" + para("El sistema de la empresa usa omega para los datos que se procesan", 600), encoding="utf-8")
    emb = HashEmbedder()
    Indexer(config.db_path(), emb, quiet=True).run({"books": {"roots": [str(md)]}})
    s = Searcher(config.db_path(), emb, {"fts_weight_same_lang": 0.25, "fts_weight_cross_lang": 0.0})
    hits = s.search("what is alpha beta and the omega", k=2)  # consulta EN
    assert hits[0]["path"].endswith("01-a.md")
    omega = [h for h in hits if h["path"].endswith("02-b.md")]
    assert not omega or omega[0]["score"] < hits[0]["score"]


def test_refresh_after_reindex(indexed):
    db, lib, emb = indexed
    s = Searcher(db, emb)
    n = s.size
    (lib / "sre-google" / "20-new.md").write_text("# SRE — New\n\n# Toil\n\n" + para("Toil is manual repetitive work.", 700),
                                                  encoding="utf-8")
    Indexer(db, emb, quiet=True).run({"books": {"roots": [str(lib)]}})
    s.search("toil")  # dentro de min_reload_s: no recarga todavia
    assert s.size == n
    s.min_reload_s = 0
    s.search("toil")
    assert s.size > n


def test_searcher_is_read_only(indexed):
    db, _, emb = indexed
    s = Searcher(db, emb)
    with pytest.raises(sqlite3.OperationalError):
        s._con.execute("delete from chunks")


def test_searcher_refuses_other_model(indexed):
    db, _, _ = indexed
    with pytest.raises(store.ModelMismatchError):
        Searcher(db, HashEmbedder(name="otro"))


def test_empty_query():
    assert fts_query("  ") is None
    assert fts_query('a "NEAR" b-c*') == '"near"'


def test_detect_lang():
    assert detect_lang("¿qué es un contexto delimitado en DDD?") == "es"
    assert detect_lang("what is a bounded context in domain-driven design") == "en"


def test_background_refresh_swaps_without_blocking(indexed):
    import time

    db, lib, emb = indexed
    s = Searcher(db, emb)
    s.min_reload_s = 0
    n = s.size
    (lib / "sre-google" / "21-new.md").write_text("# SRE — X\n\n# Y\n\n" + para("Postmortems are blameless reviews.", 700),
                                                  encoding="utf-8")
    Indexer(db, emb, quiet=True).run({"books": {"roots": [str(lib)]}})
    assert s.refresh(background=True)
    assert s.search("postmortems blameless")  # sigue respondiendo mientras carga
    for _ in range(100):
        if s.size > n:
            break
        time.sleep(0.05)
    assert s.size > n
