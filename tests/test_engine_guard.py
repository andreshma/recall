"""EngineGuard: the service recovers when the ONNX Runtime session dies.

Real case (2026-11-06): after the laptop was suspended, 54 searches in a row returned
500 while ``/health`` reported ok, until the machine was rebooted.
"""

import json
import threading
import urllib.error
import urllib.request

import pytest

from recall.embedder import HashEmbedder
from recall.service import EngineGuard, log_error


class OrtFail(RuntimeError):
    """Mimics onnxruntime.capi.onnxruntime_pybind11_state.Fail."""


OrtFail.__module__ = "onnxruntime.capi.onnxruntime_pybind11_state"


class Broken(HashEmbedder):
    """Embedder whose session is no longer usable (DirectML device lost)."""

    def embed(self, texts, kind):
        raise OrtFail("887A0005 device removed")


class FakeSearcher:
    def __init__(self, emb):
        self.emb = emb

    def search(self):
        return self.emb.embed(["q"], "query")


def _guard(searcher, rebuild, **kw):
    logs, gave = [], threading.Event()
    g = EngineGuard(searcher, rebuild, give_up=gave.set, log=lambda w, e: logs.append((w, str(e))), **kw)
    return g, logs, gave


def test_engine_error_rebuilds_and_retries_once():
    s = FakeSearcher(Broken())
    g, logs, gave = _guard(s, rebuild=HashEmbedder)
    out = g.run(s.search)
    assert out.shape == (1, 64) and isinstance(s.emb, HashEmbedder) and not isinstance(s.emb, Broken)
    assert g.rebuilds == 1 and g.epoch == 1 and not gave.is_set()
    assert [w for w, _ in logs] == ["search", "rebuild"]


def test_other_errors_rebuild_only_on_the_second_in_a_row():
    calls = {"n": 0}

    class Flaky(HashEmbedder):
        def embed(self, texts, kind):
            raise ValueError("odd query")

    s = FakeSearcher(Flaky())
    built = []

    def rebuild():
        calls["n"] += 1
        built.append(HashEmbedder())
        return built[-1]

    g, _logs, _gave = _guard(s, rebuild=rebuild)
    with pytest.raises(ValueError):
        g.run(s.search)
    assert calls["n"] == 0
    g.run(s.search)  # second in a row: rebuilds and the retry succeeds
    assert calls["n"] == 1 and s.emb is built[0]


def test_success_resets_the_failure_count():
    class Once(HashEmbedder):
        bad = True

        def embed(self, texts, kind):
            if Once.bad:
                Once.bad = False
                raise ValueError("once")
            return super().embed(texts, kind)

    s = FakeSearcher(Once())
    g, _logs, _gave = _guard(s, rebuild=lambda: pytest.fail("must not rebuild"))
    with pytest.raises(ValueError):
        g.run(s.search)
    g.run(s.search)
    assert g.failures == 0


def test_gives_up_when_rebuild_fails():
    s = FakeSearcher(Broken())

    def rebuild():
        raise OSError("no GPU")

    g, logs, gave = _guard(s, rebuild=rebuild)
    with pytest.raises(OrtFail):
        g.run(s.search)
    assert gave.wait(2) and g.gave_up
    assert logs[-1][0] == "give-up"


def test_gives_up_after_max_rebuilds_without_a_success():
    s = FakeSearcher(Broken())
    g, _logs, gave = _guard(s, rebuild=Broken, max_rebuilds=2)
    for _ in range(3):
        with pytest.raises(OrtFail):
            g.run(s.search)
    assert g.rebuilds == 2 and gave.wait(2)


def test_identity_change_gives_up_instead_of_mixing_models():
    s = FakeSearcher(Broken())
    g, _logs, gave = _guard(s, rebuild=lambda: HashEmbedder(name="otro-modelo"))
    with pytest.raises(OrtFail):
        g.run(s.search)
    assert gave.wait(2) and isinstance(s.emb, Broken)


def test_concurrent_failures_rebuild_once():
    s = FakeSearcher(Broken())
    n = {"builds": 0}
    gate = threading.Barrier(4)

    def rebuild():
        n["builds"] += 1
        return HashEmbedder()

    g, _logs, gave = _guard(s, rebuild=rebuild)
    errs = []

    def worker():
        gate.wait()
        try:
            g.run(s.search)
        except Exception as e:  # pragma: no cover - only on failure
            errs.append(e)

    ts = [threading.Thread(target=worker) for _ in range(4)]
    [t.start() for t in ts]
    [t.join(5) for t in ts]
    assert not errs and n["builds"] == 1 and not gave.is_set()


