"""Explicit two-owner transport for a future reviewed shared STOP migration.

This module does not choose or initialize the shared gate. Its caller must
prove ownership and historical STOP migration *before* enabling a live reader.
No real profile, Cookie, or OS-user gate is discovered here.
"""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from urllib.error import HTTPError

from .managed_network_stop_gate import (
    ManagedNetworkGateStorageError,
    ManagedNetworkStopGate,
    ManagedNetworkStopped,
    STOP_STATUSES,
    _attach_gate_cleanup,
    _carry_gate_cleanup,
    _gate_storage_io,
    _http_gate_rejection,
    _run_gate_cleanup,
    _report_stop_authority,
    _trip_before_admission,
)
from .managed_network_stop_profile_scope import (
    _ProfileBoundTransport,
    _require_native_stop_owner,
)
from .managed_network_stop_transport import ManagedNetworkStopTransport, _GatedReadTransport
from .pixiv_read_client import HTTPStatusRejected
from .request_policy import durable_stop_status, request_send_checkpoint


def _dispatch_dual_stop(local_gate, shared_gate, transport,
                        request, timeout, limit, deadline, *, before_send=None):
    if request.get_method() != "GET" or request.data is not None:
        raise ValueError("managed network transport accepts GET only")
    fatal_status = None
    try:
        with _report_stop_authority('local'), local_gate.request():
            try:
                with _report_stop_authority('shared'), shared_gate.request():
                    try:
                        if before_send is not None:
                            before_send(local_gate._observed_revision, shared_gate._observed_revision)
                        request_send_checkpoint()
                        return transport(request, timeout, limit, deadline)
                    except HTTPError as error:
                        rejected = _http_gate_rejection(error)
                        if isinstance(rejected, HTTPStatusRejected) and durable_stop_status(rejected.status_code):
                            fatal_status = rejected.status_code
                        raise rejected from None
                    except ManagedNetworkStopped as stopped:
                        # A raw injected transport can raise Stopped itself.
                        # Convert *inside* both request contexts so both
                        # gates persist it while their locks remain held.
                        if stopped.status_code not in STOP_STATUSES:
                            raise ManagedNetworkGateStorageError(
                                "invalid raw managed stop status") from None
                        fatal_status = stopped.status_code
                        raise _carry_gate_cleanup(stopped, HTTPStatusRejected(fatal_status)) from None
                    except HTTPStatusRejected as error:
                        if durable_stop_status(error.status_code):
                            fatal_status = error.status_code
                        raise
            except ManagedNetworkGateStorageError as error:
                if fatal_status is not None:
                    # Shared commit can fail *after* receiving 401/403/429.
                    # The outer local gate still owns its lock: persist the
                    # fatal status here before releasing it. Never turn this
                    # storage fault into a seemingly successful STOP.
                    _trip_local_after_shared_failure(local_gate, fatal_status, error)
                raise
    except HTTPStatusRejected as error:
        if durable_stop_status(error.status_code):
            stopped = ManagedNetworkStopped(error.status_code, authority='both')
            stopped.observed_response = True
            raise _carry_gate_cleanup(error, stopped) from None
        raise
    except ManagedNetworkStopped as stopped:
        # Already-stopped local/shared on entry, OR a raw transport raising
        # Stopped directly. request() only trips HTTPStatusRejected, so both
        # durable owners must explicitly receive this status before returning.
        _transfer_known_stop(local_gate, shared_gate, stopped)
        raise


def _trip_local_after_shared_failure(local_gate, status, error):
    try:
        with _report_stop_authority('local'):
            local_gate._trip_locked(status)
    except ManagedNetworkGateStorageError as local_error:
        _attach_gate_cleanup(error, local_error)


def _mirror_local_stop(shared_gate, status):
    if status != 401:
        shared_gate.trip(status)
        return True
    # After a verified shared recovery, an unrelated profile may still have
    # a local 401. Its own fence remains closed. A new observed shared 401
    # is persisted by the actual request; an unfinished send has its intent.
    with shared_gate._exclusive():
        state = shared_gate._snapshot_locked()
        if state.blocked:
            return True
        if state.revision != 0:
            return False
        shared_gate._trip_locked(401)
        return True


def transfer_current_local_stop(local_gate, shared_gate, *, expected_revision=None):
    """Read and copy under the local lock so a cleared STOP cannot be re-latched."""
    with _report_stop_authority('local'), local_gate._exclusive():
        state = local_gate._snapshot_locked()
        if not state.blocked or (expected_revision is not None and state.revision != expected_revision):
            return False
        with _report_stop_authority('shared'):
            return _mirror_local_stop(shared_gate, state.status_code)


def _transfer_known_stop(local_gate, shared_gate, stopped):
    if stopped.status_code == 401 and hasattr(stopped, 'gate_revision'):
        try:
            if stopped.authority == 'local':
                copied = transfer_current_local_stop(
                    local_gate, shared_gate, expected_revision=stopped.gate_revision)
            else:
                with local_gate._exclusive(), shared_gate._exclusive():
                    state = shared_gate._snapshot_locked()
                    copied = state.status_code == 401 and state.revision == stopped.gate_revision
                    if copied:
                        with _report_stop_authority('local'):
                            local_gate._trip_locked(401)
        except ManagedNetworkGateStorageError as error:
            _attach_gate_cleanup(stopped, error)
        else:
            if copied:
                stopped.authority = 'both'
        return
    transfer_failed = False
    for authority, gate in (('shared', shared_gate), ('local', local_gate)):
        try:
            with _report_stop_authority(authority):
                gate.trip(stopped.status_code)
        except ManagedNetworkGateStorageError as error:
            # If only close failed after readback the destination is known.
            transfer_failed |= getattr(error, 'completed_stop', None) is None
            _attach_gate_cleanup(stopped, error)
    if not transfer_failed:
        stopped.authority = 'both'


