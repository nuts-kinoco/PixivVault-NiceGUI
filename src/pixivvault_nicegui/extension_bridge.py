"""Opt-in original browser-extension bridge; injected managed queue callbacks.

Only explicit ``start`` binds loopback. The original extension sends work or
author commands to /download and reads local saved status. An independently
injected Cookie receiver restores /api/cookie/sync without remote fetch, web/iOS
APIs, URI execution or implicit startup. HTTP receipt does not prove authentication.
"""
from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import re
import socket
import threading
from typing import Callable, Mapping

MAX_BODY_BYTES = 4096  # tiny command envelope, never image/content payloads
MAX_COOKIE_BODY_BYTES = 2 * 1024 * 1024
MAX_COOKIE_ROWS = 1024
_ID = re.compile(r"[1-9][0-9]{0,19}\Z", re.ASCII)


class BridgeUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ExtensionDownload:
    kind: str
    target_id: str
    # None is reserved for an omitted legacy work flag. Existing direct callers
    # and already serialized False intents keep the illustration namespace.
    is_novel: bool | None = False
    new_only: bool = False


def parse_download(payload: object) -> ExtensionDownload:
    if not isinstance(payload, dict):
        raise ValueError("Invalid download command")
    kind = payload.get("type")
    if kind == "work":
        allowed = {"type", "work_id", "is_novel"}
        key, flag = "work_id", "is_novel"
    elif kind == "user":
        allowed = {"type", "user_id", "new_only"}
        key, flag = "user_id", "new_only"
    else:
        raise ValueError("Invalid download type")
    target = payload.get(key)
    if (set(payload) - allowed or not isinstance(target, str)
            or not _ID.fullmatch(target) or type(payload.get(flag, False)) is not bool):
        raise ValueError("Invalid download command")
    return ExtensionDownload(
        "work" if kind == "work" else "author", target,
        is_novel=payload.get("is_novel") if kind == "work" else False,
        new_only=payload.get("new_only", False),
    )


def allowed_origin(origin: str) -> bool:
    if not isinstance(origin, str):
        return False
    return bool(re.fullmatch(r"chrome-extension://[a-p]{32}", origin, re.ASCII)
                or re.fullmatch(
                    r"https://(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*pixiv\.net(?::443)?",
                    origin, re.ASCII | re.IGNORECASE))


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _invalid_json_constant(_value):
    raise ValueError("Non-finite JSON")


def _cookie_envelope(payload):
    if (type(payload) is not dict or set(payload) != {"cookies"}
            or type(payload["cookies"]) is not list
            or not 1 <= len(payload["cookies"]) <= MAX_COOKIE_ROWS
            or any(type(row) is not dict for row in payload["cookies"])):
        raise ValueError("Invalid Cookie envelope")
    return payload["cookies"]


class _Server(HTTPServer):
    allow_reuse_address = False

    def server_bind(self):
        # Unlike SO_REUSEADDR, this never attaches to another Windows listener.
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(2.0)
        return connection, address

    def handle_error(self, request, client_address):
        # Do not print headers, payloads, source paths or backend exceptions.
        pass


