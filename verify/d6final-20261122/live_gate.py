"""Shared, fail-closed request ledger for the isolated ranking measurement."""

from contextlib import contextmanager
from datetime import datetime
import ipaddress
import json
import msvcrt
import os
from pathlib import Path
import socket
from threading import current_thread, local
from time import perf_counter, sleep
from urllib.parse import parse_qsl, urlsplit

from timing import DispatchObservation, RequestBudget, StopRun

APPS = ("original", "nice")
KINDS = ("identity", "identity_extra", "ranking", "image")
RANKING_QUERY = {"mode": "daily", "content": "illust", "format": "json", "p": "1"}
MAX_NEW_GETS = 85
ROUND_THREE_PRIOR = 134
RUN_CAPS = {83: MAX_NEW_GETS, ROUND_THREE_PRIOR: 40}
QUIET_FLAG = Path("S:/Work/_tmp/PV/sup/D6_QUIET.flag")


def require_quiet_window():
    if not QUIET_FLAG.is_file():
        raise StopRun("quiet_window")


class RouteDenied(ValueError):
    def __init__(self):
        super().__init__("unselected read")


def route(url, method, has_cookie):
    parts = urlsplit(url)
    if (method != "GET" or parts.scheme != "https" or parts.fragment
            or not parts.path.startswith("/") or parts.path.startswith("//")):
        raise RouteDenied()
    if parts.netloc == "i.pximg.net" and not parts.query and not has_cookie:
        return "image"
    if parts.netloc == "www.pixiv.net" and has_cookie:
        if parts.path in ("/", "/ajax/user/extra") and not parts.query:
            return "identity" if parts.path == "/" else "identity_extra"
        query = parse_qsl(parts.query, keep_blank_values=True)
        if parts.path == "/ranking.php" and len(query) == 4 and dict(query) == RANKING_QUERY:
            return "ranking"
    raise RouteDenied()


class Ledger:
    """One lock and durable in-flight record across both server processes."""

    def __init__(self, root):
        self.root = Path(root)
        self.file = self.root / "dispatch.json"
        self.lock = self.root / "dispatch.lock"

    @classmethod
    def create(cls, root, *, prior_attempts=0):
        if type(prior_attempts) is not int or not 0 <= prior_attempts < RequestBudget.CAP:
            raise ValueError("invalid prior request count")
        ledger = cls(root)
        with ledger.lock.open("xb") as stream:
            stream.write(b"0")
        ledger._save({"count": prior_attempts, "prior_attempts": prior_attempts,
                      "stop": None, "inflight": False, "events": []})
        return ledger

    @contextmanager
    def _exclusive(self):
        with self.lock.open("r+b", buffering=0) as stream:
            deadline = perf_counter() + 15
            while True:
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if perf_counter() >= deadline:
                        raise StopRun("transport") from None
                    sleep(0.01)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)

    def _save(self, state):
        temporary = self.file.with_suffix(".next")
        with temporary.open("w", encoding="ascii") as stream:
            json.dump(state, stream, ensure_ascii=True)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.file)

    def _load(self):
        return json.loads(self.file.read_text(encoding="ascii"))

    def snapshot(self):
        with self._exclusive():
            return self._load()

    def halt(self, reason):
        with self._exclusive():
            state = self._load()
            state["stop"] = state["stop"] or StopRun(reason).reason
            self._save(state)

    def _check_window(self, state):
        try:
            require_quiet_window()
        except StopRun as error:
            state["stop"] = error.reason
            self._save(state)
            raise

    def check_quiet_window(self):
        with self._exclusive():
            state = self._load()
            if state["stop"]:
                raise StopRun(state["stop"])
            self._check_window(state)

    def wait_before_click(self):
        began = perf_counter()
        while True:
            state = self.snapshot()
            if state["stop"]:
                raise StopRun(state["stop"])
            if state["inflight"]:
                raise StopRun("transport")
            if state["prior_attempts"] == ROUND_THREE_PRIOR:
                self.check_quiet_window()
            if not state["events"]:
                return (perf_counter() - began) * 1000
            wait = state["events"][-1]["end"] + RequestBudget.MIN_SECONDS - perf_counter()
            if wait <= 0:
                return (perf_counter() - began) * 1000
            sleep(wait)

    def attempt(self, side, kind, dispatch, *, deadline=None):
        if side not in APPS or kind not in KINDS:
            raise RouteDenied()
        queued = perf_counter()
        with self._exclusive():
            state = self._load()
            if state["stop"]:
                raise StopRun(state["stop"])
            prior = state["prior_attempts"]
            cap = min(RequestBudget.CAP, prior + RUN_CAPS.get(prior, MAX_NEW_GETS))
            if state["inflight"] or state["count"] >= cap:
                state["stop"] = "transport" if state["inflight"] else "budget"
                self._save(state)
                raise StopRun(state["stop"])
            if state["events"]:
                # DNS/TLS time varies before bytes actually go out. Waiting
                # after the completed attempt also bounds the on-wire spacing.
                earliest = state["events"][-1]["end"] + RequestBudget.MIN_SECONDS
                while (wait := earliest - perf_counter()) > 0:
                    if deadline is not None and earliest >= deadline:
                        raise StopRun("transport")
                    sleep(wait)
            if prior == ROUND_THREE_PRIOR:
                self._check_window(state)
            state["count"] += 1
            state["inflight"] = True
            self._save(state)
            started = perf_counter()
            status = None
            try:
                if deadline is not None and started >= deadline:
                    raise StopRun("transport")
                if prior == ROUND_THREE_PRIOR:
                    require_quiet_window()
                status, challenge, auth = dispatch()
                DispatchObservation(status, challenge, auth).validate()
            except StopRun as error:
                state["stop"] = error.reason
                raise
            except BaseException:
                state["stop"] = "transport"
                raise
            finally:
                state["events"].append({
                    "app": side, "kind": kind, "queued": queued, "start": started,
                    "end": perf_counter(), "status": status if type(status) is int else None,
                })
                state["inflight"] = False
                self._save(state)


