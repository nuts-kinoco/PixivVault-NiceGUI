"""Explicit two-owner transport for a future reviewed shared STOP migration.

This module does not choose or initialize the shared gate. Its caller must
prove ownership and historical STOP migration *before* enabling a live reader.
No real profile, Cookie, or OS-user gate is discovered here.
"""

from __future__ import annotations

from urllib.error import HTTPError

from .managed_network_stop_gate import (
    ManagedNetworkGateStorageError,
    ManagedNetworkStopGate,
    ManagedNetworkStopped,
    STOP_STATUSES,
)
from .managed_network_stop_profile_scope import (
    _ProfileBoundTransport,
    _require_selected_cookie,
)
from .managed_network_stop_transport import ManagedNetworkStopTransport
from .pixiv_read_client import HTTPStatusRejected
from .request_policy import durable_stop_status


def _dispatch_dual_stop(local_gate, shared_gate, transport,
                        request, timeout, limit, deadline):
    if request.get_method() != "GET" or request.data is not None:
        raise ValueError("managed network transport accepts GET only")
    fatal_status = None
    try:
        with local_gate.request():
            try:
                with shared_gate.request():
                    try:
                        return transport(request, timeout, limit, deadline)
                    except HTTPError as error:
                        status = error.code
                        if type(status) is int and status in STOP_STATUSES:
                            if durable_stop_status(status):fatal_status = status
                            try:
                                error.close()
                            except Exception:
                                pass
                            raise HTTPStatusRejected(status) from None
                        raise
                    except ManagedNetworkStopped as stopped:
                        # A raw injected transport can raise Stopped itself.
                        # Convert *inside* both request contexts so both
                        # gates persist it while their locks remain held.
                        if stopped.status_code not in STOP_STATUSES:
                            raise ManagedNetworkGateStorageError(
                                "invalid raw managed stop status") from None
                        fatal_status = stopped.status_code
                        raise HTTPStatusRejected(fatal_status) from None
                    except HTTPStatusRejected as error:
                        if durable_stop_status(error.status_code):
                            fatal_status = error.status_code
                        raise
            except ManagedNetworkGateStorageError:
                if fatal_status is not None:
                    # Shared commit can fail *after* receiving 401/403/429.
                    # The outer local gate still owns its lock: persist the
                    # fatal status here before releasing it. Never turn this
                    # storage fault into a seemingly successful STOP.
                    local_gate._trip_locked(fatal_status)
                raise
    except HTTPStatusRejected as error:
        if durable_stop_status(error.status_code):
            stopped = ManagedNetworkStopped(error.status_code)
            stopped.observed_response = True
            raise stopped from None
        raise
    except ManagedNetworkStopped as stopped:
        # Already-stopped local/shared on entry, OR a raw transport raising
        # Stopped directly. request() only trips HTTPStatusRejected, so both
        # durable owners must explicitly receive this status before returning.
        try:
            shared_gate.trip(stopped.status_code)
        finally:
            local_gate.trip(stopped.status_code)
        raise


class SharedProfileStopTransport(_ProfileBoundTransport):
    """Preserve native credential ownership and trip shared STOP before local.

    Lock order is local -> shared for every GET through this adapter. On a
    fatal response the inner shared request commits first; the outer profile
    request commits after it. Never wrap two ManagedNetworkStopTransports:
    that converts the HTTP status into ManagedNetworkStopped too soon and
    silently skips the outer durable trip.
    """

    __slots__ = ("shared_gate", "shared_owner_check")

    def __init__(self, previous: _ProfileBoundTransport,
                 shared_gate: ManagedNetworkStopGate, *, shared_owner_check=None):
        if not isinstance(previous, _ProfileBoundTransport):
            raise TypeError("owned native profile transport required")
        if not isinstance(shared_gate, ManagedNetworkStopGate):
            raise TypeError("shared managed stop gate required")
        if shared_gate.path == previous.gate.path:
            raise ValueError("shared and native gate paths must differ")
        if isinstance(previous.transport, ManagedNetworkStopTransport):
            # A pre-wrapped transport would turn HTTPStatusRejected into
            # ManagedNetworkStopped *before* the shared gate can persist it.
            # Accept only the raw read transport at this boundary.
            raise ManagedNetworkGateStorageError("pre-fenced transport rejected")
        # _ProfileBoundTransport.__init__ unwraps previous.transport into the
        # exact raw callable (rather than nesting a second profile wrapper).
        # This matters: a nested ManagedNetworkStopTransport would translate
        # the local HTTPStatusRejected before shared_gate.request() could trip.
        super().__init__(previous.profile_root, previous.cookie_path,
                         previous.profile_identity, previous)
        self.shared_gate = shared_gate
        self.shared_owner_check = shared_owner_check

    def __call__(self, request, timeout: float, limit: int,
                 deadline: float) -> bytes:
        _require_selected_cookie(self.profile_root, self.cookie_path,
                                 self.profile_identity)
        if self.shared_owner_check is not None:
            self.shared_owner_check()
        from contextlib import nullcontext
        from .managed_network_stop_startup import selected_history_guard
        owner = getattr(self.shared_owner_check, '__self__', None)
        with selected_history_guard(owner) if owner is not None else nullcontext():
            return _dispatch_dual_stop(self.gate, self.shared_gate, self.transport,
                                       request, timeout, limit, deadline)


