"""Owned HTTPS resources shared by the bounded GET and opt-in POST clients.

This module performs no URL admission, Cookie handling, HTTP status decisions,
or retries. Each caller grants a prepared request an absolute deadline.
An idle connection keeps its original socket fence until disposal.
"""
from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
import http.client
from select import select
import socket
import ssl
from threading import Event, RLock, Thread, Timer, current_thread
import time
from urllib.request import HTTPSHandler

from .vault_socket_fence import SocketFence, SocketFenceError


_context_lock = RLock()
_trust_clock = time.monotonic
_TRUST_REFRESH_SECONDS = 300
_IDLE_SECONDS = 30


def _trust_epoch():
    return int(_trust_clock() // _TRUST_REFRESH_SECONDS)


@lru_cache(maxsize=1)
def _default_context(trust_epoch):
    # Cache only the current interval. SSLContexts are configured at creation
    # and never mutated after publication.
    return ssl.create_default_context()


class HTTPSOwner:
    """One connection lifetime with a refreshed watchdog for each attempt."""
    def __init__(self, timeout, deadline, host, port):
        self.timeout, self.deadline = timeout, deadline
        self.host, self.port = host, port
        self._http_status_observer = None
        self.keep_alive = False
        self.reused_dispatch = False
        self.reused_disconnect = False
        self.response_started = False
        self.trust_epoch = _trust_epoch()
        self.idle_until = 0.0
        self.fence = SocketFence()
        self.epoch = self.fence.snapshot()
        self.connections, self.responses, self.sockets, self.registrations = [], [], [], []
        self.timer = None

    def abort(self):
        try:
            self.fence.invalidate()
        except BaseException as error:
            self.fence.mark_close_unverified(error)

    def check(self):
        if time.monotonic() >= self.deadline:
            self.abort()
            raise TimeoutError('HTTPS request interrupted')
        try:
            self.fence.require_healthy(self.epoch)
        except SocketFenceError:
            raise TimeoutError('HTTPS request interrupted') from None

    def remaining(self):
        self.check()
        return max(.001, min(self.timeout, self.deadline - time.monotonic()))

    def start(self):
        self.check()
        self.timer = Timer(max(0, self.deadline - time.monotonic()), self.abort)
        self.timer.daemon = True
        self.timer.start()

    def addresses(self):
        # DNS cannot be interrupted through a socket we own. This resolver can
        # only return addresses; it never allocates/connects a request socket.
        ready, result = Event(), {}
        host, port = self.host, self.port
        def resolve():
            try:
                result['addresses'] = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
            except Exception:
                result['failed'] = True
            finally:
                ready.set()
        resolver = Thread(target=resolve, name='pixiv-https-dns', daemon=True)
        resolver.start()
        while not ready.wait(min(.05, self.remaining())):
            self.check()
        self.check()
        if result.get('failed') or not result.get('addresses'):
            raise OSError('HTTPS address unavailable')
        return result['addresses']

    def response(self, response):
        if not any(previous is response for previous in self.responses):
            self.responses.append(response)
        return response

    def release_candidate(self, sock, registration):
        with self.fence.cleanup():
            try:
                sock.close()
                registration.__exit__(None, None, None)
            except BaseException as error:
                self.fence.mark_close_unverified(error, registration=registration)
                raise TimeoutError('HTTPS request interrupted') from None
            self.sockets.remove(sock)
            self.registrations.remove(registration)

    def stop_timer(self):
        if self.timer is not None:
            self.timer.cancel()
            if self.timer.ident is not None and self.timer is not current_thread():
                self.timer.join()
            self.timer = None
        self._http_status_observer = None

    def prepare_idle(self):
        if len(self.responses) != 1 or len(self.connections) != 1:
            return False
        response, connection = self.responses[0], self.connections[0]
        if (connection.sock is None or not response.isclosed() or response.will_close
                or response.length not in (None, 0)):
            return False
        lengths = response.headers.get_all("Content-Length", [])
        encodings = response.headers.get_all("Transfer-Encoding", [])
        connection_tokens = [token.strip().lower()
                             for value in response.headers.get_all("Connection", [])
                             for token in value.split(",")]
        if ("close" in connection_tokens
                or (response.chunked and (lengths or [v.lower() for v in encodings] != ["chunked"]))
                or (not response.chunked and (encodings or len(lengths) != 1))):
            return False
        # http.client retains the completed, closed response until its next
        # request. The result has already copied any needed response metadata.
        response.headers = response.msg = response.url = response.reason = None
        self.responses.clear()
        return True

    def idle_usable(self):
        if time.monotonic() >= self.idle_until or self.trust_epoch != _trust_epoch():
            return False
        sock = self.connections[0].sock
        try:
            # A peer close or unsolicited data makes this socket unsuitable.
            # There is no recheck/replay once a new request has begun.
            return sock is not None and not sock.pending() and not select([sock], [], [], 0)[0]
        except (OSError, ValueError):
            return False

    def cleanup(self):
        self.stop_timer()
        failure = None
        with self.fence.cleanup():
            # The HTTPResponse fp is an owning alias, even when urllib has
            # detached/closed the HTTPSConnection's socket reference.
            for resource in (*reversed(self.responses), *reversed(self.connections),
                             *reversed(self.sockets)):
                try:
                    close = getattr(resource, 'close', None)
                    if callable(close):
                        close()
                    else:
                        resource.__exit__(None, None, None)
                except BaseException as error:
                    if failure is None:
                        failure = error
            if failure is not None:
                for registration in self.registrations:
                    self.fence.mark_close_unverified(failure, registration=registration)
                if not self.registrations:
                    self.fence.mark_close_unverified(failure)
            else:
                for registration in reversed(self.registrations):
                    try:
                        registration.__exit__(None, None, None)
                    except BaseException as error:
                        self.fence.mark_close_unverified(error, registration=registration)
        if self.fence.revocation_unverified:
            self.abort()
            raise TimeoutError('HTTPS request interrupted')


class _EarlyObservedHTTPResponse(http.client.HTTPResponse):
    """Report parsed numeric status before http.client parses headers.

    HTTPResponse.begin() invokes _read_status() before parse_headers(). A
    malformed oversized header can raise before urllib returns the response,
    so waiting for getcode() silently loses a received 401/403/429.
    """

    def __init__(self, sock, *args, owner, **kwargs):
        super().__init__(sock, *args, **kwargs)
        self._read_socket = sock
        self._status_owner = owner

    def read1(self, size=-1):
        # The response file can still own the socket after http.client or
        # urllib drops the connection's reference. In particular on Windows,
        # shutdown through the fence need not wake a read already in select.
        if self.fp is not None:
            self._read_socket.settimeout(self._status_owner.remaining())
        return super().read1(size)

    def _read_status(self):
        owner = self._status_owner
        if self._method == "GET" and not owner.response_started:
            # A single buffered read distinguishes empty EOF/reset from a
            # partial status line whose later recv may fail.
            if not self.fp.peek(1):
                raise http.client.RemoteDisconnected("HTTPS peer disconnected before response")
            owner.response_started = True
        version, status, reason = super()._read_status()
        observer = owner._http_status_observer
        if (callable(observer) and type(status) is int and 100 <= status <= 599
                and version.startswith('HTTP/1.')):
            observer(status)
        return version, status, reason


class OwnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, *, owner, **kwargs):
        super().__init__(host, **kwargs)
        self.owner = owner
        owner.connections.append(self)
        def response_factory(*args, **options):
            # Register before begin() can block on a drip-fed HTTP header.
            return owner.response(_EarlyObservedHTTPResponse(
                *args, owner=owner, **options))
        self.response_class = response_factory

    def connect(self):
        owner = self.owner
        owner.check()
        if self.host != owner.host or self.port != owner.port or self._tunnel_host is not None:
            raise OSError('HTTPS destination unavailable')
        self.auto_open = 0  # One connection path; never replay a sent request.
        raw = None
        for family, kind, protocol, unused, address in owner.addresses():
            owner.check()
            candidate = socket.socket(family, kind, protocol)
            owner.sockets.append(candidate)
            registration = owner.fence.register(candidate, owner.epoch)
            registration.__enter__()
            owner.registrations.append(registration)
            try:
                candidate.settimeout(owner.remaining())
                candidate.connect(address)
            except OSError:
                owner.release_candidate(candidate, registration)
                owner.check()
                continue  # TCP connect failed before any HTTP request bytes existed.
            raw = candidate
            owner.fence.activate(raw, owner.epoch)
            break
        if raw is None:
            raise OSError('HTTPS connection unavailable')
        owner.check()
        # Assign the TLS owner before handshake, so failure/cancel closes it too.
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host,
                                              do_handshake_on_connect=False)
        self.sock.settimeout(owner.remaining())
        self.sock.do_handshake()
        owner.check()

    def getresponse(self):
        self.owner.check()
        if self.sock is not None:
            self.sock.settimeout(self.owner.remaining())
        return super().getresponse()


