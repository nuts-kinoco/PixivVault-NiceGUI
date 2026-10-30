"""Owned HTTPS resources shared by the bounded GET and opt-in POST clients.

This module performs no URL admission, Cookie handling, HTTP status decisions,
or retries. Each caller retains those policies and grants one prepared request
an absolute deadline. Socket/response aliases belong to that one attempt.
"""
from __future__ import annotations

from contextlib import contextmanager
import http.client
import socket
import ssl
from threading import Event, RLock, Thread, Timer, current_thread
import time
from urllib.request import HTTPSHandler

from .vault_socket_fence import SocketFence, SocketFenceError


class HTTPSOwner:
    """Resources for one HTTPS attempt; no Cookie or client ownership."""
    def __init__(self, timeout, deadline, host, port):
        self.timeout, self.deadline = timeout, deadline
        self.host, self.port = host, port
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

    def cleanup(self):
        if self.timer is not None:
            self.timer.cancel()
            if self.timer.ident is not None and self.timer is not current_thread():
                self.timer.join()
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


class OwnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, *, owner, **kwargs):
        super().__init__(host, **kwargs)
        self.owner = owner
        owner.connections.append(self)
        def response_factory(*args, **options):
            # Register before begin() can block on a drip-fed HTTP header.
            return owner.response(http.client.HTTPResponse(*args, **options))
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
        super().__init__(context=ssl.create_default_context())
        self.owner = owner

    def https_open(self, request):
        def connection(host, **kwargs):
            return OwnedHTTPSConnection(host, owner=self.owner, **kwargs)
        return self.do_open(connection, request, context=self._context)


class OwnedHTTPSPool:
    """Own default HTTP I/O without owning the reader/writer that borrows it.

    close() revokes active I/O. A caller that requires drain confirmation must
    also wait for its request workers and active_count to reach zero.
    """
    def __init__(self):
        self._lock = RLock()
        self._owners, self._retained = set(), set()
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
        with self._lock:
            self._closed = True
            owners = tuple(self._owners | self._retained)
        for owner in owners:
            owner.abort()
        with self._lock:
            self._poisoned = self._poisoned or any(owner.fence.revocation_unverified
                                                  for owner in owners)

    @contextmanager
    def session(self, *, timeout, deadline, host, port):
        owner = HTTPSOwner(timeout, deadline, host, port)
        with self._lock:
            if self._closed or self.revocation_unverified:
                raise TimeoutError('HTTPS request interrupted')
            self._owners.add(owner)
        try:
            owner.start()
            yield owner
        finally:
            try:
                owner.cleanup()
                owner.check()
            finally:
                with self._lock:
                    self._owners.discard(owner)
                    if owner.fence.revocation_unverified:
                        self._poisoned = True
                        self._retained.add(owner)
