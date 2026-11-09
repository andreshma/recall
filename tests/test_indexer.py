"""Incremental indexing: add, change, delete, reuse, precedence, identity."""

import os
import shutil
import sqlite3

import pytest
from conftest import para

from recall import config, store
from recall.embedder import HashEmbedder
from recall.indexer import Indexer, discover


def _count(db, sql="select count(*) from chunks"):
    con = sqlite3.connect(str(db))
    try:
        return con.execute(sql).fetchone()[0]
    finally:
        con.close()


def test_first_run_adds_and_skips_front_matter(indexed):
    db, _lib, _ = indexed
    assert _count(db, "select count(*) from files") == 4  # 4 chapters, no cover or front-matter
    assert _count(db) > 5
    assert _count(db, "select count(*) from chunks where path like '%01-cover%'") == 0
    # disaster-recovery is NOT skipped even though it contains "cover"
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
    f.write_text(
        f.read_text(encoding="utf-8") + "\n\n# New Section\n\n" + para("Brand new paragraph about toil.", 700),
        encoding="utf-8",
    )
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
    """A book reconverted into _md_v2 (earlier in roots) replaces the one in _md."""
    db, lib, emb = indexed
    v2 = tmp_path / "books" / "_md_v2"
    shutil.copytree(lib / "sre-google", v2 / "sre-google")
    calls = emb.calls
    rep = Indexer(db, emb, quiet=True).run({"books": {"roots": [str(v2), str(lib)]}})
    assert rep.added == 2 and rep.deleted == 2
    assert emb.calls == calls  # same text: everything reused
    paths = [r for r in sqlite3.connect(str(db)).execute("select distinct path from chunks where book_id='sre-google'")]
    assert paths and all("_md_v2" in p[0] for p in paths)


def test_discover_precedence(library, tmp_path):
    v2 = tmp_path / "books" / "_md_v2"
    (v2 / "learning-ddd").mkdir(parents=True)
    (v2 / "learning-ddd" / "05-chapter-3.md").write_text("# a — b\n\nx", encoding="utf-8")
    found, _skipped = discover({"books": {"roots": [str(v2), str(library)]}})
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
    # --rebuild does allow switching models, without mixing
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
    """Fails on the second call: simulates an interrupted indexing run."""

    def __init__(self):
        super().__init__()
        self.n = 0

    def embed(self, texts, kind):
        self.n += 1
        if self.n == 2:
            raise RuntimeError("interrupted")
        return super().embed(texts, kind)


def test_interrupted_run_resumes(home, library):
    db = config.db_path()
    with pytest.raises(RuntimeError):
        Indexer(db, Flaky(), group_chunks=1, quiet=True).run({"books": {"roots": [str(library)]}})
    done = _count(db, "select count(*) from files")
    assert done >= 1  # the first group was committed
    rep = Indexer(db, HashEmbedder(), quiet=True).run({"books": {"roots": [str(library)]}})
    assert rep.added == 4 - done and _count(db, "select count(*) from files") == 4


def _outside(tmp_path):
    out = tmp_path / "outside-secret"
    out.mkdir()
    (out / "01-notes.md").write_text(
        "# Notes — Private\n\n" + para("OUTSIDE-ROOT-MARKER private notes.", 600), encoding="utf-8"
    )
    return out


@pytest.mark.skipif(os.name != "nt", reason="directory junctions are a Windows feature")
def test_discover_ignores_directory_junctions(library, tmp_path):
    """ADV-8: ``mklink /J`` (no admin rights) inside a root exposed content outside it."""
    import subprocess

    out = _outside(tmp_path)
    link = library / "linked-book"
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    try:
        found, _ = discover({"books": {"roots": [str(library)]}})
        assert not any(s.book_id == "linked-book" for s in found.values())
        assert any(s.book_id == "sre-google" for s in found.values())
    finally:
        os.rmdir(link)  # removes the junction only


def test_discover_ignores_symlinked_books_and_files(library, tmp_path):
    out = _outside(tmp_path)
    try:
        os.symlink(out, library / "linked-book", target_is_directory=True)
        os.symlink(out / "01-notes.md", library / "sre-google" / "50-linked.md")
    except OSError as e:  # Windows without developer mode / privilege
        pytest.skip(f"cannot create symlinks here: {e}")
    found, _ = discover({"books": {"roots": [str(library)]}})
    assert not any(s.book_id == "linked-book" for s in found.values())
    assert not any(p.endswith("50-linked.md") for p in found)
    assert any(p.endswith("09-chapter-3.md") for p in found)


@pytest.mark.skipif(os.name != "nt", reason="directory junctions are a Windows feature")
def test_root_that_is_a_junction_still_works(library, tmp_path):
    import subprocess

    link = tmp_path / "root-link"
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(library)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    try:
        found, _ = discover({"books": {"roots": [str(link)]}})
        assert {s.book_id for s in found.values()} == {"learning-ddd", "sre-google", "libro-es"}
    finally:
        os.rmdir(link)


def test_missing_root_does_not_empty_the_index(indexed, tmp_path):
    """With the neutral default root (F-05) an un-migrated config points at a missing root.

    That must not read as "every book was deleted".
    """
    db, lib, emb = indexed
    n = _count(db)
    moved = tmp_path / "moved"
    lib.rename(moved)
    rep = Indexer(db, emb, quiet=True).run({"books": {"roots": [str(lib)]}})
    assert rep.deleted == 0 and _count(db) == n
    assert rep.errors and "root not found" in rep.errors[0]
    # Dropping the root from the config does delete its books.
    rep = Indexer(db, emb, quiet=True).run({"books": {"roots": [str(moved)]}})
    assert rep.deleted == 4 and rep.added == 4 and not rep.errors
