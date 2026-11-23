"""Admission for explicit Pixiv writes using the owned STOP scopes."""

from contextlib import contextmanager, nullcontext

from .managed_network_stop_profile_scope import _require_selected_cookie
from .managed_network_stop_startup import selected_history_guard
from .managed_network_stop_shared_transport import _transfer_known_stop, _trip_local_after_shared_failure
from .managed_network_stop_gate import (
    ManagedNetworkGateStorageError, ManagedNetworkStopped, STOP_STATUSES, _report_stop_authority,
)
from .pixiv_read_client import HTTPStatusRejected


@contextmanager
def write_admission(reader, local, shared):
    """Hold the same owner and lock order as managed GET during one POST."""
    if shared is None:
        raise RuntimeError('shared STOP owner required')
    transport = reader._transport
    if local is not None:
        _require_selected_cookie(transport.profile_root, transport.cookie_path,
                                 transport.profile_identity, gate=local.gate)
    shared._check_owner()
    try:
        with selected_history_guard(shared):
            with (_report_stop_authority('local'),
                  local.gate.request() if local is not None else nullcontext()):
                fatal = None
                try:
                    with _report_stop_authority('shared'), shared.gate.request():
                        shared._check_owner()
                        if local is not None:
                            _require_selected_cookie(transport.profile_root, transport.cookie_path,
                                                     transport.profile_identity, gate=local.gate)
                        try:
                            yield
                        except HTTPStatusRejected as error:
                            if error.status_code in STOP_STATUSES:
                                fatal = error.status_code
                            raise
                except ManagedNetworkGateStorageError as error:
                    if fatal is not None and local is not None:
                        _trip_local_after_shared_failure(local.gate, fatal, error)
                    raise
    except ManagedNetworkStopped as stopped:
        if local is not None:
            _transfer_known_stop(local.gate, shared.gate, stopped)
        raise
