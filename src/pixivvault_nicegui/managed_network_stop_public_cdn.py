"""Opt-in sealed shared STOP boundary for Cookie-free public Pixiv CDN GET.

This is a *candidate* in an isolated source copy; it does not establish or
migrate a real historical user STOP owner. No account ID, URL, Cookie, image
payload or response headers are persisted in the gate.
"""

from __future__ import annotations

from .managed_network_stop_gate import ManagedNetworkGateStorageError
from .managed_network_stop_shared_scope import (
    ManagedUserNetworkStopScope, _SealedOwnerTransport,
)
from .managed_network_stop_transport import ManagedNetworkStopTransport
from .pixiv_read_client import PublicImageReadClient


def bind_public_cdn_shared_stop(client: PublicImageReadClient,
                                scope: ManagedUserNetworkStopScope | None) -> None:
    """Bind before publishing the client; never allow an unguarded fallback.

    It is intentionally distinct from a credential reader: CDN requests
    remain strictly Cookie-free and have no API capability. The owned HTTPS
    transport is retained for bounded close, cancellation and status checks.
    """
    if type(client) is not PublicImageReadClient:
        raise TypeError("an exact public image reader is required")
    transport = client._owned_transport
    if transport is None:
        # Production bootstrap creates its own owned transport. Injected
        # test transports must be handled before creating the app service.
        raise ManagedNetworkGateStorageError("public CDN owner transport unavailable")
    if scope is None:
        def deny(*_args):
            raise ManagedNetworkGateStorageError("shared Pixiv CDN STOP owner required")
        client._transport = deny
        return
    if type(scope) is not ManagedUserNetworkStopScope:
        raise ManagedNetworkGateStorageError("sealed public CDN STOP owner required")
    scope._check_owner()
    client._transport = _SealedOwnerTransport(
        scope, ManagedNetworkStopTransport(scope.gate, transport))
