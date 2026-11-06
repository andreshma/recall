"""Chunking de los Markdown de libros por seccion/encabezado.

Formato de entrada (lo produce ``Books TI\\_tools\\book2md.py``): un archivo por
capitulo, primera linea ``# <Libro> — <Capitulo>`` y una linea ``> Source: ...``
que se descarta. El resto son encabezados ``#..######`` y parrafos.

Reglas:

* Un chunk nunca cruza un encabezado salvo que lo acumulado sea corto
  (< ``min_chars``, 450) y solo no diria gran cosa; entonces se acumula con la
  siguiente y el chunk conserva la seccion donde empezo.
* Tamano objetivo ``target`` (~900) y techo ``max_chars`` (~1000). Un parrafo
  mas largo que el techo se parte por oraciones y, si hace falta, a la fuerza.
* Solapamiento: cada chunk de la misma seccion empieza con la cola (~150
  caracteres, cortada en limite de palabra) del anterior, para que una idea
  partida en el borde siga siendo recuperable.
* Cada chunk lleva libro, capitulo, seccion y lineas (1-based, inclusivas) del
  archivo de origen, para que la cita sea verificable.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

from .textutil import detect_lang

# Archivos que no son contenido: portada, indice, creditos... Se compara el
# nombre COMPLETO (sin el prefijo numerico), no una subcadena: el prototipo
# usaba ``search`` sin anclar y se comia capitulos como "disaster-recovery"
# (contiene "cover") o "service-discovery" y "anatomy-of-an-index".
SKIP_STEM = re.compile(
    r"^(?:front-matter|cover(?:-page)?|on-the-cover|about-the-cover(?:-illustration)?|"
    r".*back-cover|acknowledg(?:e)?ments?|brief-contents|contents(?:-at-a-glance)?|"
    r"contents-.*|table-of-contents?(?:-.*)?|copyright.*|list-of-figures|figures(?:-tables)?|"
    r"equations|dedications?|colophon|index(?:-[a-z])?|x-index)$",
    re.I,
)

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_SENT = re.compile(r"(?<=[.!?:;])\s+")
# Encabezados que NO abren seccion: pies de figura/tabla/listado y los avisos
# de nivel 6 (Tip, Note, Warning) que los convertidores emiten como ``######``.
_INLINE_HEAD = re.compile(r"^(figure|figura|table|tabla|example|ejemplo|listing|listado)\s*[\dA-Z]", re.I)


@dataclass(frozen=True)
class Chunk:
    """Fragmento indexable con su procedencia."""

    book: str
    chapter: str
    section: str
    line_start: int
    line_end: int
    text: str
    lang: str


def should_skip(filename: str) -> bool:
    """True si el archivo es front/back matter y no debe indexarse."""
    stem = os.path.splitext(os.path.basename(filename))[0]
    stem = re.sub(r"^\d+[-_]", "", stem)
    return bool(SKIP_STEM.match(stem))


def _clean_heading(h: str) -> str:
    return h.strip().strip("_*` ").strip()


def _split_long(text: str, max_chars: int) -> list[str]:
    """Parte un parrafo largo por oraciones; si una oracion excede, a la fuerza."""
    out, cur = [], ""
    for s in _SENT.split(text):
        while len(s) > max_chars:
            cut = s.rfind(" ", 0, max_chars)
            cut = cut if cut > max_chars // 2 else max_chars
            if cur:
                out.append(cur)
                cur = ""
            out.append(s[:cut].strip())
            s = s[cut:].strip()
        if cur and len(cur) + 1 + len(s) > max_chars:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip() if cur else s
    if cur:
        out.append(cur)
    return [p for p in out if p]


def _tail(text: str, n: int) -> str:
    """Ultimos ~n caracteres de ``text`` empezando en limite de palabra."""
    if len(text) <= n:
        return text
    t = text[-n:]
    sp = t.find(" ")
    return t[sp + 1 :] if 0 <= sp < n // 2 else t


def parse_title(first_line: str, fallback_book: str) -> tuple[str, str]:
    """``# Libro — Capitulo`` -> (libro, capitulo). Sin separador: (fallback, linea)."""
    m = _HEADING.match(first_line.strip())
    title = _clean_heading(m.group(2)) if m else first_line.strip()
    if " — " in title:
        book, chapter = title.split(" — ", 1)
        return book.strip(), chapter.strip()
    return fallback_book, title or fallback_book