class _DraftAuthSharedTransport:
    """The Native draft Cookie is not yet the saved credential selection."""

    __slots__ = ("scope", "shared_owner", "transport")

    def __init__(self, scope, shared_owner, transport):
        self.scope, self.shared_owner, self.transport = scope, shared_owner, transport

    def __call__(self, request, timeout, limit, deadline):
        from .managed_network_stop_profile_scope import _SCOPE_MARKER, _SCOPE_IDENTITY
        from .native_profile import PROFILE_DB, PROFILE_MARKER, PROFILE_IDENTITY, _owned_marker
        from .managed_network_stop_gate import _file_identity
        _owned_marker(self.scope.root, PROFILE_MARKER, PROFILE_IDENTITY)
        _owned_marker(self.scope.root, _SCOPE_MARKER, _SCOPE_IDENTITY)
        if _file_identity(self.scope.root / PROFILE_DB) != self.scope.profile_identity:
            raise ManagedNetworkGateStorageError("native draft auth owner changed")
        self.shared_owner._check_owner()
        from .managed_network_stop_startup import selected_history_guard
        with selected_history_guard(self.shared_owner):
            return _dispatch_dual_stop(self.scope.gate, self.shared_owner.gate,
                                      self.transport, request, timeout, limit, deadline)


def create_shared_draft_auth_reader(scope, cookie_path, shared_owner):
    """Explicit draft-auth GET with both STOP owners; no saved Cookie matching."""
    from .managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
    from .managed_network_stop_shared_scope import ManagedUserNetworkStopScope
    from .managed_network_stop_reader import create_exact_managed_read_client
    if (type(scope) is not ManagedProfileNetworkStopScope
            or type(shared_owner) is not ManagedUserNetworkStopScope):
        raise TypeError("sealed Native and shared STOP scopes required")
    shared_owner._check_owner()
    scope.require_allowed()
    reader = create_exact_managed_read_client(cookie_path, scope.gate)
    try:
        previous = reader._transport
        if not isinstance(previous, ManagedNetworkStopTransport):
            raise ManagedNetworkGateStorageError("draft reader is not gated")
        reader._transport = _DraftAuthSharedTransport(scope, shared_owner, previous.transport)
        return reader
    except BaseException:
        reader.close()
        raise


def create_shared_profile_reader(scope, cookie_path, shared_gate, *, transport=None,
                                 shared_owner_check=None):
    """Create, bind and publish one native reader without an unguarded window.

    This does NOT prove that the supplied shared gate represents the Windows
    user or that unknown old profiles were migrated. Those checks belong to
    the calling startup owner, before this helper is ever invoked.
    """
    from .managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
    from .pixiv_read_client import LivePixivReadClient

    if type(scope) is not ManagedProfileNetworkStopScope:
        raise TypeError("exact owned native profile scope required")
    if not isinstance(shared_gate, ManagedNetworkStopGate):
        raise TypeError("explicit shared stop gate required")
    if shared_owner_check is not None and not callable(shared_owner_check):
        raise TypeError("shared owner validation callable required")
    if shared_owner_check is not None:
        shared_owner_check()
    reader = scope.create_reader(cookie_path, transport=transport)
    try:
        # Do not offer a callable that retrofits an already-published reader:
        # a GET between its local snapshot and transport replacement could
        # trip only local STOP and leave the shared owner unblocked.
        if type(reader) is not LivePixivReadClient:
            raise TypeError("exact managed Pixiv reader required")
        previous = reader._transport
        if not isinstance(previous, _ProfileBoundTransport):
            raise TypeError("owned native profile transport required")
        shared_transport = SharedProfileStopTransport(
            previous, shared_gate, shared_owner_check=shared_owner_check)
        native_state = previous.gate.snapshot()
        if native_state.blocked:
            shared_gate.trip(native_state.status_code)
        reader._transport = shared_transport
        return reader
    except BaseException:
        reader.close()
        raise
