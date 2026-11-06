"""Busqueda: vector primero, RRF con FTS5 de peso menor, dedup y coseno.

Orden de resultados: Reciprocal Rank Fusion de dos listas, la vectorial con
peso 1 y la de FTS5 (BM25) con peso ``fts_weight_same_lang`` si el idioma de
la consulta coincide con el del chunk y ``fts_weight_cross_lang`` (casi nulo)
si no. Asi FTS5 solo desempata entre resultados vectoriales cuando puede
aportar coincidencia literal, y no arrastra al cruce ES->EN, que es donde el
prototipo midio que danaba.

Cada resultado devuelve ``cos``, el coseno entre la consulta y el chunk, que es
lo que usa el hook para su umbral (la puntuacion RRF no es comparable entre
consultas; el coseno si lo es, dentro de un mismo modelo).
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

from . import metadata, store
from .embedder import Embedder, format_query, index_identity
from .textutil import detect_lang, fts_query


#: Como mucho 6 ventanas por chunk al elegir el recorte.
SNIPPET_ROWS = 6

_STOP = set("""
the and for with that this from are was were has have had not but can its into over than then them they
their there what when where which while who why how about also been being does each more most other some
such only very will would should could may might must your you our out use used using
que los las del por para con una uno unos unas como mas pero sus son esta este estos estas ese esa eso
entre sobre cuando donde cual cuales quien hay ser fue han muy sin sus tambien porque segun libro libros
dime explica explicame dame quiero puedes hacer hace tiene tienen cosa cosas forma manera
""".split())


def _norm(s: str) -> str:
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c))


def query_terms(queries: Sequence[str]) -> dict[str, float]:
    """Terminos de la consulta (sin acentos ni palabras vacias) con su peso.

    Los largos pesan mas (mas especificos); los bigramas pesan doble porque un
    termino compuesto ("error budget") es lo que mejor ubica el parrafo.
    """
    import re

    out: dict[str, float] = {}
    for q in queries:
        words = [w for w in re.findall(r"[a-z0-9][a-z0-9\-]+", _norm(q)) if w not in _STOP]
        for w in words:
            if len(w) >= 3 or w.isupper():
                out[w] = max(out.get(w, 0.0), min(len(w), 10) / 10)
        for a, b in zip(words, words[1:]):
            out[f"{a} {b}"] = 2.0
    return out


def window_score(window: str, terms: dict[str, float]) -> tuple[int, float]:
    """(terminos distintos presentes, suma de pesos) de ``terms`` en ``window``."""
    import re

    text = " ".join(re.findall(r"[a-z0-9][a-z0-9\-]+", _norm(window)))
    padded = f" {text} "
    hit = [w for w in terms if f" {w} " in padded or (" " not in w and len(w) >= 6 and w in text)]
    return len(hit), sum(terms[w] for w in hit)


def split_windows(text: str, width: int, max_windows: int = SNIPPET_ROWS) -> list[str]:
    """Parte ``text`` en ventanas contiguas de ~``width`` caracteres por frases.

    Corta en fin de frase o de linea; una frase mas larga que ``width`` se
    corta a lo bruto. Si salen mas de ``max_windows``, ensancha las ventanas.
    """
    import re

    pieces = [p for p in re.split(r"(?<=[.!?:;])\s+|\n+", text) if p.strip()]
    flat: list[str] = []
    for p in pieces:
        while len(p) > width:
            flat.append(p[:width])
            p = p[width:]
        flat.append(p)
    out: list[str] = []
    cur = ""
    for p in flat:
        if cur and len(cur) + 1 + len(p) > width:
            out.append(cur)
            cur = p
        else:
            cur = f"{cur} {p}" if cur else p
    if cur:
        out.append(cur)
    if len(out) > max_windows:
        return split_windows(text, int(width * len(out) / max_windows) + 1, max_windows)
    return out


def make_searcher(cfg: dict, db: Path | None = None) -> "Searcher":
    """Buscador del indice configurado (modelo de consultas + troceo de ``cfg``)."""
    from . import config
    from .embedder import from_config

    pages = cfg.get("pages_file") or (config.home() / "pages.sqlite3")
    return Searcher(db or config.db_path(), from_config(cfg, "query"), cfg["search"],
                    chunking=cfg.get("chunking"), meta=metadata.load(cfg.get("metadata_file")),
                    pages=Path(pages))


class Searcher:
    """Indice cargado en memoria + embedder de consultas. Seguro entre hilos."""

    def __init__(self, db: Path, embedder: Embedder, search_cfg: dict | None = None,
                 chunking: dict | None = None, meta: dict[str, dict] | None = None,
                 pages: Path | None = None) -> None:
        self.db = db
        self.emb = embedder
        self.identity = index_identity(embedder.identity, chunking)
        self.meta = meta or {}
        # Mapa linea -> pagina del PDF (tools/build_pages.py). Opcional: sin el
        # archivo los resultados no traen pagina y se citan por capitulo.
        self._pages: sqlite3.Connection | None = None
        if pages is not None and Path(pages).is_file():
            try:
                self._pages = store.open_ro(Path(pages))
                self._pages.execute("select 1 from pages limit 1")
            except (sqlite3.Error, OSError):
                self._pages = None
        self.cfg = {
            "rrf_k": 60, "vec_weight": 1.0, "fts_weight_same_lang": 0.25,
            "fts_weight_cross_lang": 0.02, "candidates": 100,
        }
        self.cfg.update(search_cfg or {})
        self._lock = threading.RLock()
        self._con: sqlite3.Connection | None = None
        self._gen = -1
        self._loaded_at = 0.0
        # Durante un `recall index` cada commit sube la generacion; recargar la
        # matriz completa en cada consulta reventaria el presupuesto del hook.
        self.min_reload_s = float(self.cfg.get("min_reload_s", 30.0))
        self.background_reload = False
        self._loading = False
        self.ids = np.zeros(0, dtype=np.int64)
        self.V = np.zeros((0, embedder.dim), dtype=np.float32)
        self.load()

    # -- carga ---------------------------------------------------------------
    def _read(self, con: sqlite3.Connection) -> dict:
        """Lee del indice una instantanea completa (no toca ``self``)."""
        store.check_identity(con, self.identity, writing=False)
        gen = store.generation(con)
        rows = con.execute("select id, collection, lang, vec, book_id from chunks order by id").fetchall()
        n, d = len(rows), self.emb.dim
        V = np.empty((n, d), dtype=np.float32)
        for i, r in enumerate(rows):
            V[i] = np.frombuffer(r[3], dtype=np.float32, count=d)
        ids = np.fromiter((r[0] for r in rows), dtype=np.int64, count=n)
        return {
            "V": V, "ids": ids, "gen": gen,
            "coll": np.array([r[1] for r in rows], dtype=object),
            "lang": np.array([r[2] for r in rows], dtype=object),
            "book": np.array([r[4] for r in rows], dtype=object),
            "pos": {int(x): i for i, x in enumerate(ids)},
        }

    def _install(self, snap: dict) -> None:
        with self._lock:
            self.V, self.ids, self.coll, self.lang, self.pos, self.book = (
                snap["V"], snap["ids"], snap["coll"], snap["lang"], snap["pos"], snap["book"])
            self._gen = snap["gen"]
            self._loaded_at = time.monotonic()

    def load(self) -> None:
        """(Re)carga sincrona de ids, colecciones, idiomas y vectores."""
        with self._lock:
            if self._con is None:
                self._con = store.open_ro(self.db)
            self._install(self._read(self._con))

    def _background_load(self) -> None:
        try:
            con = store.open_ro(self.db)
            try:
                snap = self._read(con)
            finally:
                con.close()
            self._install(snap)
        except Exception:  # p. ej. identidad cambiada a media reconstruccion
            with self._lock:
                self._loaded_at = time.monotonic()
        finally:
            self._loading = False

    def refresh(self, force: bool = False, background: bool | None = None) -> bool:
        """Recarga si otro proceso confirmo escrituras.

        Como mucho cada ``min_reload_s`` (salvo ``force``). Con ``background``
        (lo que usa el servicio) la nueva instantanea se lee en otro hilo y otra
        conexion, y las busquedas siguen sobre la anterior hasta el intercambio:
        con ~100k chunks leer la matriz cuesta segundos y el hook tiene 1.5 s.
        Devuelve True si recargo o empezo a recargar.
        """
        background = self.background_reload if background is None else background
        with self._lock:
            if not force and time.monotonic() - self._loaded_at < self.min_reload_s:
                return False
            if self._con is None or store.generation(self._con) == self._gen:
                self._loaded_at = time.monotonic()
                return False
            if not background:
                self.load()
                return True
            if self._loading:
                return False
            self._loading = True
        threading.Thread(target=self._background_load, daemon=True).start()
        return True

    @property
    def size(self) -> int:
        """Numero de chunks cargados."""
        return len(self.ids)

    @property
    def generation(self) -> int:
        """Generacion del indice cargada en memoria."""
        return self._gen

    def _page(self, path: str, line: int) -> dict:
        """Pagina impresa (``page``), del PDF (``pdf_page``) y si es exacta."""
        if self._pages is None:
            return {}
        try:
            r = self._pages.execute(
                "select label, page, exact from pages where path=? and line_from<=? "
                "order by line_from desc limit 1", (path, int(line))).fetchone()
        except sqlite3.Error:
            return {}
        return {"page": r[0], "pdf_page": r[1], "page_exact": bool(r[2])} if r else {}

    # -- busqueda --------------------------------------------------------------
    def _fts(self, q: str, n: int, allowed: set[str] | None) -> list[int]:
        expr = fts_query(q)
        if not expr:
            return []
        rows = self._con.execute(
            "select f.rowid, c.collection from fts f join chunks c on c.id = f.rowid "
            "where fts match ? order by bm25(fts, 2.0, 3.0, 4.0, 1.0) limit ?",
            (expr, n * 2),
        ).fetchall()
        out = [r[0] for r in rows if allowed is None or r[1] in allowed]
        return out[:n]

    def search(
        self,
        queries: str | Sequence[str],
        k: int = 8,
        collections: Iterable[str] | None = None,
        filters: dict | None = None,
        snippet_chars: int | None = None,
        snippet_top: int = 3,
    ) -> list[dict]:
        """Busca una o varias formulaciones (p. ej. ES y EN) y fusiona.

        Con varias consultas, cada chunk queda con su mejor puntuacion y su
        mejor coseno entre todas. Devuelve hasta ``k`` dicts deduplicados por
        (archivo, seccion), ordenados por puntuacion. ``filters`` restringe por
        metadatos del libro (ver :mod:`recall.metadata`); uno mal formado lanza
        :class:`recall.metadata.FilterError`.
        """
        qs = [queries] if isinstance(queries, str) else [q for q in queries if q and q.strip()]
        qs = [q.strip()[:2000] for q in qs if q.strip()]
        if not qs:
            return []
        k = max(1, min(int(k), 50))
        allowed = set(collections) if collections else None
        books = metadata.allowed_books(self.meta, metadata.validate(filters))
        if books is not None and not books:
            return []
        self.refresh()
        with self._lock:
            V, ids, coll, lang, pos, book = self.V, self.ids, self.coll, self.lang, self.pos, self.book
        if not len(ids):
            return []
        # Una a una: en DirectML un lote de 2-4 consultas es una forma de tensor
        # nueva (compilacion de segundos); las de lote 1 las calienta el servicio.
        qv = np.vstack([self.emb.embed([format_query(self.emb, q)], "query") for q in qs])
        with self._lock:
            cos_all = V @ qv.T  # n x len(qs)
            mask = None
            if allowed is not None:
                mask = np.isin(coll, list(allowed))
            if books is not None:
                bmask = np.isin(book, list(books))
                mask = bmask if mask is None else (mask & bmask)
            if mask is not None and not mask.any():
                return []
            best_score: dict[int, float] = {}
            best_cos = cos_all.max(axis=1)
            via: dict[int, set] = {}
            kk = self.cfg["rrf_k"]
            ncand = int(self.cfg["candidates"])
            for qi, q in enumerate(qs):
                qlang = detect_lang(q)
                s = cos_all[:, qi].copy()
                if mask is not None:
                    s[~mask] = -np.inf
                m = min(ncand, len(s))
                top = np.argpartition(-s, m - 1)[:m]
                top = top[np.argsort(-s[top])]
                score: dict[int, float] = {}
                for r, p in enumerate(top):
                    if not np.isfinite(s[p]):
                        continue
                    score[int(p)] = self.cfg["vec_weight"] / (kk + r + 1)
                    via.setdefault(int(p), set()).add("vec")
                for r, rid in enumerate(self._fts(q, ncand, allowed)):
                    p = pos.get(rid)
                    if p is None or (mask is not None and not mask[p]):
                        continue
                    same = qlang != "und" and lang[p] == qlang
                    w = self.cfg["fts_weight_same_lang"] if same else self.cfg["fts_weight_cross_lang"]
                    score[p] = score.get(p, 0.0) + w / (kk + r + 1)
                    via.setdefault(p, set()).add("fts")
                for p, v in score.items():
                    if v > best_score.get(p, -1.0):
                        best_score[p] = v
            ranked = sorted(best_score, key=lambda p: (-best_score[p], -best_cos[p]))
            out: list[dict] = []
            seen: set[tuple[str, str]] = set()
            for p in ranked:
                rid = int(ids[p])
                row = self._con.execute(
                    "select path, collection, book_id, book, chapter, section, line_start, line_end, lang, text "
                    "from chunks where id=?", (rid,),
                ).fetchone()
                if row is None:
                    continue
                key = (row[0].lower(), row[5])
                if key in seen:
                    continue
                seen.add(key)
                out.append({
                    "id": rid, "path": row[0], "collection": row[1], "book_id": row[2], "book": row[3],
                    "chapter": row[4], "section": row[5], "line_start": row[6], "line_end": row[7],
                    "lang": row[8], "text": row[9], "cos": round(float(best_cos[p]), 4),
                    "score": round(best_score[p], 6), "via": sorted(via.get(p, ())),
                    **metadata.describe(self.meta.get(row[2])),
                    **self._page(row[0], row[6]),
                })
                if len(out) >= k:
                    break
        if snippet_chars:
            self._add_snippets(out, qs, int(snippet_chars), int(snippet_top))
        return out

    def _add_snippets(self, hits: list[dict], queries: list[str], width: int, top: int) -> None:
        """``snippet``: la ventana de ~``width`` caracteres que mas terminos comparte con la consulta.

        Solo para los primeros ``top`` archivos distintos (los que el hook puede
        inyectar) y solo si el chunk es mas largo que ``width``. Con trozos
        grandes, los primeros ``width`` caracteres a menudo no son la parte
        pertinente.

        Por terminos y no por embeddings: re-embeber 6 ventanas por resultado
        con embeddinggemma-2 costaba ~2 s por resultado en DirectML (medido el
        2026-11-05), fuera del presupuesto del hook. Los terminos tecnicos
        (bounded context, SLO, error budget) suelen ser iguales en espanol e
        ingles, que es lo que hace funcionar el cruce. Sin coincidencias, la
        primera ventana. ``snippet_hits`` = terminos distintos coincidentes.
        """
        terms = query_terms(queries)
        seen: set[str] = set()
        for h in hits:
            if len(seen) >= top:
                break
            if h["path"].lower() in seen:
                continue
            seen.add(h["path"].lower())
            if len(h["text"]) <= width:
                continue
            wins = split_windows(h["text"], width)
            scores = [window_score(w, terms) for w in wins]
            best = max(range(len(wins)), key=lambda i: (scores[i], -i))
            h["snippet"] = wins[best]
            h["snippet_hits"] = scores[best][0]
