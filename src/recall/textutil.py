"""Utilidades de texto sin dependencias: idioma, tokens para FTS5, hashes."""
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
    """Idioma aproximado ("es", "en" o "und") contando palabras vacias.

    Basta para lo que se usa: decidir si FTS5 (coincidencia literal) puede
    aportar, que solo pasa cuando consulta y documento comparten idioma. Los
    acentos y la ñ cuentan como evidencia de espanol porque los terminos
    tecnicos en ingles dominan muchas consultas cortas en espanol.
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
    """Expresion FTS5 ``"t1" OR "t2" ...`` con los terminos utiles del texto.

    Cada termino va entre comillas para que ningun caracter del prompt se
    interprete como operador FTS5 (``NEAR``, ``-``, ``*``, parentesis).
    """
    seen: list[str] = []
    for w in _WORD.findall((text or "").lower()):
        if len(w) > 1 and w not in STOP and w not in seen:
            seen.append(w)
        if len(seen) >= max_terms:
            break
    if not seen:
        return None
    return " OR ".join('"%s"' % w.replace('"', "") for w in seen)


def sha256_text(text: str) -> str:
    """SHA-256 hex de un texto UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str, bufsize: int = 1 << 20) -> str:
    """SHA-256 hex de un archivo, leido por bloques."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(bufsize)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def squash(text: str) -> str:
    """Colapsa espacios en blanco a uno solo."""
    return re.sub(r"\s+", " ", text or "").strip()