def require_operator_confirmation(ledger):
    confirmation = json.loads((ledger.root / "normal-owner-open.json").read_text(encoding="ascii"))
    at = datetime.fromisoformat(confirmation["owner_open_at"])
    now = datetime.now().astimezone()
    state = ledger.snapshot()
    if (at.utcoffset() is None or at.astimezone().date() != now.date()
            or at > now or state["prior_attempts"] not in RUN_CAPS):
        raise ValueError("current operator confirmation and shared count required")
    if state["prior_attempts"] == ROUND_THREE_PRIOR:
        ledger.check_quiet_window()
    if state["stop"]:
        raise StopRun(state["stop"])
    if state["inflight"]:
        raise StopRun("transport")
    return state


class SocketFence:
    """Block incidental application DNS/connections around the selected transports."""

    def __init__(self):
        self.scope = local()
        self.active_dns = None

    @contextmanager
    def admitted(self, host):
        permit = host, set()
        self.scope.permit = permit
        self.active_dns = permit
        try:
            yield
        finally:
            self.active_dns = None
            self.scope.permit = None

    def install(self):
        connect, connect_ex = socket.socket.connect, socket.socket.connect_ex
        getaddrinfo = socket.getaddrinfo

        def loopback(host):
            if host is None or host == "localhost":
                return True
            try:
                return ipaddress.ip_address(host).is_loopback
            except ValueError:
                return False

        def resolve(host, *args, **kwargs):
            if loopback(host):
                return getaddrinfo(host, *args, **kwargs)
            permit = getattr(self.scope, "permit", None)
            if permit is None and current_thread().name == "pixiv-https-dns":
                # The production HTTPS owner resolves the admitted host in a
                # DNS-only worker while the owning thread waits with its deadline.
                permit = self.active_dns
            port = args[0] if args else kwargs.get("port")
            if permit is None or host != permit[0] or port != 443:
                raise RouteDenied()
            answers = getaddrinfo(host, *args, **kwargs)
            permit[1].update(answer[4][0] for answer in answers)
            return answers

        def check(address):
            if isinstance(address, tuple):
                host, port = address[:2]
                permit = getattr(self.scope, "permit", None)
                if loopback(host) or (port == 443 and permit is not None and host in permit[1]):
                    return
            raise RouteDenied()

        def bound_connect(sock, address):
            check(address)
            return connect(sock, address)

        def bound_connect_ex(sock, address):
            check(address)
            return connect_ex(sock, address)

        socket.getaddrinfo = resolve
        socket.socket.connect = bound_connect
        socket.socket.connect_ex = bound_connect_ex
