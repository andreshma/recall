"""Indexado incremental de colecciones de Markdown.

Detecta altas, cambios y bajas comparando cada archivo con la tabla ``files``:
mismo tamano y mtime -> sin cambios (no se lee); si difieren se calcula el
SHA-256 y solo si cambia el contenido se re-trocea. Antes de embeber, cada
chunk busca un vector ya calculado con el mismo texto de entrada
(``input_sha``): reconvertir un libro que conserva la mayoria del texto, o
moverlo de ``_md`` a ``_md_v2``, re-embebe solo lo que de verdad cambio. Por
eso las bajas se aplican al final, despues de haber podido reutilizar sus
vectores.

El trabajo se confirma por grupos de archivos (~``group_chunks`` chunks por
transaccion): un indexado interrumpido pierde a lo sumo el grupo en curso y
el siguiente ``recall index`` continua donde quedo.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import store
from .chunking import Chunk, chunk_file, should_skip
from .embedder import CHUNK_DEFAULTS, Embedder, format_doc, index_identity
from .textutil import sha256_file, sha256_text


@dataclass
class SourceFile:
    """Archivo descubierto en una coleccion."""

    path: str
    collection: str
    book_id: str


@dataclass
class Report:
    """Resumen de una corrida de indexado."""

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


def discover(collections: dict) -> tuple[dict[str, SourceFile], int]:
    """Archivos ``*.md`` de cada coleccion, aplicando precedencia de raices.

    ``roots`` de una coleccion va en orden de precedencia: un libro (directorio
    de primer nivel) que aparece en una raiz anterior oculta al homonimo de las
    siguientes. Devuelve ({ruta: SourceFile}, n_omitidos).
    """
    found: dict[str, SourceFile] = {}
    skipped = 0
    for coll, spec in collections.items():
        claimed: set[str] = set()
        for root in spec.get("roots", []):
            if not os.path.isdir(root):
                continue
            books = sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))
            for book in books:
                if book.startswith(("_", ".")) or book in claimed:
                    continue
                files = [f for f in sorted(os.listdir(os.path.join(root, book))) if f.lower().endswith(".md")]
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
    """Aplica el plan de altas/cambios/bajas sobre el indice SQLite."""

    def __init__(self, db: Path, embedder: Embedder, *, group_chunks: int = 512, quiet: bool = False,
                 chunking: dict | None = None) -> None:
        self.db = db
        self.emb = embedder
        self.group_chunks = group_chunks
        self.quiet = quiet
        self.chunking = {**CHUNK_DEFAULTS, **(chunking or {})}
        self.identity = index_identity(embedder.identity, self.chunking)

    def run(self, collections: dict, *, rebuild: bool = False, limit_files: int | None = None) -> Report:
        """Indexa ``collections``; con ``rebuild`` borra todo antes."""
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
            gone = sorted(set(known) - set(found))
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
                    _log(f"  {i}/{len(todo)} archivos, {rep.chunks_embedded} embebidos "
                         f"({rate:.1f}/s), {rep.chunks_reused} reutilizados, {el:.0f}s")
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
                raise RuntimeError("El embedder devolvio vectores con forma o valores invalidos.")
            for j, v in zip(need, arr):
                vecs[j] = np.ascontiguousarray(v, dtype=np.float32).tobytes()
            rep.chunks_embedded += len(need)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with con:
            for gi, (sf, digest, st, existed, chunks) in enumerate(group):
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
                    (sf.path, sf.collection, sf.book_id, c.book, c.chapter, c.section,
                     c.line_start, c.line_end, c.lang, c.text, h, vecs[j]),
                )
            store.bump_generation(con)
        return len(flat)
