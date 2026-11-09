"""Per-book metadata (year, topics, format) and search filters.

``data/books_meta.json`` (in the repo; the wheel ships a copy as
``recall/data/books_meta.json``) maps ``book_id`` (the book's folder under a
library root) to ``title``, ``author``, ``year``, ``domains``, ``format``...
The year comes from that edition's copyright page; if there was no evidence it
is ``null`` (never guessed). The path can be changed with ``metadata_file`` in
``config.json``.

The file is data, not code, and may be edited by hand: :func:`load` keeps only
well-formed entries and coerces every field to the type the search expects, so
a bad entry cannot break searches (ADV-4).

Filters (all optional; with no filters nothing is restricted):

* ``domains``: list; a book passes if it has AT LEAST one of those topics.
* ``year_min`` / ``year_max``: inclusive. A book with no known year does NOT
  pass a year filter: if "since 2020" is requested, it cannot be claimed to
  match.

A missing or unreadable metadata file does not break search: without metadata
no filter can be applied and any filter returns zero books. That is reported
on stderr and in ``Searcher.meta_error`` so it is not mistaken for "no
matching books".
"""

from __future__ import annotations

import json
from pathlib import Path

#: Copy shipped inside the wheel (``pyproject.toml``, ``force-include``).
PACKAGED_FILE = Path(__file__).resolve().parent / "data" / "books_meta.json"
#: The file in a source checkout (or an editable install).
REPO_FILE = Path(__file__).resolve().parents[2] / "data" / "books_meta.json"
DEFAULT_FILE = PACKAGED_FILE if PACKAGED_FILE.is_file() else REPO_FILE

#: Free-text fields kept when they are strings (anything else becomes None).
_TEXT_FIELDS = ("title", "author", "format", "edition", "lang", "year_source")

#: Closed topic taxonomy (the same one the library was classified with), used by
#: books_meta.json and the search filters.
DOMAINS = (
    "architecture",
    "data",
    "security",
    "sre-observability",
    "devops-cicd",
    "cloud",
    "testing",
    "agile-management",
    "product",
    "ai-ml",
    "programming",
    "requirements-analysis",
    "ux-ui",
    "leadership-organization",
)


class FilterError(ValueError):
    """Malformed filter (unknown topic, non-integer year...)."""


def load(path: str | Path | None = None) -> dict[str, dict]:
    """``{book_id: meta}``; ``{}`` if the file does not exist or cannot be read.

    Every entry is normalized (see :func:`normalize_entry`); entries that are
    not objects are dropped.
    """
    p = Path(path) if path else DEFAULT_FILE
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, RecursionError):
        return {}
    return normalize(data)


def normalize(data: object) -> dict[str, dict]:
    """``{book_id: meta}`` keeping only object entries with string keys, normalized."""
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict] = {}
    for bid, m in data.items():
        entry = normalize_entry(m)
        if isinstance(bid, str) and entry is not None:
            out[bid] = entry
    return out


def normalize_entry(m: object) -> dict | None:
    """One book's metadata with safe types, or None if it is not an object.

    ``domains`` -> list of strings; ``year`` -> int in 1900..2100
    or None; the text fields -> str or None. Unknown keys are kept as they are
    (nothing reads them).
    """
    if not isinstance(m, dict):
        return None
    out = dict(m)
    doms = m.get("domains")
    if isinstance(doms, str):
        doms = [doms]
    out["domains"] = [d for d in doms if isinstance(d, str)] if isinstance(doms, list) else []
    y = m.get("year")
    out["year"] = y if isinstance(y, int) and not isinstance(y, bool) and 1900 <= y <= 2100 else None
    for k in _TEXT_FIELDS:
        if k in m and not isinstance(m[k], str):
            out[k] = None
    return out


def validate(filters: dict | None) -> dict:
    """Normalize ``filters``; raise :class:`FilterError` if anything is off."""
    if not filters:
        return {}
    if not isinstance(filters, dict):
        raise FilterError("filters must be an object")
    out: dict = {}
    unknown = set(filters) - {"domains", "year_min", "year_max"}
    if unknown:
        raise FilterError(f"unknown filters: {', '.join(sorted(unknown))}")
    doms = filters.get("domains")
    if doms:
        if isinstance(doms, str):
            doms = [doms]
        if not isinstance(doms, list) or not all(isinstance(d, str) for d in doms):
            raise FilterError("domains must be a list of strings")
        bad = [d for d in doms if d not in DOMAINS]
        if bad:
            raise FilterError(f"unknown domains: {', '.join(bad)} (valid: {', '.join(DOMAINS)})")
        out["domains"] = sorted(set(doms))
    for k in ("year_min", "year_max"):
        v = filters.get(k)
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, int) or not 1900 <= v <= 2100:
            raise FilterError(f"{k} must be an integer year")
        out[k] = v
    return out


def allowed_books(meta: dict[str, dict], filters: dict) -> set[str] | None:
    """``book_id`` values that pass ``filters`` (already validated); None = no restriction."""
    if not filters:
        return None
    doms = set(filters.get("domains") or ())
    lo, hi = filters.get("year_min"), filters.get("year_max")
    out = set()
    for bid, m in meta.items():
        if doms and not doms & set(m.get("domains") or ()):
            continue
        y = m.get("year")
        if (lo is not None or hi is not None) and not isinstance(y, int):
            continue
        if lo is not None and y < lo:
            continue
        if hi is not None and y > hi:
            continue
        out.add(bid)
    return out


def describe(m: dict | None) -> dict:
    """Metadata fields that accompany each result."""
    if not m:
        return {}
    return {k: m.get(k) for k in ("year", "domains", "format", "edition") if m.get(k) not in (None, [])}
