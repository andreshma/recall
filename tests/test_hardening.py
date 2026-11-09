"""Fixes from the 360 audit and the adversarial review (2026-11-06).

Each test names the finding it covers (``docs/ADVERSARIAL_REVIEW.md``,
``audits/report-recall-2026-11-06.md``).
"""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

import numpy as np
import pytest

from recall import store


def _post(service, body: bytes):
    req = urllib.request.Request(
        f"http://127.0.0.1:{service['port']}/search",
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {service['token']}"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=10) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


# -- AR-05: doc_max_tokens in the identity --------------------------------------------
def _db_with_identity(tmp_path, identity: dict):
    con = store.open_rw(tmp_path / "i.sqlite3")
    from recall.embedder import identity_key

    con.execute("insert or replace into meta values('model_identity', ?)", (identity_key(identity),))
    con.execute(
        "insert into chunks(path, collection, book_id, book, chapter, section, line_start, line_end, "
        "lang, text, input_sha, vec) values('a.md','books','b','B','C','S',1,2,'en','t','x',?)",
        (np.zeros(4, dtype=np.float32).tobytes(),),
    )
    con.commit()
    return con


BASE = {
    "model": "m",
    "revision": "r",
    "files": {},
    "dim": 4,
    "doc_format": "f",
    "query_prefix": "q",
    "pooling": "mean",
    "normalized": True,
}


def test_legacy_index_without_doc_max_tokens_is_upgraded(tmp_path):
    con = _db_with_identity(tmp_path, BASE)
    current = {**BASE, "doc_max_tokens": 1100}
    store.check_identity(con, current, writing=False)  # read: accepted, left untouched
    assert "doc_max_tokens" not in json.loads(store.get_meta(con, "model_identity"))
    store.check_identity(con, current, writing=True)  # write: recorded
    assert json.loads(store.get_meta(con, "model_identity"))["doc_max_tokens"] == 1100
    # from then on, a different limit (or removing it from the config) is an explicit error
    with pytest.raises(store.ModelMismatchError):
        store.check_identity(con, BASE, writing=False)
    with pytest.raises(store.ModelMismatchError):
        store.check_identity(con, {**BASE, "doc_max_tokens": 2000}, writing=False)


def test_legacy_upgrade_does_not_hide_other_differences(tmp_path):
    con = _db_with_identity(tmp_path, BASE)
    with pytest.raises(store.ModelMismatchError):
        store.check_identity(con, {**BASE, "dim": 8, "doc_max_tokens": 1100}, writing=True)


# -- AR-11: non-finite values in the request -------------------------------------------
@pytest.mark.parametrize(
    "body",
    [
        b'{"queries": ["x y"], "k": 1e400}',
        b'{"queries": ["x y"], "k": Infinity}',
        b'{"queries": ["x y"], "snippet_chars": NaN}',
    ],
)
def test_non_finite_numbers_are_a_400(service, body):
    status, data = _post(service, body)
    assert status == 400 and "error" in data
    assert _post(service, b'{"queries": ["bounded context"], "k": 2}')[0] == 200  # still alive


# -- AR-08: pid reused by another program ----------------------------------------------
@pytest.mark.skipif(os.name != "nt", reason="the process image check is Windows-only")
def test_reused_pid_is_not_a_live_service():
    from recall.service import is_python_process

    other = subprocess.Popen(["ping", "-n", "30", "127.0.0.1"], stdout=subprocess.DEVNULL)
    try:
        assert is_python_process(os.getpid()) is True
        assert is_python_process(other.pid) is False
    finally:
        other.kill()


# -- AR-09: startup lock ---------------------------------------------------------------
def test_serve_lock_is_exclusive(tmp_path):
    from recall.service import _ServeLock

    a, b = _ServeLock(tmp_path / "serve.lock"), _ServeLock(tmp_path / "serve.lock")
    assert a.acquire() is True
    assert b.acquire() is False
    a.release()
    assert b.acquire() is True
    b.release()


def test_second_process_waits_for_the_lock(home):
    """A `recall serve` that finds the lock taken exits with 0 without loading the model."""
    from recall import config
    from recall.service import _ServeLock

    lock = _ServeLock(config.home() / "serve.lock")
    assert lock.acquire()
    try:
        r = subprocess.run(
            [sys.executable, "-I", "-m", "recall", "serve"],
            capture_output=True,
            timeout=60,
            env={**os.environ, "RECALL_HOME": str(config.home())},
        )
        assert r.returncode == 0 and "is starting" in r.stderr.decode("utf-8", "replace")
    finally:
        lock.release()


# -- AR-10: cap on concurrent connections ----------------------------------------------
def _until(cond, timeout: float = 10.0) -> bool:
    """Poll ``cond`` until it holds or ``timeout`` seconds pass (no fixed sleeps)."""
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return bool(cond())


def test_connections_beyond_the_cap_are_closed_and_slots_come_back():
    import socket
    import threading
    from http.server import BaseHTTPRequestHandler

    from recall.service import BoundedThreadingHTTPServer

    class Idle(BaseHTTPRequestHandler):
        timeout = 5

        def do_GET(self):
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = BoundedThreadingHTTPServer(("127.0.0.1", 0), Idle, max_connections=2)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    addr = srv.server_address
    try:
        idle = [socket.create_connection(addr, timeout=5) for _ in range(2)]
        # Wait (bounded) until the server has accepted both and filled the slots.
        assert _until(lambda: srv._slots._value == 0)
        extra = socket.create_connection(addr, timeout=5)
        assert extra.recv(1) == b""  # closed without being served
        extra.close()
        for s in idle:
            s.close()
        assert _until(lambda: srv._slots._value == 2)  # the threads see EOF and free their slot
        ok = socket.create_connection(addr, timeout=5)
        ok.sendall(b"GET / HTTP/1.0\r\nHost: x\r\n\r\n")
        assert ok.recv(64).startswith(b"HTTP/1.0 204")
        ok.close()
    finally:
        srv.shutdown()
        srv.server_close()


# -- AR-14: the service does not trust the digest cache --------------------------------
def test_forget_digest_cache_detects_in_place_swap(home, tmp_path):
    from recall.embedder import _file_digest, forget_digest_cache

    f = tmp_path / "model.onnx"
    f.write_bytes(b"A" * 64)
    st = f.stat()
    first = _file_digest(f)
    f.write_bytes(b"B" * 64)  # same size...
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))  # ...and same mtime
    assert _file_digest(f) == first  # the cache misses it: that is why AR-14 exists
    forget_digest_cache()
    assert _file_digest(f) != first
    forget_digest_cache()
    forget_digest_cache()  # no cache: does not fail


