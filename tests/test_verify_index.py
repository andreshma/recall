import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import verify_index as vi  # noqa: E402


def test_clean_index_passes(indexed):
    db, _lib, _emb = indexed
    r = vi.verify(Path(db).parent, source=False)
    assert r["ok"], r["checks"]
    assert r["chunks"] > 0


def test_detects_corruption(indexed, tmp_path):
    import shutil

    db, _lib, _emb = indexed
    home = tmp_path / "copia"
    home.mkdir()
    shutil.copy(db, home / "index.sqlite3")
    con = sqlite3.connect(home / "index.sqlite3")
    con.execute("update chunks set vec=zeroblob(8) where id=(select min(id) from chunks)")
    con.execute("update chunks set text=text||' x' where id=(select max(id) from chunks)")
    con.execute("update files set n_chunks=n_chunks+1 where rowid=1")
    con.commit()
    con.close()
    c = vi.verify(home, source=False)["checks"]
    assert not c["vectors"]["ok"] and c["vectors"]["detail"]["wrong_length"] == 1
    assert not c["text_matches_embedded_input"]["ok"]
    assert not c["chunk_accounting"]["ok"]
