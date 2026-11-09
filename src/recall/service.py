"""Local search service: one process, read-only, warm model.

Why it exists: loading embeddinggemma takes seconds and the hook has a 1.5 s
budget. A single warm process pays that once and is shared by the hook and
each session's MCP servers (instead of N copies of the model).

Security (Zero Trust even on loopback):

* Listens only on ``127.0.0.1`` on an ephemeral port, and also rejects any
  request whose origin is not 127.0.0.1.
* Requires ``Authorization: Bearer <token>``; the token is random per start
  (``secrets``), compared in constant time, and lives in ``service.json``
  inside ``%LOCALAPPDATA%\\recall`` with a user-only ACL.
* Validates ``Host`` (127.0.0.1/localhost with its own port) against DNS
  rebinding from a browser.
* Opens the index with ``mode=ro``: it cannot write it. Executes nothing from
  the content; returns text.
* Maximum body 64 KiB; ``k`` is clamped.
* Concurrent connections are capped (``MAX_CONNECTIONS``); extra ones are
  closed immediately instead of spawning another thread (AR-10). A connection
  that has not delivered its whole request within ``REQUEST_READ_DEADLINE``
  seconds is cut, so trickled bytes cannot hold a slot (ADV-6).
* Proves itself to its clients before they send anything secret (ADV-2):
  ``GET /proof?nonce=<n>`` (no token) answers ``HMAC-SHA256(token,
  "recall-proof:<n>:<port>")``. The hook, the client and ``start-recall.ps1``
  check it with the token from ``service.json`` before sending the token or a
  prompt, so a process that took over the port of a dead service learns
  neither.
"""

from __future__ import annotations

import _socket
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, BinaryIO, ClassVar
from urllib.parse import parse_qs, urlsplit

from . import config

MAX_BODY = 64 * 1024
# Legitimate clients send their whole request at once (loopback, < 64 KiB);
# the per-read ``timeout`` alone let a client trickle one byte per read forever.
REQUEST_READ_DEADLINE = 3.0
_NONCE = re.compile(r"[A-Za-z0-9_-]{16,128}")
# The hook and a few MCP servers only fill a handful of connections; 32
# leaves headroom without letting N idle sockets create N threads (AR-10).
MAX_CONNECTIONS = 32


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """``ThreadingHTTPServer`` with a cap on live request threads.

    At the cap, a new connection is closed without being served: the client
    (hook or MCP) treats it as just another failure and carries on without
    recall (fail-open).
    """

    def __init__(self, *a, max_connections: int = MAX_CONNECTIONS, **kw):
        super().__init__(*a, **kw)
        self._slots = threading.BoundedSemaphore(max_connections)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def _write_private_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    config.restrict_to_user(tmp)
    os.replace(tmp, path)


