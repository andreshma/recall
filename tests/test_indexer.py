"""Indexado incremental: alta, cambio, baja, reutilizacion, precedencia, identidad."""
import os
import shutil
import sqlite3

import pytest

from recall import config, store
from recall.embedder import HashEmbedder
from recall.indexer import Indexer, discover

from conftest import para


def _count(db, sql="select count(*) from chunks"):
    con = sqlite3.connect(str(db))
    try:
        return con.execute(sql).fetchone()[0]
    finally:
        con.close()


def test_first_run_adds_and_skips_front_matter(indexed):
    db, lib, _ = indexed
    assert _count(db, "select count(*) from files") == 4  # 4 capitulos, sin cover ni front-matter
    assert _count(db) > 5
    assert _count(db, "select count(*) from chunks where path like '%01-cover%'") == 0
    # disaster-recovery NO se omite aunque contenga "cover"
    assert _count(db, "select count(*) from files where path like '%disaster-recovery%'") == 1


def test_fts_in_sync_with_chunks(indexed):
    db, _, _ = indexed
    assert _count(db, "select count(*) from fts where fts match 'bounded'") > 0


def test_second_run_is_noop(indexed):
    db, lib, emb = indexed
    calls = emb.calls
    rep = Indexer(db, emb, quiet=True).run({"books": {"roots": [str(lib)]}})
    assert (rep.added, rep.changed, rep.deleted) == (0, 0, 0)
    assert rep.unchanged == 4 and emb.calls == calls


def test_touch_without_change_does_not_reembed(indexed):
    db, lib, emb = indexed
    f = lib / "sre-google" / "09-chapter-3.md"
    os.utime(f, (f.stat().st_atime, f.stat().st_mtime + 100))
    calls = emb.calls
    rep = Indexer(db, emb, quiet=True).run({"books": {"roots": [str(lib)]}})
    assert rep.changed == 0 and rep.unchanged == 4 and emb.calls == calls


def test_change_reembeds_only_new_text(indexed):
    db, lib, emb = indexed
    f = lib / "sre-google" / "09-chapter-3.md"
    f.write_text(f.read_text(encoding="utf-8") + "\n\n# New Section\n\n" + para("Brand new paragraph about toil.", 700),
                 encoding="utf-8")
    rep = Indexer(db, emb, quiet=True).run({"books": {"roots": [str(lib)]}})
    assert rep.changed == 1 and rep.added == 0
    assert rep.chunks_reused >= 1 and rep.chunks_embedded >= 1
    assert _count(db, "select count(*) from chunks where section='New Section'") >= 1


def test_delete_removes_chunks_and_fts(indexed):
    db, lib, emb = indexed
    (lib / "libro-es" / "01-capitulo-1.md").unlink()
    rep = Indexer(db, emb, quiet=True).run({"books": {"roots": [str(lib)]}})
    assert rep.deleted == 1
    assert _count(db, "select count(*) from chunks where book_id='libro-es'") == 0
    assert _count(db, "select count(*) from fts where fts match 'incertidumbre'") == 0


def test_v2_root_replaces_book_and_reuses_vectors(indexed, tmp_path):
    """Un libro reconvertido en _md_v2 (por delante en roots) reemplaza al de _md."""
    db, lib, emb = indexed
    v2 = tmp_path / "books" / "_md_v2"
    shutil.copytree(lib / "sre-google", v2 / "sre-google")
    calls = emb.calls
    rep = Indexer(db, emb, quiet=True).run({"books": {"roots": [str(v2), str(lib)]}})
    assert rep.added == 2 and rep.deleted == 2
    assert emb.calls == calls  # mismo texto: todo reutilizado
    paths = [r for r in sqlite3.connect(str(db)).execute("select distinct path from chunks where book_id='sre-google'")]
    assert paths and all("_md_v2" in p[0] for p in paths)


def test_discover_precedence(library, tmp_path):
    v2 = tmp_path / "books" / "_md_v2"
    (v2 / "learning-ddd").mkdir(parents=True)
    (v2 / "learning-ddd" / "05-chapter-3.md").write_text("# a — b\n\nx", encoding="utf-8")
    found, skipped = discover({"books": {"roots": [str(v2), str(library)]}})
    ddd = [p for p, s in found.items() if s.book_id == "learning-ddd"]
    assert ddd and all("_md_v2" in p for p in ddd)
    assert any(s.book_id == "sre-google" for s in found.values())


def test_rebuild(indexed):
    db, lib, emb = indexed
    n = _count(db)
    rep = Indexer(db, emb, quiet=True).run({"books": {"roots": [str(lib)]}}, rebuild=True)
    assert rep.added == 4 and _count(db) == n


def test_model_mismatch_refused(indexed):
    db, lib, _ = indexed
    other = HashEmbedder(dim=64, name="otro-modelo")
    with pytest.raises(store.ModelMismatchError):
        Indexer(db, other, quiet=True).run({"books": {"roots": [str(lib)]}})
    # --rebuild si permite cambiar de modelo, sin mezclar
    Indexer(db, other, quiet=True).run({"books": {"roots": [str(lib)]}}, rebuild=True)
    assert '"otro-modelo"' in store.get_meta(sqlite3.connect(str(db)), "model_identity")


def test_missing_identity_with_chunks_refused(indexed):
    db, lib, emb = indexed
    con = sqlite3.connect(str(db))
    con.execute("delete from meta where key='model_identity'")
    con.commit()
    con.close()
    with pytest.raises(store.ModelMismatchError):
        Indexer(db, emb, quiet=True).run({"books": {"roots": [str(lib)]}})


class Flaky(HashEmbedder):
    """Falla en la segunda llamada: simula un indexado interrumpido."""

    def __init__(self):
        super().__init__()
        self.n = 0

    def embed(self, texts, kind):
        self.n += 1
        if self.n == 2:
            raise RuntimeError("corte")
        return super().embed(texts, kind)


def test_interrupted_run_resumes(home, library):
    db = config.db_path()
    with pytest.raises(RuntimeError):
        Indexer(db, Flaky(), group_chunks=1, quiet=True).run({"books": {"roots": [str(library)]}})
    done = _count(db, "select count(*) from files")
    assert done >= 1  # el primer grupo quedo confirmado
    rep = Indexer(db, HashEmbedder(), quiet=True).run({"books": {"roots": [str(library)]}})
    assert rep.added == 4 - done and _count(db, "select count(*) from files") == 4
