"""Search: vector first, RRF with lower-weighted FTS5, dedup and cosine.

Result order: Reciprocal Rank Fusion of two lists, the vector one with
weight 1 and the FTS5 (BM25) one with weight ``fts_weight_same_lang`` if the
query language matches the chunk's and ``fts_weight_cross_lang`` (near zero)
if not. That way FTS5 only breaks ties between vector results when it can
contribute a literal match, and does not drag down ES->EN cross-language
retrieval, which is where the prototype measured that it hurt.

Each result returns ``cos``, the cosine between the query and the chunk, which
is what the hook uses for its threshold (the RRF score is not comparable across
queries; the cosine is, within the same model).

The vectors live in an in-memory snapshot while the text of each hit is read
from the live index by id. Row ids are reused after ``recall index --rebuild``,
so each fetched row is checked against the ``input_sha`` (hash of the embedded
text) recorded in the snapshot; a row that no longer matches is never returned
and the snapshot is reloaded on the next search (F-03).
"""

from __future__ import annotations

import itertools
import math
import sqlite3
import sys
import threading
import time
from collections.abc import Iterable, Sequence
from pathlib import Path

import numpy as np

from . import metadata, store
from .embedder import Embedder, format_query, index_identity, query_windows
from .textutil import detect_lang, fts_query

#: At most 6 windows per chunk when choosing the snippet.
SNIPPET_ROWS = 6


def _sha_key(input_sha: str | None) -> int:
    """First 64 bits of a chunk's ``input_sha`` (hex), to check rows cheaply."""
    try:
        return int((input_sha or "")[:16], 16)
    except ValueError:
        return 0


_STOP = set(
    """
the and for with that this from are was were has have had not but can its into over than then them they
their there what when where which while who why how about also been being does each more most other some
such only very will would should could may might must your you our out use used using
que los las del por para con una uno unos unas como mas pero sus son esta este estos estas ese esa eso
entre sobre cuando donde cual cuales quien hay ser fue han muy sin sus tambien porque segun libro libros
dime explica explicame dame quiero puedes hacer hace tiene tienen cosa cosas forma manera
""".split()
)


def _norm(s: str) -> str:
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c))


def query_terms(queries: Sequence[str]) -> dict[str, float]:
    """Query terms (without accents or stopwords) with their weight.

    Longer ones weigh more (more specific); bigrams weigh double because a
    compound term ("error budget") is what best locates the paragraph.
    """
    import re

    out: dict[str, float] = {}
    for q in queries:
        words = [w for w in re.findall(r"[a-z0-9][a-z0-9\-]+", _norm(q)) if w not in _STOP]
        for w in words:
            if len(w) >= 3 or w.isupper():
                out[w] = max(out.get(w, 0.0), min(len(w), 10) / 10)
        for a, b in itertools.pairwise(words):
            out[f"{a} {b}"] = 2.0
    return out


def window_score(window: str, terms: dict[str, float]) -> tuple[int, float]:
    """(distinct terms present, sum of weights) of ``terms`` in ``window``."""
    import re

    text = " ".join(re.findall(r"[a-z0-9][a-z0-9\-]+", _norm(window)))
    padded = f" {text} "
    hit = [w for w in terms if f" {w} " in padded or (" " not in w and len(w) >= 6 and w in text)]
    return len(hit), sum(terms[w] for w in hit)


