"""Owned desktop profile entry and explicit native picker/authentication actions."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from threading import Event, RLock


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
        self.auth_retry_policy = RequestRetryPolicy(lambda:RequestPreferences(.6,3,5.0))
        # Explicit native launch wires its stable durable network-stop scope
        # before this binding becomes interactive. Legacy unit fixtures do
        # not acquire network privileges by merely creating a binding.
        self.auth_stop_required = False
        self.managed_network_stop_scope = None
        self.auth_shared_stop_required = False
        self.shared_network_stop_scope = None
        self.auth_startup_result = None

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
                                  verify_auth=self.check_authentication if allow_auth else None)
        view.migration_factory = self.create_migration_view
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

    async def check_authentication(self, cookie_file):
        """Explicit button only; bounded GET reader, never expose an account ID."""
        async with self._auth_lock:
            if self._closed:
                from .native_auth_result import NativeAuthResult
                return NativeAuthResult('closed')
            stopped = Event()
            with self._reader_lock:
                self._requests[stopped] = None
            try:
                return await asyncio.to_thread(self._verify_auth_result, Path(cookie_file), stopped)
            except asyncio.CancelledError:
                stopped.set()
                with self._reader_lock:
                    reader = self._requests.get(stopped)
                    if reader is None:
                        self._requests.pop(stopped, None)
                if reader is not None:
                    reader.close()
                raise

    def _verify_auth(self, cookie_file, stopped):
        # Keep the historical bool interface for non-UI callers.
        from .test_owner_runtime import require_inside
        require_inside(cookie_file)
        return self._verify_auth_result(cookie_file, stopped).ok

    def _verify_auth_result(self, cookie_file, stopped):
        from .test_owner_runtime import require_inside
        require_inside(cookie_file)
        from .pixiv_read_client import (LivePixivReadClient, CookieSourceRejected,
            HTTPStatusRejected, AuthenticationMissing, ApiRejected)
        from .managed_network_stop_gate import (
            ManagedNetworkStopped, ManagedNetworkGateBusy, ManagedNetworkGateStorageError)
        from .managed_network_stop_shared_scope import SharedStopOwnerAbsent, SharedGetOutcomeUncertain
        from .native_auth_result import NativeAuthResult

        reader = None
        try:
            with self._reader_lock:
                if self._closed or stopped.is_set():
                    return NativeAuthResult('closed')
                scope = self.managed_network_stop_scope
                if self.auth_stop_required:
                    if scope is None:
                        # A configured historical profile with unknown root
                        # stop history must never send an authentication GET.
                        return NativeAuthResult('local_unavailable')
                    # Reject revoked/replaced profile owners as well as an
                    # already-latched 401/403/429 before the candidate Cookie
                    # is parsed. The reader's actual HTTP transport below is
                    # guarded again so a race cannot skip persistence.
                    from .managed_network_stop_gate import _file_identity
                    from .native_profile import PROFILE_DB, PROFILE_MARKER, PROFILE_IDENTITY, _owned_marker
                    from .managed_network_stop_profile_scope import _SCOPE_MARKER, _SCOPE_IDENTITY
                    _owned_marker(scope.root, PROFILE_MARKER, PROFILE_IDENTITY)
                    _owned_marker(scope.root, _SCOPE_MARKER, _SCOPE_IDENTITY)
                    if _file_identity(scope.root / PROFILE_DB) != scope.profile_identity:
                        return NativeAuthResult('local_unavailable')
                    scope.require_allowed()
                    if self.auth_shared_stop_required:
                        if self.shared_network_stop_scope is None:
                            return NativeAuthResult('owner_absent')
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
                    return NativeAuthResult('closed')
            reader.require_identity()
            with self._reader_lock:
                return NativeAuthResult('verified' if not self._closed and not stopped.is_set() else 'closed')
        except CookieSourceRejected:
            return NativeAuthResult('cookie_unreadable')
        except SharedStopOwnerAbsent:
            return NativeAuthResult('owner_absent')
        except SharedGetOutcomeUncertain:
            return NativeAuthResult('uncertain')
        except ManagedNetworkGateBusy:
            return NativeAuthResult('busy')
        except ManagedNetworkGateStorageError:
            return NativeAuthResult('local_unavailable')
        except ManagedNetworkStopped as error:
            return NativeAuthResult('rate_limited' if error.status_code == 429 else
                                    'remote_denied' if getattr(error, 'observed_response', False)
                                    else 'stopped', error.status_code)
        except HTTPStatusRejected as error:
            return NativeAuthResult('rate_limited' if error.status_code == 429 else
                                    'remote_denied' if error.status_code in (401, 403) else
                                    'communication_failed', error.status_code)
        except (AuthenticationMissing, ApiRejected):
            return NativeAuthResult('identity_unverified')
        except Exception:
            return NativeAuthResult('communication_failed')
        finally:
            with self._reader_lock:
                self._requests.pop(stopped, None)
            if reader is not None:
                reader.close()

    def close(self):
        with self._reader_lock:
            if self._closed:
                return
            self._closed = True
            requests = tuple(self._requests.items())
        for stopped, reader in requests:
            stopped.set()
            if reader is not None:
                reader.close()
        self.store.close()


def register_profile_recovery(binding, *, cookie_rejected=False,
                              stop_migration_required=False, stop_gate_busy=False,
                              stop_get_uncertain=False):
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
            ui.label(startup_result.message if type(startup_result) is NativeAuthResult else
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
            view = binding.create_view(allow_auth=not (stop_migration_required or stop_gate_busy or stop_get_uncertain))
            view.build()
            ui.context.client.on_delete(view.close)

    @app.get('/healthz', include_in_schema=False)
    def profile_health():
        return {'status': 'configuration-required', 'mode': 'configuration', 'data': 'none'}

    @app.get('/readyz', include_in_schema=False)
    def profile_ready():
        from nicegui import Client
        return {'ui_connected': any(c.has_socket_connection for c in Client.instances.values())}

    app.on_shutdown(binding.close)
