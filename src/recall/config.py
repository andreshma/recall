"""recall paths and configuration.

All state lives in ``%LOCALAPPDATA%\\recall`` (or in ``RECALL_HOME`` if set,
which is what the tests use): the SQLite index, ``service.json`` (the service's
port and token), ``config.json`` (optional, edited by the user) and the logs.
Nothing is written to the repo.

``config.json`` is merged over :data:`DEFAULTS` section by section, so it is
enough to write only what changes, e.g. ``{"hook": {"mode": "active"}}``.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

#: Default library root: one directory per book with one Markdown file per
#: chapter (docs/USAGE.md §4). Set ``collections.books.roots`` in config.json
#: to point elsewhere.
BOOKS_MD = str(Path.home() / "books" / "md")

MODEL_SNAPSHOT = str(
    Path.home()
    / ".cache"
    / "huggingface"
    / "hub"
    / "models--onnx-community--embeddinggemma-300m-ONNX"
    / "snapshots"
    / "5090578d9565bb06545b4552f76e6bc2c93e4a66"
)

DEFAULTS: dict[str, Any] = {
    # Indexed collections. ``roots`` is in per-book PRECEDENCE order: if the
    # same book directory exists in two roots, the first wins and the other is
    # treated as removed. So putting ``...\\_md_v2`` before ``...\\_md`` makes
    # the reconverted books replace the old ones on the next incremental
    # ``recall index``, without --rebuild.
    "collections": {
        "books": {"roots": [BOOKS_MD]},
    },
    # Chunking (see chunking.py). Changing it requires `recall index --rebuild`:
    # it is part of the index identity. With larger chunks also raise
    # model.doc_max_tokens (~1 token per 3-4 characters) or they get truncated.
    "chunking": {"target": 900, "max_chars": 1000, "min_chars": 450, "overlap": 150},
    "model": {
        "snapshot": MODEL_SNAPSHOT,
        "onnx_file": "onnx/model_quantized.onnx",
        # Matryoshka: embeddinggemma supports 768/512/256/128. 768 measured MRR
        # 1.00 versus 0.93 for 384 in the prototype (ES->EN cross 1.00 vs 0.85).
        "dim": 768,
        "doc_max_tokens": 320,
        "query_max_tokens": 256,
        # Measured 2026-10-27: indexing ~20 chunks/s on DirectML (idle GPU) versus
        # 1.3-2.8 on CPU; one query, 81 ms on DirectML versus 2.2 s on CPU with
        # the machine under load. No adapter number by default: DXGI numbers
        # adapters per machine. "DmlExecutionProvider:N" in config.json picks
        # adapter N (e.g. a discrete GPU); if it cannot start, the others are
        # tried before CPU and the choice is logged (embedder.open_session).
        "index_providers": ["DmlExecutionProvider", "CPUExecutionProvider"],
        "query_providers": ["DmlExecutionProvider", "CPUExecutionProvider"],
        "batch_size": 32,
    },
    "search": {
        "rrf_k": 60,
        "vec_weight": 1.0,
        # FTS5 only helps when query and document share a language; for the
        # ES->EN cross the prototype measured that it made things worse (R@5 0.90 vs 1.00).
        "fts_weight_same_lang": 0.25,
        "fts_weight_cross_lang": 0.02,
        "candidates": 100,
        # The service reloads the index at most every N s (see search.py).
        "min_reload_s": 30.0,
    },
    "hook": {
        # "shadow": logs what it would have injected and injects nothing.
        # "active": injects. "off": does nothing.
        "mode": "shadow",
        # Measured 2026-10-27 over the 126 books: negatives that pass the
        # mechanical filter <= 0.542; legitimate queries 0.524-0.774 (17/20 of the
        # regression set and 7/10 real prompts >= 0.58). Precision is favored:
        # whatever is not injected is still one recall_books_search call away.
        "threshold": 0.58,
        "max_snippets": 3,
        "max_chars": 700,
        "budget_s": 1.5,
        "min_prompt_chars": 20,
        "collections": ["books"],
    },
}


def home() -> Path:
    """State directory (``RECALL_HOME`` or ``%LOCALAPPDATA%\\recall``)."""
    env = os.environ.get("RECALL_HOME")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "recall"


def db_path() -> Path:
    """Path of the SQLite index."""
    return home() / "index.sqlite3"


def service_file() -> Path:
    """File with the port, token and pid of the running service."""
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
    """Effective configuration: :data:`DEFAULTS` + ``config.json`` if present.

    An unreadable ``config.json`` breaks nothing: it is ignored and the
    defaults are used, because the hook and the service must fail open.
    """
    p = home() / "config.json"
    try:
        with open(p, encoding="utf-8") as f:
            user = json.load(f)
    except (OSError, ValueError):
        user = {}
    return _merge(DEFAULTS, user if isinstance(user, dict) else {})


def ensure_home() -> Path:
    """Create the state directory and restrict it to the current user.

    ``%LOCALAPPDATA%`` already inherits a user ACL; ``icacls`` removes
    inheritance and leaves only the user, in case the profile has relaxed ACLs.
    If icacls fails (non-Windows, tests) it carries on: the restriction is
    defense in depth.
    """
    h = home()
    h.mkdir(parents=True, exist_ok=True)
    restrict_to_user(h)
    return h


def restrict_to_user(path: Path) -> bool:
    """Make ``path`` accessible only to the current user (Windows, icacls)."""
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
            capture_output=True,
            timeout=10,
        )
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