def chunk_markdown(
    text: str,
    fallback_book: str,
    *,
    target: int = 900,
    max_chars: int = 1000,
    min_chars: int = 450,
    overlap: int = 150,
) -> list[Chunk]:
    """Divide un capitulo Markdown en :class:`Chunk` segun las reglas del modulo."""
    lines = text.splitlines()
    if not lines:
        return []
    book, chapter = parse_title(lines[0], fallback_book)
    first_is_title = bool(_HEADING.match(lines[0].strip()))

    # 1) unidades: (seccion, linea_ini, linea_fin, texto); parrafos separados por
    #    lineas en blanco, encabezados como unidad propia que abre seccion.
    units: list[tuple[str, int, int, str, bool]] = []  # ..., es_encabezado
    section = chapter
    para: list[str] = []
    p_start = 0

    def flush_para(end_line: int) -> None:
        nonlocal para
        if para:
            body = " ".join(s.strip() for s in para).strip()
            if body:
                units.append((section, p_start, end_line, body, False))
        para = []

    for i, raw in enumerate(lines, start=1):
        if i == 1 and first_is_title:
            continue
        line = raw.rstrip()
        if line.startswith("> Source:"):
            continue
        h = _HEADING.match(line)
        if h:
            flush_para(i - 1)
            name = _clean_heading(h.group(2))
            inline = len(h.group(1)) == 6 or bool(_INLINE_HEAD.match(name))
            if inline:
                if name:
                    if not para:
                        p_start = i
                    para.append(name + ".")
                continue
            if name and name != chapter:
                section = name
            if name:
                units.append((section, i, i, name, True))
            continue
        if not line.strip():
            flush_para(i - 1)
            continue
        if not para:
            p_start = i
        para.append(line)
    flush_para(len(lines))

    # 2) empaquetado en chunks
    chunks: list[Chunk] = []
    cur: list[str] = []
    cur_len = 0
    cur_sec = chapter
    cur_start = cur_end = 0
    has_body = False
    prev_tail = ""

    def emit() -> None:
        nonlocal cur, cur_len, has_body, prev_tail
        body = " ".join(cur).strip()
        if has_body and len(body) >= 100:
            chunks.append(Chunk(book, chapter, cur_sec, cur_start, cur_end, body, detect_lang(body)))
            prev_tail = _tail(body, overlap)
        cur, cur_len, has_body = [], 0, False

    for sec, ls, le, body, is_head in units:
        pieces = [body] if len(body) <= max_chars else _split_long(body, max_chars)
        for piece in pieces:
            new_section = sec != cur_sec
            if cur and new_section and cur_len >= min_chars and has_body:
                emit()
                prev_tail = ""
            elif cur and cur_len + 1 + len(piece) > max_chars:
                same = not new_section
                emit()
                if not same:
                    prev_tail = ""
            if not cur:
                cur_sec, cur_start = sec, ls
                if prev_tail and not is_head and len(prev_tail) + 1 + len(piece) <= max_chars:
                    cur.append(prev_tail)
                    cur_len = len(prev_tail)
                prev_tail = ""
            cur.append(piece)
            cur_len += len(piece) + 1
            cur_end = le
            has_body = has_body or not is_head
            if cur_len >= target and not is_head:
                emit()
    emit()
    return chunks


def chunk_file(path: str, fallback_book: str, **kw) -> list[Chunk]:
    """Lee ``path`` (UTF-8, errores reemplazados) y lo trocea."""
    with open(path, encoding="utf-8", errors="replace") as f:
        return chunk_markdown(f.read(), fallback_book, **kw)
