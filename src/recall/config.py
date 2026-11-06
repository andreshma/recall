"""Rutas y configuracion de recall.

Todo el estado vive en ``%LOCALAPPDATA%\\recall`` (o en ``RECALL_HOME`` si esta
definida, que es lo que usan los tests): el indice SQLite, ``service.json``
(puerto y token del servicio), ``config.json`` (opcional, lo edita el usuario) y
los logs. Nada se escribe en el repo.

``config.json`` se mezcla sobre :data:`DEFAULTS` seccion por seccion, de modo
que basta escribir solo lo que cambia, p. ej. ``{"hook": {"mode": "active"}}``.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

BOOKS_MD = str(Path.home() / "Downloads" / "Books TI" / "_md")

MODEL_SNAPSHOT = str(
    Path.home() / ".cache" / "huggingface" / "hub"
    / "models--onnx-community--embeddinggemma-300m-ONNX" / "snapshots"
    / "5090578d9565bb06545b4552f76e6bc2c93e4a66"
)

DEFAULTS: dict[str, Any] = {
    # Colecciones indexadas. ``roots`` va en orden de PRECEDENCIA por libro: si un
    # mismo directorio de libro existe en dos raices, gana la primera y el de la
    # otra se trata como baja. Asi, poner ``...\\_md_v2`` delante de ``...\\_md``
    # hace que los libros reconvertidos reemplacen a los viejos en el siguiente
    # ``recall index`` incremental, sin --rebuild.
    "collections": {
        "books": {"roots": [BOOKS_MD]},
    },
    # Troceo (ver chunking.py). Cambiarlo exige `recall index --rebuild`: queda en
    # la identidad del indice. Con fragmentos mas grandes sube tambien
    # model.doc_max_tokens (~1 token cada 3-4 caracteres) o se truncan.
    "chunking": {"target": 900, "max_chars": 1000, "min_chars": 450, "overlap": 150},
    "model": {
        "snapshot": MODEL_SNAPSHOT,
        "onnx_file": "onnx/model_quantized.onnx",
        # Matryoshka: embeddinggemma admite 768/512/256/128. 768 midio MRR 1.00
        # frente a 0.93 de 384 en el prototipo (cruce ES->EN 1.00 vs 0.85).
        "dim": 768,
        "doc_max_tokens": 320,
        "query_max_tokens": 256,
        # Medido 2026-10-27: indexar ~20 chunks/s en DirectML (GPU libre) frente a
        # 1.3-2.8 en CPU; una consulta, 81 ms en DirectML frente a 2.2 s en CPU con
        # la maquina cargada. ":1" = adaptador DXGI 1, el que rindio mas aqui.
        "index_providers": ["DmlExecutionProvider:1", "CPUExecutionProvider"],
        "query_providers": ["DmlExecutionProvider:1", "CPUExecutionProvider"],
        "batch_size": 32,
    },
    "search": {
        "rrf_k": 60,
        "vec_weight": 1.0,
        # FTS5 solo ayuda cuando consulta y documento comparten idioma; en el
        # cruce ES->EN el prototipo midio que lo empeoraba (R@5 0.90 vs 1.00).
        "fts_weight_same_lang": 0.25,
        "fts_weight_cross_lang": 0.02,
        "candidates": 100,
        # Recarga del indice en el servicio como mucho cada N s (ver search.py).
        "min_reload_s": 30.0,
    },
    "hook": {
        # "shadow": registra lo que habria inyectado y no inyecta nada.
        # "active": inyecta. "off": no hace nada.
        "mode": "shadow",
        # Medido 2026-10-27 sobre los 126 libros: negativos que pasan el filtro
        # mecanico <= 0.542; consultas legitimas 0.524-0.774 (17/20 del set de
        # regresion y 7/10 prompts reales >= 0.58). Se prioriza precision: lo que
        # no se inyecta sigue a un llamado de recall_books_search.
        "threshold": 0.58,
        "max_snippets": 3,
        "max_chars": 700,
        "budget_s": 1.5,
        "min_prompt_chars": 20,
        "collections": ["books"],
    },
}


def home() -> Path:
    """Directorio de estado (``RECALL_HOME`` o ``%LOCALAPPDATA%\\recall``)."""
    env = os.environ.get("RECALL_HOME")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "recall"


def db_path() -> Path:
    """Ruta del indice SQLite."""
    return home() / "index.sqlite3"


def service_file() -> Path:
    """Archivo con puerto, token y pid del servicio en ejecucion."""
    return home() / "service.json"


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict[str, Any]:
    """Configuracion efectiva: :data:`DEFAULTS` + ``config.json`` si existe.

    Un ``config.json`` ilegible no tumba nada: se ignora y se usan los
    defaults, porque el hook y el servicio deben fallar abiertos.
    """
    p = home() / "config.json"
    try:
        with open(p, encoding="utf-8") as f:
            user = json.load(f)
    except (OSError, ValueError):
        user = {}
    return _merge(DEFAULTS, user if isinstance(user, dict) else {})


def ensure_home() -> Path:
    """Crea el directorio de estado y lo restringe al usuario actual.

    ``%LOCALAPPDATA%`` ya hereda ACL de usuario; ``icacls`` quita la herencia y
    deja solo al usuario, por si el perfil tiene ACL relajadas. Si icacls falla
    (no Windows, tests) se sigue: la restriccion es defensa en profundidad.
    """
    h = home()
    h.mkdir(parents=True, exist_ok=True)
    restrict_to_user(h)
    return h


def restrict_to_user(path: Path) -> bool:
    """Deja ``path`` accesible solo para el usuario actual (Windows, icacls)."""
    if os.name != "nt":
        try:
            os.chmod(path, 0o700 if path.is_dir() else 0o600)
            return True
        except OSError:
            return False
    import subprocess

    user = os.environ.get("USERNAME")
    if not user:
        return False
    flag = "(OI)(CI)F" if path.is_dir() else "F"
    try:
        r = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:{flag}"],
            capture_output=True, timeout=10,
        )
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
