"""HTTP client (stdlib) for the local service, shared by the MCP server and the CLI."""

from __future__ import annotations

import hmac
import json
import secrets
import time
import urllib.error
import urllib.request

from .service import read_service_file, server_proof


class ServiceUnavailable(RuntimeError):
    """No live service, or it did not answer in time."""


def _svc() -> dict:
    svc = read_service_file()
    if svc is None:
        raise ServiceUnavailable("no valid service.json")
    return svc


def _verify_server(opener, port: int, token: str, timeout: float) -> None:
    """Check that the process on ``port`` knows ``token`` before sending it (ADV-2).

    After a crash ``service.json`` may point to a port now held by another
    process; it gets neither the token nor the query. Raises ServiceUnavailable.
    """
    nonce = secrets.token_urlsafe(24)
    try:
        with opener.open(f"http://127.0.0.1:{port}/proof?nonce={nonce}", timeout=timeout) as r:
            got = json.loads(r.read(4096)).get("proof")
    except (urllib.error.URLError, OSError, ValueError, AttributeError, RecursionError) as e:
        raise ServiceUnavailable(type(e).__name__) from e
    if not isinstance(got, str) or not hmac.compare_digest(got.encode(), server_proof(token, nonce, port).encode()):
        raise ServiceUnavailable("the process on the service port did not prove the token")


def _call(method: str, path: str, body: dict | None, timeout: float) -> dict:
    svc = _svc()
    port, token = svc["port"], svc["token"]
    # No proxies: an HTTP_PROXY environment variable must not divert loopback.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    _verify_server(opener, port, token, timeout)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with opener.open(req, timeout=max(0.05, deadline - time.monotonic())) as r:
            return json.load(r)
    except (urllib.error.URLError, OSError, ValueError, RecursionError) as e:
        raise ServiceUnavailable(type(e).__name__) from e


def health(timeout: float = 1.0) -> dict:
    """The service's ``GET /health``."""
    return _call("GET", "/health", None, timeout)


def search(
    queries: list[str],
    k: int = 8,
    collections: list[str] | None = None,
    timeout: float = 10.0,
    filters: dict | None = None,
) -> list[dict]:
    """``POST /search``; returns the list of hits."""
    body = {"queries": queries, "k": k}
    if collections:
        body["collections"] = collections
    if filters:
        body["filters"] = filters
    out = _call("POST", "/search", body, timeout)
    hits = out.get("hits") if isinstance(out, dict) else None
    if not isinstance(hits, list):
        raise ServiceUnavailable("invalid response")
    return hits
