"""Retrieval regression over the hand-labeled book queries.

Metrics per group (TOTAL, EN->EN, ES->EN cross-language): R@5 (at least one relevant chunk in the top 5) and
MRR@10. Relevance is by path: the chunk's path matches some regex in
``rel.path``.

It also calibrates the hook threshold: best cosine of queries that SHOULD
inject (regression set + realistic prompts) versus prompts that should NOT
(commands, chit-chat, mechanical tasks), after applying the same
trivial/mechanical filters as the hook. Negatives the filter already skips do
not count toward the threshold, and a positive the filter would skip is a
filter bug.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

NEGATIVE_PROMPTS = [
    "arregla el typo en la línea 12 del README",
    "corre los tests y dime qué falla",
    "hola, ¿cómo estás?",
    "haz commit de los cambios con un mensaje claro",
    "renombra la variable foo a bar en utils.py",
    "what time is it in Mexico City",
    "resume este correo para mi equipo",
    "sube la versión a 0.12.0 en el CHANGELOG",
    "ordena alfabéticamente esta lista",
    "explícame este error de PowerShell: Access is denied",
    "borra la rama vieja y haz push",
    "abre el archivo de configuración y cambia el puerto a 8080",
    "gracias, eso era todo por hoy",
    "can you format this JSON nicely",
    "agrega un endpoint GET /users al controlador de FastAPI",
    "por qué falla este test de pytest con KeyError en la línea 40",
    "actualiza las dependencias del package.json a la última versión",
    "revisa este PR y dime si hay algo raro",
    "escribe un script de PowerShell que liste los archivos más grandes",
    "pon el título del README en negritas",
    "crea un archivo .gitignore para un proyecto de Python",
    "dame el diff del último commit",
    "cuánto espacio libre queda en el disco C",
    "mueve estos archivos a la carpeta docs",
    "traduce este párrafo al inglés",
    "can you fix the failing build",
    "add a docstring to this function",
    "lee el archivo config.yaml y dime qué puerto usa",
]

# Realistic prompts (full sentences, as the user writes them) that SHOULD inject.
POSITIVE_PROMPTS = [
    "explícame el cono de incertidumbre para estimar este proyecto",
    "¿cómo diseño las alertas por tasa de consumo del presupuesto de errores?",
    "qué patrón uso para publicar eventos de forma confiable desde una transacción",
    "cómo partir el monolito en microservicios sin big bang",
    "¿conviene usar sagas o transacciones distribuidas para este flujo de pedidos?",
    "how should I model aggregates and their boundaries",
    "qué diferencia hay entre mocks y stubs y cuándo usar cada uno",
    "cómo hago threat modeling de esta API con STRIDE",
    "recomiéndame una estrategia de versionado para la API REST",
    "cómo gestiono el estado remoto de terraform en equipo",
]


def _hook_filters():
    """The hook's trivial/mechanical filters (recall_hook.py, outside the package)."""
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "recall_hook.py"
    spec = importlib.util.spec_from_file_location("recall_hook", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return lambda p: mod.is_trivial(p, mod.DEFAULT_HOOK["min_prompt_chars"]) or mod.is_mechanical(p)


def load_queries(path: Path) -> list[dict]:
    """Queries of type ``libro`` (book)."""
    with open(path, encoding="utf-8") as f:
        return [q for q in json.load(f) if q.get("type") == "book"]


def is_relevant(q: dict, hit: dict) -> bool:
    """The hit's path matches some regex in ``q['rel']['path']``."""
    p = (hit.get("path") or "").replace("/", "\\")
    return any(re.search(rx, p, re.I) for rx in q["rel"].get("path", []))


def _rank_scores(q: dict, hits: list[dict]) -> tuple[float, int]:
    """(reciprocal rank@10, hit in top 5) of the first relevant hit."""
    for i, h in enumerate(hits[:10]):
        if is_relevant(q, h):
            return 1.0 / (i + 1), int(i < 5)
    return 0.0, 0


def _agg(rows: list[dict], sel) -> dict | None:
    s = [r for r in rows if sel(r)]
    if not s:
        return None
    return {"n": len(s), "r5": sum(r["r5"] for r in s) / len(s), "mrr": sum(r["rr"] for r in s) / len(s)}


def _probe(searcher, prompts, skipped) -> list[dict]:
    """Best cosine of each prompt and whether the hook's filters would skip it."""
    out = []
    for p in prompts:
        hits = searcher.search(p, k=1)
        out.append({"prompt": p, "best_cos": hits[0]["cos"] if hits else 0.0, "skipped": skipped(p)})
    return out


def evaluate(searcher, queries: list[dict], k: int = 10) -> dict:
    """R@5 and MRR@10 per group, latencies and threshold calibration."""
    rows, lat = [], []
    for q in queries:
        t = time.perf_counter()
        hits = searcher.search(q["q"], k=k)
        lat.append(time.perf_counter() - t)
        rr, r5 = _rank_scores(q, hits)
        rows.append(
            {
                "id": q["id"],
                "qlang": q["qlang"],
                "dlang": q["dlang"],
                "r5": r5,
                "rr": rr,
                "best_cos": hits[0]["cos"] if hits else 0.0,
                "top": [f"{h['book_id']}/{Path(h['path']).name}" for h in hits[:3]],
            }
        )

    skipped = _hook_filters()
    neg = _probe(searcher, NEGATIVE_PROMPTS, skipped)
    real = _probe(searcher, POSITIVE_PROMPTS, skipped)
    pos_cos = sorted([r["best_cos"] for r in rows] + [r["best_cos"] for r in real if not r["skipped"]])
    neg_cos = sorted(n["best_cos"] for n in neg if not n["skipped"])
    lat.sort()
    return {
        "groups": {
            "TOTAL": _agg(rows, lambda r: True),
            "EN->EN": _agg(rows, lambda r: r["qlang"] == "en"),
            "ES->EN (cross)": _agg(rows, lambda r: r["qlang"] == "es" and r["dlang"] == "en"),
        },
        "latency_s": {"p50": lat[len(lat) // 2], "max": lat[-1]},
        "calibration": {
            "pos_min": pos_cos[0],
            "neg_max": neg_cos[-1],
            "pos": pos_cos,
            "neg": neg_cos,
            "neg_detail": neg,
            "pos_real": real,
            "pos_skipped": [r["prompt"] for r in real if r["skipped"]],
        },
        "rows": rows,
    }
