"""Service: mandatory token, loopback only, Host, limits and health."""

import http.client
import json
import socket

import pytest

from recall import client, config


def _req(svc, method, path, body=None, token="ok", host=None):
    c = http.client.HTTPConnection("127.0.0.1", svc["port"], timeout=5)
    headers = {"Content-Type": "application/json"}
    if token == "ok":
        headers["Authorization"] = f"Bearer {svc['token']}"
    elif token:
        headers["Authorization"] = f"Bearer {token}"
    if host:
        headers["Host"] = host
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    c.request(method, path, body=data, headers=headers)
    r = c.getresponse()
    out = r.status, r.read()
    c.close()
    return out


def test_health_requires_token(service):
    assert _req(service, "GET", "/health", token=None)[0] == 401
    assert _req(service, "GET", "/health", token="incorrecto")[0] == 401
    code, body = _req(service, "GET", "/health")
    assert code == 200 and json.loads(body)["ok"] is True


def test_search_requires_token(service):
    assert _req(service, "POST", "/search", {"q": "error budget"}, token=None)[0] == 401
    code, body = _req(service, "POST", "/search", {"queries": ["error budget burn rate"], "k": 3})
    assert code == 200
    hits = json.loads(body)["hits"]
    assert hits and "cos" in hits[0]


def test_response_is_ascii_json(service):
    _code, body = _req(service, "POST", "/search", {"queries": ["cono de incertidumbre estimación"], "k": 3})
    body.decode("ascii")  # does not raise
    assert any("ó" in h["text"] for h in json.loads(body)["hits"])


def test_bad_host_rejected(service):
    assert _req(service, "GET", "/health", host="evil.example:80")[0] == 403


def test_limits_and_validation(service):
    assert _req(service, "POST", "/search", b"x" * (70 * 1024))[0] == 413
    assert _req(service, "POST", "/search", {"queries": "no-lista"})[0] == 400
    assert _req(service, "POST", "/search", b"{no json")[0] == 400
    assert _req(service, "GET", "/otra")[0] == 404


def test_binds_loopback_only(service):
    """From another local interface the port does not accept connections."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # sends nothing; only picks an interface
        ip = s.getsockname()[0]
        s.close()
    except OSError:
        ip = None
    if not ip or ip.startswith("127."):
        pytest.skip("no non-loopback interface")
    with pytest.raises(OSError):
        socket.create_connection((ip, service["port"]), timeout=1).close()


def test_service_file_and_client(service):
    assert config.service_file().is_file()
    assert client.health()["chunks"] > 0
    assert client.search(["bounded context"], k=2)


def test_second_instance_exits(service):
    """A second `recall serve` sees the live service and exits with 0 without clobbering it."""
    import os
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, "-I", "-m", "recall", "serve"],
        capture_output=True,
        timeout=60,
        env={**os.environ, "RECALL_HOME": str(config.home())},
    )
    assert r.returncode == 0 and b"already running" in r.stderr
    assert json.loads(config.service_file().read_text())["port"] == service["port"]


def test_warm_up_embeds_several_lengths():
    from recall.embedder import HashEmbedder
    from recall.service import warm_up

    class S:
        emb = HashEmbedder()

    warm_up(S)
    assert S.emb.calls >= 5


def test_lone_surrogate_is_a_400(service):
    """ADV-1: text that is not encodable as UTF-8 is a bad request, not an engine failure."""
    for body in ({"queries": ["bounded context \ud800"]}, {"q": "x", "collections": ["books\udfff"]}):
        code, msg = _req(service, "POST", "/search", json.dumps(body).encode())
        assert code == 400, msg
    assert _req(service, "POST", "/search", {"queries": ["bounded context"]})[0] == 200


def test_deeply_nested_body_is_a_400(service):
    """ADV-10: a nested-array body under 64 KiB is answered, not dropped."""
    assert _req(service, "POST", "/search", b"[" * 60000)[0] == 400
    assert _req(service, "POST", "/search", {"queries": ["bounded context"]})[0] == 200


def test_trickled_request_is_cut_at_the_deadline(service, monkeypatch):
    """ADV-6: one header byte at a time cannot hold a connection slot past the read deadline."""
    import time

    from recall import service as svc_mod

    monkeypatch.setattr(svc_mod, "REQUEST_READ_DEADLINE", 0.6)
    s = socket.create_connection(("127.0.0.1", service["port"]), timeout=5)
    s.sendall(b"GET /health HTTP/1.1\r\nX: ")
    t0 = time.monotonic()
    closed = False
    while time.monotonic() - t0 < 4:
        try:
            s.sendall(b"a")
            s.settimeout(0.2)
            if s.recv(1024) == b"":
                closed = True
                break
        except TimeoutError:
            continue
        except OSError:
            closed = True
            break
    s.close()
    assert closed and time.monotonic() - t0 < 2.5
    assert _req(service, "GET", "/health")[0] == 200


def test_health_reports_engine_and_metadata_state(service):
    """providers/device_id (None for HashEmbedder), meta_error and load_error are present."""
    code, body = _req(service, "GET", "/health")
    body = json.loads(body)
    assert code == 200 and body["load_error"] is None
    assert body["providers"] is None and body["device_id"] is None and "meta_error" in body