def split_windows(text: str, width: int, max_windows: int = SNIPPET_ROWS) -> list[str]:
    """Split ``text`` into contiguous windows of ~``width`` characters by sentence.

    Cuts at the end of a sentence or line; a sentence longer than ``width`` is
    cut bluntly. If there are more than ``max_windows``, it widens the windows.
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


def make_searcher(cfg: dict, db: Path | None = None) -> Searcher:
    """Searcher for the configured index (query model + chunking from ``cfg``)."""
    from . import config
    from .embedder import from_config

    pages = cfg.get("pages_file") or (config.home() / "pages.sqlite3")
    meta_file = Path(cfg.get("metadata_file") or metadata.DEFAULT_FILE)
    return Searcher(
        db or config.db_path(),
        from_config(cfg, "query"),
        cfg["search"],
        chunking=cfg.get("chunking"),
        meta_file=meta_file,
        pages=Path(pages),
    )


class Searcher:
    """Index loaded in memory + query embedder. Thread-safe."""

    def __init__(
        self,
        db: Path,
        embedder: Embedder,
        search_cfg: dict | None = None,
        chunking: dict | None = None,
        meta: dict[str, dict] | None = None,
        pages: Path | None = None,
        meta_file: Path | None = None,
    ) -> None:
        self.db = db
        self.emb = embedder
        self.identity = index_identity(embedder.identity, chunking)
        self.meta = metadata.normalize(meta or {})
        # With ``meta_file`` the metadata is re-read when the file changes. The
        # service lives for days: without this, a book added after it started
        # passed no topic/year filter until it was restarted (2026-11-07).
        self._meta_file = meta_file
        self._meta_stamp: tuple[int, int] | None = None  # read on the first search
        #: Why the book metadata is not available (missing or unreadable file),
        #: or None. Without metadata every topic/year filter returns nothing, so
        #: this is reported instead of looking like "no matching books" (F-10).
        self.meta_error: str | None = None
        # Line -> PDF page map (tools/build_pages.py). Optional: without the
        # file, results carry no page and are cited by chapter.
        self._pages: sqlite3.Connection | None = None
        if pages is not None and Path(pages).is_file():
            try:
                self._pages = store.open_ro(Path(pages))
                self._pages.execute("select 1 from pages limit 1")
            except (sqlite3.Error, OSError):
                self._pages = None
        self.cfg = {
            "rrf_k": 60,
            "vec_weight": 1.0,
            "fts_weight_same_lang": 0.25,
            "fts_weight_cross_lang": 0.02,
            "candidates": 100,
            # 0 = the whole query in one call. See embedder.query_windows.
            "query_window_tokens": 0,
            "query_max_windows": 6,
            "query_window_overlap": 5,
        }
        self.cfg.update(search_cfg or {})
        self._lock = threading.RLock()
        self._con: sqlite3.Connection | None = None
        self._gen = -1
        self._loaded_at = 0.0
        # During a `recall index` every commit bumps the generation; reloading the
        # full matrix on every query would blow the hook's budget.
        self.min_reload_s = float(self.cfg.get("min_reload_s", 30.0))
        self.background_reload = False
        self._loading = False
        #: Set when a background reload finds an index built with another
        #: model or chunking (``store.ModelMismatchError``): from then on
        #: ``search`` raises it, because the old vectors can no longer be
        #: refreshed. Cleared by the next successful load. The service should
        #: report it in /health (not ok) so that the launcher notices.
        self.load_error: Exception | None = None
        self.ids = np.zeros(0, dtype=np.int64)
        self.sha = np.zeros(0, dtype=np.uint64)
        self.V = np.zeros((0, embedder.dim), dtype=np.float32)
        self.load()
        self._reload_meta()  # so that ``meta_error`` is known before the first search

    # -- loading -------------------------------------------------------------
    def _read(self, con: sqlite3.Connection) -> dict:
        """Read a full snapshot from the index (does not touch ``self``)."""
        store.check_identity(con, self.identity, writing=False)
        gen = store.generation(con)
        rows = con.execute("select id, collection, lang, vec, book_id, input_sha from chunks order by id").fetchall()
        d = self.emb.dim
        # A blob of the wrong length (truncated file, half-done write) no longer
        # aborts the whole load: the row is skipped with a warning (AR-15).
        good = [r for r in rows if isinstance(r[3], bytes) and len(r[3]) == 4 * d]
        if len(good) != len(rows):
            print(
                f"recall: skipped {len(rows) - len(good)} fragment(s) with an invalid vector; "
                "check the index with tools/verify_index.py",
                file=sys.stderr,
            )
            rows = good
        n = len(rows)
        V = np.empty((n, d), dtype=np.float32)
        for i, r in enumerate(rows):
            V[i] = np.frombuffer(r[3], dtype=np.float32, count=d)
        ids = np.fromiter((r[0] for r in rows), dtype=np.int64, count=n)
        return {
            "V": V,
            "ids": ids,
            "gen": gen,
            "coll": np.array([r[1] for r in rows], dtype=object),
            "lang": np.array([r[2] for r in rows], dtype=object),
            "book": np.array([r[4] for r in rows], dtype=object),
            "sha": np.fromiter((_sha_key(r[5]) for r in rows), dtype=np.uint64, count=n),
            "pos": {int(x): i for i, x in enumerate(ids)},
        }

    def _install(self, snap: dict) -> None:
        with self._lock:
            self.V, self.ids, self.coll, self.lang, self.pos, self.book, self.sha = (
                snap["V"],
                snap["ids"],
                snap["coll"],
                snap["lang"],
                snap["pos"],
                snap["book"],
                snap["sha"],
            )
            self._gen = snap["gen"]
            self._loaded_at = time.monotonic()
            self.load_error = None

    def load(self) -> None:
        """Synchronous (re)load of ids, collections, languages and vectors."""
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
        except store.ModelMismatchError as e:
            # The index now holds another model's (or chunking's) vectors. Keeping
            # the old snapshot would serve it forever while looking healthy.
            print(f"recall: index reload refused: {e}", file=sys.stderr)
            with self._lock:
                self.load_error = e
                self._loaded_at = time.monotonic()
        except Exception as e:  # e.g. the index is being rewritten: retry later
            print(f"recall: index reload failed, retrying later: {type(e).__name__}: {e}", file=sys.stderr)
            with self._lock:
                self._loaded_at = time.monotonic()
        finally:
            self._loading = False

    def refresh(self, force: bool = False, background: bool | None = None) -> bool:
        """Reload if another process committed writes.

        At most every ``min_reload_s`` (unless ``force``). With ``background``
        (what the service uses) the new snapshot is read on another thread and
        another connection, and searches keep using the old one until the swap:
        with ~100k chunks reading the matrix takes seconds and the hook has 1.5 s.
        Returns True if it reloaded or started reloading.
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
        """Number of loaded chunks."""
        return len(self.ids)

    @property
    def generation(self) -> int:
        """Index generation loaded in memory."""
        return self._gen

    def _page(self, path: str, line: int) -> dict:
        """Printed page (``page``), PDF page (``pdf_page``) and whether it is exact."""
        if self._pages is None:
            return {}
        try:
            r = self._pages.execute(
                "select label, page, exact from pages where path=? and line_from<=? order by line_from desc limit 1",
                (path, int(line)),
            ).fetchone()
        except sqlite3.Error:
            return {}
        return {"page": r[0], "pdf_page": r[1], "page_exact": bool(r[2])} if r else {}

    def _reload_meta(self) -> None:
        """Re-read ``meta_file`` if it changed (mtime/size); one stat per query."""
        if self._meta_file is None:
            return
        try:
            st = self._meta_file.stat()
        except OSError:
            # No file: the metadata already loaded is kept.
            self._meta_problem(None if self.meta else f"book metadata file not found: {self._meta_file}")
            return
        stamp = (st.st_mtime_ns, st.st_size)
        if stamp == self._meta_stamp:
            return
        meta = metadata.load(self._meta_file)
        with self._lock:
            if meta or self._meta_stamp is None:
                self.meta = meta
            self._meta_stamp = stamp
        self._meta_problem(
            None if self.meta else f"book metadata file is unreadable or has no valid entries: {self._meta_file}"
        )

    def _meta_problem(self, msg: str | None) -> None:
        """Record (and log once to stderr) why filters cannot work."""
        if msg and msg != self.meta_error:
            print(f"recall: {msg}; topic/year filters will match no book", file=sys.stderr)
        self.meta_error = msg

    # -- search ----------------------------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        """Read connection; ``load()`` opens it in the constructor."""
        if self._con is None:
            raise RuntimeError("index not loaded")
        return self._con

    def _fts(self, q: str, n: int, allowed: set[str] | None) -> list[int]:
        expr = fts_query(q)
        if not expr:
            return []
        con = self._conn()
        rows = con.execute(
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
        deadline: float | None = None,
    ) -> list[dict]:
        """Search one or more phrasings (e.g. ES and EN) and fuse them.

        With ``query_window_tokens`` in the config, a longer query is split
        into windows that are searched as separate phrasings. ``deadline``
        (``time.perf_counter()``) stops embedding windows once passed; the
        first one is always embedded.

        With several queries, each chunk keeps its best score and its best
        cosine across all of them. Returns up to ``k`` dicts deduplicated by
        (file, section), sorted by score. ``filters`` restricts by book
        metadata (see :mod:`recall.metadata`); a malformed one raises
        :class:`recall.metadata.FilterError`.
        """
        qs = [queries] if isinstance(queries, str) else [q for q in queries if q and q.strip()]
        qs = [q.strip()[:2000] for q in qs if q.strip()]
        if not qs:
            return []
        k = max(1, min(int(k), 50))
        allowed = set(collections) if collections else None
        self._reload_meta()
        books = metadata.allowed_books(self.meta, metadata.validate(filters))
        if books is not None and not books:
            return []
        self.refresh()
        with self._lock:
            if self.load_error is not None:
                raise self.load_error
            V, ids, coll, lang, pos, book, sha = self.V, self.ids, self.coll, self.lang, self.pos, self.book, self.sha
        if not len(ids):
            return []
        # One at a time: on DirectML a batch of 2-4 queries is a new tensor shape
        # (seconds of compilation); batch-1 shapes are warmed up by the service.
        full = qs
        wt, wn = int(self.cfg["query_window_tokens"]), int(self.cfg["query_max_windows"])
        ov = int(self.cfg["query_window_overlap"])
        # (index of the original query, window), interleaved across queries so
        # that the cap or the deadline does not leave one (e.g. the EN one) with none.
        per = [query_windows(self.emb, q, wt, wn, ov) for q in full]
        wins = [(g, ws[i]) for i in range(max(map(len, per))) for g, ws in enumerate(per) if i < len(ws)]
        wins = wins[: max(wn, len(full))]
        vecs: list[np.ndarray] = []
        for _g, w in wins:
            if vecs and deadline is not None and time.perf_counter() > deadline:
                break
            vecs.append(self.emb.embed([format_query(self.emb, w)], "query"))
        wins = wins[: len(vecs)]
        qv = np.vstack(vecs)
        with self._lock:
            cos_all = V @ qv.T  # n x windows
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
            # Each window adds its RRF (vector + FTS) to its original query, and
            # they are summed: what appears in several windows of the same prompt
            # wins (measured 2026-11-06: with "max", a window of pure context weighed
            # as much as the one carrying the topic). Across original queries (e.g.
            # ES and EN) each fragment keeps its best score. Without windows there
            # is one per query and this is the usual RRF.
            group: list[dict[int, float]] = [{} for _ in full]
            for wi, (g, w) in enumerate(wins):
                qlang = detect_lang(w)
                s = cos_all[:, wi].copy()
                if mask is not None:
                    s[~mask] = -np.inf
                m = min(ncand, len(s))
                top = np.argpartition(-s, m - 1)[:m]
                top = top[np.argsort(-s[top])]
                acc = group[g]
                for r, p in enumerate(top):
                    if not np.isfinite(s[p]):
                        continue
                    acc[int(p)] = acc.get(int(p), 0.0) + self.cfg["vec_weight"] / (kk + r + 1)
                    via.setdefault(int(p), set()).add("vec")
                for r, rid in enumerate(self._fts(w, ncand, allowed)):
                    p = pos.get(rid)
                    if p is None or (mask is not None and not mask[p]):
                        continue
                    same = qlang != "und" and lang[p] == qlang
                    fw = self.cfg["fts_weight_same_lang"] if same else self.cfg["fts_weight_cross_lang"]
                    acc[p] = acc.get(p, 0.0) + fw / (kk + r + 1)
                    via.setdefault(p, set()).add("fts")
            for score in group:
                for p, v in score.items():
                    if v > best_score.get(p, -1.0):
                        best_score[p] = v
            ranked = sorted(best_score, key=lambda p: (-best_score[p], -best_cos[p]))
            stale = False
            out: list[dict] = []
            seen: set[tuple[str, str]] = set()
            for p in ranked:
                rid = int(ids[p])
                row = (
                    self._conn()
                    .execute(
                        "select path, collection, book_id, book, chapter, section, line_start, line_end, lang, text, "
                        "input_sha from chunks where id=?",
                        (rid,),
                    )
                    .fetchone()
                )
                if row is None or _sha_key(row[10]) != int(sha[p]):
                    # The id now belongs to another chunk (or to none): the index
                    # was rewritten after this snapshot. Never pair this vector
                    # with that text; reload right after this search.
                    stale = True
                    continue
                if not math.isfinite(float(best_cos[p])):
                    continue  # corrupt vector (NaN/inf): never returned
                key = (row[0].lower(), row[5])
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    {
                        "id": rid,
                        "path": row[0],
                        "collection": row[1],
                        "book_id": row[2],
                        "book": row[3],
                        "chapter": row[4],
                        "section": row[5],
                        "line_start": row[6],
                        "line_end": row[7],
                        "lang": row[8],
                        "text": row[9],
                        "cos": round(float(best_cos[p]), 4),
                        "score": round(best_score[p], 6),
                        "via": sorted(via.get(p, ())),
                        **metadata.describe(self.meta.get(row[2])),
                        **self._page(row[0], row[6]),
                    }
                )
                if len(out) >= k:
                    break
        if stale:
            self.refresh(force=True)
        if snippet_chars:
            self._add_snippets(out, full, int(snippet_chars), int(snippet_top))
        return out

    def _add_snippets(self, hits: list[dict], queries: list[str], width: int, top: int) -> None:
        """``snippet``: the ~``width``-character window sharing the most terms with the query.

        Only for the first ``top`` distinct files (the ones the hook can
        inject) and only if the chunk is longer than ``width``. With large
        chunks, the first ``width`` characters are often not the relevant
        part.

        By terms and not by embeddings: re-embedding 6 windows per result
        with embeddinggemma-2 cost ~2 s per result on DirectML (measured
        2026-11-05), outside the hook's budget. Technical terms
        (bounded context, SLO, error budget) are usually the same in Spanish and
        English, which is what makes cross-language matching work. With no
        matches, the first window. ``snippet_hits`` = distinct matching terms.
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
