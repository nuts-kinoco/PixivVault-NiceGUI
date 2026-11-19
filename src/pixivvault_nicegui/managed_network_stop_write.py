"""Admission for explicit Pixiv writes using the owned STOP scopes."""

from contextlib import contextmanager, nullcontext

from .managed_network_stop_profile_scope import _require_selected_cookie
from .managed_network_stop_startup import selected_history_guard
from .managed_network_stop_gate import ManagedNetworkGateStorageError, ManagedNetworkStopped, STOP_STATUSES
from .pixiv_read_client import HTTPStatusRejected


@contextmanager
def write_admission(reader, local, shared):
    """Hold the same owner and lock order as managed GET during one POST."""
    if shared is None:
        raise RuntimeError('shared STOP owner required')
    transport = reader._transport
    if local is not None:
        _require_selected_cookie(transport.profile_root, transport.cookie_path,
                                 transport.profile_identity)
    shared._check_owner()
    try:
        with selected_history_guard(shared):
            with local.gate.request() if local is not None else nullcontext():
                fatal = None
                try:
                    with shared.gate.request():
                        shared._check_owner()
                        if local is not None:
                            _require_selected_cookie(transport.profile_root, transport.cookie_path,
                                                     transport.profile_identity)
                        try:
                            yield
                        except HTTPStatusRejected as error:
                            if error.status_code in STOP_STATUSES:
                                fatal = error.status_code
                            raise
                except ManagedNetworkGateStorageError:
                    if fatal is not None and local is not None:
                        local.gate._trip_locked(fatal)
                    raise
    except ManagedNetworkStopped as stopped:
        if local is not None:
            try:
                shared.gate.trip(stopped.status_code)
            finally:
                local.gate.trip(stopped.status_code)
        raise