# -- AR-15: a truncated blob does not break loading ------------------------------------
def test_truncated_vector_is_skipped_not_fatal(indexed, capsys):
    from recall.search import Searcher

    db, _lib, emb = indexed
    con = store.open_rw(db)
    n = con.execute("select count(*) from chunks").fetchone()[0]
    victim = con.execute("select min(id) from chunks").fetchone()[0]
    con.execute("update chunks set vec = substr(vec, 1, 8) where id = ?", (victim,))
    con.commit()
    con.close()
    s = Searcher(db, emb)
    assert len(s.ids) == n - 1 and victim not in set(s.ids.tolist())
    assert "verify_index.py" in capsys.readouterr().err


# -- budget_ms: the service stops embedding once the hook has given up ----------------
def test_budget_ms_is_accepted_and_validated(service):
    ok, body = _post(service, json.dumps({"queries": ["cono de incertidumbre"], "budget_ms": 1000}).encode())
    assert ok == 200 and body["hits"]
    bad, _ = _post(service, json.dumps({"queries": ["x"], "budget_ms": "pronto"}).encode())
    assert bad == 400


# -- EcoQoS: the hidden service must not run as background work -----------------------
def test_power_throttling_opt_out_succeeds_on_windows():
    from recall.service import opt_out_of_power_throttling

    assert opt_out_of_power_throttling() is (sys.platform == "win32")
