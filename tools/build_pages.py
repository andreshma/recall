"""Mapa linea-del-Markdown -> pagina del PDF, para citar con pagina.

Se corre con el Python de ``Books TI\\_tools\\.venv`` (tiene PyMuPDF); recall no
depende de PyMuPDF. Escribe ``pages.sqlite3`` (por defecto en
``%LOCALAPPDATA%\\recall``), que el buscador consulta si existe. Es
independiente del indice: sirve para cualquier troceo o modelo.

    "<tools-venv>\\python.exe" -I tools\\build_pages.py [--out pages.sqlite3] [--only slug ...]

Como funciona (por libro en PDF):

1. Texto de cada pagina del PDF, normalizado (minusculas, solo letras y
   digitos) y concatenado, con el desplazamiento donde empieza cada pagina.
2. Cada parrafo de los ``.md`` del libro (en orden de capitulo) se busca por sus
   primeros ~60 caracteres normalizados, avanzando un cursor (el libro se lee
   en orden). Si no aparece cerca del cursor se busca en todo el libro; si
   tampoco, con una clave mas corta; si nada, hereda la pagina del parrafo
   anterior y queda marcado como inferido.
3. Se guardan rangos ``(path, line_from, line_to, page, label, exact)``.

``page`` es la pagina del PDF (1 = primera del archivo). ``label`` es la
numeracion impresa si el PDF la define (p. ej. "179" o "xix"); es la que se
cita. Los EPUB no tienen paginas fijas: no entran y se citan por capitulo.
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

BOOKS = Path.home() / "Downloads" / "Books TI"
MD = BOOKS / "_md"
META = Path(__file__).resolve().parents[1] / "data" / "books_meta.json"
_NORM = re.compile(r"[^0-9a-z]+")
_HEADING = re.compile(r"^#{1,6}\s")
KEY = 60
SHORT = 30
WINDOW = 400_000  # caracteres normalizados por delante del cursor (~100 paginas)


def norm(text: str) -> str:
    return _NORM.sub("", text.lower())


def paragraphs(path: Path):
    """(linea_ini, linea_fin, texto) de cada parrafo de cuerpo (sin encabezados)."""
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
    for i, page in enumerate(doc):
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


def map_book(slug: str, pdf: Path):
    text, offsets, labels = book_pages(pdf)
    files = sorted(p for p in (MD / slug).glob("*.md"))
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
    """Funde parrafos consecutivos del mismo archivo y pagina en un rango."""
    out = []
    for r in rows:
        if out and out[-1][0] == r[0] and out[-1][3] == r[3] and out[-1][5] == r[5]:
            p = out[-1]
            out[-1] = (p[0], p[1], r[2], p[3], p[4], p[5])
        else:
            out.append(r)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "recall"
    ap.add_argument("--out", default=str(base / "pages.sqlite3"))
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args()
    meta = json.loads(META.read_text(encoding="utf-8"))
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
    for slug, m in sorted(meta.items()):
        if a.only and slug not in a.only:
            continue
        if m.get("format") != "pdf" or not m.get("source_file"):
            continue
        pdf = BOOKS / m["source_file"]
        if not pdf.is_file():
            print(f"{slug}: no existe {pdf}", file=sys.stderr)
            continue
        t = time.perf_counter()
        try:
            rows, ex, tot = map_book(slug, pdf)
        except Exception as e:  # un PDF roto no detiene el resto
            print(f"{slug}: error {type(e).__name__}: {e}", file=sys.stderr)
            continue
        rows = compress(rows)
        con.executemany("insert into pages values(?,?,?,?,?,?)", rows)
        dt = time.perf_counter() - t
        con.execute("insert into books values(?,?,?,?,?)", (slug, str(pdf), tot, ex, dt))
        con.commit()
        summary.append((slug, tot, ex))
        print(f"{slug}: {ex}/{tot} parrafos ubicados exactos ({ex / max(tot, 1):.0%}), {dt:.0f}s", flush=True)
    con.close()
    os.replace(tmp, a.out)
    tot = sum(s[1] for s in summary)
    ex = sum(s[2] for s in summary)
    print(f"TOTAL {len(summary)} libros PDF, {ex}/{tot} parrafos exactos ({ex / max(tot, 1):.1%}) -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
