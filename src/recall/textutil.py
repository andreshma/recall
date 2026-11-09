"""Dependency-free text utilities: language, FTS5 tokens, hashes."""

from __future__ import annotations

import hashlib
import re

STOP_ES = frozenset(
    """a al algo algunos ante antes como con contra cual cuales cuando de del desde donde
    dos el ella ellos en entre era es esa ese eso esta estas este esto estos fue ha hay
    la las le les lo los mas más me mi muy no nos o otra otro para pero por porque que
    qué quien se sea ser si sí sin sobre son su sus también tiene todo tu un una uno
    unos y ya cómo cuál dónde por qué explícame diferencia entre usar cuándo""".split()
)
STOP_EN = frozenset(
    """a about after all also an and any are as at be been but by can could do does for
    from had has have how i if in into is it its just more most not of on or other our
    out should so some than that the their them then there these they this those to up
    use used using was we were what when where which while who why will with would you
    your between vs versus""".split()
)
STOP = STOP_ES | STOP_EN

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_ES_MARKS = re.compile(r"[ñáéíóú¿¡]", re.I)


def detect_lang(text: str) -> str:
    """Approximate language ("es", "en" or "und") by counting stopwords.

    Good enough for its use: deciding whether FTS5 (literal matching) can
    help, which only happens when query and document share a language.
    Accents and ñ count as evidence of Spanish because English technical
    terms dominate many short Spanish queries.
    """
    words = [w.lower() for w in _WORD.findall(text or "")[:400]]
    if not words:
        return "und"
    es = sum(w in STOP_ES for w in words) + 2 * len(_ES_MARKS.findall(text[:2000]))
    en = sum(w in STOP_EN for w in words)
    if es == en:
        return "und"
    return "es" if es > en else "en"


def fts_query(text: str, max_terms: int = 24) -> str | None:
    """FTS5 expression ``"t1" OR "t2" ...`` with the useful terms of the text.

    Each term is quoted so that no character of the prompt is interpreted
    as an FTS5 operator (``NEAR``, ``-``, ``*``, parentheses).
    """
    seen: list[str] = []
    for w in _WORD.findall((text or "").lower()):
        if len(w) > 1 and w not in STOP and w not in seen:
            seen.append(w)
        if len(seen) >= max_terms:
            break
    if not seen:
        return None
    return " OR ".join('"' + w.replace('"', "") + '"' for w in seen)


def sha256_text(text: str) -> str:
    """Hex SHA-256 of a UTF-8 text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str, bufsize: int = 1 << 20) -> str:
    """Hex SHA-256 of a file, read in blocks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(bufsize)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def squash(text: str) -> str:
    """Collapse runs of whitespace into a single space."""
    return re.sub(r"\s+", " ", text or "").strip()


#: ``<``/``>`` and the characters that look like them (fullwidth, angle
#: quotation marks, CJK and math angle brackets...) -> the entities ``&lt;``/``&gt;``:
#: they cannot form a tag yet keep the meaning of ``a < b`` or ``List<T>``
#: (ADV-11). ``recall_hook.TAG_CHARS`` is the same table.
TAG_CHARS = str.maketrans(
    {**dict.fromkeys("<\uff1c\ufe64\u2039\u2329\u27e8\u3008\u300a\u276c\u276e\u2770", "&lt;"),
     **dict.fromkeys(">\uff1e\ufe65\u203a\u232a\u27e9\u3009\u300b\u276d\u276f\u2771", "&gt;")}
)  # fmt: skip


def defang(value: object) -> str:
    """Text safe inside a data block: nothing that is or looks like ``<``/``>``.

    No index field can then open or close a tag or a ``<<<...>>>`` delimiter
    (with spaces, overlapping, or with lookalike characters).
    """
    return str(value).translate(TAG_CHARS)


def block_field(value: object) -> str:
    """A value on a single line and defanged, for a data block header (ADV-5/ADV-7)."""
    return defang(squash(str(value)))
