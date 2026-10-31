"""Reversible parent-process external socket/DNS denial with attempt receipts.

Only for dedicated single-owner offline acceptance processes. This is a
process-global Python socket fence, not an OS network sandbox. Any blocked
attempt remains observable even if a product component catches its exception.
"""
from __future__ import annotations

import ipaddress
import socket


class OfflineNetworkFence:
    def __init__(self):
        self.attempts = []
        self._previous = None

    @property
    def attempt_count(self):
        return len(self.attempts)

    @property
    def clean(self):
        return not self.attempts

    @staticmethod
    def _allowed(host):
        if host is None or host == 'localhost':
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except (ValueError, TypeError):
            return False

    def _ensure(self, address, route):
        # Non-tuple paths are permitted only for a local UNIX-domain socket.
        # The wrappers pass their own socket family for that decision.
        if not isinstance(address, tuple) or not address or not self._allowed(address[0]):
            self.attempts.append(route)
            raise PermissionError('offline parent external socket/DNS attempt blocked')

    def __enter__(self):
        if self._previous is not None:
            raise RuntimeError('offline fence already entered')
        previous = (
            socket.socket.connect, socket.socket.connect_ex,
            socket.create_connection, socket.getaddrinfo, socket.socket.sendto,
        )
        self._previous = previous
        connect, connect_ex, create_connection, getaddrinfo, sendto = previous
        fence = self

        def guarded_connect(sock, address):
            if getattr(sock, 'family', None) != getattr(socket, 'AF_UNIX', None):
                fence._ensure(address, 'socket.connect')
            return connect(sock, address)

        def guarded_connect_ex(sock, address):
            if getattr(sock, 'family', None) != getattr(socket, 'AF_UNIX', None):
                fence._ensure(address, 'socket.connect_ex')
            return connect_ex(sock, address)

        def guarded_create_connection(address, *args, **kwargs):
            fence._ensure(address, 'socket.create_connection')
            return create_connection(address, *args, **kwargs)

        def guarded_getaddrinfo(host, *args, **kwargs):
            if not fence._allowed(host):
                fence.attempts.append('socket.getaddrinfo')
                raise PermissionError('offline parent external DNS attempt blocked')
            return getaddrinfo(host, *args, **kwargs)

        def guarded_sendto(sock, data, *args):
            if getattr(sock, 'family', None) != getattr(socket, 'AF_UNIX', None):
                # Python accepts sendto(data, address) and sendto(data, flags, address).
                address = args[-1] if args else None
                fence._ensure(address, 'socket.sendto')
            return sendto(sock, data, *args)

        socket.socket.connect = guarded_connect
        socket.socket.connect_ex = guarded_connect_ex
        socket.create_connection = guarded_create_connection
        socket.getaddrinfo = guarded_getaddrinfo
        socket.socket.sendto = guarded_sendto
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._previous is not None:
            (socket.socket.connect, socket.socket.connect_ex,
             socket.create_connection, socket.getaddrinfo,
             socket.socket.sendto) = self._previous
            self._previous = None
        return False
