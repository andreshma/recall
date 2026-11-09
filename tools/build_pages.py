"""Markdown-line -> PDF-page map, for citations with page numbers.

Run with any Python that has PyMuPDF (``pip install pymupdf`` in a separate
venv); recall itself does not depend on it. Writes ``pages.sqlite3`` (by
default in ``%LOCALAPPDATA%\\recall``), which the searcher reads if it exists.
It is independent of the index: it works for any chunking or model.

    "<venv-with-pymupdf>\\python.exe" -I tools\\build_pages.py
        [--sources data\\books_sources.local.json] [--pdf-dir DIR] [--md-root DIR]
        [--out pages.sqlite3] [--only book-id ...]

Where the PDFs live is local knowledge and never goes in the repo: it is read
from ``--sources`` (default ``data/books_sources.local.json``, git-ignored).
Its format is a JSON object mapping each book id (the book's folder under the
library root, the same key as in ``data/books_meta.json``) to its PDF::

    {
      "learning-ddd": "C:\\Users\\me\\books\\pdf\\learning-ddd.pdf",
      "site-reliability-engineering": "sre/site-reliability-engineering.pdf"
    }

Relative paths are resolved against ``--pdf-dir`` (default ``~/books``).
Entries that do not end in ``.pdf`` are ignored (EPUBs have no pages). The
Markdown of each book is read from ``<--md-root>/<book-id>/*.md`` (default
``~/books/md``, recall's default library root).

How it works (per PDF book):

1. Text of each PDF page, normalized (lowercase, only letters and digits)
   and concatenated, with the offset where each page starts.
2. Each paragraph of the book's ``.md`` files (in chapter order) is searched
   for by its first ~60 normalized characters, advancing a cursor (the book
   is read in order). If it is not near the cursor, the whole book is
   searched; failing that, a shorter key; failing everything, it inherits the
   previous paragraph's page and is marked as inferred.
3. Ranges ``(path, line_from, line_to, page, label, exact)`` are stored.

``page`` is the PDF page (1 = first page of the file). ``label`` is the
printed numbering if the PDF defines one (e.g. "179" or "xix"); that is the
one cited. EPUBs have no fixed pages: they are left out and cited by chapter.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

SOURCES = Path(__file__).resolve().parents[1] / "data" / "books_sources.local.json"
PDF_DIR = Path.home() / "books"
MD = Path.home() / "books" / "md"
_NORM = re.compile(r"[^0-9a-z]+")
_HEADING = re.compile(r"^#{1,6}\s")
KEY = 60
SHORT = 30
WINDOW = 400_000  # normalized characters ahead of the cursor (~100 pages)


def norm(text: str) -> str:
    return _NORM.sub("", text.lower())


def paragraphs(path: Path):
    """(start_line, end_line, text) of each body paragraph (no headings)."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start, buf = None, []
    for i, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or _HEADING.match(line) or line.startswith("> Source:"):
            if buf:
                yield start, i - 1, " ".join(buf)
            start, buf = None, []
            continue
        if start is None:
            start = i
        buf.append(line)
    if buf:
        yield start, len(lines), " ".join(buf)


def book_pages(pdf: Path):
    import pymupdf

    doc = pymupdf.open(str(pdf))
    offsets, parts, labels, pos = [], [], [], 0
    for _i, page in enumerate(doc):
        t = norm(page.get_text("text"))
        offsets.append(pos)
        parts.append(t)
        pos += len(t)
        try:
            labels.append(page.get_label() or "")
        except Exception:
            labels.append("")
    doc.close()
    return "".join(parts), offsets, labels


def page_at(offsets: list[int], pos: int) -> int:
    lo, hi = 0, len(offsets) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if offsets[mid] <= pos:
            lo = mid
        else:
            hi = mid - 1
    return lo


