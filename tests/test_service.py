"""Servicio: token obligatorio, solo loopback, Host, limites y salud."""
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
    code, body = _req(service, "POST", "/search", {"queries": ["cono de incertidumbre estimación"], "k": 3})
    body.decode("ascii")  # no lanza
    assert any("ó" in h["text"] for h in json.loads(body)["hits"])


def test_bad_host_rejected(service):
    assert _req(service, "GET", "/health", host="evil.example:80")[0] == 403


def test_limits_and_validation(service):
    assert _req(service, "POST", "/search", b"x" * (70 * 1024))[0] == 413
    assert _req(service, "POST", "/search", {"queries": "no-lista"})[0] == 400
    assert _req(service, "POST", "/search", b"{no json")[0] == 400
    assert _req(service, "GET", "/otra")[0] == 404


def test_binds_loopback_only(service):
    """Desde otra interfaz local el puerto no acepta conexiones."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # no envia nada; solo elige interfaz
        ip = s.getsockname()[0]
        s.close()
    except OSError:
        ip = None
    if not ip or ip.startswith("127."):
        pytest.skip("sin interfaz no-loopback")
    with pytest.raises(OSError):
        socket.create_connection((ip, service["port"]), timeout=1).close()


def test_service_file_and_client(service):
    assert config.service_file().is_file()
    assert client.health()["chunks"] > 0
    assert client.search(["bounded context"], k=2)


def test_second_instance_exits(service):
    """Un segundo `recall serve` ve el servicio vivo y sale con 0 sin pisarlo."""
    import os
    import subprocess
    import sys

    r = subprocess.run([sys.executable, "-I", "-m", "recall", "serve"], capture_output=True, timeout=60,
                       env={**os.environ, "RECALL_HOME": str(config.home())})
    assert r.returncode == 0 and b"ya hay un servicio" in r.stderr
    assert json.loads(config.service_file().read_text())["port"] == service["port"]


def test_warm_up_embeds_several_lengths():
    from recall.embedder import HashEmbedder
    from recall.service import warm_up

    class S:
        emb = HashEmbedder()

    warm_up(S)
    assert S.emb.calls >= 5