@contextmanager
def _shared_owner_admission(check, *, confirming):
    from .managed_network_stop_startup import selected_history_guard
    owner = getattr(check, '__self__', None)
    with _report_stop_authority('shared'):
        if check is not None:
            with _gate_storage_io('shared STOP owner unavailable'):
                check()
        guard = (selected_history_guard(owner, transfer_stop=not confirming)
                 if owner is not None else nullcontext())
        with guard:
            yield


class SharedProfileStopTransport(_ProfileBoundTransport):
    """Preserve native credential ownership and trip shared STOP before local.

    Lock order is local -> shared for every GET through this adapter. On a
    fatal response the inner shared request commits first; the outer profile
    request commits after it. Never wrap two ManagedNetworkStopTransports:
    that converts the HTTP status into ManagedNetworkStopped too soon and
    silently skips the outer durable trip.
    """

    __slots__ = ("shared_gate", "shared_owner_check")
    resource_authority = 'shared'

    def __init__(self, previous: _ProfileBoundTransport,
                 shared_gate: ManagedNetworkStopGate, *, shared_owner_check=None, shared_generation=None):
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
                         previous.profile_identity, previous, previous.local_generation)
        self.shared_gate = shared_gate
        self.shared_owner_check = shared_owner_check
        self.shared_generation = (shared_gate._observed_revision // 2
                                  if shared_generation is None else shared_generation)

    def _require_generation(self, local_revision, shared_revision=None):
        super()._require_generation(local_revision)
        ManagedNetworkStopTransport._require_generation(self, local_revision, shared_revision)

    @property
    def _notice_gates(self):
        return self.gate, self.shared_gate

    @contextmanager
    def _admission_owner_checks(self, *, confirming=False):
        with super()._admission_owner_checks(confirming=confirming), \
                _shared_owner_admission(self.shared_owner_check, confirming=confirming):
            yield

    def __call__(self, request, timeout: float, limit: int,
                 deadline: float) -> bytes:
        with self._admission_owner_checks():
            return _dispatch_dual_stop(self.gate, self.shared_gate, self.transport,
                                       request, timeout, limit, deadline,
                                       before_send=self._require_generation)


class _DraftAuthSharedTransport(_GatedReadTransport):
    """The Native draft Cookie is not yet the saved credential selection."""

    __slots__ = ("scope", "shared_owner", "transport", "generations")
    resource_authority = 'shared'

    def __init__(self, scope, shared_owner, transport, generations=None):
        self.scope, self.shared_owner, self.transport = scope, shared_owner, transport
        self.generations = generations

    def _require_generation(self, local_revision, shared_revision=None):
        if self.generations is not None and self.generations != (local_revision // 2, shared_revision // 2):
            from .managed_network_stop_transport import StaleCredentialReader
            raise StaleCredentialReader('draft reader predates credential recovery', authority='both')

    @property
    def _notice_gates(self):
        return self.scope.gate, self.shared_owner.gate

    @contextmanager
    def _admission_owner_checks(self, *, confirming=False):
        _require_native_stop_owner(self.scope.root, self.scope.profile_identity)
        with _shared_owner_admission(self.shared_owner._check_owner, confirming=confirming):
            yield

    def __call__(self, request, timeout, limit, deadline):
        with self._admission_owner_checks():
            return _dispatch_dual_stop(self.scope.gate, self.shared_owner.gate,
                                       self.transport, request, timeout, limit, deadline,
                                       before_send=self._require_generation)


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
    generations = (scope.gate.snapshot().revision // 2, shared_owner.gate.snapshot().revision // 2)
    reader = create_exact_managed_read_client(cookie_path, scope.gate)
    try:
        previous = reader._transport
        if not isinstance(previous, ManagedNetworkStopTransport):
            raise ManagedNetworkGateStorageError("draft reader is not gated")
        reader._transport = _DraftAuthSharedTransport(scope, shared_owner, previous.transport, generations)
        return reader
    except BaseException as error:
        _run_gate_cleanup(reader.close, 'managed reader close failed', primary=error, authority='shared')
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
    shared_generation = shared_gate._observed_revision // 2
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
            previous, shared_gate, shared_owner_check=shared_owner_check,
            shared_generation=shared_generation)
        with _report_stop_authority('local'), previous.gate._exclusive():
            try:
                previous.gate._require_open_locked()
            except ManagedNetworkStopped as stopped:
                with _report_stop_authority('shared'):
                    _trip_before_admission(lambda status: _mirror_local_stop(shared_gate, status), stopped)
        reader._transport = shared_transport
        return reader
    except BaseException as error:
        _run_gate_cleanup(reader.close, 'managed reader close failed', primary=error, authority='shared')
        raise
