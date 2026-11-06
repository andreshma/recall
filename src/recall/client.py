"""Cliente HTTP (stdlib) del servicio local, compartido por MCP y CLI."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import config


class ServiceUnavailable(RuntimeError):
    """No hay servicio vivo o no respondio a tiempo."""


def _svc() -> dict:
    try:
        with open(config.service_file(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise ServiceUnavailable("sin service.json") from e


def _call(method: str, path: str, body: dict | None, timeout: float) -> dict:
    svc = _svc()
    url = f"http://127.0.0.1:{int(svc['port'])}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {svc['token']}", "Content-Type": "application/json"})
    # Sin proxies: una variable HTTP_PROXY del entorno no debe desviar loopback.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as r:
            return json.load(r)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise ServiceUnavailable(type(e).__name__) from e


def health(timeout: float = 1.0) -> dict:
    """``GET /health`` del servicio."""
    return _call("GET", "/health", None, timeout)


def search(queries: list[str], k: int = 8, collections: list[str] | None = None, timeout: float = 10.0,
           filters: dict | None = None) -> list[dict]:
    """``POST /search``; devuelve la lista de hits."""
    body = {"queries": queries, "k": k}
    if collections:
        body["collections"] = collections
    if filters:
        body["filters"] = filters
    return _call("POST", "/search", body, timeout)["hits"]