def test_serve_log_records_type_without_newlines_and_rotates(tmp_path, monkeypatch):
    import recall.service as svc

    p = tmp_path / "serve.log"
    log_error("search", OrtFail("linea1\nlinea2"), p)
    line = p.read_text(encoding="utf-8")
    assert "onnxruntime.capi.onnxruntime_pybind11_state.OrtFail: linea1 linea2" in line
    assert line.count("\n") == 1
    monkeypatch.setattr(svc, "SERVE_LOG_MAX", 10)
    log_error("search", ValueError("x"), p)
    assert (tmp_path / "serve.log.1").exists()


def test_health_is_503_after_giving_up(indexed):
    from recall.search import Searcher
    from recall.service import BoundedThreadingHTTPServer, make_handler

    db, _lib, emb = indexed
    searcher = Searcher(db, emb)
    guard = EngineGuard(
        searcher, rebuild=lambda: (_ for _ in ()).throw(OSError("no GPU")), give_up=lambda: None, log=lambda w, e: None
    )
    port = [0]
    srv = BoundedThreadingHTTPServer(("127.0.0.1", 0), make_handler(searcher, "tok", port, {}, guard))
    port[0] = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(path, body=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port[0]}{path}",
            data=body,
            method="POST" if body else "GET",
            headers={"Authorization": "Bearer tok"},
        )
        try:
            with opener.open(req, timeout=10) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    try:
        assert call("/health") == (200, call("/health")[1]) and call("/health")[1]["ok"] is True
        searcher.emb = Broken()
        code, _ = call("/search", json.dumps({"q": "bounded context"}).encode())
        assert code == 500
        code, body = call("/health")
        assert code == 503 and body["ok"] is False
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_deterministic_request_error_never_gives_up():
    """ADV-1/F-18: a non-engine error that survives a fresh session reloads it once, never gives up."""

    class BadRequest(HashEmbedder):
        def embed(self, texts, kind):
            raise TypeError("TextInputSequence must be str")

    s = FakeSearcher(BadRequest())
    built = []

    def rebuild():
        built.append(BadRequest())
        return built[-1]

    g, _logs, gave = _guard(s, rebuild=rebuild)
    for _ in range(20):
        with pytest.raises(TypeError):
            g.run(s.search)
    assert len(built) == 1 and not g.gave_up and not gave.is_set()


def test_replaced_session_is_released():
    """F-18: the old ONNX session is dropped when the embedder is replaced."""
    old = Broken()
    old._sess = object()
    s = FakeSearcher(old)
    g, _logs, _gave = _guard(s, rebuild=HashEmbedder)
    g.run(s.search)
    assert s.emb is not old and old._sess is None


def test_index_mismatch_is_not_an_engine_fault():
    """A changed index identity never rebuilds the session nor gives up."""
    from recall.store import ModelMismatchError

    class Mismatch(HashEmbedder):
        def embed(self, texts, kind):
            raise ModelMismatchError("index built with another model")

    s = FakeSearcher(Mismatch())
    g, _logs, gave = _guard(s, rebuild=lambda: pytest.fail("must not rebuild"))
    for _ in range(10):
        with pytest.raises(ModelMismatchError):
            g.run(s.search)
    assert g.rebuilds == 0 and g.failures == 0 and not g.gave_up and not gave.is_set()


def test_load_error_makes_health_503_and_search_503(indexed):
    """The searcher's load_error (changed identity) -> /health not ok, /search 503, no rebuild."""
    import os

    from recall.search import Searcher
    from recall.service import BoundedThreadingHTTPServer, make_handler
    from recall.store import ModelMismatchError

    db, _lib, emb = indexed
    searcher = Searcher(db, emb)
    searcher.emb.providers = ["DmlExecutionProvider", "CPUExecutionProvider"]
    searcher.emb.device_id = 1
    built = []
    guard = EngineGuard(
        searcher, rebuild=lambda: built.append(1) or HashEmbedder(), give_up=lambda: None, log=lambda w, e: None
    )
    port = [0]
    srv = BoundedThreadingHTTPServer(("127.0.0.1", 0), make_handler(searcher, "tok", port, {}, guard))
    port[0] = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(path, body=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port[0]}{path}",
            data=body,
            method="POST" if body else "GET",
            headers={"Authorization": "Bearer tok"},
        )
        try:
            with opener.open(req, timeout=10) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    try:
        code, body = call("/health")
        assert code == 200 and body["load_error"] is None
        assert body["providers"] == ["DmlExecutionProvider", "CPUExecutionProvider"] and body["device_id"] == 1
        assert "meta_error" in body
        searcher.load_error = ModelMismatchError("index built with model other")
        code, body = call("/health")
        assert code == 503 and body["ok"] is False and body["pid"] == os.getpid()
        assert body["load_error"] == "ModelMismatchError: index built with model other"
        for _ in range(4):
            code, body = call("/search", json.dumps({"q": "bounded context"}).encode())
            assert (code, body) == (503, {"error": "index identity changed"})
        assert not built and guard.rebuilds == 0 and not guard.gave_up
    finally:
        srv.shutdown()
        srv.server_close()
