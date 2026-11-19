"""Hub prior: how close each chunk is to generic coding prompts (ADR 0011).

Some chunks score a high cosine against almost any prompt a developer types
into a coding assistant (agent frameworks, "context-aware agents", system
management): with an absolute cosine threshold they were what the hook would
inject for a quarter of real prompts (shadow log, 2026-11-07 → 11-19). This
module measures that per chunk against a fixed set of synthetic operational
prompts (``hub_prompts.json``) and returns it centered on the median chunk:

    hub[d] = mean of the K highest cos(prompt, d) over the reference prompts
             − median of that value over all chunks

The cosine of a prompt is computed as the search computes it (max over the
prompt's query windows). The hook compares ``cos - hub_weight * hub`` with its
threshold; the search ranking does not use it.

The reference vectors depend only on the model and the window settings, so they
are embedded once and cached in the state directory; the prior is recomputed
from the in-memory vectors on every index (re)load, which takes well under a
second.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from .embedder import format_query, identity_key, query_windows

#: Synthetic prompts shipped with the package (no book topic in any of them).
PROMPTS_FILE = Path(__file__).with_name("hub_prompts.json")

#: Prompts averaged per chunk. Measured 2026-11-19 (ADR 0011): top-5, 10 and 20
#: and the plain mean separated almost the same; 5 tracks the closest prompts.
TOP_K = 5

CACHE_NAME = "hub_ref.npz"


def load_prompts(path: Path = PROMPTS_FILE) -> list[str]:
    """The reference prompts (non-empty strings) from ``path``."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return [p for p in data["prompts"] if isinstance(p, str) and p.strip()]


def window_params(search_cfg: dict) -> tuple[int, int, int]:
    """(window tokens, max windows, overlap) as the search uses them."""
    return (
        int(search_cfg.get("query_window_tokens", 0)),
        int(search_cfg.get("query_max_windows", 6)),
        int(search_cfg.get("query_window_overlap", 5)),
    )


def reference_key(identity: dict, prompts: Sequence[str], windows: tuple[int, int, int]) -> str:
    """Cache key: the vectors change with the model, the prompts or the windows."""
    h = hashlib.sha256()
    h.update(identity_key(identity).encode("utf-8"))
    h.update(json.dumps(list(prompts), ensure_ascii=False).encode("utf-8"))
    h.update(json.dumps(list(windows)).encode("utf-8"))
    return h.hexdigest()


def embed_reference(emb, prompts: Sequence[str], windows: tuple[int, int, int]) -> list[np.ndarray]:
    """Window vectors of each prompt (windows x dim), one embed call per prompt.

    One prompt per call keeps each call short, so a hook query that arrives
    meanwhile waits for at most one prompt's windows.
    """
    wt, wn, ov = windows
    out = []
    for p in prompts:
        ws = query_windows(emb, p[:1000], wt, wn, ov)
        out.append(np.asarray(emb.embed([format_query(emb, w) for w in ws], "query"), dtype=np.float32))
    return out


def load_cached(path: Path, key: str) -> list[np.ndarray] | None:
    """Cached reference vectors if ``path`` holds the ones for ``key``."""
    try:
        with np.load(path, allow_pickle=False) as z:
            if str(z["key"]) != key:
                return None
            vecs, splits = z["vecs"], z["splits"]
    except (OSError, ValueError, KeyError):
        return None
    return [a for a in np.split(vecs, np.cumsum(splits)[:-1]) if len(a)]


def save_cache(path: Path, key: str, ref: Sequence[np.ndarray]) -> None:
    """Write the reference vectors atomically (best effort)."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "wb") as f:
            np.savez(f, key=np.array(key), vecs=np.vstack(ref), splits=np.array([len(a) for a in ref]))
        os.replace(tmp, path)
    except OSError as e:
        print(f"recall: could not cache the hub reference: {e}", file=sys.stderr)


def reference(emb, search_cfg: dict, cache_dir: Path | None, prompts: Sequence[str] | None = None) -> list[np.ndarray]:
    """Reference vectors for ``emb``: from the cache, or embedded and cached."""
    prompts = load_prompts() if prompts is None else list(prompts)
    windows = window_params(search_cfg)
    key = reference_key(emb.identity, prompts, windows)
    path = cache_dir / CACHE_NAME if cache_dir is not None else None
    if path is not None:
        cached = load_cached(path, key)
        if cached is not None and len(cached) == len(prompts):
            return cached
    ref = embed_reference(emb, prompts, windows)
    if path is not None:
        save_cache(path, key, ref)
    return ref


def prior(V: np.ndarray, ref: Sequence[np.ndarray], k: int = TOP_K) -> np.ndarray:
    """Per-chunk hub value, centered on the median chunk (float32, len(V))."""
    if not len(V) or not ref:
        return np.zeros(len(V), dtype=np.float32)
    per = np.stack([(V @ q.T).max(axis=1) for q in ref])  # prompts x chunks
    k = max(1, min(int(k), len(ref)))
    top = np.partition(per, len(ref) - k, axis=0)[len(ref) - k :]
    r = top.mean(axis=0)
    return (r - np.median(r)).astype(np.float32)