class ExtensionBridge:
    """Owned loopback listener, with serialized short synchronous callbacks.

    ``enqueue`` must return an accepted managed job ID and deduplicate existing
    jobs through JobService. It must not perform a download before returning.
    Exceptions produce a generic 503; the bridge never retries a callback.
    Call close off the application's event loop if enqueue marshals to it.
    close waits for the current handler, so services may close safely afterward.
    cookie_sync, when supplied, performs bounded offline storage acceptance only;
    reader-generation drain/publication belongs to its coordinator. It is called
    once. Its result exposes only state and saved, never credentials or paths.
    Port zero is reserved for isolated fixture tests; production default 25010
    matches the unmodified extension. Bind failure never kills/reuses an owner.
    """
    def __init__(self, *, enqueue: Callable[[ExtensionDownload], str],
                 work_status: Callable[[str], Mapping],
                 author_status: Callable[[str], Mapping], port: int = 25010,
                 cookie_sync: Callable[[list[dict]], object] | None = None):
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("Invalid bridge port")
        if cookie_sync is not None and not callable(cookie_sync):
            raise ValueError("Invalid Cookie receiver")
        self.enqueue = enqueue
        self.work_status = work_status
        self.author_status = author_status
        self.cookie_sync = cookie_sync
        self.port = port
        self._server = None
        self._thread = None
        self._lock = threading.RLock()
        self._close_lock = threading.Lock()
        self._closed = False

    @property
    def running(self):
        with self._lock:
            return self._thread is not None and self._thread.is_alive() and not self._closed

    @property
    def bound_port(self):
        with self._lock:
            return self._server.server_port if self._server is not None else None

    def start(self):
        with self._lock:
            if self._closed:
                raise BridgeUnavailable("拡張連携は終了しています。")
            if self._server is not None:
                return self._server.server_port
            owner = self

            class Handler(BaseHTTPRequestHandler):
                server_version = "PixivVaultBridge"
                sys_version = ""

                def log_message(self, *args):
                    pass

                def _authorized(self):
                    if (len(self.headers.get_all("Origin", [])) != 1
                            or not allowed_origin(self.headers.get("Origin", ""))
                            or len(self.headers.get_all("Host", [])) != 1
                            or self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}"
                            or self.client_address[0] != "127.0.0.1"
                            or self.headers.get_all("Cookie", [])):
                        self._respond(403, {"status": "error", "message": "Forbidden"})
                        return False
                    return True

                def _respond(self, status, payload):
                    data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
                    self.send_response(status)
                    origin = self.headers.get("Origin", "")
                    if allowed_origin(origin):
                        self.send_header("Access-Control-Allow-Origin", origin)
                        self.send_header("Vary", "Origin")
                        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                        self.send_header("Access-Control-Allow-Headers", "Content-Type")
                        self.send_header("Access-Control-Allow-Private-Network", "true")
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.close_connection = True
                    self.wfile.write(data)

                def _route_method(self):
                    if (self.path == "/download" or
                            (self.path == "/api/cookie/sync" and owner.cookie_sync is not None)):
                        return "POST"
                    if (self.path == "/ping" or re.fullmatch(r"/api/work/[1-9][0-9]{0,19}", self.path)
                            or re.fullmatch(r"/api/user/[1-9][0-9]{0,19}/status", self.path)):
                        return "GET"
                    return None

                def do_OPTIONS(self):
                    if not self._authorized():
                        return
                    method = self._route_method()
                    headers = {v.strip().lower() for v in self.headers.get("Access-Control-Request-Headers", "").split(",") if v.strip()}
                    if method is None:
                        self._respond(404, {"status": "error"})
                    elif self.headers.get("Access-Control-Request-Method") != method or headers - {"content-type"}:
                        self._respond(403, {"status": "error"})
                    else:
                        self._respond(200, {"status": "ok"})

                def do_GET(self):
                    if not self._authorized():
                        return
                    if self._route_method() != "GET":
                        self._respond(404, {"status": "error"})
                        return
                    try:
                        if self.path == "/ping":
                            result = {"status": "ok"}
                        elif self.path.startswith("/api/work/"):
                            value = owner.work_status(self.path.rsplit("/", 1)[1])
                            if type(value.get("downloaded")) is not bool:
                                raise ValueError("Invalid saved status")
                            result = {"downloaded": value["downloaded"]}
                            for key in ("is_deleted", "is_latest"):
                                if key in value:
                                    if type(value[key]) is not bool: raise ValueError("Invalid status flag")
                                    result[key] = value[key]
                            if "update_date" in value:
                                if not isinstance(value["update_date"], str) or len(value["update_date"]) > 64:
                                    raise ValueError("Invalid update date")
                                result["update_date"] = value["update_date"]
                        else:
                            value = owner.author_status(self.path.split("/")[3])
                            count = value.get("downloaded")
                            if type(count) is not int or count < 0:
                                raise ValueError("Invalid saved count")
                            result = {"downloaded": count, "total": count}
                    except Exception:
                        self._respond(503, {"status": "error", "message": "Saved status unavailable"})
                        return
                    self._respond(200, result)

                def do_POST(self):
                    if not self._authorized():
                        return
                    if self._route_method() != "POST":
                        self._respond(404, {"status": "error"})
                        return
                    cookie_request = self.path == "/api/cookie/sync"
                    length_pattern = r"[0-9]{1,7}" if cookie_request else r"[0-9]{1,5}"
                    lengths = self.headers.get_all("Content-Length", [])
                    if (len(lengths) != 1 or not re.fullmatch(length_pattern, lengths[0])
                            or self.headers.get_all("Transfer-Encoding", [])
                            or self.headers.get_all("Content-Encoding", [])
                            or (cookie_request and len(self.headers.get_all("Content-Type", [])) != 1)):
                        self._respond(400, {"status": "error", "message": "Invalid request framing"})
                        return
                    length = int(lengths[0])
                    maximum = MAX_COOKIE_BODY_BYTES if cookie_request else MAX_BODY_BYTES
                    if not 1 <= length <= maximum:
                        self._respond(413, {"status": "error", "message": "Command envelope too large"})
                        return
                    if self.headers.get_content_type() != "application/json":
                        self._respond(415, {"status": "error", "message": "JSON required"})
                        return
                    try:
                        data = self.rfile.read(length)
                        if len(data) != length: raise ValueError("Incomplete body")
                        payload = json.loads(data.decode("utf-8"), object_pairs_hook=_json_object,
                                             parse_constant=_invalid_json_constant)
                        command = _cookie_envelope(payload) if cookie_request else parse_download(payload)
                    except (ValueError, UnicodeError, RecursionError, OSError):
                        self._respond(400, {"status": "error", "message":
                            "Invalid Cookie envelope" if cookie_request else "Invalid download command"})
                        return
                    if cookie_request:
                        try:
                            # Never repeat acceptance after an exception or a
                            # lost/unverified response; storage may have committed.
                            receipt = owner.cookie_sync(command)
                            state = getattr(receipt, "state", None)
                            if (type(state) is not str or state not in {"unchanged", "pending", "applied"}
                                    or getattr(receipt, "saved", None) is not True):
                                raise ValueError("Unconfirmed Cookie receipt")
                        except Exception as error:
                            try:
                                code = getattr(error, "code", None)
                            except Exception:
                                code = None
                            codes = {"cookie_sync_rejected": 400, "cookie_sync_conflict": 409,
                                     "cookie_sync_busy": 409, "cookie_sync_commit_unconfirmed": 503}
                            code = code if type(code) is str and code in codes else "cookie_sync_unconfirmed"
                            status = codes.get(code, 503)
                            body = {"status": "error", "code": code, "message": "Cookie update unavailable"}
                            if status == 503:
                                body["state"] = "unconfirmed"
                            self._respond(status, body)
                            return
                        self._respond(200, {"status": "ok", "state": state, "saved": True,
                                            "message": "Cookie update received"})
                        return
                    try:
                        job_id = owner.enqueue(command)
                        if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", job_id):
                            raise ValueError("Invalid managed job ID")
                    except Exception:
                        self._respond(503, {"status": "error", "message": "Managed queue unavailable"})
                        return
                    self._respond(200, {"status": "ok", "jobId": job_id})

            try:
                server = _Server(("127.0.0.1", self.port), Handler)
            except OSError:
                raise BridgeUnavailable("拡張連携ポートを使用できません。他のプロセスは変更していません。") from None
            thread = threading.Thread(target=server.serve_forever,
                                      kwargs={"poll_interval": 0.05},
                                      name="pixivvault-extension-bridge", daemon=True)
            self._server, self._thread = server, thread
            try:
                thread.start()
            except BaseException:
                self._server = self._thread = None
                server.server_close()
                raise
            return server.server_port

    def close(self):
        with self._lock:
            if self._thread is threading.current_thread():
                raise RuntimeError("Bridge cannot close from its own callback")
        with self._close_lock:
            with self._lock:
                server, thread = self._server, self._thread
                self._closed = True
            if server is None:
                return
            # Do not hold the state lock: an accepted callback may read status.
            server.shutdown()
            thread.join()
            server.server_close()
            with self._lock:
                self._server = self._thread = None
