"""Metadatos por libro (año, temas, formato) y filtros de busqueda.

``data/books_meta.json`` (en el repo) mapea ``book_id`` (la carpeta del libro en
``_md``) a ``title``, ``author``, ``year``, ``domains``, ``format``... El año sale
de la pagina de copyright de esa edicion; si no hubo evidencia es ``null`` (no
se adivina). La ruta se puede cambiar con ``metadata_file`` en ``config.json``.

Filtros (todos opcionales; sin filtros no se restringe nada):

* ``domains``: lista; pasa un libro que tenga AL MENOS uno de esos temas.
* ``year_min`` / ``year_max``: inclusivos. Un libro sin año conocido NO pasa un
  filtro de año: si se pide "desde 2020", no se puede afirmar que lo cumple.

Un archivo de metadatos ausente o ilegible no tumba la busqueda: sin metadatos
no hay filtros posibles y cualquier filtro devuelve cero libros, lo que el
llamador ve como "sin resultados", no como error.
"""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_FILE = Path(__file__).resolve().parents[2] / "data" / "books_meta.json"

#: Taxonomia cerrada de temas (la misma con la que se clasifico la biblioteca).
DOMAINS = (
    "arquitectura", "datos", "seguridad", "sre-observabilidad", "devops-cicd", "cloud", "testing",
    "agile-gestion", "producto", "ia-ml", "programacion", "requisitos-analisis", "ux-ui",
    "liderazgo-organizacion",
)


class FilterError(ValueError):
    """Filtro mal formado (tema desconocido, año no entero...)."""


def load(path: str | Path | None = None) -> dict[str, dict]:
    """``{book_id: meta}``; ``{}`` si el archivo no existe o no se puede leer."""
    p = Path(path) if path else DEFAULT_FILE
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def validate(filters: dict | None) -> dict:
    """Normaliza ``filters``; lanza :class:`FilterError` si algo no cuadra."""
    if not filters:
        return {}
    if not isinstance(filters, dict):
        raise FilterError("filters debe ser un objeto")
    out: dict = {}
    unknown = set(filters) - {"domains", "year_min", "year_max"}
    if unknown:
        raise FilterError(f"filtros desconocidos: {', '.join(sorted(unknown))}")
    doms = filters.get("domains")
    if doms:
        if isinstance(doms, str):
            doms = [doms]
        if not isinstance(doms, list) or not all(isinstance(d, str) for d in doms):
            raise FilterError("domains debe ser una lista de strings")
        bad = [d for d in doms if d not in DOMAINS]
        if bad:
            raise FilterError(f"temas desconocidos: {', '.join(bad)} (validos: {', '.join(DOMAINS)})")
        out["domains"] = sorted(set(doms))
    for k in ("year_min", "year_max"):
        v = filters.get(k)
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, int) or not 1900 <= v <= 2100:
            raise FilterError(f"{k} debe ser un año entero")
        out[k] = v
    return out


def allowed_books(meta: dict[str, dict], filters: dict) -> set[str] | None:
    """``book_id`` que pasan ``filters`` (ya validados); None = sin restriccion."""
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
    """Campos de metadatos que acompañan a cada resultado."""
    if not m:
        return {}
    return {k: m.get(k) for k in ("year", "domains", "format", "edition") if m.get(k) is not None}
