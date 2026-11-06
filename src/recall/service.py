"""Servicio local de busqueda: un proceso, solo lectura, modelo caliente.

Por que existe: cargar embeddinggemma cuesta segundos y el hook tiene 1.5 s
de presupuesto. Un unico proceso caliente lo paga una vez y lo comparten el
hook y los servidores MCP de cada sesion (en vez de N copias del modelo).

Seguridad (Zero Trust aun en loopback):

* Escucha solo en ``127.0.0.1`` en un puerto efimero, y ademas rechaza
  cualquier peticion cuyo origen no sea 127.0.0.1.
* Exige ``Authorization: Bearer <token>``; el token es aleatorio por arranque
  (``secrets``), se compara en tiempo constante y vive en ``service.json``
  dentro de ``%LOCALAPPDATA%\\recall`` con ACL solo del usuario.
* Valida ``Host`` (127.0.0.1/localhost con el puerto propio) contra DNS
  rebinding desde un navegador.
* Abre el indice con ``mode=ro``: no puede escribirlo. No ejecuta nada del
  contenido; devuelve texto.
* Cuerpo maximo 64 KiB; ``k`` acotado.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config

MAX_BODY = 64 * 1024


def _write_private_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    config.restrict_to_user(tmp)
    os.replace(tmp, path)


def pid_alive(pid: int) -> bool:
    """True si existe un proceso con ese pid (Windows y POSIX)."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def make_handler(searcher, token: str, port_ref: list, info: dict):
    """Clase manejadora ligada a un ``searcher`` y un ``token``."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "recall"
        sys_version = ""

        def log_message(self, *a):  # sin logs de acceso: llevarian consultas
            pass

        def _send(self, code: int, obj: dict) -> None:
            data = json.dumps(obj, ensure_ascii=True).encode("ascii")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self) -> bool:
            if self.client_address[0] != "127.0.0.1":
                self._send(403, {"error": "solo loopback"})
                return False
            host = (self.headers.get("Host") or "").lower()
            if host not in (f"127.0.0.1:{port_ref[0]}", f"localhost:{port_ref[0]}"):
                self._send(403, {"error": "host invalido"})
                return False
            auth = self.headers.get("Authorization") or ""
            given = auth[7:] if auth.startswith("Bearer ") else ""
            if not given or not hmac.compare_digest(given.encode(), token.encode()):
                self._send(401, {"error": "token requerido"})
                return False
            return True

        def do_GET(self):
            if not self._authorized():
                return
            if self.path != "/health":
                self._send(404, {"error": "no existe"})
                return
            searcher.refresh()
            self._send(200, {"ok": True, "chunks": searcher.size, "generation": searcher.generation,
                             "pid": os.getpid(), **info})

        def do_POST(self):
            if not self._authorized():
                return
            if self.path != "/search":
                self._send(404, {"error": "no existe"})
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if n < 0 or n > MAX_BODY:
                # Drenar (hasta 1 MiB) antes de responder: si se cierra con el
                # cuerpo sin leer, Windows aborta la conexion y el cliente ve
                # ConnectionAborted en vez del 413.
                left = min(max(n, 0), 1 << 20)
                while left > 0:
                    chunk = self.rfile.read(min(left, 65536))
                    if not chunk:
                        break
                    left -= len(chunk)
                self.close_connection = True
                self._send(413, {"error": "cuerpo demasiado grande"})
                return
            try:
                req = json.loads(self.rfile.read(n) or b"{}")
                qs = req.get("queries") or [req.get("q", "")]
                if not isinstance(qs, list) or not all(isinstance(q, str) for q in qs):
                    raise ValueError("queries debe ser lista de strings")
                colls = req.get("collections")
                if colls is not None and not (isinstance(colls, list) and all(isinstance(c, str) for c in colls)):
                    raise ValueError("collections debe ser lista de strings")
                k = max(1, min(int(req.get("k", 8)), 20))
                snip = req.get("snippet_chars")
                snip = None if snip is None else max(200, min(int(snip), 2000))
                snip_top = max(1, min(int(req.get("snippet_top", 3)), 5))
                from .metadata import validate

                filters = validate(req.get("filters"))
            except (ValueError, TypeError, AttributeError) as e:  # FilterError es ValueError
                self._send(400, {"error": f"peticion invalida: {e}"})
                return
            t = time.perf_counter()
            try:
                hits = searcher.search(qs[:4], k=k, collections=colls, filters=filters,
                                       snippet_chars=snip, snippet_top=snip_top)
            except Exception as e:  # el servicio no se cae por una consulta
                self._send(500, {"error": type(e).__name__})
                return
            self._send(200, {"hits": hits, "ms": int((time.perf_counter() - t) * 1000)})

    return Handler


def warm_up(searcher) -> None:
    """Embebe consultas de varias longitudes antes de anunciar el servicio.

    DirectML compila el grafo para cada forma de tensor nueva (las consultas se
    rellenan a multiplos de 32 tokens): sin esto, las primeras consultas de cada
    longitud agotaban el presupuesto de 1.5 s del hook (medido en el e2e del
    2026-10-27: 5 de 5 ``service-unavailable`` recien arrancado).
    """
    from .embedder import format_query

    for words in (4, 20, 45, 70, 100, 140, 190):
        try:
            searcher.emb.embed([format_query(searcher.emb, " ".join(["architecture"] * words))], "query")
        except Exception:
            return


def running_service() -> dict | None:
    """Contenido de ``service.json`` si el proceso que lo escribio sigue vivo."""
    try:
        with open(config.service_file(), encoding="utf-8") as f:
            svc = json.load(f)
    except (OSError, ValueError):
        return None
    return svc if pid_alive(int(svc.get("pid", 0))) else None


def serve(cfg: dict | None = None, searcher=None, ready: threading.Event | None = None,
          server_box: list | None = None) -> int:
    """Arranca el servicio y bloquea. Devuelve codigo de salida.

    Si ya hay uno vivo (segun ``service.json``) sale con 0 sin arrancar otro.
    ``searcher``/``ready``/``server_box`` existen para los tests.
    """
    cfg = cfg or config.load()
    config.ensure_home()
    other = running_service()
    if other and other.get("pid") != os.getpid():
        print(f"recall: ya hay un servicio (pid {other['pid']}).", file=sys.stderr)
        return 0
    if searcher is None:
        from .search import make_searcher

        searcher = make_searcher(cfg)
    searcher.background_reload = True
    warm_up(searcher)
    token = secrets.token_urlsafe(32)
    port_ref = [0]
    info = {"model": searcher.emb.identity.get("model"), "dim": searcher.emb.dim}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(searcher, token, port_ref, info))
    srv.daemon_threads = True
    port_ref[0] = srv.server_address[1]
    _write_private_json(config.service_file(), {
        "port": port_ref[0], "token": token, "pid": os.getpid(),
        "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    if server_box is not None:
        server_box.append(srv)
    if ready is not None:
        ready.set()
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        try:
            cur = json.loads(config.service_file().read_text(encoding="utf-8"))
            if cur.get("pid") == os.getpid() and cur.get("token") == token:
                config.service_file().unlink()
        except (OSError, ValueError):
            pass
    return 0
