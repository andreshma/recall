"""Incremental indexing of Markdown collections.

Detects additions, changes and removals by comparing each file with the
``files`` table: same size and mtime -> unchanged (not read); if they differ
the SHA-256 is computed and the file is re-chunked only if the content
changed. Before embedding, each chunk looks for an already computed vector
with the same input text (``input_sha``): reconverting a book that keeps most
of its text, or moving it from ``_md`` to ``_md_v2``, re-embeds only what
really changed. That is why removals are applied last, after their vectors
have had a chance to be reused.

Work is committed in groups of files (~``group_chunks`` chunks per
transaction): an interrupted indexing run loses at most the group in progress
and the next ``recall index`` picks up where it left off.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from . import store
from .chunking import Chunk, chunk_file, should_skip
from .embedder import CHUNK_DEFAULTS, Embedder, format_doc, index_identity
from .textutil import sha256_file, sha256_text


@dataclass
class SourceFile:
    """File discovered in a collection."""

    path: str
    collection: str
    book_id: str


@dataclass
class Report:
    """Summary of an indexing run."""

    added: int = 0
    changed: int = 0
    deleted: int = 0
    unchanged: int = 0
    skipped: int = 0
    chunks_embedded: int = 0
    chunks_reused: int = 0
    seconds: float = 0.0
    total_chunks: int = 0
    total_files: int = 0
    errors: list[str] = field(default_factory=list)


def _contained(path: str, real_root: str) -> bool:
    """True if ``path`` is not a symlink/junction and really lives under ``real_root``."""
    if os.path.islink(path) or os.path.isjunction(path):
        return False
    real = os.path.normcase(os.path.realpath(path))
    try:
        return os.path.commonpath([real, real_root]) == real_root
    except ValueError:  # different drives
        return False


def discover(collections: dict) -> tuple[dict[str, SourceFile], int]:
    """``*.md`` files of each collection, applying root precedence.

    A collection's ``roots`` are in precedence order: a book (top-level
    directory) that appears in an earlier root hides the same-named one in
    later roots. Returns ({path: SourceFile}, n_skipped).

    Only what physically lives under a root is indexed: a book directory or
    file that is a symlink or a directory junction (``mklink /J`` needs no
    admin rights) is ignored, and so is anything whose real path is outside
    the root. The root itself may be a link; it is resolved once.
    """
    found: dict[str, SourceFile] = {}
    skipped = 0
    for coll, spec in collections.items():
        claimed: set[str] = set()
        for root in spec.get("roots", []):
            if not os.path.isdir(root):
                continue
            real_root = os.path.normcase(os.path.realpath(root))
            books = sorted(
                d
                for d in os.listdir(root)
                if os.path.isdir(os.path.join(root, d)) and _contained(os.path.join(root, d), real_root)
            )
            for book in books:
                if book.startswith(("_", ".")) or book in claimed:
                    continue
                bdir = os.path.join(root, book)
                files = [
                    f
                    for f in sorted(os.listdir(bdir))
                    if f.lower().endswith(".md")
                    and os.path.isfile(os.path.join(bdir, f))
                    and _contained(os.path.join(bdir, f), real_root)
                ]
                if not files:
                    continue
                claimed.add(book)
                for fn in files:
                    if should_skip(fn):
                        skipped += 1
                        continue
                    p = os.path.normpath(os.path.join(root, book, fn))
                    found[p] = SourceFile(p, coll, book)
    return found, skipped


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


class Indexer:
    """Applies the plan of additions/changes/removals to the SQLite index."""

    def __init__(
        self,
        db: Path,
        embedder: Embedder,
        *,
        group_chunks: int = 512,
        quiet: bool = False,
        chunking: dict | None = None,
    ) -> None:
        self.db = db
        self.emb = embedder
        self.group_chunks = group_chunks
        self.quiet = quiet
        self.chunking = {**CHUNK_DEFAULTS, **(chunking or {})}
        self.identity = index_identity(embedder.identity, self.chunking)

    def run(self, collections: dict, *, rebuild: bool = False, limit_files: int | None = None) -> Report:
        """Index ``collections``; with ``rebuild``, delete everything first."""
        t0 = time.perf_counter()
        rep = Report()
        con = store.open_rw(self.db)
        try:
            if rebuild:
                con.execute("delete from chunks")
                con.execute("delete from files")
                con.execute("delete from meta where key='model_identity'")
                store.bump_generation(con)
                con.commit()
            store.check_identity(con, self.identity, writing=True)
            found, rep.skipped = discover(collections)
            known = {r[0]: r for r in con.execute("select path, sha256, size, mtime_ns from files")}
            todo: list[tuple[SourceFile, str, os.stat_result, bool]] = []
            for p, sf in sorted(found.items()):
                st = os.stat(p)
                old = known.get(p)
                if old and old[2] == st.st_size and old[3] == st.st_mtime_ns:
                    rep.unchanged += 1
                    continue
                digest = sha256_file(p)
                if old and old[1] == digest:
                    con.execute("update files set mtime_ns=?, size=? where path=?", (st.st_mtime_ns, st.st_size, p))
                    rep.unchanged += 1
                    continue
                todo.append((sf, digest, st, old is not None))
            con.commit()
            if limit_files is not None:
                todo = todo[:limit_files]
            gone = self._gone(collections, set(known) - set(found), rep)
            self._apply(con, todo, rep)
            if gone:
                with con:
                    for p in gone:
                        con.execute("delete from chunks where path=?", (p,))
                        con.execute("delete from files where path=?", (p,))
                    store.bump_generation(con)
                rep.deleted = len(gone)
            rep.total_chunks = con.execute("select count(*) from chunks").fetchone()[0]
            rep.total_files = con.execute("select count(*) from files").fetchone()[0]
        finally:
            con.close()
        rep.seconds = time.perf_counter() - t0
        return rep

    @staticmethod
    def _gone(collections: dict, missing: set[str], rep: Report) -> list[str]:
        """Indexed files to delete: not found any more, unless their root is missing.

        A configured root that does not exist (a drive not mounted, a config
        still on the old default root) is not "every book was deleted": its
        files are kept and the run reports an error instead of emptying the
        index. Removing a root from the config still deletes its books.
        """
        absent = {
            os.path.normcase(os.path.normpath(r))
            for spec in collections.values()
            for r in spec.get("roots", [])
            if not os.path.isdir(r)
        }
        kept: dict[str, int] = {}
        out = []
        for p in sorted(missing):
            np_ = os.path.normcase(p)
            root = next((r for r in absent if np_.startswith(r + os.sep)), None)
            if root is None:
                out.append(p)
            else:
                kept[root] = kept.get(root, 0) + 1
        for root, n in kept.items():
            rep.errors.append(f"root not found: {root}; its {n} indexed file(s) were kept (fix the root or --rebuild)")
        return out

    def _apply(self, con: sqlite3.Connection, todo: list, rep: Report) -> None:
        group: list[tuple[SourceFile, str, os.stat_result, bool, list[Chunk]]] = []
        pending = 0
        total_new = 0
        t0 = time.perf_counter()
        for i, (sf, digest, st, existed) in enumerate(todo, 1):
            try:
                chunks = chunk_file(sf.path, sf.book_id, **self.chunking)
            except OSError as e:
                rep.errors.append(f"{sf.path}: {e}")
                continue
            group.append((sf, digest, st, existed, chunks))
            pending += len(chunks)
            if pending >= self.group_chunks or i == len(todo):
                total_new += self._commit_group(con, group, rep)
                group, pending = [], 0
                if not self.quiet:
                    el = time.perf_counter() - t0
                    rate = rep.chunks_embedded / el if el else 0.0
                    _log(
                        f"  {i}/{len(todo)} files, {rep.chunks_embedded} embedded "
                        f"({rate:.1f}/s), {rep.chunks_reused} reused, {el:.0f}s"
                    )
        if group:
            self._commit_group(con, group, rep)

    def _commit_group(self, con: sqlite3.Connection, group: list, rep: Report) -> int:
        inputs: list[str] = []
        flat: list[tuple[int, Chunk, str]] = []
        for gi, (_sf, _d, _st, _e, chunks) in enumerate(group):
            for c in chunks:
                inp = format_doc(self.emb, c.book, c.chapter, c.text)
                flat.append((gi, c, sha256_text(inp)))
                inputs.append(inp)
        vecs = self._vectors(con, flat, inputs, rep)
        self._write_group(con, group, flat, vecs, rep)
        return len(flat)

    def _vectors(self, con: sqlite3.Connection, flat: list, inputs: list[str], rep: Report) -> list[bytes | None]:
        """Reuse the vector of an already indexed ``input_sha``; embed only what is new."""
        vecs: list[bytes | None] = [None] * len(flat)
        need: list[int] = []
        for j, (_gi, _c, h) in enumerate(flat):
            r = con.execute("select vec from chunks where input_sha=? limit 1", (h,)).fetchone()
            if r is not None:
                vecs[j] = r[0]
                rep.chunks_reused += 1
            else:
                need.append(j)
        if need:
            arr = self.emb.embed([inputs[j] for j in need], "doc")
            if arr.shape != (len(need), self.emb.dim) or not np.isfinite(arr).all():
                raise RuntimeError("The embedder returned vectors with an invalid shape or values.")
            for j, v in zip(need, arr, strict=True):
                vecs[j] = np.ascontiguousarray(v, dtype=np.float32).tobytes()
            rep.chunks_embedded += len(need)
        return vecs

    def _write_group(self, con: sqlite3.Connection, group: list, flat: list, vecs: list, rep: Report) -> None:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with con:
            for sf, digest, st, existed, chunks in group:
                con.execute("delete from chunks where path=?", (sf.path,))
                con.execute(
                    "insert or replace into files values(?,?,?,?,?,?,?,?)",
                    (sf.path, sf.collection, sf.book_id, digest, st.st_size, st.st_mtime_ns, len(chunks), now),
                )
                if existed:
                    rep.changed += 1
                else:
                    rep.added += 1
            for j, (gi, c, h) in enumerate(flat):
                sf = group[gi][0]
                con.execute(
                    "insert into chunks(path, collection, book_id, book, chapter, section, line_start, "
                    "line_end, lang, text, input_sha, vec) values(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        sf.path,
                        sf.collection,
                        sf.book_id,
                        c.book,
                        c.chapter,
                        c.section,
                        c.line_start,
                        c.line_end,
                        c.lang,
                        c.text,
                        h,
                        vecs[j],
                    ),
                )
            store.bump_generation(con)
