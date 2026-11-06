"""Correcciones de la auditoria 360 y la revision adversarial (2026-11-06).

Cada test nombra el hallazgo que cubre (``docs/ADVERSARIAL_REVIEW.md``,
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
    req = urllib.request.Request(f"http://127.0.0.1:{service['port']}/search", data=body, method="POST",
                                 headers={"Authorization": f"Bearer {service['token']}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=10) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


# -- AR-05: doc_max_tokens en la identidad -------------------------------------------
def _db_with_identity(tmp_path, identity: dict):
    con = store.open_rw(tmp_path / "i.sqlite3")
    from recall.embedder import identity_key

    con.execute("insert or replace into meta values('model_identity', ?)", (identity_key(identity),))
    con.execute("insert into chunks(path, collection, book_id, book, chapter, section, line_start, line_end, "
                "lang, text, input_sha, vec) values('a.md','books','b','B','C','S',1,2,'en','t','x',?)",
                (np.zeros(4, dtype=np.float32).tobytes(),))
    con.commit()
    return con


BASE = {"model": "m", "revision": "r", "files": {}, "dim": 4, "doc_format": "f", "query_prefix": "q",
        "pooling": "mean", "normalized": True}


def test_legacy_index_without_doc_max_tokens_is_upgraded(tmp_path):
    con = _db_with_identity(tmp_path, BASE)
    current = {**BASE, "doc_max_tokens": 1100}
    store.check_identity(con, current, writing=False)  # lectura: se acepta, no se toca
    assert "doc_max_tokens" not in json.loads(store.get_meta(con, "model_identity"))
    store.check_identity(con, current, writing=True)  # escritura: se registra
    assert json.loads(store.get_meta(con, "model_identity"))["doc_max_tokens"] == 1100
    # desde entonces, otro limite (o quitarlo de la config) es un error explicito
    with pytest.raises(store.ModelMismatchError):
        store.check_identity(con, BASE, writing=False)
    with pytest.raises(store.ModelMismatchError):
        store.check_identity(con, {**BASE, "doc_max_tokens": 2000}, writing=False)


def test_legacy_upgrade_does_not_hide_other_differences(tmp_path):
    con = _db_with_identity(tmp_path, BASE)
    with pytest.raises(store.ModelMismatchError):
        store.check_identity(con, {**BASE, "dim": 8, "doc_max_tokens": 1100}, writing=True)


# -- AR-11: valores no finitos en la peticion ------------------------------------------
@pytest.mark.parametrize("body", [b'{"queries": ["x y"], "k": 1e400}', b'{"queries": ["x y"], "k": Infinity}',
                                  b'{"queries": ["x y"], "snippet_chars": NaN}'])
def test_non_finite_numbers_are_a_400(service, body):
    status, data = _post(service, body)
    assert status == 400 and "error" in data
    assert _post(service, b'{"queries": ["bounded context"], "k": 2}')[0] == 200  # sigue vivo


# -- AR-08: pid reusado por otro programa ----------------------------------------------
@pytest.mark.skipif(os.name != "nt", reason="la comprobacion de imagen es de Windows")
def test_reused_pid_is_not_a_live_service():
    from recall.service import is_python_process

    other = subprocess.Popen(["ping", "-n", "30", "127.0.0.1"], stdout=subprocess.DEVNULL)
    try:
        assert is_python_process(os.getpid()) is True
        assert is_python_process(other.pid) is False
    finally:
        other.kill()


# -- AR-09: candado de arranque --------------------------------------------------------
def test_serve_lock_is_exclusive(tmp_path):
    from recall.service import _ServeLock

    a, b = _ServeLock(tmp_path / "serve.lock"), _ServeLock(tmp_path / "serve.lock")
    assert a.acquire() is True
    assert b.acquire() is False
    a.release()
    assert b.acquire() is True
    b.release()


def test_second_process_waits_for_the_lock(home):
    """Un `recall serve` que ve el candado tomado sale con 0 sin cargar el modelo."""
    from recall import config
    from recall.service import _ServeLock

    lock = _ServeLock(config.home() / "serve.lock")
    assert lock.acquire()
    try:
        r = subprocess.run([sys.executable, "-I", "-m", "recall", "serve"], capture_output=True, timeout=60,
                           env={**os.environ, "RECALL_HOME": str(config.home())})
        assert r.returncode == 0 and "arrancando" in r.stderr.decode("utf-8", "replace")
    finally:
        lock.release()
