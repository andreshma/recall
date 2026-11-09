"""Chunking of book Markdown by section/heading.

Input format (any PDF/EPUB-to-Markdown converter can produce it; see
docs/USAGE.md §4): one UTF-8 file per chapter, first line
``# <Book> — <Chapter>`` and an optional ``> Source: ...`` line that is
discarded. The rest are ``#..######`` ATX headings and paragraphs separated by
blank lines.

Rules:

* A chunk never crosses a heading unless what has accumulated is short
  (< ``min_chars``, 450) and would say little on its own; then it is merged
  with the next one and the chunk keeps the section where it started.
* Target size ``target`` (~900) and ceiling ``max_chars`` (~1000). A paragraph
  longer than the ceiling is split by sentences and, if needed, by force.
* Overlap: each chunk of the same section starts with the tail (~150
  characters, cut at a word boundary) of the previous one, so that an idea
  split at the edge is still retrievable.
* Each chunk carries book, chapter, section and lines (1-based, inclusive) of
  the source file, so that the citation is verifiable.
* Text too short to stand on its own (< 100 characters), such as a short
  last section, is not dropped: it is appended to the previous chunk of the
  file when that still fits in ``max_chars``, otherwise it becomes a chunk of
  its own. Only a file whose whole text is that short (a part divider, a title
  page) yields no chunks, and a heading with no body is still not indexed.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace

from .textutil import detect_lang

# Files that are not content: cover, table of contents, credits... The FULL
# name (without the numeric prefix) is compared, not a substring: the prototype
# used an unanchored ``search`` and swallowed chapters like "disaster-recovery"
# (contains "cover") or "service-discovery" and "anatomy-of-an-index".
SKIP_STEM = re.compile(
    r"^(?:front-matter|cover(?:-page)?|on-the-cover|about-the-cover(?:-illustration)?|"
    r".*back-cover|acknowledg(?:e)?ments?|brief-contents|contents(?:-at-a-glance)?|"
    # A table of contents whose first entry leaked into the name, as some EPUB
    # converters do ("contents-foreword-by-...-07-foreword-1-xhtml"). A bare
    # "contents-.*" / "copyright.*" also swallowed chapters such as
    # "contents-of-a-container-image" or "copyright-law-for-developers".
    r"contents-.+-x?html?|table-of-contents?(?:-.*)?|"
    r"copyright(?:-page|-and-credits|-notice|-\d{4}(?:-.*)?)?|list-of-figures|figures(?:-tables)?|"
    r"equations|dedications?|colophon|index(?:-[a-z])?|x-index)$",
    re.I,
)

_SENT = re.compile(r"(?<=[.!?:;])\s+")
# Headings that do NOT open a section: figure/table/listing captions and the
# level-6 callouts (Tip, Note, Warning) that converters emit as ``######``.
_INLINE_HEAD = re.compile(r"^(figure|figura|table|tabla|example|ejemplo|listing|listado)\s*[\dA-Z]", re.I)


@dataclass(frozen=True)
class Chunk:
    """Indexable fragment with its provenance."""

    book: str
    chapter: str
    section: str
    line_start: int
    line_end: int
    text: str
    lang: str


def should_skip(filename: str) -> bool:
    """True if the file is front/back matter and must not be indexed."""
    stem = os.path.splitext(os.path.basename(filename))[0]
    stem = re.sub(r"^\d+[-_]", "", stem)
    return bool(SKIP_STEM.match(stem))


def parse_heading(line: str) -> tuple[int, str] | None:
    """ATX heading -> (level, text), or None if ``line`` is not a heading.

    Parsed by hand instead of with a regex: the previous pattern
    ``^(#{1,6})\\s+(.+?)\\s*#*\\s*$`` backtracked quadratically on long runs of
    spaces, which PDF/EPUB converters emit (a heading with 4,000 spaces took
    minutes and stalled indexing). This is linear. A closing ``#`` sequence is
    removed only when whitespace precedes it (CommonMark), so "C#" and "F#"
    keep their hash; every other heading parses exactly as before.
    """
    n = len(line) - len(line.lstrip("#"))
    if not 1 <= n <= 6 or not line[n : n + 1].isspace():
        return None
    body = line[n:].strip()
    if not body:
        return None
    bare = body.rstrip("#")
    if not bare:
        body = "#"  # nothing but hashes: what the previous parser returned
    elif bare != body and bare[-1].isspace():
        body = bare.rstrip()
    return n, body


def _clean_heading(h: str) -> str:
    return h.strip().strip("_*` ").strip()


def _split_long(text: str, max_chars: int) -> list[str]:
    """Split a long paragraph by sentences; if a sentence is too long, by force."""
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
    """Last ~n characters of ``text``, starting at a word boundary."""
    if len(text) <= n:
        return text
    t = text[-n:]
    sp = t.find(" ")
    return t[sp + 1 :] if 0 <= sp < n // 2 else t


def parse_title(first_line: str, fallback_book: str) -> tuple[str, str]:
    """``# Book — Chapter`` -> (book, chapter). Without separator: (fallback, line)."""
    m = parse_heading(first_line.strip())
    title = _clean_heading(m[1]) if m else first_line.strip()
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
    """Split a Markdown chapter into :class:`Chunk` per the module's rules."""
    lines = text.splitlines()
    if not lines:
        return []
    book, chapter = parse_title(lines[0], fallback_book)
    first_is_title = parse_heading(lines[0].strip()) is not None

    # 1) units: (section, start_line, end_line, text); paragraphs separated by
    #    blank lines, headings as their own unit that opens a section.
    units: list[tuple[str, int, int, str, bool]] = []  # ..., is_heading
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
        h = parse_heading(line)
        if h:
            flush_para(i - 1)
            name = _clean_heading(h[1])
            inline = h[0] == 6 or bool(_INLINE_HEAD.match(name))
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

    # 2) packing into chunks
    chunks: list[Chunk] = []
    cur: list[str] = []
    cur_len = 0
    cur_sec = chapter
    cur_start = cur_end = 0
    has_body = False
    prev_tail = ""
    cur_overlap = False  # cur[0] is the previous chunk's tail

    def emit() -> None:
        nonlocal cur, cur_len, has_body, prev_tail, cur_overlap
        body = " ".join(cur).strip()
        if has_body and len(body) >= 100:
            chunks.append(Chunk(book, chapter, cur_sec, cur_start, cur_end, body, detect_lang(body)))
            prev_tail = _tail(body, overlap)
        elif has_body:
            # Too short to stand alone (a short last section or paragraph):
            # append it to the previous chunk if it fits, otherwise keep it as
            # its own chunk. It used to be dropped, losing that text entirely.
            own = " ".join(cur[1:] if cur_overlap else cur).strip()
            last = chunks[-1] if chunks else None
            if last is not None and len(last.text) + 1 + len(own) <= max_chars:
                text = f"{last.text} {own}"
                chunks[-1] = replace(last, line_end=cur_end, text=text, lang=detect_lang(text))
            else:
                text = body
                chunks.append(Chunk(book, chapter, cur_sec, cur_start, cur_end, text, detect_lang(text)))
            prev_tail = _tail(text, overlap)
        cur, cur_len, has_body, cur_overlap = [], 0, False, False

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
                    cur_overlap = True
                prev_tail = ""
            cur.append(piece)
            cur_len += len(piece) + 1
            cur_end = le
            has_body = has_body or not is_head
            if cur_len >= target and not is_head:
                emit()
    emit()
    if all(len(c.text) < 100 for c in chunks):
        # The whole file is under 100 characters of text: in the libraries
        # measured these are part dividers and title pages ("Part II ... This
        # page intentionally left blank"), not content worth a vector.
        return []
    return chunks


def chunk_file(path: str, fallback_book: str, **kw) -> list[Chunk]:
    """Read ``path`` (UTF-8, errors replaced) and chunk it."""
    with open(path, encoding="utf-8", errors="replace") as f:
        return chunk_markdown(f.read(), fallback_book, **kw)
