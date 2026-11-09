"""The server proves it knows the token before clients send it (ADV-2), and a
malformed ``service.json`` is "no service", never a crash (F-12)."""

import http.client
import io
import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

import recall_hook as hook
from recall import client, mcp_server, service, textutil

PROMPT = "PRIVATE-PROMPT-MARKER explain bounded contexts and the ubiquitous language"


def _run_hook(home):
    (home / "config.json").write_text(json.dumps({"hook": {"mode": "active", "threshold": 0.0}}), encoding="utf-8")
    out = io.BytesIO()
    code = hook.main(stdin=io.BytesIO(json.dumps({"prompt": PROMPT}).encode()), out=out)
    return code, out.getvalue()


def _last_log(home):
    return json.loads((home / "shadow.log").read_text(encoding="utf-8").splitlines()[-1])


class _Recorder(BaseHTTPRequestHandler):
    """A process that holds the port of a dead service; records whatever it receives."""

    seen: ClassVar[list] = []
    upstream: int | None = None  # relay /proof to the genuine service on this port

    def log_message(self, *a):
        pass

    def _record(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.seen.append((self.requestline, dict(self.headers), self.rfile.read(n) if n else b""))

    def do_GET(self):
        self._record()
        if self.upstream is not None and self.path.startswith("/proof"):
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(f"http://127.0.0.1:{self.upstream}{self.path}", timeout=5) as r:
                data = r.read()
        else:
            data = json.dumps({"proof": "0" * 64}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        self._record()
        data = json.dumps({"hits": [{"path": "forged.md", "cos": 0.99, "text": "forged"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def impostor(home):
    def start(token="STALE-TOKEN-123", upstream=None):
        handler = type("Rec", (_Recorder,), {"seen": [], "upstream": upstream})
        srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        svc = {"port": srv.server_address[1], "token": token, "pid": 999999}
        (home / "service.json").write_text(json.dumps(svc), encoding="utf-8")
        return handler.seen

    servers: list = []
    yield start
    for s in servers:
        s.shutdown()
        s.server_close()


def _leaked(seen, secret):
    return [r for r in seen if secret in r[0] or secret in json.dumps(r[1]) or secret.encode() in r[2]]


def test_hook_sends_no_token_or_prompt_to_an_impostor(home, impostor):
    seen = impostor()
    assert _run_hook(home) == (0, b"")
    assert [r[0].split("?")[0] for r in seen] == ["GET /proof"]
    assert not _leaked(seen, "STALE-TOKEN-123") and not _leaked(seen, "PRIVATE-PROMPT-MARKER")
    assert "Authorization" not in seen[0][1]
    assert _last_log(home)["err"] == "ServerProofError"


def test_client_sends_no_token_to_an_impostor(home, impostor):
    seen = impostor()
    with pytest.raises(client.ServiceUnavailable):
        client.health()
    with pytest.raises(client.ServiceUnavailable):
        client.search(["PRIVATE-PROMPT-MARKER"])
    assert [r[0].split("?")[0] for r in seen] == ["GET /proof", "GET /proof"]
    assert not _leaked(seen, "STALE-TOKEN-123") and not _leaked(seen, "PRIVATE-PROMPT-MARKER")


def test_relayed_proof_from_the_genuine_service_is_rejected(home, service, impostor):
    """The proof is bound to the port: relaying the real server's answer does not pass."""
    seen = impostor(token=service["token"], upstream=service["port"])
    assert _run_hook(home) == (0, b"")
    with pytest.raises(client.ServiceUnavailable):
        client.health()
    assert all(r[0].startswith("GET /proof") for r in seen)
    assert not _leaked(seen, service["token"]) and not _leaked(seen, "PRIVATE-PROMPT-MARKER")


def test_genuine_service_proves_itself(home, service):
    assert client.health()["ok"] is True
    code, out = _run_hook(home)
    assert code == 0 and b"recall_books" in out


def test_proof_endpoint(service):
    port = service["port"]

    def get(path, host=f"127.0.0.1:{port}"):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("GET", path, headers={"Host": host})
        r = c.getresponse()
        out = r.status, r.read()
        c.close()
        return out

    nonce = "n" * 32
    code, body = get(f"/proof?nonce={nonce}")
    assert code == 200 and json.loads(body)["proof"] == service_proof(service["token"], nonce, port)
    assert get("/proof?nonce=short")[0] == 400
    assert get("/proof?nonce=" + "a:b" * 10)[0] == 400
    assert get(f"/proof?nonce={nonce}", host="evil.example:80")[0] == 403
    assert get("/health")[0] == 401  # everything else still needs the token


def service_proof(token, nonce, port):
    return service.server_proof(token, nonce, port)


def test_hook_and_package_share_the_proof_and_tag_rules():
    assert hook.server_proof("tok", "n" * 20, 4242) == service.server_proof("tok", "n" * 20, 4242)
    assert hook.TAG_CHARS == textutil.TAG_CHARS


# -- F-12: malformed service.json -----------------------------------------------------
BAD = [
    [],
    "str",
    {"port": "1234", "token": "t", "pid": 1},
    {"port": 1234, "token": "t", "pid": [1]},
    {"port": 1234, "token": "t", "pid": "1"},
    {"port": True, "token": "t", "pid": 1},
    {"port": 70000, "token": "t", "pid": 1},
    {"port": 1234, "pid": 1},
    {"port": 1234, "token": 5, "pid": 1},
    {"port": 1234, "token": "a b", "pid": 1},
    {"port": 1234, "token": "t\r\nX: y", "pid": 1},
    {"port": 1234, "token": "ñ", "pid": 1},
]


@pytest.mark.parametrize("svc", BAD, ids=range(len(BAD)))
def test_malformed_service_file_is_no_service(home, svc):
    (home / "service.json").write_text(json.dumps(svc), encoding="utf-8")
    assert service.running_service() is None
    with pytest.raises(client.ServiceUnavailable):
        client.health()
    with pytest.raises(ValueError):
        hook.read_service(str(home))
    assert _run_hook(home) == (0, b"")
    calls = []

    class Local:
        def search(self, queries, k, filters=None):
            calls.append(queries)
            return []

    assert mcp_server.default_search(Local())(["q"], 3) == [] and calls == [["q"]]


def test_hook_and_package_validate_service_files_alike(home):
    good = {"port": 1234, "token": "abc_DEF-123", "pid": 7}
    assert service.parse_service_info(good) == good == hook.read_service_from(good)
    for svc in BAD:
        assert service.parse_service_info(svc) is None
        with pytest.raises(ValueError):
            hook.read_service_from(svc)