def load_sources(path: Path, pdf_dir: Path) -> dict[str, Path]:
    """``{book_id: pdf path}`` from the local sources map (format in the module docstring)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object {{book_id: pdf path}}")
    out = {}
    for slug, pdf in data.items():
        if isinstance(slug, str) and isinstance(pdf, str) and pdf.lower().endswith(".pdf"):
            p = Path(pdf).expanduser()
            out[slug] = p if p.is_absolute() else pdf_dir / p
    return out


def map_book(slug: str, pdf: Path, md_root: Path = MD):
    text, offsets, labels = book_pages(pdf)
    files = sorted(p for p in (md_root / slug).glob("*.md"))
    cursor, last_page = 0, None
    rows, n_exact, n_total = [], 0, 0
    for f in files:
        for ls, le, para in paragraphs(f):
            key = norm(para)
            if len(key) < 12:
                continue
            n_total += 1
            hit = -1
            for k in (key[:KEY], key[:SHORT]):
                if len(k) < 12:
                    continue
                hit = text.find(k, cursor, cursor + WINDOW)
                if hit < 0:
                    hit = text.find(k)
                if hit >= 0:
                    break
            if hit >= 0:
                pg = page_at(offsets, hit)
                cursor, last_page, exact = hit, pg, 1
                n_exact += 1
            elif last_page is not None:
                pg, exact = last_page, 0
            else:
                continue
            rows.append((str(f), ls, le, pg + 1, labels[pg] or str(pg + 1), exact))
    return rows, n_exact, n_total


def compress(rows):
    """Merge consecutive paragraphs of the same file and page into one range."""
    out = []
    for r in rows:
        if out and out[-1][0] == r[0] and out[-1][3] == r[3] and out[-1][5] == r[5]:
            p = out[-1]
            out[-1] = (p[0], p[1], r[2], p[3], p[4], p[5])
        else:
            out.append(r)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Map Markdown lines to PDF pages, for citations with page numbers.")
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "recall"
    ap.add_argument("--out", default=str(base / "pages.sqlite3"), help="output SQLite file")
    ap.add_argument("--only", nargs="*", help="only these book ids (keys of the sources map)")
    ap.add_argument("--sources", default=str(SOURCES), help="local JSON map {book_id: pdf path} (git-ignored)")
    ap.add_argument("--pdf-dir", default=str(PDF_DIR), help="base directory for relative PDF paths")
    ap.add_argument("--md-root", default=str(MD), help="library root holding <book-id>/*.md")
    a = ap.parse_args()
    if not Path(a.sources).is_file():
        print(f"no sources map at {a.sources}; its format is described in tools/build_pages.py", file=sys.stderr)
        return 2
    sources = load_sources(Path(a.sources), Path(a.pdf_dir).expanduser())
    md_root = Path(a.md_root).expanduser()
    tmp = a.out + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    con = sqlite3.connect(tmp)
    con.executescript("""
        create table pages(path text not null, line_from integer not null, line_to integer not null,
                           page integer not null, label text not null, exact integer not null);
        create index pages_path on pages(path, line_from);
        create table books(book_id text primary key, pdf text, paragraphs integer, exact integer, seconds real);
    """)
    summary = []
    for slug, pdf in sorted(sources.items()):
        if a.only and slug not in a.only:
            continue
        if not pdf.is_file():
            print(f"{slug}: missing {pdf}", file=sys.stderr)
            continue
        t = time.perf_counter()
        try:
            rows, ex, tot = map_book(slug, pdf, md_root)
        except Exception as e:  # a broken PDF does not stop the rest
            print(f"{slug}: error {type(e).__name__}: {e}", file=sys.stderr)
            continue
        rows = compress(rows)
        con.executemany("insert into pages values(?,?,?,?,?,?)", rows)
        dt = time.perf_counter() - t
        con.execute("insert into books values(?,?,?,?,?)", (slug, str(pdf), tot, ex, dt))
        con.commit()
        summary.append((slug, tot, ex))
        print(f"{slug}: {ex}/{tot} paragraphs located exactly ({ex / max(tot, 1):.0%}), {dt:.0f}s", flush=True)
    con.close()
    os.replace(tmp, a.out)
    tot = sum(s[1] for s in summary)
    ex = sum(s[2] for s in summary)
    print(f"TOTAL {len(summary)} PDF books, {ex}/{tot} paragraphs exact ({ex / max(tot, 1):.1%}) -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
