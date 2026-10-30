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

    def create_view(self):
        from .ui.native_profile_view import NativeProfileView

        return NativeProfileView(self.store, pick_file=self.pick_file,
                                 pick_folder=self.pick_folder,
                                 pick_database=self.pick_database,
                                 pick_images=self.pick_folder,
                                 pick_model=self.pick_model,
                                 verify_auth=self.verify_auth)

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
        """Explicit button only; bounded GET reader, never expose an account ID."""
        async with self._auth_lock:
            if self._closed:
                return False
            stopped = Event()
            with self._reader_lock:
                self._requests[stopped] = None
            try:
                return await asyncio.to_thread(self._verify_auth, Path(cookie_file), stopped)
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
        from .pixiv_read_client import LivePixivReadClient

        reader = None
        try:
            with self._reader_lock:
                if self._closed or stopped.is_set():
                    return False
                reader = LivePixivReadClient(cookie_file)
                self._requests[stopped] = reader
                if stopped.is_set():
                    return False
            reader.require_identity()
            with self._reader_lock:
                return not self._closed and not stopped.is_set()
        except Exception:
            return False
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


def register_profile_recovery(binding, *, cookie_rejected=False):
    """Show editable configuration only; no media/service fallback is started."""
    from nicegui import app, ui
    from .ui.theme import CSS, TOKENS

    @ui.page('/')
    def profile_recovery_page():
        ui.add_css(CSS)
        ui.colors(primary=TOKENS['accent'])
        with ui.column().classes('w-full max-w-2xl mx-auto p-6 gap-4'):
            ui.label('PixivVault — 起動設定の確認').classes('text-h5')
            ui.label('選択したCookieファイルを読み込めません。ファイルを選び直して保存し、アプリを再起動してください。'
                     if cookie_rejected else
                     '設定した参照先を利用できません。接続と保存先を確認し、旧DBは使用中でない凍結コピーを選択して再起動してください。'
                     ).props('role=alert data-testid=native-profile-recovery')
            view = binding.create_view()
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