class OwnedHTTPSHandler(HTTPSHandler):
    def __init__(self, owner):
        if owner is not None and owner.connections:
            context = owner.connections[0]._context
        else:
            with _context_lock:
                epoch = _trust_epoch()
                context = _default_context(epoch)
            if owner is not None:
                owner.trust_epoch = epoch
        super().__init__(context=context)
        self.owner = owner

    def https_open(self, request):
        if self.owner.keep_alive:
            return self._persistent_open(request)

        def connection(host, **kwargs):
            return OwnedHTTPSConnection(host, owner=self.owner, **kwargs)
        return self.do_open(connection, request, context=self._context)

    def _persistent_open(self, request):
        owner = self.owner
        headers = {name.title(): value for name, value in
                   (request.headers | request.unredirected_hdrs).items()}
        if (request.host != owner.host or headers.get("Host") != owner.host
                or request._tunnel_host is not None):
            raise OSError("HTTPS destination unavailable")
        connection = (owner.connections[0] if owner.connections else OwnedHTTPSConnection(
            request.host, context=self._context, owner=owner, timeout=request.timeout))
        remaining = owner.remaining()
        if connection.sock is not None:
            connection.sock.settimeout(remaining)
            # From here a failure can follow a partially sent GET.
            owner.reused_dispatch = True
        headers["Connection"] = "keep-alive"
        try:
            connection.request("GET", request.selector, headers=headers)
            response = connection.getresponse()
        except (ConnectionError, ssl.SSLEOFError, ssl.SSLZeroReturnError):
            owner.reused_disconnect = owner.reused_dispatch and not owner.response_started
            raise
        response.url = request.full_url
        response.msg = response.reason
        return response


