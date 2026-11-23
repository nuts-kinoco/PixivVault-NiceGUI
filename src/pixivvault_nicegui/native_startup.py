"""Owned desktop profile entry and explicit native picker/authentication actions."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from threading import Event, Lock, RLock


def native_entry_arguments(arguments):
    """Only an ordinary no-argument executable launch selects the user profile."""
    arguments = list(arguments)
    if arguments:
        return arguments
    configured = os.environ.get('PIXIVVAULT_PROFILE_ROOT')
    if configured:
        root = Path(configured)
    else:
        local = os.environ.get('LOCALAPPDATA')
        if not local:
            raise ValueError('起動設定の保存先を確認できません。')
        # LOCALAPPDATA already exists on Windows. Own one direct child rather
        # than requiring an uncreated, unmarked application parent directory.
        root = Path(local) / 'PixivVault-NiceGUI-Profile'
    if not root.is_absolute():
        raise ValueError('起動設定の保存先には絶対パスが必要です。')
    # Resolving or creating the directory belongs to the validated startup path.
    return ['--profile-root', str(root)]


def validate_profile_boundaries(profile_root, snapshot, values):
    """Validate the profile itself before any profile/storage file is created."""
    from .test_owner_runtime import require_inside
    require_inside(profile_root)
    for value in values.values():
        if isinstance(value, Path): require_inside(value)
    if snapshot.save_parent is not None: require_inside(Path(snapshot.save_parent))
    from .media_jobs import validate_media_roots

    readonly = ('pixiv_cookie_file', 'library_readonly_db', 'library_image_root', 'similarity_model_file')
    sources = [value for value in (
        Path(snapshot.cookie_file).parent if snapshot.cookie_file else None,
        Path(snapshot.library_readonly_db).parent if snapshot.library_readonly_db else None,
        Path(snapshot.library_image_root) if snapshot.library_image_root else None,
        Path(snapshot.similarity_model_file).parent if snapshot.similarity_model_file else None,
    ) if value is not None]
    writable = [value for key, value in values.items()
                if key not in readonly and isinstance(value, Path)]
    validate_media_roots(profile_root, *writable)
    for source in sources:
        validate_media_roots(profile_root, source)
    if snapshot.save_parent is not None:
        # The whole marked application directory must stay outside its profile
        # and every read-only source, including unused future media subfolders.
        storage = Path(snapshot.save_parent) / 'PixivVault-NiceGUI'
        validate_media_roots(profile_root, storage)
        for source in sources:
            validate_media_roots(storage, source)


# Seconds between retries when the shared gate reports busy (transient contention).
BUSY_RETRY_DELAYS = (3.0, 8.0, 20.0, 45.0)


def _no_request_notice_send(*_):
    raise AssertionError('notice confirmation must not send a request')


class NativeProfileBinding:
    """One application-owned store; each client gets its own draft view."""

    def __init__(self, store, *, launch_snapshot=None):
        self.store = store
        # Keep the configuration actually checked/provisioned at startup.
        # A later preferences save must not redirect a queued folder action.
        self.launch_snapshot = (store.snapshot() if launch_snapshot is None
                                and callable(getattr(store, 'snapshot', None)) else launch_snapshot)
        self._closed = False
        self._picker_lock = asyncio.Lock()
        self._auth_lock = asyncio.Lock()
        self._reader_lock = RLock()
        self._requests = {}
        from .request_policy import RequestRetryPolicy
        from .preferences import RequestPreferences
        self.auth_retry_policy = RequestRetryPolicy(
            lambda: RequestPreferences(.6, 3, 5.0), check_gate=self._check_request_notice_gate,
            confirm_routes=self._request_notice_routes,
        )
        # Explicit native launch wires its stable durable network-stop scope
        # before this binding becomes interactive. Legacy unit fixtures do
        # not acquire network privileges by merely creating a binding.
        self.auth_stop_required = False
        self.managed_network_stop_scope = None
        self.auth_shared_stop_required = False
        self.shared_network_stop_scope = None
        self.auth_startup_result = None
        self.recheck_startup_stop = None
        self._stop_recheck_worker = None
        self._stop_recheck_result = (None, 'busy')
        # Header login status shared by every client of this process.
        from .auth_status import AuthStatusHolder
        self.auth_status = AuthStatusHolder()
        self.startup_auth_task = None
        self._loop = None
        self._refresh_running = False
        self._refresh_again = None
        self._background_tasks = set()
        # FL-A08: replaceable browser Cookie source and the factory of the
        # credential store that adopts what it finds. Both stay None for a bare
        # binding; only the explicit native launch supplies them, so a test or
        # tool never reads a real browser profile implicitly.
        self.browser_cookie_source = None
        self.cookie_store_factory = None
        self._cookie_store = None
        self._cookie_lock = Lock()
        self._expiry_lock = Lock()
        self._cookie_expiry_coordinator = None
        self._startup_extract_done = False
        self.last_cookie_adoption = None
        # FL-A09/A10: after a confirmed Cookie, account-switch confirmation and
        # the 24 hour automatic following sync (same stop gate as the check).
        from .account_followup import AccountFollowup
        self.followup = AccountFollowup(self)

    def _request_notice_routes(self):
        # The banner calls this without doing file I/O or taking the reader
        # lock. The actual confirmation revalidates the captured scopes.
        scope = self.managed_network_stop_scope
        shared = self.shared_network_stop_scope
        if (self._closed or not self.auth_stop_required or scope is None
                or (self.auth_shared_stop_required and shared is None)):
            return ()
        gates = scope.gate, None if shared is None else shared.gate
        return (
            (gates, False, lambda: self._confirm_request_notice_reader(scope, shared, selected=False)),
            (gates, True, lambda: self._confirm_request_notice_reader(scope, shared, selected=True)),
        )

    def _confirm_request_notice_reader(self, scope, shared, *, selected):
        from .managed_network_stop_profile_scope import _read_stop_profile, _require_native_stop_owner
        from .managed_network_stop_shared_transport import SharedProfileStopTransport, _DraftAuthSharedTransport
        from .managed_network_stop_transport import ManagedNetworkStopTransport
        from .managed_network_stop_gate import _gate_storage_io

        with self._reader_lock:
            if (self._closed or not self.auth_stop_required
                    or scope is not self.managed_network_stop_scope
                    or shared is not self.shared_network_stop_scope
                    or (self.auth_shared_stop_required and shared is None)):
                return None
            if self.recheck_startup_stop is not None:
                with _gate_storage_io('native profile revision unavailable'):
                    if self.store.snapshot().revision != self.launch_snapshot.revision:
                        return None
            if not selected:
                # Draft admission verifies the profile owner and each physical
                # gate but is independent of the saved Cookie selection.
                if shared is not None:
                    return _DraftAuthSharedTransport(scope, shared, None)._confirm_admission()
                _require_native_stop_owner(scope.root, scope.profile_identity)
                return ManagedNetworkStopTransport(scope.gate, _no_request_notice_send)._confirm_admission()

            saved = _read_stop_profile(scope.root, scope.profile_identity)
            if saved.cookie_file is None:
                return None
            with scope.create_reader(saved.cookie_file) as reader:
                if shared is not None:
                    # Only construct the adapter. The normal reader factory's
                    # startup STOP propagation is a write and belongs to GET.
                    reader._transport = SharedProfileStopTransport(
                        reader._transport, shared.gate, shared_owner_check=shared._check_owner)
                return reader._confirm_gate_admission()

    def _check_request_notice_gate(self):
        from .managed_network_stop_gate import ManagedNetworkGateStorageError
        from .request_notice_gates import GateNoticeState, GateObservationSnapshot, check_request_notice_gates

        with self._reader_lock:
            local = self.managed_network_stop_scope
            shared = self.shared_network_stop_scope
            required = self.auth_stop_required, self.auth_shared_stop_required
            if self._closed:
                raise ManagedNetworkGateStorageError()
        snapshot = (check_request_notice_gates(local, shared) if local is not None or shared is not None
                    else GateObservationSnapshot(()))
        states = snapshot.states
        if required[0] and local is None:
            states += (GateNoticeState('local', 'unverified'),)
        if required[1] and shared is None:
            states += (GateNoticeState('shared', 'unverified'),)
        with self._reader_lock:
            if (self._closed or self.managed_network_stop_scope is not local
                    or self.shared_network_stop_scope is not shared
                    or required != (self.auth_stop_required, self.auth_shared_stop_required)):
                raise ManagedNetworkGateStorageError()
        return GateObservationSnapshot(states or (GateNoticeState('both', 'unverified'),), snapshot.consistent)

    def saved_folder_context(self):
        from .desktop_integration import DesktopActionRejected
        from .fs_paths import logical_path, native_io_path
        from .native_profile import STORAGE_FOLDER, STORAGE_MARKER, STORAGE_IDENTITY, _owned_marker

        with self._reader_lock:
            if self._closed or self.launch_snapshot is None:
                raise DesktopActionRejected('終了処理中は保存先を開けません。')
            try:
                current = self.store.snapshot()
            except Exception:
                raise DesktopActionRejected('保存先の設定を確認できません。') from None
            names = ('save_parent', 'library_readonly_db', 'library_image_root')
            if any(getattr(current, name) != getattr(self.launch_snapshot, name) for name in names):
                raise DesktopActionRejected('再起動後に新しい保存先を開けます。')
            parent = self.launch_snapshot.save_parent
            base = logical_path(parent / STORAGE_FOLDER) if parent is not None else None
            if base is not None:
                try:
                    _owned_marker(native_io_path(base), STORAGE_MARKER, STORAGE_IDENTITY)
                except Exception:
                    raise DesktopActionRejected('保存先の所有情報を確認できません。') from None
            return base, current.revision

    def inspect_stop_history(self):
        """Read only the configured roots; never authorize GET or create a seal."""
        from .managed_network_stop_history_readonly import read_existing_stop_history_readonly
        from .native_profile import plan_storage
        with self._reader_lock:
            if self._closed:
                return ({'owner':'native','state':'history_unverified','status_code':None,'authorizes_get':False},)
            roots = [('native', self.store.root)]
            incomplete = False
            try:
                paths = plan_storage(self.store.snapshot(), self.store.root)
                for key, kind in (('pixiv_download_root','images'), ('novel_download_root','novel'), ('ugoira_root','ugoira')):
                    if paths.get(key) is not None:
                        roots.append((kind, paths[key]))
            except Exception:
                incomplete = True
            result = []
            for kind, root in roots:
                observed = read_existing_stop_history_readonly(root, owner_kind=kind)
                result.append({'owner':kind, 'state':observed.state,
                               'status_code':observed.status_code, 'authorizes_get':False})
            if incomplete:
                result.append({'owner':'configured','state':'history_unverified','status_code':None,'authorizes_get':False})
            return tuple(result)

    def create_view(self, *, allow_auth=True):
        from .ui.native_profile_view import NativeProfileView

        view = NativeProfileView(self.store, pick_file=self.pick_file,
                                 pick_folder=self.pick_folder,
                                 pick_database=self.pick_database,
                                 pick_images=self.pick_folder,
                                 pick_model=self.pick_model,
                                 verify_auth=self.check_selected_authentication if allow_auth else None)
        view.migration_factory = self.create_migration_view
        view.on_saved = self.request_cookie_check
        view.request_retry_policy = self.auth_retry_policy
        return view

    def create_migration_service(self):
        from .managed_stop_migration import StopMigrationService
        return StopMigrationService(self.store)

    def create_migration_view(self):
        from .ui.stop_migration_view import StopMigrationView
        return StopMigrationView(self.create_migration_service(), pick_folder=self.pick_folder)

    async def _pick(self, *, folder, database=False, model=False):
        from nicegui import app
        from webview import FileDialog

        async with self._picker_lock:
            if self._closed or app.native.main_window is None:
                return None
            selected = await app.native.main_window.create_file_dialog(
                dialog_type=FileDialog.FOLDER if folder else FileDialog.OPEN,
                allow_multiple=False,
                file_types=() if folder else
                    ('ONNX model (*.onnx)', 'All files (*.*)') if model else
                    ('SQLite files (*.sqlite;*.sqlite3;*.db)', 'All files (*.*)') if database else
                    ('Cookie files (*.txt)', 'All files (*.*)'),
            )
            if self._closed or not selected:
                return None
            if not isinstance(selected, (list, tuple)) or len(selected) != 1:
                raise ValueError('選択した項目を確認できません。')
            return Path(selected[0])

    async def pick_file(self):
        return await self._pick(folder=False)

    async def pick_folder(self):
        return await self._pick(folder=True)

    async def pick_database(self):
        return await self._pick(folder=False, database=True)

    async def pick_model(self):
        return await self._pick(folder=False, model=True)

    async def verify_auth(self, cookie_file):
        return (await self.check_authentication(cookie_file)).ok

    async def check_selected_authentication(self, cookie_file):
        """The settings check may recover startup using the saved credential."""
        result, identity = await self._check_authentication_with_identity(cookie_file)
        services = getattr(self, '_shared_event_services', None)
        if (identity is not None and identity[0] is not None and not self._closed
                and getattr(services, '_scheduler_waiting_for_auth', False)
                and Path(cookie_file) == self._configured_cookie()
                and not self.cookie_replaced_since_launch()):
            # Keep the settings button's bounded authentication result separate
            # from the potentially long following fetch.
            self._spawn(self.followup.after_confirmed(str(identity[0])))
        return result

    async def check_authentication(self, cookie_file, *, _imported=False, _publish=True):
        """Bounded GET reader through the STOP fence.

        Triggers: the settings button, application start, a saved Cookie and an
        extension Cookie receipt. The result is unchanged; the signed-in user ID
        and Cookie expiry observed by the same check are published only to the
        header status (restoring the original login-status line).
        """
        result, _ = await self._check_authentication_with_identity(cookie_file, imported=_imported, publish=_publish)
        return result

    async def _check_authentication_with_identity(self, cookie_file, *, imported=False, publish=True):
        async with self._auth_lock:
            if self._closed:
                from .native_auth_result import NativeAuthResult
                return NativeAuthResult('closed'), None
            stopped = Event()
            with self._reader_lock:
                self._requests[stopped] = None
            try:
                result, identity = await asyncio.to_thread(self._verify_auth_result, Path(cookie_file), stopped)
                if publish:
                    self._publish_auth(result, imported, identity=identity)
                return result, identity
            except asyncio.CancelledError:
                stopped.set()
                with self._reader_lock:
                    reader = self._requests.get(stopped)
                    if reader is None:
                        self._requests.pop(stopped, None)
                if reader is not None:
                    reader.close()
                raise

    def _publish_auth(self, result, imported, *, identity, notice=None):
        from .auth_status import status_from_result, NOTICE_UPDATED
        if self._closed or result.code == 'closed':
            return
        uid, days = identity or (None, None)
        status = status_from_result(result, uid, days)
        if notice is None and imported and status.ok:
            notice = NOTICE_UPDATED
        self.auth_status.publish(status, notice if status.ok else None)

    # -- FL-A08: automatic extraction from the browser -------------------------

    def _extraction_wanted(self, result, cookie, identity):
        """Original check_login_status: expired, or 1 week or less left.

        Never for a missing setting (original "missing"), a communication
        failure or any STOP/busy/uncertain state: no request outcome says the
        Cookie is stale there. A 401/403 is excluded too, because it latches the
        fence and the replacement could not be verified.
        """
        code = getattr(result, 'code', None)
        if code in ('cookie_unreadable', 'identity_unverified'):
            try:  # original "missing": an absent or empty file is not an expiry
                if cookie is not None and (not Path(cookie).is_file() or Path(cookie).stat().st_size == 0):
                    return False
            except OSError:
                return False
            return True
        if code == 'verified':
            days = identity[1] if identity is not None else None
            return days is not None and days <= 7
        return False

    def _open_cookie_store(self):
        with self._cookie_lock:
            if self._cookie_store is None:
                if self._closed or self.cookie_store_factory is None:
                    raise RuntimeError('credential store unavailable')
                self._cookie_store = self.cookie_store_factory()
            claim_notice = getattr(self._cookie_store, 'take_legacy_migration_notice', None)
            event_owner = getattr(self, '_shared_event_services', None)
            if claim_notice is not None and getattr(event_owner, 'shared_log', None) is not None:
                try:
                    if claim_notice():
                        from .shared_events import emit_shared
                        emit_shared(event_owner, '\u7531\u6765\u4e0d\u660e\u306e\u65e7 Cookie \u4e16\u4ee3\u306f\u524a\u9664\u305b\u305a\u4fdd\u6301\u3057\u307e\u3057\u305f\u3002', level='normal')
                except Exception:
                    pass  # Notification failure must not interrupt authentication.
            return self._cookie_store

    def cookie_expiry_coordinator(self):
        with self._expiry_lock:
            if self._closed or self.cookie_store_factory is None:
                raise RuntimeError('credential store unavailable')
            if self._cookie_expiry_coordinator is None:
                from .cookie_expiry_refresh import CookieExpiryCoordinator
                self._cookie_expiry_coordinator = CookieExpiryCoordinator(self.store, self._open_cookie_store)
            return self._cookie_expiry_coordinator

    async def adopt_browser_cookies(self):
        """Extract, verify through the gate, then adopt the browser Cookie.

        Returns adopted / unchanged / not_found / rejected / blocked /
        unavailable / error. The original manual helper
        (_run_auto_browser_cookie_picker) has no call site, so this has no
        button; the startup path is its only caller. The profile changes only
        after the candidate file passed the same gated authentication check.
        """
        outcome, _ = await self._adopt_browser_cookies()
        self.last_cookie_adoption = outcome
        return outcome

    async def _adopt_browser_cookies(self):
        source = self.browser_cookie_source
        if source is None or self.cookie_store_factory is None or self._closed:
            return 'unavailable', None
        try:
            found = await asyncio.to_thread(source.extract)
        except Exception:
            return 'error', None
        if found is None:
            return 'not_found', None
        candidate = store = None
        try:
            store = await asyncio.to_thread(self._open_cookie_store)
            revision = self.store.snapshot().revision
            candidate = await asyncio.to_thread(store.prepare, found.cookies, expected_revision=revision)
            if not candidate.changed:
                store.discard(candidate)
                return 'unchanged', None
            result, identity = await self._check_authentication_with_identity(candidate.cookie_file, publish=False)
            if result.code != 'verified' or self._closed:
                store.discard(candidate)
                outcome = ('rejected' if result.code in ('identity_unverified', 'cookie_unreadable',
                                                        'remote_denied') else 'blocked')
                return outcome, None
            await asyncio.to_thread(store.commit, candidate)
        except asyncio.CancelledError:
            if candidate is not None:
                store.discard(candidate)
            raise
        except Exception:
            if candidate is not None and store is not None:
                store.discard(candidate)
            return 'error', None
        self._publish_auth(result, True, identity=identity,
                           notice=f'ブラウザ ({found.browser}) からの Cookie 自動抽出・更新に成功しました！'
                                  '取得・更新の通信は再起動後に新しい Cookie で行われます。')
        return 'adopted', identity

    def cookie_replaced_since_launch(self):
        """True when the saved Cookie differs from the one the readers were built with."""
        try:
            if self.launch_snapshot is None:
                return False
            current = self.store.snapshot()
            if current.cookie_file == self.launch_snapshot.cookie_file:
                return False
            coordinator = self._cookie_expiry_coordinator
            return not (coordinator is not None and coordinator.recognizes_renewal(
                current, self.launch_snapshot.cookie_file))
        except Exception:
            return True

    def _configured_cookie(self):
        try:
            return self.store.snapshot().cookie_file
        except Exception:
            return None

    async def refresh_auth_status(self, *, imported=False, startup=False):
        """Header check through the same gated reader as the settings button.

        Concurrent requests coalesce: one runs, and one more follows only if a
        request arrived meanwhile (a Cookie saved during a check is re-read).
        """
        if self._refresh_running:
            self._refresh_again = bool(self._refresh_again) or imported
            return
        self._refresh_running = True
        try:
            while True:
                if self._closed:
                    return
                cookie = self._configured_cookie()
                if cookie is None:
                    from .auth_status import missing_status
                    self.auth_status.publish(missing_status())
                else:
                    result, identity = await self._check_authentication_with_identity(cookie, imported=imported)
                    # The shared stop gate is briefly busy while another request holds it
                    # (seen on the real app at startup). It is transient, so retry a few
                    # times instead of leaving the header on the busy message.
                    for delay in BUSY_RETRY_DELAYS:
                        if result.code != 'busy' or self._closed:
                            break
                        await asyncio.sleep(delay)
                        result, identity = await self._check_authentication_with_identity(cookie, imported=imported)
                    if (startup and not imported and not self._startup_extract_done
                            and self._extraction_wanted(result, cookie, identity)):
                        # Original: only the startup check tries the browser, once.
                        self._startup_extract_done = True
                        from .shared_events import emit_shared
                        event_owner = getattr(self, '_shared_event_services', None)
                        emit_shared(event_owner, 'Cookie 有効期限チェック: ブラウザから自動抽出を試行します。', level='info')
                        outcome, adopted_identity = await self._adopt_browser_cookies()
                        self.last_cookie_adoption = outcome
                        message, level = {
                            'adopted': ('ブラウザからの Cookie 自動抽出・更新に成功しました。', 'success'),
                            'unchanged': ('ブラウザの Cookie に変更はありません。', 'normal'),
                            'not_found': ('ブラウザに有効な Cookie が見つかりませんでした。', 'normal'),
                            'blocked': ('通信停止または状態未確認のため Cookie 自動抽出の確認を中断しました。', 'normal'),
                            'rejected': ('自動抽出した Cookie を確認できませんでした。', 'normal'),
                            'unavailable': ('Cookie 自動抽出を利用できません。', 'normal'),
                            'error': ('Cookie 自動抽出に失敗しました。', 'error'),
                        }.get(outcome, ('Cookie 自動抽出の結果を確認できません。', 'normal'))
                        emit_shared(event_owner, message, level=level)
                        if outcome == 'adopted':
                            identity = adopted_identity
                    if identity is not None and identity[0] is not None and not self._closed:
                        await self.followup.after_confirmed(str(identity[0]))
                if self._refresh_again is None:
                    return
                imported, self._refresh_again = self._refresh_again, None
        finally:
            self._refresh_running = False

    def _spawn(self, coroutine):
        task = asyncio.ensure_future(coroutine)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    def bind_loop(self, loop):
        self._loop = loop

    def request_cookie_check(self, *, imported=True):
        """Schedule a header check from any thread; never blocks or connects inline."""
        if self._closed:
            return
        loop = self._loop
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None:
            self._loop = running
            self._spawn(self.refresh_auth_status(imported=imported))
        elif loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(
                lambda: self._spawn(self.refresh_auth_status(imported=imported)))

    def start_startup_auth_check(self):
        """Once per process, only for a STOP-fenced native profile."""
        if self.startup_auth_task is not None:
            return self.startup_auth_task
        if self._closed or not self.auth_stop_required or self.auth_startup_result is not None:
            return None
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            return None  # no event loop yet (not a served page): retry at the next shell build
        self.startup_auth_task = self._spawn(self.refresh_auth_status(startup=True))
        return self.startup_auth_task

    def _verify_auth(self, cookie_file, stopped):
        # Keep the historical bool interface for non-UI callers.
        from .test_owner_runtime import require_inside
        require_inside(cookie_file)
        result, _ = self._verify_auth_result(cookie_file, stopped)
        return result.ok

    def _verify_auth_result(self, cookie_file, stopped):
        from .test_owner_runtime import require_inside
        require_inside(cookie_file)
        from .pixiv_read_client import (LivePixivReadClient, CookieSourceRejected,
            HTTPStatusRejected, AuthenticationMissing, ApiRejected)
        from .managed_network_stop_gate import (
            ManagedNetworkStopped, ManagedNetworkGateBusy, ManagedNetworkGateStorageError, _gate_storage_io)
        from .managed_network_stop_shared_scope import SharedStopOwnerAbsent, SharedGetOutcomeUncertain
        from .native_auth_result import NativeAuthResult
        from .request_policy import request_gate_preflight

        reader = None
        try:
            with request_gate_preflight(self.auth_retry_policy), self._reader_lock:
                if self._closed or stopped.is_set():
                    return NativeAuthResult('closed'), None
                if self.recheck_startup_stop is not None:
                    with _gate_storage_io('native profile revision unavailable'):
                        if self.store.snapshot().revision != self.launch_snapshot.revision:
                            self.auth_retry_policy.record_refusal()
                            return NativeAuthResult('profile_changed'), None
                scope = self.managed_network_stop_scope
                if self.auth_stop_required:
                    if scope is None:
                        # A configured historical profile with unknown root
                        # stop history must never send an authentication GET.
                        self.auth_retry_policy.record_refusal()
                        return NativeAuthResult('local_unavailable'), None
                    # Reject revoked/replaced profile owners as well as an
                    # already-latched 401/403/429 before the candidate Cookie
                    # is parsed. The reader's actual HTTP transport below is
                    # guarded again so a race cannot skip persistence.
                    from .managed_network_stop_profile_scope import _require_native_stop_owner
                    _require_native_stop_owner(scope.root, scope.profile_identity)
                    scope.require_allowed()
                    if self.auth_shared_stop_required:
                        if self.shared_network_stop_scope is None:
                            self.auth_retry_policy.record_refusal()
                            return NativeAuthResult('owner_absent'), None
                        self.shared_network_stop_scope.require_allowed()
                        from .managed_network_stop_shared_transport import create_shared_draft_auth_reader
                        reader = create_shared_draft_auth_reader(
                            scope, cookie_file, self.shared_network_stop_scope)
                    else:
                        from .managed_network_stop_reader import create_exact_managed_read_client
                        reader = create_exact_managed_read_client(cookie_file, scope.gate)
                else:
                    reader = LivePixivReadClient(cookie_file)
                reader.set_retry_policy(self.auth_retry_policy)
                self._requests[stopped] = reader
                if stopped.is_set():
                    return NativeAuthResult('closed'), None
            uid = reader.require_identity()
            with self._reader_lock:
                if self._closed or stopped.is_set():
                    return NativeAuthResult('closed'), None
                from .auth_status import cookie_days_left
                return NativeAuthResult('verified'), (uid, cookie_days_left(cookie_file))
        except CookieSourceRejected:
            return NativeAuthResult('cookie_unreadable'), None
        except SharedStopOwnerAbsent:
            return NativeAuthResult('owner_absent'), None
        except SharedGetOutcomeUncertain:
            return NativeAuthResult('uncertain'), None
        except ManagedNetworkGateBusy:
            return NativeAuthResult('busy'), None
        except ManagedNetworkGateStorageError:
            return NativeAuthResult('local_unavailable'), None
        except ManagedNetworkStopped as error:
            return NativeAuthResult('rate_limited' if error.status_code == 429 else
                                    'remote_denied' if getattr(error, 'observed_response', False)
                                    else 'stopped', error.status_code), None
        except HTTPStatusRejected as error:
            return NativeAuthResult('rate_limited' if error.status_code == 429 else
                                    'remote_denied' if error.status_code in (401, 403) else
                                    'communication_failed', error.status_code), None
        except (AuthenticationMissing, ApiRejected):
            return NativeAuthResult('identity_unverified'), None
        except Exception:
            return NativeAuthResult('communication_failed'), None
        finally:
            with self._reader_lock:
                self._requests.pop(stopped, None)
            if reader is not None:
                reader.close()

    def startup_stop_recheck_result(self):
        with self._reader_lock:
            return self._stop_recheck_result

    async def check_startup_stop(self, cancel):
        with self._reader_lock:
            if self._closed or cancel.is_set():
                return None, 'closed'
            if self._stop_recheck_worker is not None or self._stop_recheck_result[1] != 'busy':
                return self._stop_recheck_result
            finished = Event()
            self._stop_recheck_worker = (cancel, finished)
        worker = asyncio.create_task(asyncio.to_thread(self._probe_startup_stop, cancel, finished))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            cancel.set()
            raise

    def _probe_startup_stop(self, cancel, finished):
        from .managed_network_stop_gate import (
            ManagedNetworkGateBusy, ManagedNetworkGateStorageError, ManagedNetworkStopped,
        )
        from .managed_network_stop_shared_scope import SharedGetOutcomeUncertain, SharedStopOwnerAbsent
        from .managed_network_stop_startup import (
            StartupStopCancelled, StartupStopProfileChanged, retry_startup_lock, startup_lock_deadline,
        )
        from .native_profile import NativeProfileError

        scope = profile_scope = None
        try:
            try:
                deadline = startup_lock_deadline()
                if self.store.snapshot().revision != self.launch_snapshot.revision:
                    code = 'profile_changed'
                else:
                    profile_scope, scope = self.recheck_startup_stop(deadline=deadline, cancel=cancel)
                    plan = retry_startup_lock(scope.preview_uncertain_get_recovery,
                                              deadline=deadline, cancel=cancel)
                    code = 'uncertain' if plan is not None else 'stop_rechecked'
            except StartupStopCancelled:
                return None, 'closed'
            except StartupStopProfileChanged:
                code = 'profile_changed'
            except ManagedNetworkGateBusy:
                return None, 'busy'
            except ManagedNetworkStopped:
                code = 'stopped'
            except SharedGetOutcomeUncertain:
                code = 'uncertain'
            except SharedStopOwnerAbsent:
                code = 'owner_absent'
            except (ManagedNetworkGateStorageError, NativeProfileError, OSError):
                code = 'local_unavailable'
            with self._reader_lock:
                if self._closed or cancel.is_set():
                    return None, 'closed'
                if code == 'stop_rechecked':
                    try:
                        if self.store.snapshot().revision != self.launch_snapshot.revision:
                            code = 'profile_changed'
                    except NativeProfileError:
                        code = 'local_unavailable'
                    if code == 'stop_rechecked':
                        old = self.managed_network_stop_scope, self.shared_network_stop_scope
                        profile_scope.shared_owner = scope
                        self.managed_network_stop_scope = profile_scope
                        self.shared_network_stop_scope = scope
                        self.auth_retry_policy.replace_notice_gates(
                            tuple(owner.gate if owner is not None else None for owner in old),
                            (profile_scope.gate, scope.gate),
                        )
                self._stop_recheck_result = (scope if code == 'uncertain' else None, code)
                return self._stop_recheck_result
        finally:
            with self._reader_lock:
                self._stop_recheck_worker = None
                finished.set()

    def close(self):
        with self._reader_lock:
            if self._closed:
                return
            self._closed = True
            requests = tuple(self._requests.items())
            stop_recheck = self._stop_recheck_worker
        if stop_recheck is not None:
            cancel, finished = stop_recheck
            cancel.set()
            finished.wait()
        for stopped, reader in requests:
            stopped.set()
            if reader is not None:
                reader.close()
        self.followup.close()
        with self._expiry_lock:
            expiry, self._cookie_expiry_coordinator = self._cookie_expiry_coordinator, None
        if expiry is not None:
            expiry.close()
        with self._cookie_lock:
            cookie_store, self._cookie_store = self._cookie_store, None
        if cookie_store is not None:
            try:
                cookie_store.close()
            except Exception:
                pass
        self.store.close()


def register_profile_recovery(binding, *, cookie_rejected=False,
                              stop_migration_required=False, stop_gate_busy=False,
                              stop_get_uncertain=False, legacy_v5_uncertain=False,
                              legacy_copy_error=False):
    """Show editable configuration only; no media/service fallback is started."""
    from nicegui import app, ui
    from .ui.theme import CSS, TOKENS

    @ui.page('/')
    def profile_recovery_page():
        ui.add_css(CSS)
        ui.colors(primary=TOKENS['accent'])
        with ui.column().classes('w-full max-w-2xl mx-auto p-6 gap-4'):
            ui.label('PixivVault — 起動設定の確認').classes('text-h5')
            from .native_auth_result import NativeAuthResult
            startup_result = getattr(binding, 'auth_startup_result', None)
            recheck_stop = stop_gate_busy and getattr(binding, 'recheck_startup_stop', None)
            notice = ui.label('保管領域の状態を確認できません（旧形式の判別不能）。バックアップから復元してください。'
                     if legacy_v5_uncertain else
                     '\u65e7DB\u306e\u8907\u88fd\u4f5c\u6210\u306b\u5931\u6557\u3057\u307e\u3057\u305f\u3002\u4fdd\u5b58\u5148\u30fb\u5bb9\u91cf\u30fb\u65e7DB\u306e\u4f7f\u7528\u72b6\u614b\u3092\u78ba\u8a8d\u3057\u3066\u518d\u8d77\u52d5\u3057\u3066\u304f\u3060\u3055\u3044\u3002'
                     if legacy_copy_error else
                     '通信記録が使用中です。ほかの処理が終わってから「再確認」を押してください。'
                     if recheck_stop else
                     startup_result.message if type(startup_result) is NativeAuthResult else
                     '前の取得結果が不確かなため通信を停止しています。取得停止の確認・回復を開いて手順を確認してください。'
                     if stop_get_uncertain else
                     '通信記録が使用中のため確認できません。ほかの処理が終わってからアプリを再起動してください。確認できるまで通信は停止しています。'
                     if stop_gate_busy else
                     '認証・通信の停止履歴と保存先の所有状態を安全に照合できません。通信は停止しています。'
                     '保存済みデータを削除せず、停止履歴の確認と正式な移行を行ってください。'
                     if stop_migration_required else
                     '選択したCookieファイルを読み込めません。ファイルを選び直して保存し、アプリを再起動してください。'
                     if cookie_rejected else
                     '設定した参照先を利用できません。接続と保存先を確認し、旧DBは使用中でない凍結コピーを選択して再起動してください。'
                     ).props('role=alert data-testid=native-profile-recovery')
            if stop_migration_required:
                from .ui.stop_recovery_view import StopRecoveryPanel
                recovery = StopRecoveryPanel(binding)
                ui.context.client.on_delete(recovery.close)
            if stop_get_uncertain:
                from .ui.shared_get_recovery_view import SharedGetRecoveryView
                recovery = SharedGetRecoveryView(binding.shared_network_stop_scope)
                ui.context.client.on_delete(recovery.close)
            view = binding.create_view(allow_auth=not (stop_migration_required or stop_gate_busy or stop_get_uncertain or legacy_v5_uncertain))
            if recheck_stop:
                from .ui.stop_recovery_view import build_startup_stop_recheck
                notice.classes('pv-warning-banner')
                view.reserve_auth_button = True
                view.on_saved = None
                build_startup_stop_recheck(binding, notice, view)
            view.build()
            ui.context.client.on_delete(view.close)

    @app.get('/healthz', include_in_schema=False)
    def profile_health():
        return {'status': 'configuration-required', 'mode': 'configuration', 'data': 'none'}

    @app.get('/readyz', include_in_schema=False)
    def profile_ready():
        from nicegui import Client
        return {'ui_connected': any(c.has_socket_connection for c in Client.instances.values())}

    if stop_gate_busy and getattr(binding, 'recheck_startup_stop', None):
        async def close_busy_recovery():
            await asyncio.to_thread(binding.close)

        app.on_shutdown(close_busy_recovery)
    else:
        app.on_shutdown(binding.close)
