"""Opt-in, owned synchronous Pixiv reader with the durable managed GET fence.

This is an integration building block, not automatic product wiring. A caller
must provide the *same* gate to all managed readers sharing a download root;
otherwise a fatal HTTP response in one reader cannot stop the other readers.
Only synthetic roots are exercised by this module's tests.
"""

from __future__ import annotations

from .managed_network_stop_gate import (
    ManagedNetworkStopped,
    ManagedNetworkStopGate,
    STOP_STATUSES,
)
from .managed_network_stop_transport import ManagedNetworkStopTransport
from .pixiv_read_client import HTTPStatusRejected, LivePixivReadClient, _OwnedReadTransport


def create_exact_managed_read_client(cookie_path, gate: ManagedNetworkStopGate, *, transport=None):
    """Build an exact LivePixivReadClient with a guarded owned GET transport.

    Some product routes intentionally require type(reader) is
    LivePixivReadClient rather than accepting an arbitrary subclass. This
    adapter preserves that check without weakening it. The caller must still
    own the gate path and install the same gate across every managed GET path.
    """
    if not isinstance(gate, ManagedNetworkStopGate):
        raise TypeError("managed stop gate required")
    owner = _OwnedReadTransport() if transport is None else None
    try:
        wrapper = ManagedNetworkStopTransport(gate, owner if owner is not None else transport)
        client = LivePixivReadClient(cookie_path, transport=wrapper)
    except BaseException:
        if owner is not None:
            try:
                owner.close()
            except Exception:
                pass
        raise
    # The base reader treats injected transports as borrowed. Rebind only
    # its own default pool so close() and shutdown_unverified remain truthful.
    client._owned_transport = owner
    return client


class ManagedLivePixivReadClient(LivePixivReadClient):
    """Drop-in ``LivePixivReadClient`` with one guarded, owned GET transport.

    ``LivePixivReadClient`` normally creates and closes its own HTTPS pool.
    Passing a transport makes that pool *borrowed*, so merely injecting the
    guard would leak the real pool on close and skip shutdown verification.
    Restore the existing owner's lifecycle after constructing the guarded
    client. Explicitly supplied synthetic transports remain borrowed.
    """

    def __init__(self, cookie_path, gate: ManagedNetworkStopGate, *, transport=None):
        if not isinstance(gate, ManagedNetworkStopGate):
            raise TypeError("managed stop gate required")
        owner = _OwnedReadTransport() if transport is None else None
        try:
            wrapper = ManagedNetworkStopTransport(gate, owner if owner is not None else transport)
            super().__init__(cookie_path, transport=wrapper)
        except BaseException:
            if owner is not None:
                try:
                    owner.close()
                except Exception:
                    pass
            raise
        self._owned_transport = owner

    def _get_once(self, kind: str, url: str, *, deadline: float) -> bytes:
        """Do not let the base retry policy wait/retry a latched fatal status.

        The transport has already persisted the stop before propagating the
        original HTTP status. Converting it here makes both 401/403 and 429
        leave RequestRetryPolicy immediately, without an extra sleep. Other
        HTTP statuses retain the existing bounded retry policy unchanged.
        """
        try:
            return super()._get_once(kind, url, deadline=deadline)
        except HTTPStatusRejected as error:
            from .request_policy import durable_stop_status
            if durable_stop_status(error.status_code):
                raise ManagedNetworkStopped(error.status_code) from None
            raise

