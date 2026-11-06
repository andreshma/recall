"""Embeddings con embeddinggemma-300m (ONNX q8) cargado de la cache local.

Prefijos de recuperacion de embeddinggemma (medidos en mpeval: sin ellos el
cruce ES->EN se degrada):

* consulta: ``task: search result | query: <consulta>``
* documento: ``title: <libro — capitulo> | text: <chunk>``

La identidad del modelo (repo, revision del snapshot, SHA-256 de los archivos
de pesos, dimension, formato de prefijos) se registra en el indice. Si cambia
cualquiera de esos datos, los vectores guardados ya no son comparables con los
nuevos y el indice se niega a mezclarlos (ver :mod:`recall.store`).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Protocol, Sequence

import numpy as np

QUERY_PREFIX = "task: search result | query: "
DOC_FORMAT = "title: {title} | text: {text}"


def doc_input(book: str, chapter: str, text: str, fmt: str = DOC_FORMAT) -> str:
    """Texto que se embebe para un chunk de documento."""
    title = f"{book} — {chapter}" if chapter and chapter != book else (book or "none")
    return fmt.format(title=title, text=text)


def query_input(query: str, prefix: str = QUERY_PREFIX) -> str:
    """Texto que se embebe para una consulta."""
    return prefix + query


def format_query(emb, query: str) -> str:
    """Consulta con el prefijo del embedder (o el de embeddinggemma si no define)."""
    f = getattr(emb, "format_query", None)
    return f(query) if f else query_input(query)


def format_doc(emb, book: str, chapter: str, text: str) -> str:
    """Documento con el formato del embedder (o el de embeddinggemma si no define)."""
    f = getattr(emb, "format_doc", None)
    return f(book, chapter, text) if f else doc_input(book, chapter, text)


#: Parametros de chunking por defecto (ver :mod:`recall.chunking`). Un indice con
#: otros parametros guarda los suyos en la identidad: mezclar chunks de dos
#: troceos distintos no rompe nada, pero el incremental no re-trocea archivos sin
#: cambios, asi que un cambio de troceo exige ``--rebuild``.
CHUNK_DEFAULTS = {"target": 900, "max_chars": 1000, "min_chars": 450, "overlap": 150}


def index_identity(model_identity: dict, chunking: dict | None = None) -> dict:
    """Identidad del indice = la del modelo + el troceo si no es el de por defecto.

    Con el troceo por defecto la identidad es exactamente la del modelo, para
    que los indices construidos antes de que el troceo fuera configurable sigan
    siendo validos sin reconstruirlos.
    """
    ch = {**CHUNK_DEFAULTS, **(chunking or {})}
    if ch == CHUNK_DEFAULTS:
        return model_identity
    return {**model_identity, "chunking": ch}


class Embedder(Protocol):
    """Lo que el indice y el buscador necesitan de un modelo de embeddings."""

    identity: dict
    dim: int

    def embed(self, texts: Sequence[str], kind: str) -> np.ndarray:
        """Vectores L2-normalizados float32 (n x dim). ``kind``: "doc" | "query"."""
        ...


def identity_key(identity: dict) -> str:
    """Serializacion canonica de una identidad, para comparar exacto."""
    return json.dumps(identity, sort_keys=True, ensure_ascii=True)


def _file_digest(path: Path) -> str:
    """SHA-256 completo del archivo, cacheado por (tamano, mtime) en RECALL_HOME."""
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
    """Modelo de embeddings ONNX via onnxruntime; nunca descarga nada.

    Por defecto es embeddinggemma-300m. Otros modelos se configuran con:

    * ``name``: id del repo (entra en la identidad del indice).
    * ``query_prefix`` / ``doc_format``: el prompt que espera el modelo.
    * ``pooling``: ``auto`` usa la salida ``sentence_embedding`` si el grafo la
      trae (embeddinggemma); si no, ``mean`` (promedio con mascara) o ``last``
      (ultimo token real; Qwen3-Embedding).
    * ``padding_side`` / ``pad_token``: Qwen3 rellena por la izquierda con
      ``<|endoftext|>``; con relleno a la izquierda el ultimo token es el real.
    * ``append_eos``: token que se agrega al final si el tokenizer no lo pone
      (Qwen3-Embedding agrupa sobre el token de fin).

    ``providers`` es la lista de proveedores de ONNX Runtime en orden de
    preferencia. Para indexar se usa DirectML (GPU integrada; ~20 chunks/s
    medidos en lote 32-64 con la GPU libre) y tambien para consultas: con la
    maquina cargada una consulta costo 2.2 s (p50) en CPU y 81 ms en DirectML,
    y el hook solo tiene 1.5 s. Si DirectML no arranca, se cae a CPU.
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
                f"Modelo no encontrado en la cache local: {model}. recall no descarga "
                "modelos; restaura la cache de HF o ajusta model.snapshot en config.json."
            )
        self.dim = int(dim)
        self.batch_size = int(batch_size)
        self._doc_max = int(doc_max_tokens)
        self._query_max = int(query_max_tokens)
        self._tok = Tokenizer.from_file(str(tok_path))
        avail = set(ort.get_available_providers())
        # "DmlExecutionProvider:1" elige el adaptador DirectML 1 (en esta laptop hay
        # Iris Xe y RTX 3070 Ti; el orden lo da DXGI, asi que se mide, no se asume).
        provs: list = []
        for p in providers:
            prov, _, dev = p.partition(":")
            if prov in avail:
                provs.append((prov, {"device_id": int(dev)}) if dev else prov)
        provs = provs or ["CPUExecutionProvider"]
        so = ort.SessionOptions()
        so.log_severity_level = 3
        try:
            self._sess = ort.InferenceSession(str(model), so, providers=provs)
        except Exception:
            # Adaptador DirectML inexistente o driver roto: CPU antes que nada.
            self._sess = ort.InferenceSession(str(model), so, providers=["CPUExecutionProvider"])
        self.providers = self._sess.get_providers()
        self._inputs = [i.name for i in self._sess.get_inputs()]
        outs = [o.name for o in self._sess.get_outputs()]
        if pooling == "auto":
            pooling = "sentence_embedding" if "sentence_embedding" in outs else "mean"
        if pooling not in ("sentence_embedding", "mean", "last"):
            raise ValueError(f"pooling desconocido: {pooling}")
        self.pooling = pooling
        if pooling == "sentence_embedding":
            self._out = outs.index("sentence_embedding")
        else:
            self._out = outs.index("last_hidden_state") if "last_hidden_state" in outs else 0
        self.query_prefix, self.doc_format = query_prefix, doc_format
        self._padding_side = padding_side
        self._pad_id = self._tok.token_to_id(pad_token) if pad_token else 0
        if pad_token and self._pad_id is None:
            raise ValueError(f"pad_token {pad_token!r} no existe en el tokenizer")
        self._pad_token = pad_token or "[PAD]"
        self._eos_id = self._tok.token_to_id(append_eos) if append_eos else None
        if append_eos and self._eos_id is None:
            raise ValueError(f"append_eos {append_eos!r} no existe en el tokenizer")
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
        # Solo los modelos no-default declaran estos campos: asi la identidad de
        # embeddinggemma-300m no cambia y su indice sigue valido.
        if padding_side != "right":
            self.identity["padding_side"] = padding_side
        if append_eos:
            self.identity["append_eos"] = append_eos

    def format_query(self, query: str) -> str:
        """Consulta con el prefijo de este modelo."""
        return query_input(query, self.query_prefix)

    def format_doc(self, book: str, chapter: str, text: str) -> str:
        """Documento con el formato de este modelo."""
        return doc_input(book, chapter, text, self.doc_format)

    def _encode(self, texts: list[str], max_len: int) -> tuple[np.ndarray, np.ndarray]:
        """ids y mascara, con fin de secuencia y relleno segun el modelo."""
        self._tok.no_padding()
        # Con EOS a mano se trunca un token antes para que el EOS siempre quepa.
        self._tok.enable_truncation(max_length=max_len - (1 if self._eos_id is not None else 0))
        seqs = [e.ids for e in self._tok.encode_batch(texts)]
        if self._eos_id is not None:
            seqs = [s if s and s[-1] == self._eos_id else s + [self._eos_id] for s in seqs]
        # Rellenar a multiplos de 32 reduce las formas distintas que ve DirectML.
        width = max(len(s) for s in seqs)
        width = -(-width // 32) * 32
        ii = np.full((len(seqs), width), self._pad_id, dtype=np.int64)
        am = np.zeros((len(seqs), width), dtype=np.int64)
        for r, s in enumerate(seqs):
            if self._padding_side == "left":
                ii[r, width - len(s):], am[r, width - len(s):] = s, 1
            else:
                ii[r, : len(s)], am[r, : len(s)] = s, 1
        return ii, am

    _DTYPES = {"tensor(float)": np.float32, "tensor(float16)": np.float16, "tensor(int64)": np.int64,
               "tensor(int32)": np.int32}

    def _feed(self, ii: np.ndarray, am: np.ndarray) -> dict:
        """Entradas del grafo. Las que no son de texto van vacias (longitud 0).

        embeddinggemma-2 es multimodal y pide ``image/video/audio_features``;
        Qwen3-Embedding se exporto como decodificador y pide ``past_key_values.*``.
        Con longitud 0 en la dimension simbolica (salvo el lote) el grafo las
        trata como ausentes: sin imagen, sin cache.
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
        # last: ultimo token real (con relleno a la izquierda es la ultima columna)
        last = am.shape[1] - 1 - np.argmax(am[:, ::-1], axis=1)
        return h[np.arange(len(h)), last]

    def embed(self, texts: Sequence[str], kind: str) -> np.ndarray:
        """Embebe ``texts`` (ya con prefijo) en lotes ordenados por longitud."""
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        max_len = self._query_max if kind == "query" else self._doc_max
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for s in range(0, len(order), self.batch_size):
            ids = order[s : s + self.batch_size]
            ii, am = self._encode([texts[i] for i in ids], max_len)
            h = self._sess.run([self._sess.get_outputs()[self._out].name], self._feed(ii, am))[0]
            v = self._pool(np.asarray(h, dtype=np.float32), am)
            v = np.asarray(v[:, : self.dim], dtype=np.float32)
            v /= np.linalg.norm(v, axis=1, keepdims=True) + 1e-12
            out[ids] = v
        return out


def from_config(cfg: dict, role: str) -> OnnxEmbedder:
    """Construye el embedder para ``role`` ("index" o "query") desde la config."""
    m = cfg["model"]
    provs = m["index_providers"] if role == "index" else m["query_providers"]
    extra = {k: m[k] for k in ("name", "query_prefix", "doc_format", "pooling", "padding_side",
                               "pad_token", "append_eos") if k in m}
    return OnnxEmbedder(
        m["snapshot"], m["onnx_file"], m["dim"], provs,
        m["doc_max_tokens"], m["query_max_tokens"], m["batch_size"], **extra,
    )


class HashEmbedder:
    """Embedder determinista y sin modelo para tests (bolsa de palabras hasheada).

    Textos con palabras en comun dan coseno alto; sirve para probar indice,
    incremental, dedup y umbral sin cargar 300 MB de pesos.
    """

    def __init__(self, dim: int = 64, name: str = "hash-test") -> None:
        self.dim = dim
        self.identity = {"model": name, "dim": dim, "doc_format": DOC_FORMAT, "query_prefix": QUERY_PREFIX}
        self.calls = 0

    def embed(self, texts: Sequence[str], kind: str) -> np.ndarray:
        """Vectores normalizados; cuenta los textos embebidos en ``calls``."""
        from .textutil import STOP, _WORD

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
