"""Embeddings with embeddinggemma-300m (ONNX q8) loaded from the local cache.

embeddinggemma retrieval prefixes (measured in the prototype: without them
ES->EN cross-language retrieval degrades):

* query: ``task: search result | query: <query>``
* document: ``title: <book — chapter> | text: <chunk>``

The model identity (repo, snapshot revision, SHA-256 of the weight files,
dimension, prefix format) is recorded in the index. If any of those changes,
the stored vectors are no longer comparable with the new ones and the index
refuses to mix them (see :mod:`recall.store`).
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
from collections.abc import Sequence
from contextlib import nullcontext
from pathlib import Path
from typing import ClassVar, Protocol

import numpy as np

QUERY_PREFIX = "task: search result | query: "
DOC_FORMAT = "title: {title} | text: {text}"


def doc_input(book: str, chapter: str, text: str, fmt: str = DOC_FORMAT) -> str:
    """Text embedded for a document chunk."""
    title = f"{book} — {chapter}" if chapter and chapter != book else (book or "none")
    return fmt.format(title=title, text=text)


def query_input(query: str, prefix: str = QUERY_PREFIX) -> str:
    """Text embedded for a query."""
    return prefix + query


def format_query(emb, query: str) -> str:
    """Query with the embedder's prefix (or embeddinggemma's if it defines none)."""
    f = getattr(emb, "format_query", None)
    return f(query) if f else query_input(query)


def format_doc(emb, book: str, chapter: str, text: str) -> str:
    """Document with the embedder's format (or embeddinggemma's if it defines none)."""
    f = getattr(emb, "format_doc", None)
    return f(book, chapter, text) if f else doc_input(book, chapter, text)


#: Default chunking parameters (see :mod:`recall.chunking`). An index with
#: other parameters stores them in its identity: mixing chunks from two
#: different chunkings breaks nothing, but incremental indexing does not
#: re-chunk unchanged files, so a chunking change requires ``--rebuild``.
CHUNK_DEFAULTS = {"target": 900, "max_chars": 1000, "min_chars": 450, "overlap": 150}


def index_identity(model_identity: dict, chunking: dict | None = None) -> dict:
    """Index identity = the model's + the chunking if it is not the default.

    With the default chunking the identity is exactly the model's, so that
    indexes built before chunking was configurable remain valid without
    rebuilding them.
    """
    ch = {**CHUNK_DEFAULTS, **(chunking or {})}
    if ch == CHUNK_DEFAULTS:
        return model_identity
    return {**model_identity, "chunking": ch}


class Embedder(Protocol):
    """What the index and the searcher need from an embedding model."""

    identity: dict
    dim: int

    def embed(self, texts: Sequence[str], kind: str) -> np.ndarray:
        """L2-normalized float32 vectors (n x dim). ``kind``: "doc" | "query"."""
        ...


def query_windows(emb, query: str, max_tokens: int, max_windows: int, overlap: int = 0) -> list[str]:
    """Split ``query`` into word windows of up to ``max_tokens`` tokens.

    Why: with quantized embeddinggemma-2, DirectML takes ~150 ms with up to
    32 tokens per call and 1.2-5.5 s from 33 on (measured 2026-11-06 on both
    GPUs of the laptop). A whole long prompt exhausts the hook's budget;
    several short windows, one per call, do not. ``max_tokens`` counts the
    query prefix and the special tokens.

    Returns ``[query]`` if it fits, if ``max_tokens`` is 0 or if the embedder
    cannot count tokens. The first ``max_windows`` windows; each one repeats
    the last ``overlap`` words of the previous one, so as not to split a key
    phrase in two. A word that does not fit on its own (a long URL, a hash) is dropped.
    """
    count = getattr(emb, "count_query_tokens", None)
    if not max_tokens or count is None or count(query) <= max_tokens:
        return [query]
    out: list[str] = []
    cur: list[str] = []
    for w in (w for w in query.split() if count(w) <= max_tokens):
        if cur and count(" ".join([*cur, w])) > max_tokens:
            out.append(" ".join(cur))
            if len(out) >= max_windows:
                return out
            cur = _carry_over(cur, w, overlap, max_tokens, count)
        cur.append(w)
    if cur:
        out.append(" ".join(cur))
    return out or [query]


def _carry_over(cur: list[str], w: str, overlap: int, max_tokens: int, count) -> list[str]:
    """Last ``overlap`` words of ``cur`` that fit together with ``w`` in the new window."""
    keep = cur[-overlap:] if overlap > 0 else []
    while keep and count(" ".join([*keep, w])) > max_tokens:
        keep = keep[1:]
    return keep


def identity_key(identity: dict) -> str:
    """Canonical serialization of an identity, for exact comparison."""
    return json.dumps(identity, sort_keys=True, ensure_ascii=True)


def forget_digest_cache() -> None:
    """Clear the digest cache: the next ``_file_digest`` reads the file again.

    The cache is keyed by path + size + mtime, which an in-place replacement
    can preserve (AR-14). The service calls this at startup; the CLI keeps
    using the cache so as not to re-read hundreds of MB on every command.
    """
    from . import config

    try:
        (config.home() / "model_digest.json").unlink()
    except FileNotFoundError:
        pass


def _file_digest(path: Path) -> str:
    """Full SHA-256 of the file, cached by (size, mtime) in RECALL_HOME."""
    from . import config

    st = path.stat()
    cache = config.home() / "model_digest.json"
    key = f"{path}|{st.st_size}|{st.st_mtime_ns}"
    try:
        with open(cache, encoding="utf-8") as f:
            known = json.load(f)
    except (OSError, ValueError):
        known = {}
    if key in known:
        return known[key]
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    known[key] = h.hexdigest()
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(known, f)
    except OSError:
        pass
    return known[key]


class OnnxEmbedder:
    """ONNX embedding model via onnxruntime; never downloads anything.

    The default is embeddinggemma-300m. Other models are configured with:

    * ``name``: repo id (part of the index identity).
    * ``query_prefix`` / ``doc_format``: the prompt the model expects.
    * ``pooling``: ``auto`` uses the ``sentence_embedding`` output if the graph
      has it (embeddinggemma); otherwise ``mean`` (masked average) or ``last``
      (last real token; Qwen3-Embedding).
    * ``padding_side`` / ``pad_token``: Qwen3 pads on the left with
      ``<|endoftext|>``; with left padding the last token is the real one.
    * ``append_eos``: token appended at the end if the tokenizer does not add it
      (Qwen3-Embedding pools over the end token).

    ``providers`` is the list of ONNX Runtime providers in order of
    preference. Indexing uses DirectML (integrated GPU; ~20 chunks/s
    measured at batch 32-64 with the GPU idle) and so do queries: with the
    machine under load a query cost 2.2 s (p50) on CPU and 81 ms on DirectML,
    and the hook only has 1.5 s. ``DmlExecutionProvider:N`` asks for DXGI
    adapter N; if that adapter cannot start, the other adapters are tried
    before falling back to CPU (see :func:`open_session`). What was chosen is
    logged to stderr and kept in ``providers`` / ``device_id``.
    """

    def __init__(
        self,
        snapshot: str,
        onnx_file: str = "onnx/model_quantized.onnx",
        dim: int = 768,
        providers: Sequence[str] = ("CPUExecutionProvider",),
        doc_max_tokens: int = 320,
        query_max_tokens: int = 256,
        batch_size: int = 32,
        *,
        name: str = "onnx-community/embeddinggemma-300m-ONNX",
        query_prefix: str = QUERY_PREFIX,
        doc_format: str = DOC_FORMAT,
        pooling: str = "auto",
        padding_side: str = "right",
        pad_token: str | None = None,
        append_eos: str | None = None,
    ) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        snap = Path(snapshot).expanduser()
        model = snap / onnx_file
        tok_path = snap / "tokenizer.json"
        if not model.is_file() or not tok_path.is_file():
            raise FileNotFoundError(
                f"Model not found in the local cache: {model}. recall does not download "
                "models; restore the HF cache or set model.snapshot in config.json."
            )
        self.dim = int(dim)
        self.batch_size = int(batch_size)
        self._doc_max = int(doc_max_tokens)
        self._query_max = int(query_max_tokens)
        self._tok = Tokenizer.from_file(str(tok_path))
        # Separate copy for counting: ``_encode`` changes the other one's truncation
        # and the service handles several requests at once.
        self._count_tok = Tokenizer.from_file(str(tok_path))
        self._count_tok.no_truncation()
        self._count_tok.no_padding()

        # Session options depend on the provider that actually starts, so they
        # are built per attempt inside open_session (DirectML must disable the
        # memory pattern; the CPU fallback keeps it on). See _session_options.
        def session_options(dml: bool):
            return _session_options(ort, dml=dml)

        # The tokenizer is shared and mutated per call (truncation length); this
        # guards it so concurrent requests cannot corrupt each other's encoding.
        self._encode_lock = threading.Lock()
        self._sess, self.device_id = open_session(ort, str(model), session_options, providers)
        # "only one thread may call Run at a time" on a DirectML session: two
        # concurrent Runs crashed the process inside the NVIDIA driver
        # (nvwgf2umx.dll, 0xc0000005, 2026-11-06). On CPU, ONNX Runtime allows
        # concurrent Run calls, so the lock is taken only for DirectML.
        self._run_lock = threading.Lock()
        self._run_guard = self._run_lock if self.device_id is not None else nullcontext()
        self.providers = self._sess.get_providers()
        self._inputs = [i.name for i in self._sess.get_inputs()]
        outs = [o.name for o in self._sess.get_outputs()]
        if pooling == "auto":
            pooling = "sentence_embedding" if "sentence_embedding" in outs else "mean"
        if pooling not in ("sentence_embedding", "mean", "last"):
            raise ValueError(f"unknown pooling: {pooling}")
        self.pooling = pooling
        if pooling == "sentence_embedding":
            self._out = outs.index("sentence_embedding")
        else:
            self._out = outs.index("last_hidden_state") if "last_hidden_state" in outs else 0
        self.query_prefix, self.doc_format = query_prefix, doc_format
        self._padding_side = padding_side
        self._pad_id = self._tok.token_to_id(pad_token) if pad_token else 0
        if pad_token and self._pad_id is None:
            raise ValueError(f"pad_token {pad_token!r} is not in the tokenizer")
        self._pad_token = pad_token or "[PAD]"
        self._eos_id = self._tok.token_to_id(append_eos) if append_eos else None
        if append_eos and self._eos_id is None:
            raise ValueError(f"append_eos {append_eos!r} is not in the tokenizer")
        data = model.with_name(model.name + "_data")
        files = {onnx_file: _file_digest(model), "tokenizer.json": _file_digest(tok_path)}
        if data.is_file():
            files[onnx_file + "_data"] = _file_digest(data)
        self.identity = {
            "model": name,
            "revision": snap.name,
            "files": files,
            "dim": self.dim,
            "doc_format": doc_format,
            "query_prefix": query_prefix,
            "pooling": pooling,
            "normalized": True,
        }
        # Only non-default models declare these fields: that way the identity of
        # embeddinggemma-300m does not change and its index stays valid.
        if padding_side != "right":
            self.identity["padding_side"] = padding_side
        if append_eos:
            self.identity["append_eos"] = append_eos
        # The document token limit changes the stored vectors (with ~3500-character
        # chunks, 320 versus 1100 tokens gives cosine 0.93): it goes into the
        # identity if it is not the default. The query limit is not stored in the
        # index, so it does not.
        if int(doc_max_tokens) != 320:
            self.identity["doc_max_tokens"] = int(doc_max_tokens)

    def format_query(self, query: str) -> str:
        """Query with this model's prefix."""
        return query_input(query, self.query_prefix)

    def count_query_tokens(self, query: str) -> int:
        """Tokens ``query`` will take with its prefix (and the EOS if one is appended)."""
        ids = self._count_tok.encode(self.format_query(query)).ids
        return len(ids) + (1 if self._eos_id is not None and (not ids or ids[-1] != self._eos_id) else 0)

    def format_doc(self, book: str, chapter: str, text: str) -> str:
        """Document with this model's format."""
        return doc_input(book, chapter, text, self.doc_format)

    def _encode(self, texts: list[str], max_len: int) -> tuple[np.ndarray, np.ndarray]:
        """ids and mask, with end-of-sequence and padding as the model requires."""
        # The tokenizer is shared and its truncation/padding is mutated here, so
        # hold the encode lock: concurrent CPU requests must not interleave this.
        with self._encode_lock:
            self._tok.no_padding()
            # With a manual EOS, truncate one token earlier so the EOS always fits.
            self._tok.enable_truncation(max_length=max_len - (1 if self._eos_id is not None else 0))
            seqs = [e.ids for e in self._tok.encode_batch(texts)]
        if self._eos_id is not None:
            seqs = [s if s and s[-1] == self._eos_id else [*s, self._eos_id] for s in seqs]
        # Padding to multiples of 32 reduces the distinct shapes DirectML sees.
        width = max(len(s) for s in seqs)
        width = -(-width // 32) * 32
        ii = np.full((len(seqs), width), self._pad_id, dtype=np.int64)
        am = np.zeros((len(seqs), width), dtype=np.int64)
        for r, s in enumerate(seqs):
            if self._padding_side == "left":
                ii[r, width - len(s) :], am[r, width - len(s) :] = s, 1
            else:
                ii[r, : len(s)], am[r, : len(s)] = s, 1
        return ii, am

    _DTYPES: ClassVar[dict[str, type]] = {
        "tensor(float)": np.float32,
        "tensor(float16)": np.float16,
        "tensor(int64)": np.int64,
        "tensor(int32)": np.int32,
    }

    def _feed(self, ii: np.ndarray, am: np.ndarray) -> dict:
        """Graph inputs. Non-text ones are passed empty (length 0).

        embeddinggemma-2 is multimodal and asks for ``image/video/audio_features``;
        Qwen3-Embedding was exported as a decoder and asks for ``past_key_values.*``.
        With length 0 in the symbolic dimension (except the batch) the graph
        treats them as absent: no image, no cache.
        """
        feed = {"input_ids": ii, "attention_mask": am}
        if "position_ids" in self._inputs:
            feed["position_ids"] = np.clip(np.cumsum(am, axis=1) - 1, 0, None).astype(np.int64)
        if "token_type_ids" in self._inputs:
            feed["token_type_ids"] = np.zeros_like(ii)
        for inp in self._sess.get_inputs():
            if inp.name in feed:
                continue
            shape = [d if isinstance(d, int) else (len(ii) if "batch" in str(d) else 0) for d in inp.shape]
            feed[inp.name] = np.zeros(shape, dtype=self._DTYPES.get(inp.type, np.float32))
        return {k: v for k, v in feed.items() if k in self._inputs}

    def _pool(self, h: np.ndarray, am: np.ndarray) -> np.ndarray:
        if self.pooling == "sentence_embedding":
            return h
        if self.pooling == "mean":
            m = am[:, :, None].astype(np.float32)
            return (h * m).sum(axis=1) / np.maximum(m.sum(axis=1), 1.0)
        # last: last real token (with left padding it is the last column)
        last = am.shape[1] - 1 - np.argmax(am[:, ::-1], axis=1)
        return h[np.arange(len(h)), last]

    def embed(self, texts: Sequence[str], kind: str) -> np.ndarray:
        """Embed ``texts`` (already prefixed) in batches sorted by length."""
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        max_len = self._query_max if kind == "query" else self._doc_max
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for s in range(0, len(order), self.batch_size):
            ids = order[s : s + self.batch_size]
            ii, am = self._encode([texts[i] for i in ids], max_len)
            with self._run_guard:
                h = self._sess.run([self._sess.get_outputs()[self._out].name], self._feed(ii, am))[0]
            v = self._pool(np.asarray(h, dtype=np.float32), am)
            v = np.asarray(v[:, : self.dim], dtype=np.float32)
            v /= np.linalg.norm(v, axis=1, keepdims=True) + 1e-12
            out[ids] = v
        return out


#: DirectML adapters (DXGI order) tried when the requested one cannot start.
DML_ADAPTERS = 4
_DML = "DmlExecutionProvider"


def provider_attempts(providers: Sequence[str], available: Sequence[str]) -> list[list]:
    """ONNX Runtime provider lists to try, in order; the last one is CPU only.

    ``providers`` uses ``Name`` or ``Name:device`` (``DmlExecutionProvider:1``
    = DXGI adapter 1; without a device, adapter 0). Unavailable providers are
    dropped. With DirectML, the requested adapter goes first and then every
    other one up to :data:`DML_ADAPTERS`: the adapter numbering depends on
    the machine (DXGI sets it), so a configured number may not exist.
    """
    parsed: list[tuple[str, int | None]] = []
    for p in providers:
        name, _, dev = p.partition(":")
        if name in available:
            parsed.append((name, int(dev) if dev else None))

    def build(dml_device: int | None) -> list:
        out: list = []
        for name, dev in parsed:
            d = dml_device if name == _DML else dev
            out.append((name, {"device_id": d}) if d is not None else name)
        return out

    attempts: list[list] = []
    if any(name == _DML for name, _ in parsed):
        first = next(dev for name, dev in parsed if name == _DML) or 0
        attempts += [build(d) for d in [first, *(d for d in range(DML_ADAPTERS) if d != first)]]
    elif parsed:
        attempts.append(build(None))
    cpu = ["CPUExecutionProvider"]
    if cpu not in attempts:
        attempts.append(cpu)
    return attempts


def _describe(attempt: list) -> str:
    return ", ".join(p if isinstance(p, str) else f"{p[0]}:{p[1]['device_id']}" for p in attempt)


def _session_options(ort, *, dml: bool):
    """ONNX Runtime ``SessionOptions`` tuned for the provider that will run.

    DirectML supports neither the memory pattern nor parallel execution (ONNX
    Runtime docs, DirectML EP). On CPU the memory pattern is a real speed-up and
    does not change the result, so the CPU fallback keeps it on instead of
    inheriting the DirectML-tuned options.
    """
    so = ort.SessionOptions()
    so.log_severity_level = 3
    so.enable_mem_pattern = not dml
    so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    return so


def open_session(ort, model: str, so, providers: Sequence[str], log=None) -> tuple:
    """(InferenceSession, DirectML adapter or None), trying :func:`provider_attempts` in order.

    A DirectML attempt counts as failed if the session raises or comes up
    without DirectML. Missing adapter or broken driver: CPU rather than
    nothing. The choice is always logged (stderr by default): a silent CPU
    fallback made queries miss the hook's 1.5 s budget with no trace (F-09).

    ``so`` is the ONNX Runtime ``SessionOptions``, or a ``callable(is_dml)``
    that builds them for each attempt (so the CPU fallback is not stuck with
    the DirectML-tuned options); anything else is passed through unchanged.
    """
    log = log or (lambda m: print(m, file=sys.stderr, flush=True))
    attempts = provider_attempts(providers, ort.get_available_providers())
    failed: list[str] = []
    for i, attempt in enumerate(attempts):
        last = i == len(attempts) - 1
        dml = next((p[1]["device_id"] for p in attempt if not isinstance(p, str) and p[0] == _DML), None)
        opts = so(dml is not None) if callable(so) else so
        try:
            sess = ort.InferenceSession(model, opts, providers=attempt)
        except Exception as e:
            if last:
                raise
            failed.append(f"{_describe(attempt)} ({type(e).__name__})")
            continue
        if dml is not None and _DML not in sess.get_providers() and not last:
            failed.append(f"{_describe(attempt)} (came up without DirectML)")
            continue
        chosen = f"DirectML adapter {dml}" if dml is not None else ", ".join(sess.get_providers())
        tried = f"; failed first: {'; '.join(failed)}" if failed else ""
        log(f"recall: ONNX Runtime on {chosen}{tried}")
        return sess, dml
    raise RuntimeError("no ONNX Runtime provider could be started")  # unreachable: the last attempt raises


def from_config(cfg: dict, role: str) -> OnnxEmbedder:
    """Build the embedder for ``role`` ("index" or "query") from the config."""
    m = cfg["model"]
    provs = m["index_providers"] if role == "index" else m["query_providers"]
    extra = {
        k: m[k]
        for k in ("name", "query_prefix", "doc_format", "pooling", "padding_side", "pad_token", "append_eos")
        if k in m
    }
    return OnnxEmbedder(
        m["snapshot"],
        m["onnx_file"],
        m["dim"],
        provs,
        m["doc_max_tokens"],
        m["query_max_tokens"],
        m["batch_size"],
        **extra,
    )


class HashEmbedder:
    """Deterministic, model-free embedder: the TEST embedder (hashed bag of words).

    A test double kept next to the real one so that the unit suite and the
    PoCs share it; :func:`from_config` never builds it.

    Texts with words in common give a high cosine; useful for testing the index,
    incremental indexing, dedup and threshold without loading 300 MB of weights.
    """

    def __init__(self, dim: int = 64, name: str = "hash-test") -> None:
        self.dim = dim
        self.identity = {"model": name, "dim": dim, "doc_format": DOC_FORMAT, "query_prefix": QUERY_PREFIX}
        self.calls = 0

    def embed(self, texts: Sequence[str], kind: str) -> np.ndarray:
        """Normalized vectors; counts the embedded texts in ``calls``."""
        from .textutil import _WORD, STOP

        self.calls += len(texts)
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for r, t in enumerate(texts):
            body = t.split("|", 1)[1] if "|" in t else t
            for w in _WORD.findall(body.lower()):
                if w in STOP or w in ("text", "query", "title"):
                    continue
                h = int.from_bytes(hashlib.blake2b(w.encode(), digest_size=4).digest(), "little")
                out[r, h % self.dim] += 1.0
            n = np.linalg.norm(out[r])
            out[r] = out[r] / n if n else out[r]
        return out
