"""Opt-in GET transport fence for the durable managed network stop gate.

This is a separately owned integration component, not product wiring. Install
it at the *transport* boundary of each approved managed read client, before
any caller can swallow/translate HTTP failures. Non-managed clients remain
unchanged. No URL, Cookie, request body, or personal ID is persisted.
"""

from __future__ import annotations

from urllib.error import HTTPError

from .managed_network_stop_gate import (
    ManagedNetworkStopGate,
    ManagedNetworkStopped,
    _carry_gate_cleanup,
    _http_gate_rejection,
    _report_stop_authority,
)
from .pixiv_read_client import HTTPStatusRejected


class ManagedNetworkStopTransport:
    """Fence one synchronous GET and latch fatal HTTP statuses before return.

    `transport` must be the existing trusted read-only callable. The owner is
    still responsible for closing it and for installing this wrapper on *all*
    managed GET paths, including metadata, images and scheduler previews.
    Async readers need a separate cancellation-aware boundary.
    """

    __slots__ = ("gate", "transport")

    def __init__(self, gate: ManagedNetworkStopGate, transport):
        if not isinstance(gate, ManagedNetworkStopGate):
            raise TypeError("managed stop gate required")
        if not callable(transport):
            raise TypeError("read-only transport callable required")
        self.gate = gate
        self.transport = transport

    def __call__(self, request, timeout: float, limit: int, deadline: float) -> bytes:
        if (request.get_method() != "GET" or request.data is not None):
            raise ValueError("managed network transport accepts GET only")
        authority = 'shared' if hasattr(self.gate, 'inflight_path') else 'local'
        try:
            with _report_stop_authority(authority), self.gate.request():
                try:
                    return self.transport(request, timeout, limit, deadline)
                except HTTPError as error:
                    # Raw urllib transports may raise HTTPError before the
                    # caller can convert it. Persist fatal status inside the
                    # durable gate even when a higher layer handles errors.
                    raise _http_gate_rejection(error) from None
        except HTTPStatusRejected as error:
            from .request_policy import durable_stop_status
            if durable_stop_status(error.status_code):
                # The gate has persisted this status before leaving request().
                # A plain, exact-type LivePixivReadClient must not enter the
                # legacy retry policy's 429 wait or issue a second GET.
                stopped = ManagedNetworkStopped(error.status_code, authority=authority)
                raise _carry_gate_cleanup(error, stopped) from None
            raise
