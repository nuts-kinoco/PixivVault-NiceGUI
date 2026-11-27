"""Explicit Native confirmation of a new credential under a durable 401 stop."""

from contextlib import ExitStack, contextmanager
import os
from urllib.error import HTTPError

from .managed_network_stop_gate import (
    ManagedNetworkStopped, ManagedNetworkGateStorageError, _http_gate_rejection,
    _report_stop_authority, _attach_gate_cleanup,
)
from .frozen_source_pin import FrozenSourceError, _open_held
from .managed_network_stop_profile_scope import _require_native_stop_owner
from .native_profile import NativeProfileError, _assert_chain
from .managed_network_stop_startup import selected_history_guard
from .pixiv_read_client import (
    API_ORIGIN, ClientClosed, CookieSourceRejected, HTTPStatusRejected, PixivReadError,
)
from .request_policy import request_scope


@contextmanager
def _pin_cookie(path):
    try:
        _assert_chain(path, kind='file')
        descriptor = _open_held(path)
    except (FrozenSourceError, NativeProfileError, OSError):
        raise CookieSourceRejected() from None
    try:
        pinned = os.fstat(descriptor)

        def check():
            try:
                _assert_chain(path, kind='file')
                current = path.stat()
                keys = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns')
                if any(getattr(current, key) != getattr(pinned, key) for key in keys):
                    raise CookieSourceRejected()
            except (NativeProfileError, OSError):
                raise CookieSourceRejected() from None

        check()
        yield check
    finally:
        os.close(descriptor)


def recover_draft_auth_401(scope, shared_owner, cookie_file, save, checkpoint, commit_started):
    """Return the verified ID, or None when ordinary authentication applies."""
    from .managed_network_stop_reader import create_exact_managed_read_client

    local, shared = scope.gate, shared_owner.gate
    _require_native_stop_owner(scope.root, scope.profile_identity)
    shared_owner._check_owner()
    with (selected_history_guard(shared_owner, transfer_stop=False), local._exclusive(),
          shared._exclusive(), ExitStack() as pins):
        checkpoint()
        _require_native_stop_owner(scope.root, scope.profile_identity)
        shared_owner._check_owner()
        local_state, shared_state = local._snapshot_locked(), shared._snapshot_locked()
        for authority, state in (('shared', shared_state), ('local', local_state)):
            if state.status_code in (403, 429):
                raise ManagedNetworkStopped(state.status_code, authority=authority)

        # Even a marker alongside 401 may belong to an unrelated request.
        with _report_stop_authority('shared'):
            shared._no_inflight()
        if not local_state.blocked and not shared_state.blocked:
            return None

        # Fence other profiles before attempting confirmation. If interrupted
        # after clearing local, shared still fences every guarded route.
        if not shared_state.blocked:
            shared_state = shared._trip_locked(401)
        if not local_state.blocked:
            local_state = local._trip_locked(401)

        check_cookie = pins.enter_context(_pin_cookie(cookie_file))
        urls = iter((API_ORIGIN + '/', API_ORIGIN + '/ajax/user/extra'))
        with create_exact_managed_read_client(cookie_file, local) as reader:
            if not any(cookie.name == 'PHPSESSID' for cookie in reader._cookies):
                raise CookieSourceRejected()
            raw_transport = reader._transport.transport

            def confirm(request, timeout, limit, deadline):
                if (request.get_method() != 'GET' or request.data is not None
                        or request.full_url != next(urls, None)):
                    raise ManagedNetworkGateStorageError('unexpected authentication request')
                checkpoint()
                check_cookie()
                with _report_stop_authority('shared'):
                    intent = shared._begin_inflight()
                    try:
                        checkpoint()
                    except ClientClosed:
                        shared._finish_inflight(intent)
                        raise
                    try:
                        try:
                            response = raw_transport(request, timeout, limit, deadline)
                        except HTTPError as error:
                            raise _http_gate_rejection(error) from None
                    except HTTPStatusRejected as error:
                        status = error.status_code
                        if status in (403, 429):
                            try:
                                shared._advance_401_locked(status)
                            except ManagedNetworkGateStorageError as failure:
                                try:
                                    with _report_stop_authority('local'):
                                        local._advance_401_locked(status)
                                except ManagedNetworkGateStorageError as local_error:
                                    _attach_gate_cleanup(failure, local_error)
                                raise
                            local._advance_401_locked(status)
                        else:
                            uncertain = shared._settle_failed_get(intent, error)
                            if uncertain is not None and status != 401:
                                raise uncertain from None
                        raise
                    except PixivReadError as error:
                        uncertain = shared._settle_failed_get(intent, error)
                        if uncertain is not None:
                            raise uncertain from None
                        raise
                    else:
                        shared._finish_inflight(intent)
                        return response

            reader._transport = confirm
            with request_scope(checkpoint=checkpoint, allow_retry=False):
                uid = reader.require_identity()

        if reader.shutdown_unverified:
            raise ManagedNetworkGateStorageError('authentication HTTPS cleanup unverified', authority='shared')
        check_cookie()
        _require_native_stop_owner(scope.root, scope.profile_identity)
        shared_owner._check_owner()
        shared._no_inflight()
        commit_started.set()
        checkpoint()
        with save(uid, local_state.revision + 1, shared_state.revision + 1):
            check_cookie()
            _require_native_stop_owner(scope.root, scope.profile_identity)
            shared_owner._check_owner()
            shared._no_inflight()
            local._advance_401_locked(None)
            shared._advance_401_locked(None)
        return uid
