"""SQLite schema of the index and the model identity guard.

A single SQLite file in WAL mode holds everything: indexed files (with hash),
chunks with their provenance, each chunk's vector as a float32 BLOB, and an
external-content FTS5 table kept in sync by triggers.

Why vectors as BLOBs and not sqlite-vec or a separate memmap (evaluated in the
prototype and here):

* sqlite-vec (0.1.x) is not in the local uv cache: installing it required
  network access, and its stable version is also exhaustive search, same as numpy.
* A separate memmap has to be compacted on every deletion and does not share
  a transaction with the metadata: an interrupted indexing run could leave them
  out of sync. As a BLOB, adding/changing/removing a file is ONE transaction.
* The searcher loads the matrix into RAM at startup (N x 768 float32; ~0.3 GB
  for ~100k chunks) and the exhaustive dot product costs tens of ms; the
  dominant cost of a query is embedding the query, not searching.

``meta.generation`` goes up on every write commit; the service checks it
before each search and reloads if it changed, so a ``recall index`` shows up
without restarting anything.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .embedder import identity_key

SCHEMA_VERSION = "1"

SCHEMA = """
create table if not exists meta(key text primary key, value text not null);
create table if not exists files(
    path text primary key,
    collection text not null,
    book_id text not null,
    sha256 text not null,
    size integer not null,
    mtime_ns integer not null,
    n_chunks integer not null,
    indexed_at text not null
);
create table if not exists chunks(
    id integer primary key,
    path text not null,
    collection text not null,
    book_id text not null,
    book text not null,
    chapter text not null,
    section text not null,
    line_start integer not null,
    line_end integer not null,
    lang text not null,
    text text not null,
    input_sha text not null,
    vec blob not null
);
create index if not exists chunks_path on chunks(path);
create index if not exists chunks_input_sha on chunks(input_sha);
create virtual table if not exists fts using fts5(
    book, chapter, section, text,
    content='chunks', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
create trigger if not exists chunks_ai after insert on chunks begin
    insert into fts(rowid, book, chapter, section, text)
    values (new.id, new.book, new.chapter, new.section, new.text);
end;
create trigger if not exists chunks_ad after delete on chunks begin
    insert into fts(fts, rowid, book, chapter, section, text)
    values ('delete', old.id, old.book, old.chapter, old.section, old.text);
end;
"""


class ModelMismatchError(RuntimeError):
    """The index holds vectors from another model (or of unknown identity)."""


def open_rw(path: Path) -> sqlite3.Connection:
    """Write connection (creates the schema if missing)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=30)
    con.execute("pragma journal_mode=wal")
    con.execute("pragma synchronous=normal")
    con.executescript(SCHEMA)
    con.execute("insert or ignore into meta values('schema_version', ?)", (SCHEMA_VERSION,))
    con.execute("insert or ignore into meta values('generation', '0')")
    con.commit()
    return con


def open_ro(path: Path) -> sqlite3.Connection:
    """READ-ONLY connection (``mode=ro``): the service never writes."""
    if not path.is_file():
        raise FileNotFoundError(f"No index at {path}; run `recall index` first.")
    uri = "file:" + path.as_posix() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=30, check_same_thread=False)


def get_meta(con: sqlite3.Connection, key: str) -> str | None:
    """Value of ``meta[key]`` or None."""
    r = con.execute("select value from meta where key=?", (key,)).fetchone()
    return r[0] if r else None


def generation(con: sqlite3.Connection) -> int:
    """Counter of committed writes."""
    return int(get_meta(con, "generation") or 0)


def check_identity(con: sqlite3.Connection, identity: dict, *, writing: bool) -> None:
    """Fail if the index holds vectors from a model other than ``identity``.

    When writing to an empty index, records the identity. An index with chunks
    but no recorded identity also fails: "assuming" the current model is
    exactly the silent error that already cost dearly in another index on this machine.
    """
    stored = get_meta(con, "model_identity")
    n = con.execute("select count(*) from chunks").fetchone()[0]
    if stored is None:
        if n:
            raise ModelMismatchError(
                "The index has chunks but no recorded model identity; rebuild it with `recall index --rebuild`."
            )
        if writing:
            con.execute("insert or replace into meta values('model_identity', ?)", (identity_key(identity),))
            con.commit()
        return
    if stored != identity_key(identity):
        a, b = json.loads(stored), identity
        if _legacy_upgrade(a, b):
            # Index predating ``doc_max_tokens`` in the identity: assumed to be
            # built with the configured value (the one used to index it) and the
            # next write records it; from then on, changing it fails.
            if writing:
                con.execute("insert or replace into meta values('model_identity', ?)", (identity_key(b),))
                con.commit()
            return
        diff = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
        raise ModelMismatchError(
            f"The index was built with a different model (differs in: {', '.join(diff)}). "
            "Vectors from different models are never mixed: run `recall index --rebuild`."
        )


#: Fields added to the identity after indexes in use were built. An index that
#: lacks them is accepted (and updated on write) if everything else matches.
LEGACY_OPTIONAL = ("doc_max_tokens",)


def _legacy_upgrade(stored: dict, current: dict) -> bool:
    """True if ``stored`` differs from ``current`` only in new fields it lacked."""
    missing = [k for k in LEGACY_OPTIONAL if k in current and k not in stored]
    if not missing:
        return False
    return stored == {k: v for k, v in current.items() if k not in missing}


def bump_generation(con: sqlite3.Connection) -> None:
    """Increment ``meta.generation`` (within the current transaction)."""
    con.execute("update meta set value = cast(value as integer) + 1 where key='generation'")
