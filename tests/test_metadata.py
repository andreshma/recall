"""Metadatos por libro: validacion de filtros, libros permitidos y busqueda filtrada."""
import json

import pytest

from recall import metadata
from recall.mcp_server import TOOL, McpServer
from recall.search import Searcher

META = {
    "learning-ddd": {"year": 2021, "domains": ["arquitectura"], "format": "epub"},
    "sre-google": {"year": 2016, "domains": ["sre-observabilidad", "devops-cicd"], "format": "pdf", "edition": "1a"},
    "libro-es": {"year": None, "domains": ["agile-gestion"], "format": "pdf"},
}


def test_validate_normalizes_and_rejects():
    assert metadata.validate(None) == {}
    assert metadata.validate({"domains": "datos"}) == {"domains": ["datos"]}
    assert metadata.validate({"domains": ["seguridad", "datos", "datos"], "year_min": 2020}) == {
        "domains": ["datos", "seguridad"], "year_min": 2020}
    for bad in ({"domains": ["inventado"]}, {"year_min": "2020"}, {"year_min": True}, {"year_max": 3000},
                {"otro": 1}, {"domains": [3]}, "texto"):
        with pytest.raises(metadata.FilterError):
            metadata.validate(bad)


def test_allowed_books_semantics():
    assert metadata.allowed_books(META, {}) is None
    assert metadata.allowed_books(META, {"domains": ["devops-cicd"]}) == {"sre-google"}
    # sin año conocido no pasa un filtro de año
    assert metadata.allowed_books(META, {"year_min": 2000}) == {"learning-ddd", "sre-google"}
    assert metadata.allowed_books(META, {"year_min": 2017, "year_max": 2022}) == {"learning-ddd"}
    assert metadata.allowed_books(META, {"domains": ["agile-gestion"], "year_min": 2000}) == set()


def test_load_missing_or_corrupt_file_is_empty(tmp_path):
    assert metadata.load(tmp_path / "no.json") == {}
    (tmp_path / "m.json").write_text("{roto", encoding="utf-8")
    assert metadata.load(tmp_path / "m.json") == {}


def test_repo_metadata_file_is_valid():
    meta = metadata.load()
    assert len(meta) >= 100
    for bid, m in meta.items():
        assert m["domains"] and set(m["domains"]) <= set(metadata.DOMAINS), bid
        assert m["year"] is None or 1950 <= m["year"] <= 2030, bid


def test_search_filters_by_domain_and_year(indexed):
    db, _lib, emb = indexed
    s = Searcher(db, emb, meta=META)
    q = "error budget bounded context cono incertidumbre"
    assert {h["book_id"] for h in s.search(q, k=20)} == {"learning-ddd", "sre-google", "libro-es"}
    hits = s.search(q, k=20, filters={"domains": ["sre-observabilidad"]})
    assert hits and {h["book_id"] for h in hits} == {"sre-google"}
    assert hits[0]["year"] == 2016 and hits[0]["edition"] == "1a"
    assert {h["book_id"] for h in s.search(q, k=20, filters={"year_min": 2020})} == {"learning-ddd"}
    assert s.search(q, k=20, filters={"domains": ["ux-ui"]}) == []
    with pytest.raises(metadata.FilterError):
        s.search(q, filters={"domains": ["inventado"]})


def test_fts_hits_respect_filters(indexed):
    """Un resultado que solo llega por FTS tampoco se cuela fuera del filtro."""
    db, _lib, emb = indexed
    s = Searcher(db, emb, {"vec_weight": 0.0}, meta=META)
    hits = s.search("disaster recovery backups", k=20, filters={"domains": ["arquitectura"]})
    assert all(h["book_id"] == "learning-ddd" for h in hits)


def test_mcp_passes_filters_and_rejects_bad_ones():
    calls = []

    def fn(qs, k, filters=None):
        calls.append(filters)
        return [{"book": "SRE", "chapter": "Ch 3", "path": "x.md", "line_start": 1, "line_end": 2,
                 "cos": 0.7, "text": "t", "year": 2016}]

    s = McpServer(fn)
    props = TOOL["inputSchema"]["properties"]
    assert set(props["domains"]["items"]["enum"]) == set(metadata.DOMAINS)
    r = s.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "recall_books_search", "arguments": {"queries": ["slo"], "domains": ["sre-observabilidad"], "year_min": 2015}}})
    assert calls == [{"domains": ["sre-observabilidad"], "year_min": 2015}]
    assert "(2016)" in r["result"]["content"][0]["text"]
    r = s.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "recall_books_search", "arguments": {"queries": ["slo"], "domains": ["nope"]}}})
    assert r["result"]["isError"] is True and "Filtro invalido" in r["result"]["content"][0]["text"]


def test_hits_carry_page_from_pages_db(indexed, tmp_path):
    import sqlite3

    db, lib, emb = indexed
    pdb = tmp_path / "pages.sqlite3"
    con = sqlite3.connect(pdb)
    con.execute("create table pages(path text, line_from int, line_to int, page int, label text, exact int)")
    path = str(lib / "sre-google" / "09-chapter-3.md")
    con.executemany("insert into pages values(?,?,?,?,?,?)", [(path, 1, 6, 30, "12", 1), (path, 7, 999, 31, "13", 0)])
    con.commit()
    con.close()
    hits = Searcher(db, emb, meta=META, pages=pdb).search("error budget burn rate alerting", k=10)
    sre = [h for h in hits if h["path"] == path]
    assert sre and all(h["page"] in ("12", "13") and "pdf_page" in h for h in sre)
    assert any(h["page"] == "13" and h["page_exact"] is False for h in sre)
    # un capitulo sin mapa de paginas no trae pagina (se cita por capitulo)
    assert all("page" not in h for h in hits if h["path"] != path)
    # sin archivo de paginas: sin campo page, sin error
    assert "page" not in Searcher(db, emb, pages=tmp_path / "no.sqlite3").search("error budget", k=1)[0]


def test_page_shown_in_mcp_and_hook():
    import recall_hook as hook
    from recall.mcp_server import format_hits

    h = {"book": "DDIA", "chapter": "Ch 6", "path": "x.md", "line_start": 1, "line_end": 2, "cos": 0.7,
         "text": "t", "year": 2017, "page": "199", "page_exact": True}
    assert "(2017, p. 199)" in format_hits([h])
    assert "(2017, p. ~199)" in hook.render([{**h, "page_exact": False}], 700)


def test_service_rejects_bad_filters(service):
    import urllib.error
    import urllib.request

    body = json.dumps({"queries": ["x y"], "filters": {"domains": ["nope"]}}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{service['port']}/search", data=body, method="POST",
                                 headers={"Authorization": f"Bearer {service['token']}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with pytest.raises(urllib.error.HTTPError) as e:
        opener.open(req, timeout=10)
    assert e.value.code == 400
