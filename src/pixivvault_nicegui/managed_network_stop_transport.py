"""Opt-in GET transport fence for the durable managed network stop gate.

This is a separately owned integration component, not product wiring. Install
it at the *transport* boundary of each approved managed read client, before
any caller can swallow/translate HTTP failures. Non-managed clients remain
unchanged. No URL, Cookie, request body, or personal ID is persisted.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from urllib.error import HTTPError

from .managed_network_stop_gate import (
    ManagedNetworkStopGate,
    ManagedNetworkStopped,
    _carry_gate_cleanup,
    _http_gate_rejection,
    _report_stop_authority,
)
from .pixiv_read_client import HTTPStatusRejected


class _GatedReadTransport:
    __slots__ = ()
    _checks_notice_selection = False

    @property
    def _notice_selection_proof(self):
        return None

    @property
    def _notice_selection_binding(self):
        return (*self._notice_gates, self._notice_selection_proof)

    @contextmanager
    def _admission_owner_checks(self, *, confirming=False):
        yield

    def _confirm_admission(self):
        with self._admission_owner_checks(confirming=True), ExitStack() as locks:
            for authority, gate in zip(('local', 'shared'), self._notice_gates):
                if gate is not None:
                    locks.enter_context(_report_stop_authority(authority))
                    locks.enter_context(gate._permission(confirming=True))
        return self._notice_gates, self._checks_notice_selection


class ManagedNetworkStopTransport(_GatedReadTransport):
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

    @property
    def resource_authority(self):
        return 'shared' if hasattr(self.gate, 'inflight_path') else 'local'

    @property
    def _notice_gates(self):
        return (None, self.gate) if self.resource_authority == 'shared' else (self.gate, None)

    def __call__(self, request, timeout: float, limit: int, deadline: float) -> bytes:
        if (request.get_method() != "GET" or request.data is not None):
            raise ValueError("managed network transport accepts GET only")
        from .request_policy import request_send_checkpoint
        authority = self.resource_authority
        try:
            with self._admission_owner_checks(), _report_stop_authority(authority), self.gate.request():
                try:
                    request_send_checkpoint()
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