class OwnedHTTPSPool:
    """Own default HTTP I/O without owning the reader/writer that borrows it.

    close() revokes active I/O. A caller that requires drain confirmation must
    also wait for its request workers and active_count to reach zero.
    """
    def __init__(self):
        self._lock = RLock()
        # Serialize only checkout/retirement. A replacement must wait for
        # proof of retirement (or poison), but readers of pool state and
        # active owners returning their lease need not wait on socket close.
        self._checkout_lock = RLock()
        self._owners, self._retained = set(), set()
        self._idle = None
        self._expiry_timer = None
        self._closed = self._poisoned = False

    @property
    def closed(self):
        with self._lock:
            return self._closed

    @property
    def revocation_unverified(self):
        with self._lock:
            return self._poisoned or any(owner.fence.revocation_unverified
                                         for owner in self._owners)

    @property
    def active_count(self):
        with self._lock:
            return len(self._owners)

    def close(self):
        # Publish closure and revoke active I/O before waiting for any
        # unrelated retirement to finish at checkout.
        with self._lock:
            self._closed = True
            owners = tuple(self._owners | self._retained)
        for owner in owners:
            owner.abort()
        with self._checkout_lock:
            with self._lock:
                idle, self._idle = self._idle, None
                if self._expiry_timer is not None:
                    self._expiry_timer.cancel()
                    self._expiry_timer = None
            if idle is not None:
                self._discard_idle(idle)
            with self._lock:
                self._poisoned = self._poisoned or any(owner.fence.revocation_unverified
                                                      for owner in owners)

    def _discard_idle(self, owner):
        try:
            owner.cleanup()
        except TimeoutError:
            with self._lock:
                self._poisoned = True
                self._retained.add(owner)

    def _arm_expiry(self, deadline):
        timer = Timer(max(0, deadline - time.monotonic()), self._expire_idle)
        timer.daemon = True
        try:
            timer.start()
        except RuntimeError:
            return  # The checkout deadline and close still discard the socket.
        self._expiry_timer = timer

    def _expire_idle(self):
        with self._checkout_lock:
            with self._lock:
                self._expiry_timer = None
                idle = self._idle
                if self._closed or idle is None:
                    return
                if time.monotonic() < idle.idle_until:
                    self._arm_expiry(idle.idle_until)
                    return
                self._idle = None
            self._discard_idle(idle)

    @contextmanager
    def session(self, *, timeout, deadline, host, port, keep_alive=False):
        with self._checkout_lock:
            owner = None
            with self._lock:
                if self._closed or self.revocation_unverified:
                    raise TimeoutError('HTTPS request interrupted')
                if (keep_alive and self._idle is not None
                        and (self._idle.host, self._idle.port) == (host, port)):
                    owner, self._idle = self._idle, None
            if owner is not None and not owner.idle_usable():
                self._discard_idle(owner)
                owner = None
            with self._lock:
                healthy = not self._closed and not self.revocation_unverified
                if healthy:
                    if owner is None:
                        owner = HTTPSOwner(timeout, deadline, host, port)
                    else:
                        owner.timeout, owner.deadline = timeout, deadline
                        owner.reused_dispatch = False
                        owner.reused_disconnect = False
                        owner.response_started = False
                    owner.keep_alive = keep_alive
                    self._owners.add(owner)
            if not healthy:
                if owner is not None:
                    self._discard_idle(owner)
                raise TimeoutError('HTTPS request interrupted')
        parked = False
        try:
            owner.start()
            yield owner
            owner.stop_timer()
            owner.check()
            with self._lock:
                if self._closed:
                    raise TimeoutError('HTTPS request interrupted')
                if keep_alive and self._idle is None and owner.prepare_idle():
                    self._owners.remove(owner)
                    owner.idle_until = time.monotonic() + _IDLE_SECONDS
                    self._idle = owner
                    parked = True
                    # A running timer will observe the latest idle deadline
                    # when it wakes, so a warm GET need not start a new thread.
                    if self._expiry_timer is None:
                        self._arm_expiry(owner.idle_until)
        finally:
            if not parked:
                try:
                    owner.cleanup()
                    owner.check()
                finally:
                    with self._lock:
                        self._owners.discard(owner)
                        if owner.fence.revocation_unverified:
                            self._poisoned = True
                            self._retained.add(owner)
