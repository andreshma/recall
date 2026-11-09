"""Stand-in for a recall service process, for the start-recall.ps1 tests (stdlib only).

Usage: ``python -I _fake_service.py <mode> <token> <record-file>``. Prints its
port on stdout and serves until killed. Every request line and its
Authorization header are appended to ``record-file``.

Modes: ``impostor`` answers ``/proof`` without knowing the token (a process that
took over the port of a dead service); ``degraded`` proves the token and
answers ``/health`` with 503 and its own pid, like a service whose engine guard
gave up.
"""

import hashlib
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

MODE, TOKEN, RECORD = sys.argv[1], sys.argv[2], sys.argv[3]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        with open(RECORD, "a", encoding="utf-8") as f:
            f.write(f"{self.requestline} | auth={self.headers.get('Authorization') or ''}\n")
        url = urlsplit(self.path)
        if url.path == "/proof":
            nonce = (parse_qs(url.query).get("nonce") or [""])[0]
            key = TOKEN.encode() if MODE == "degraded" else b"not-the-token"
            msg = f"recall-proof:{nonce}:{self.server.server_address[1]}".encode()
            self._send(200, {"proof": hmac.new(key, msg, hashlib.sha256).hexdigest()})
        elif url.path == "/health":
            self._send(503, {"ok": False, "pid": os.getpid()})
        else:
            self._send(404, {"error": "not found"})


srv = HTTPServer(("127.0.0.1", 0), Handler)
print(srv.server_address[1], flush=True)
srv.serve_forever()