def _valid_int(value: object, lo: int, hi: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi


def parse_service_info(svc: object) -> dict | None:
    """``service.json`` contents if well formed, else None (F-12).

    Requires an int ``port`` (1-65535), an int ``pid`` > 0 and a printable
    ASCII ``token`` without spaces. ``recall_hook.py`` (stdlib only, cannot
    import this package) applies the same rules in ``read_service``.
    """
    if not isinstance(svc, dict):
        return None
    port, pid, token = svc.get("port"), svc.get("pid"), svc.get("token")
    if not (_valid_int(port, 1, 65535) and _valid_int(pid, 1, 2**32)):
        return None
    if not (isinstance(token, str) and 0 < len(token) <= 512 and token.isascii() and token.isprintable()):
        return None
    if " " in token:
        return None
    return svc


def read_service_file(path: Path | None = None) -> dict | None:
    """Validated ``service.json`` (see :func:`parse_service_info`); None if missing or invalid."""
    try:
        with open(path or config.service_file(), encoding="utf-8") as f:
            return parse_service_info(json.load(f))
    except (OSError, ValueError, RecursionError):
        return None


def server_proof(token: str, nonce: str, port: int) -> str:
    """HMAC proving that the server on ``port`` knows ``token`` (ADV-2).

    The port is part of the message so that an impostor on another port cannot
    relay the genuine server's answer. Clients recompute it and compare.
    """
    msg = f"recall-proof:{nonce}:{port}".encode("ascii")
    return hmac.new(token.encode("ascii"), msg, hashlib.sha256).hexdigest()


def pid_alive(pid: int) -> bool:
    """True if a process with that pid exists (Windows and POSIX)."""
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


def is_python_process(pid: int) -> bool:
    """True if ``pid`` is a Python interpreter (or if it cannot be told).

    After a reboot, the pid in a stale ``service.json`` may be reused by
    another program; without this check recall would think it is already
    running and would not start until that program exited.
    """
    if os.name != "nt":
        return True
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return True
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if not k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return True
        return Path(buf.value).name.lower().startswith("python")
    finally:
        k32.CloseHandle(h)


class _ServeLock:
    """Startup lock: only one ``recall serve`` loads the model at a time.

    The ``service.json`` check happens before loading the model (several
    seconds); two near-simultaneous starts both passed it and one was left
    orphaned holding the GPU and the index open. The lock is taken before
    loading and the OS releases it by itself if the process dies.
    """

    def __init__(self, path: Path):
        self.path = path
        self._f: BinaryIO | None = None

    def acquire(self) -> bool:
        f = open(self.path, "a+b")  # noqa: SIM115 - stays open while the lock is held
        try:
            if sys.platform == "win32":
                import msvcrt

                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            return False
        self._f = f
        return True

    def release(self) -> None:
        if self._f is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                self._f.seek(0)
                msvcrt.locking(self._f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        self._f.close()
        self._f = None


def _reject_constant(name: str):
    raise ValueError(f"value not allowed: {name}")


def opt_out_of_power_throttling() -> bool:
    """Declare the process HighQoS on Windows (no EcoQoS). Returns whether it applied.

    Why: launched hidden (``pythonw`` via WMI from ``start-recall.ps1``) the
    service has no window and Windows 11 heuristically classifies it as
    background work. Measured 2026-11-06: the same long prompt took 1.4-1.7 s
    that way versus 0.26-0.53 s launched in the foreground, outside the hook's
    budget. ``ControlMask = EXECUTION_SPEED`` with ``StateMask = 0`` is the
    documented opt-out of ``SetProcessInformation(ProcessPowerThrottling)``. If
    it fails (other OS, missing API) the service works the same, just slower.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class _State(ctypes.Structure):
            _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.SetProcessInformation.restype = wintypes.BOOL
        st = _State(1, 0x1, 0x0)  # CURRENT_VERSION, EXECUTION_SPEED controlled, off
        process_power_throttling = 4  # PROCESS_INFORMATION_CLASS
        return bool(
            k32.SetProcessInformation(
                k32.GetCurrentProcess(), process_power_throttling, ctypes.byref(st), ctypes.sizeof(st)
            )
        )
    except (OSError, AttributeError):
        return False


SERVE_LOG_MAX = 1024 * 1024


def log_error(where: str, e: BaseException, path: Path | None = None) -> None:
    """Record a service error in ``serve.log`` (without query text).

    ``pythonw`` has no console: without this a service answering 500 leaves no
    trace of the cause. A single rotated copy (``serve.log.1``) past 1 MiB.
    """
    path = path or (config.home() / "serve.log")
    try:
        if path.exists() and path.stat().st_size > SERVE_LOG_MAX:
            os.replace(path, path.with_name(path.name + ".1"))
        msg = str(e).replace("\n", " ")[:300]
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {where} {type(e).__module__}.{type(e).__name__}: {msg}\n")
    except OSError:
        pass


def _is_engine_error(e: BaseException) -> bool:
    """ONNX Runtime error (session or device), not a request error."""
    return type(e).__module__.startswith("onnxruntime")


def _is_index_mismatch(e: BaseException) -> bool:
    """The index was rebuilt with another model or chunking (``store.ModelMismatchError``).

    Not a device fault: a new session cannot fix it, only a restart that loads
    the new index. Imported lazily so that ``client`` (which imports this
    module) does not pull in numpy.
    """
    from .store import ModelMismatchError

    return isinstance(e, ModelMismatchError)


class EngineGuard:
    """Recreate the embedder when the ONNX Runtime session stops working.

    Why: after the laptop was suspended (05:27 on 2026-11-06) the service stayed
    alive and ``/health`` answered ok, but the next 54 searches returned 500
    until the machine was rebooted; ``start-recall.ps1`` did not relaunch it
    because it looked healthy. With DirectML that is a lost device: the session
    does not recover by itself.

    * An ONNX Runtime error recreates the session immediately; any other error,
      on the second in a row (one odd query does not bring the model down), and
      only if nothing was rebuilt since the last success: a non-engine error
      that survives a fresh session is not the engine (SQLite, a request the
      tokenizer rejects...), so it neither reloads the model again nor counts
      toward giving up (ADV-1, F-18).
    * An index rebuilt with another model or chunking (``ModelMismatchError``)
      is not a device fault: no rebuild, no give-up; /health reports it.
    * After recreating it the request is retried once; the old session is
      released.
    * If recreating fails, the model identity changes, or ``max_rebuilds`` in a
      row run out without a success after engine errors, it calls ``give_up``
      (the service shuts down and the next SessionStart starts it clean).
    """

    def __init__(
        self, searcher, rebuild, give_up, *, after_failures: int = 2, max_rebuilds: int = 3, log=log_error, warm=None
    ):
        self.searcher, self.rebuild, self.give_up = searcher, rebuild, give_up
        self.after_failures, self.max_rebuilds = after_failures, max_rebuilds
        self.log, self.warm = log, warm
        self._lock = threading.Lock()
        self.epoch = 0  # incremented on each rebuild
        self.failures = 0  # consecutive failures since the last success
        self.streak = 0  # consecutive rebuilds without a success
        self.rebuilds = 0  # total, for /health
        self.gave_up = False

    def run(self, fn):
        """Run ``fn()``; on an engine failure, recreate it and retry once."""
        for attempt in (0, 1):
            epoch = self.epoch
            try:
                out = fn()
            except Exception as e:
                if attempt or not self._failed(e, epoch):
                    raise
                continue
            with self._lock:
                self.failures = self.streak = 0
            return out
        raise AssertionError("unreachable")

    def _failed(self, e: BaseException, epoch: int) -> bool:
        """Record the failure. True if there is a new engine and a retry is worthwhile."""
        self.log("search", e)
        with self._lock:
            if self.gave_up:
                return False
            if self.epoch != epoch:  # another thread already rebuilt it meanwhile
                return True
            if _is_index_mismatch(e):  # neither rebuilds nor counts toward giving up
                return False
            self.failures += 1
            if not _is_engine_error(e) and (self.failures < self.after_failures or self.streak > 0):
                return False
            if self.streak >= self.max_rebuilds:
                return self._give_up(RuntimeError(f"{self.streak} rebuilds without success"))
            old = self.searcher.emb
            try:
                new = self.rebuild()
            except Exception as re:
                return self._give_up(re)
            if new.identity != old.identity:
                return self._give_up(RuntimeError("model identity changed; restarting"))
            self.searcher.emb = new
            _release_session(old)
            self.epoch += 1
            self.streak += 1
            self.rebuilds += 1
            self.failures = 0
            self.log("rebuild", RuntimeError(f"session rebuilt ({self.rebuilds})"))
        if self.warm is not None:
            try:
                self.warm(self.searcher)
            except Exception:
                pass
        return True

    def _give_up(self, e: BaseException) -> bool:
        self.gave_up = True
        self.log("give-up", e)
        threading.Thread(target=self.give_up, daemon=True).start()
        return False


def _release_session(emb) -> None:
    """Drop the replaced embedder's ONNX session now (F-18).

    A request still running on the old embedder may hold a reference; clearing
    the attribute frees the GPU session as soon as that request ends instead of
    whenever the embedder object is collected. A later call on it fails, and the
    guard retries it on the new engine (the epoch changed).
    """
    if hasattr(emb, "_sess"):
        try:
            emb._sess = None
        except AttributeError:
            pass


def _origin_error(client_ip: str, host: str, port: int) -> tuple[int, str] | None:
    """(code, message) if the request is not from loopback with a valid Host."""
    if client_ip != "127.0.0.1":
        return 403, "loopback only"
    if host.lower() not in (f"127.0.0.1:{port}", f"localhost:{port}"):
        return 403, "invalid host"
    return None


def _auth_error(client_ip: str, host: str, auth: str, port: int, token: str) -> tuple[int, str] | None:
    """(code, message) if the request is not from loopback with a valid Host and token."""
    err = _origin_error(client_ip, host, port)
    if err is not None:
        return err
    given = auth[7:] if auth.startswith("Bearer ") else ""
    if not given or not hmac.compare_digest(given.encode(), token.encode()):
        return 401, "token required"
    return None


def _drain(rfile, n: int) -> None:
    """Read and discard up to 1 MiB of the body.

    If the connection is closed with the body unread, Windows aborts it and the
    client sees ConnectionAborted instead of the 413.
    """
    left = min(max(n, 0), 1 << 20)
    while left > 0:
        chunk = rfile.read(min(left, 65536))
        if not chunk:
            break
        left -= len(chunk)


def _clamp_opt(value, lo: int, hi: int) -> int | None:
    return None if value is None else max(lo, min(int(value), hi))


def _parse_search(body: bytes) -> dict:
    """``Searcher.search`` arguments from the ``/search`` JSON.

    Raises ValueError/TypeError/AttributeError/OverflowError if the request is
    invalid (FilterError is a ValueError).
    """
    from .metadata import validate

    req = json.loads(body or b"{}", parse_constant=_reject_constant)
    # JSON may carry lone surrogates ("\ud800"): the tokenizer and SQLite then
    # fail on every attempt, which looked like a broken engine (ADV-1). Text that
    # cannot be encoded as UTF-8 is a bad request (UnicodeEncodeError -> 400).
    json.dumps(req, ensure_ascii=False).encode("utf-8")
    qs = req.get("queries") or [req.get("q", "")]
    if not isinstance(qs, list) or not all(isinstance(q, str) for q in qs):
        raise ValueError("queries must be a list of strings")
    colls = req.get("collections")
    if colls is not None and not (isinstance(colls, list) and all(isinstance(c, str) for c in colls)):
        raise ValueError("collections must be a list of strings")
    return {
        "queries": qs[:4],
        "k": max(1, min(int(req.get("k", 8)), 20)),
        "collections": colls,
        "snippet_chars": _clamp_opt(req.get("snippet_chars"), 200, 2000),
        "snippet_top": max(1, min(int(req.get("snippet_top", 3)), 5)),
        "budget_ms": _clamp_opt(req.get("budget_ms"), 50, 10000),
        "filters": validate(req.get("filters")),
    }


class _Handler(BaseHTTPRequestHandler):
    """HTTP handler; ``make_handler`` specializes it with searcher, token and guard."""

    server_version = "recall"
    sys_version = ""
    # An idle connection does not hold its thread forever (AR-10).
    timeout = 10
    searcher: Any = None
    token = ""
    port_ref: ClassVar[list] = [0]
    info: ClassVar[dict] = {}
    guard: EngineGuard | None = None

    def log_message(self, *a):  # no access logs: they would contain queries
        pass

    def setup(self):
        super().setup()
        # Absolute deadline for receiving the request (ADV-6): ``timeout`` is
        # per read, so a client trickling a byte every few seconds kept its slot
        # forever.
        self._read_done = self._was_cut = False
        self._deadline = threading.Timer(REQUEST_READ_DEADLINE, self._cut)
        self._deadline.daemon = True
        self._deadline.start()

    def _cut(self) -> None:
        """Close the socket under the pending read so the handler thread ends.

        On Windows ``shutdown()`` does not wake a blocked ``recv`` (measured),
        and ``socket.close()`` waits for ``rfile``/``wfile`` to be closed; the
        base ``_socket.socket.close`` closes the handle now and leaves the
        object with fileno -1, so later calls fail instead of touching a reused
        handle.
        """
        if self._read_done:
            return
        self._was_cut = True
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            _socket.socket.close(self.connection)
        except OSError:
            pass

    def handle(self):
        try:
            super().handle()
        except OSError:
            if not self._was_cut:  # the cut read is expected; anything else is reported
                raise

    def _request_read(self) -> None:
        """The whole request has arrived: the read deadline no longer applies."""
        self._read_done = True
        self._deadline.cancel()

    def finish(self):
        self._request_read()
        try:
            super().finish()
        except OSError:  # socket already cut by the read deadline
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
        err = _auth_error(
            self.client_address[0],
            self.headers.get("Host") or "",
            self.headers.get("Authorization") or "",
            self.port_ref[0],
            self.token,
        )
        if err is not None:
            self._send(err[0], {"error": err[1]})
        return err is None

    def _run(self, fn):
        return self.guard.run(fn) if self.guard is not None else fn()

    def _proof(self) -> None:
        """``GET /proof?nonce=<n>``: no token, but loopback and Host still apply."""
        err = _origin_error(self.client_address[0], self.headers.get("Host") or "", self.port_ref[0])
        if err is not None:
            self._send(err[0], {"error": err[1]})
            return
        nonce = (parse_qs(urlsplit(self.path).query).get("nonce") or [""])[0]
        if not _NONCE.fullmatch(nonce):
            self._send(400, {"error": "invalid nonce"})
            return
        self._send(200, {"proof": server_proof(self.token, nonce, self.port_ref[0])})

    def do_GET(self):
        self._request_read()
        if urlsplit(self.path).path == "/proof":
            self._proof()
            return
        if not self._authorized():
            return
        if self.path != "/health":
            self._send(404, {"error": "not found"})
            return
        self.searcher.refresh()
        guard = self.guard
        # A background reload that found another model/chunking identity: the
        # service can no longer search, so it reports itself not ok and
        # start-recall.ps1 restarts it on the new index.
        load_error = getattr(self.searcher, "load_error", None)
        ok = (guard is None or not guard.gave_up) and load_error is None
        emb = self.searcher.emb
        providers = getattr(emb, "providers", None)
        self._send(
            200 if ok else 503,
            {
                "ok": ok,
                "chunks": self.searcher.size,
                "generation": self.searcher.generation,
                "pid": os.getpid(),
                "engine_rebuilds": guard.rebuilds if guard else 0,
                "load_error": None if load_error is None else f"{type(load_error).__name__}: {load_error}"[:300],
                "providers": None if providers is None else [str(x) for x in providers],
                "device_id": getattr(emb, "device_id", None),
                "meta_error": getattr(self.searcher, "meta_error", None),
                **self.info,
            },
        )

    def _body_length(self) -> int:
        try:
            return int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return -1

    def do_POST(self):
        if not self._authorized():
            return
        if self.path != "/search":
            self._send(404, {"error": "not found"})
            return
        n = self._body_length()
        if n < 0 or n > MAX_BODY:
            _drain(self.rfile, n)  # before responding (see _drain)
            self._request_read()
            self.close_connection = True
            self._send(413, {"error": "body too large"})
            return
        body = self.rfile.read(n)
        self._request_read()
        if len(body) < n:  # cut by the read deadline or the client went away
            self.close_connection = True
            return
        try:
            args = _parse_search(body)
        except (ValueError, TypeError, AttributeError, OverflowError, RecursionError) as e:
            self._send(400, {"error": f"invalid request: {e}"})
            return
        t = time.perf_counter()
        # With ``budget_ms`` the service stops embedding windows once the
        # client will have given up, instead of burning more CPU/GPU.
        budget = args.pop("budget_ms")
        args["deadline"] = None if budget is None else t + budget / 1000
        qs = args.pop("queries")
        try:
            hits = self._run(lambda: self.searcher.search(qs, **args))
        except Exception as e:  # one query does not bring the service down
            if self.guard is None:
                log_error("search", e)
            if _is_index_mismatch(e):
                self._send(503, {"error": "index identity changed"})
                return
            self._send(500, {"error": type(e).__name__})
            return
        self._send(200, {"hits": hits, "ms": int((time.perf_counter() - t) * 1000)})


def make_handler(searcher, token: str, port_ref: list, info: dict, guard: EngineGuard | None = None):
    """Handler class bound to a ``searcher`` and a ``token``."""
    return type(
        "Handler",
        (_Handler,),
        {"searcher": searcher, "token": token, "port_ref": port_ref, "info": info, "guard": guard},
    )


def warm_up(searcher) -> None:
    """Embed queries of several lengths before announcing the service.

    DirectML compiles the graph for each new tensor shape (queries are padded
    to multiples of 32 tokens): without this, the first queries of each length
    exhausted the hook's 1.5 s budget (measured in the 2026-10-27 e2e: 5 of 5
    ``service-unavailable`` right after start).
    """
    from .embedder import format_query

    for words in (4, 20, 45, 70, 100, 140, 190):
        try:
            searcher.emb.embed([format_query(searcher.emb, " ".join(["architecture"] * words))], "query")
        except Exception:
            return


def running_service() -> dict | None:
    """Contents of ``service.json`` if the process that wrote it is still alive."""
    svc = read_service_file()
    if svc is None:
        return None
    pid = svc["pid"]
    return svc if pid_alive(pid) and is_python_process(pid) else None


def serve(
    cfg: dict | None = None, searcher=None, ready: threading.Event | None = None, server_box: list | None = None
) -> int:
    """Start the service and block. Returns an exit code.

    If one is already alive (per ``service.json``) it exits with 0 without
    starting another. ``searcher``/``ready``/``server_box`` exist for the tests.
    """
    cfg = cfg or config.load()
    config.ensure_home()
    opt_out_of_power_throttling()
    other = running_service()
    if other and other.get("pid") != os.getpid():
        print(f"recall: a service is already running (pid {other['pid']}).", file=sys.stderr)
        return 0
    lock = None
    guard = None
    srv_box: list = []
    if searcher is None:
        lock = _ServeLock(config.home() / "serve.lock")
        if not lock.acquire():
            print("recall: another `recall serve` is starting; not launching a second one.", file=sys.stderr)
            return 0
        from .embedder import forget_digest_cache
        from .search import make_searcher

        # On start the SHA-256 of the model files is recomputed instead of
        # trusting the cache (size + mtime): an in-place replacement that keeps
        # both is detected here as an identity change (AR-14).
        forget_digest_cache()
        searcher = make_searcher(cfg)
        from .embedder import from_config

        guard = EngineGuard(
            searcher, rebuild=lambda: from_config(cfg, "query"), give_up=lambda: srv_box[0].shutdown(), warm=warm_up
        )
    searcher.background_reload = True
    warm_up(searcher)
    token = secrets.token_urlsafe(32)
    port_ref = [0]
    info = {"model": searcher.emb.identity.get("model"), "dim": searcher.emb.dim}
    srv = BoundedThreadingHTTPServer(("127.0.0.1", 0), make_handler(searcher, token, port_ref, info, guard))
    srv_box.append(srv)
    srv.daemon_threads = True
    port_ref[0] = srv.server_address[1]
    _write_private_json(
        config.service_file(),
        {
            "port": port_ref[0],
            "token": token,
            "pid": os.getpid(),
            "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
    )
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
        if lock is not None:
            lock.release()
        try:
            cur = json.loads(config.service_file().read_text(encoding="utf-8"))
            if cur.get("pid") == os.getpid() and cur.get("token") == token:
                config.service_file().unlink()
        except (OSError, ValueError):
            pass
    # 1 if the engine was given up as lost (EngineGuard shut the server down).
    return 1 if guard is not None and guard.gave_up else 0
