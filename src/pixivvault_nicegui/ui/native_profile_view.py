"""S01/S04 native startup profile controls; local picker/auth are injected.

Embed ``build()`` in the existing PreferencesView card, or call ``open()``
from the first-run settings button. A Save changes only next-start settings.
"""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

from nicegui import ui

from ..native_profile import NativeProfileConflict, NativeProfileError


class NativeProfileView:
    def __init__(self, store, *, pick_file=None, pick_folder=None,
                 pick_database=None, pick_images=None, pick_model=None,
                 verify_auth=None):
        self.store = store
        self.pick_file = pick_file
        self.pick_folder = pick_folder
        self.pick_database = pick_database
        self.pick_images = pick_images
        self.pick_model = pick_model
        self.draft_model = None
        self.model_value = None
        self.model_button = None
        self.provider_select = None
        self.auto_switch = None
        self.verify_auth = verify_auth
        # Called (no arguments) after a successful save; the application then
        # rechecks the saved Cookie and updates the header login status.
        self.on_saved = None
        self.dialog = ui.dialog().props('persistent data-testid=native-profile-dialog')
        self.snapshot = None
        self.draft_cookie = None
        self.draft_parent = None
        self.draft_database = None
        self.draft_images = None
        self.status = None
        self.cookie_value = None
        self.folder_value = None
        self.database_value = None
        self.images_value = None
        self.cookie_button = None
        self.folder_button = None
        self.database_button = None
        self.images_button = None
        self.auth_button = None
        self.save_button = None
        self._epoch = 0
        self._busy = False
        self._picking = False
        self._picker_revision = 0
        self._auth_revision = 0
        self._active = False
        self._closed = False
        self._standalone = False
        self.migration_factory = None
        self.migration_view = None

    def open(self) -> None:
        if self._closed:
            return
        self.dialog.clear()
        with self.dialog, ui.card().classes('w-full max-w-xl'):
            ui.label('起動と保存先の設定').classes('text-h6')
            self.build(standalone=True)
        self.dialog.open()

    def build(self, *, standalone: bool = False) -> None:
        """Inline view, suitable for the application's existing settings card."""
        if self._closed:
            return
        self._epoch += 1
        self._picker_revision += 1
        self._auth_revision += 1
        self._standalone = standalone
        self._active = True
        self._busy = False
        self._picking = False
        epoch = self._epoch
        previous=getattr(self,'_request_status',None)
        if previous is not None:previous.close()
        policy=getattr(self,'request_retry_policy',None)
        if policy is not None:
            from .request_status_view import RequestStatusView
            self._request_status=RequestStatusView(policy)
        async def choose_cookie():
            if self._active and self._epoch == epoch:
                await self.choose_cookie()
        async def choose_folder():
            if self._active and self._epoch == epoch:
                await self.choose_folder()
        async def choose_database():
            if self._active and self._epoch == epoch:
                await self.choose_database()
        async def choose_images():
            if self._active and self._epoch == epoch:
                await self.choose_images()
        async def check_auth():
            if self._active and self._epoch == epoch:
                await self.check_auth()
        async def choose_model():
            if self._active and self._epoch == epoch:
                await self.choose_model()
        try:
            self.snapshot = self.store.snapshot()
        except NativeProfileError:
            self.snapshot = None
        self.draft_cookie = self.snapshot.cookie_file if self.snapshot else None
        self.draft_parent = self.snapshot.save_parent if self.snapshot else None
        self.draft_database = self.snapshot.library_readonly_db if self.snapshot else None
        self.draft_images = self.snapshot.library_image_root if self.snapshot else None
        self.draft_model = self.snapshot.similarity_model_file if self.snapshot else None
        self.model_value = self.model_button = self.provider_select = self.auto_switch = None
        # A previous PreferencesView generation may have destroyed its widgets.
        self.cookie_button = None
        self.folder_button = None
        self.database_button = None
        self.images_button = None
        self.auth_button = None
        self.save_button = None
        ui.label('Pixiv Cookieファイル').classes('text-subtitle1')
        self.cookie_value = ui.label().props('data-testid=native-profile-cookie-value')
        self.folder_value = None
        self._sync_values()
        with ui.row():
            self.cookie_button = ui.button('Cookieファイルを選択', icon='upload_file', color=None, on_click=choose_cookie).props(
                'flat no-caps data-testid=native-profile-pick-cookie')
            self.cookie_button.set_enabled(self.pick_file is not None and self.snapshot is not None)
            ui.button('選択解除', on_click=lambda:self.clear_cookie() if self._epoch == epoch else None).props(
                'flat no-caps data-testid=native-profile-clear-cookie').set_enabled(self.snapshot is not None)
        ui.label('作品の保存先（親フォルダ）').classes('text-subtitle1')
        self.folder_value = ui.label().props('data-testid=native-profile-folder-value')
        self._sync_values()
        with ui.row():
            self.folder_button = ui.button('保存先を選択', icon='folder_open', color=None, on_click=choose_folder).props(
                'flat no-caps data-testid=native-profile-pick-folder')
            self.folder_button.set_enabled(self.pick_folder is not None and self.snapshot is not None)
            ui.button('選択解除', on_click=lambda:self.clear_folder() if self._epoch == epoch else None).props(
                'flat no-caps data-testid=native-profile-clear-folder').set_enabled(self.snapshot is not None)
        with ui.expansion('旧ライブラリ（任意・読取専用）', icon='folder_open',
                          value=self.draft_database is not None or self.draft_images is not None).props(
                              'data-testid=native-profile-library-section'):
            ui.label('旧DBは凍結コピー、画像は読取専用の参照先を選択してください。元データの移行や変更は行いません。').classes('text-caption')
            self.database_value = ui.label().props('data-testid=native-profile-database-value')
            self.images_value = ui.label().props('data-testid=native-profile-images-value')
            self._sync_values()
            with ui.row():
                self.database_button = ui.button('旧DBコピーを選択',on_click=choose_database).props(
                    'data-testid=native-profile-pick-database')
                self.database_button.set_enabled(self.pick_database is not None and self.snapshot is not None)
                self.images_button = ui.button('旧画像rootを選択',on_click=choose_images).props(
                    'data-testid=native-profile-pick-images')
                self.images_button.set_enabled(self.pick_images is not None and self.snapshot is not None)
                ui.button('旧ライブラリ参照を解除',
                          on_click=lambda:self.clear_library() if self._epoch == epoch else None).props(
                    'data-testid=native-profile-clear-library').set_enabled(self.snapshot is not None)
        with ui.expansion('画像解析・GPU', icon='memory', value=self.draft_model is not None).props('data-testid=native-gpu-section'):
            self.model_value = ui.label().props('data-testid=native-gpu-model')
            self.model_button = ui.button('EgaraNetモデルを選択', on_click=choose_model).props('data-testid=native-gpu-pick-model')
            self.model_button.set_enabled(self.pick_model is not None and self.snapshot is not None)
            ui.button('解析モデルの選択解除', on_click=lambda:self.clear_model() if self._epoch == epoch else None).props('data-testid=native-gpu-clear-model')
            self.provider_select = ui.select({'auto':'自動（GPU優先・利用不可ならCPU）', 'cuda':'GPU（CUDA必須）', 'cpu':'CPU'},
                label='解析の実行先', value=self.snapshot.similarity_provider if self.snapshot else 'auto').classes('w-full').props('data-testid=native-gpu-provider')
            ui.label('元のegara_bb.onnxを参照します。設定保存だけでは推論・モデルの取得を行いません。モデルと実行先の変更は次回起動から適用します。').classes('text-caption')
            ui.label('自動で推論するかどうかは、この下の設定「絵柄・構図の推論」で切り替えます（すぐ反映します）。').classes('text-caption')
            self._sync_values()
        ui.label('保存した設定は次回起動から反映されます。実行中のジョブや保存先は変わりません。').classes('text-caption')
        self.status = ui.label('' if self.snapshot else '設定を読み込めません。旧設定は保持されています。').props(
            'data-testid=native-profile-status role=status')
        if self.snapshot is not None and self.snapshot.library_readonly_db is not None:
            ui.label('旧ライブラリを読み取り、新しい作品は選択した専用保存先へ保存します。Cookie接続と併用できます。').classes('text-caption')
        self.auth_button = None
        if self.verify_auth is not None:
            self.auth_button = ui.button('選択したCookieの認証を確認', on_click=check_auth).props(
                'flat no-caps data-testid=native-profile-auth-check')
            self.auth_button.set_enabled(self.draft_cookie is not None)
        with ui.row():
            self.save_button = ui.button('保存', on_click=lambda:self.save() if self._epoch == epoch else None).props('flat no-caps data-testid=native-profile-save')
            self.save_button.set_enabled(self.snapshot is not None)
            ui.button('取消', on_click=lambda:self.cancel() if self._epoch == epoch else None).props('flat no-caps data-testid=native-profile-cancel')
        if self.migration_factory is not None:
            with ui.column().classes('w-full gap-3 mt-6'):
                ui.label('停止履歴の引き継ぎ').classes('text-lg font-semibold')
                ui.label('以前の保存元を確認し、停止履歴を安全に引き継ぎます。通信・認証は行いません。').style('line-height:1.6')
                ui.button('正式移行を実行する', on_click=self.open_migration).props('outline data-testid=native-profile-migration')

    def open_migration(self):
        if self._closed or not self._active or self.migration_factory is None:return
        if self.migration_view is None:
            self.migration_view = self.migration_factory()
        self.migration_view.open()

    def _sync_values(self) -> None:
        if self.cookie_value is not None:
            self.cookie_value.set_text(str(self.draft_cookie) if self.draft_cookie else '未設定')
        if self.folder_value is not None:
            self.folder_value.set_text(str(self.draft_parent) if self.draft_parent else '未設定')
        if self.database_value is not None:
            self.database_value.set_text('旧DB: '+(str(self.draft_database) if self.draft_database else '未設定'))
        if self.images_value is not None:
            self.images_value.set_text('旧画像: '+(str(self.draft_images) if self.draft_images else '未設定'))
        if self.model_value is not None:
            self.model_value.set_text(str(self.draft_model) if self.draft_model else '解析モデル未設定')
        if self.model_button is not None:
            self.model_button.set_enabled(self.pick_model is not None and not self._picking and not self._busy and self._active)
        if self.auth_button is not None:
            self.auth_button.set_enabled(self.verify_auth is not None and self.draft_cookie is not None
                                         and not self._busy and not self._picking and self._active)
        if self.cookie_button is not None:
            self.cookie_button.set_enabled(self.pick_file is not None and not self._picking and not self._busy and self._active)
        if self.folder_button is not None:
            self.folder_button.set_enabled(self.pick_folder is not None and not self._picking and not self._busy and self._active)
        if self.database_button is not None:
            self.database_button.set_enabled(self.pick_database is not None and not self._picking and not self._busy and self._active)
        if self.images_button is not None:
            self.images_button.set_enabled(self.pick_images is not None and not self._picking and not self._busy and self._active)
        if self.save_button is not None:
            self.save_button.set_enabled(self.snapshot is not None and not self._picking and not self._busy and self._active)

    async def _choose(self, callback):
        if callback is None or self.snapshot is None or self._busy or self._picking or not self._active:
            return None
        epoch = self._epoch
        picker_revision = self._picker_revision
        self._picking = True
        self._sync_values()
        try:
            # Picker callback owns native OS interaction. No file reading here.
            result = callback()
            if inspect.isawaitable(result):
                result = await result
            if (epoch != self._epoch or picker_revision != self._picker_revision
                    or not self._active or result is None or result == ''):
                return None  # OS picker cancelled: keep the existing draft.
            if not isinstance(result, (str, Path)) or not Path(result).is_absolute():
                raise NativeProfileError('絶対パスを選択してください。')
            return Path(result)
        except Exception:
            if (epoch == self._epoch and picker_revision == self._picker_revision
                    and self._active):
                self.status.set_text('選択を完了できませんでした。')
            return None
        finally:
            if (epoch == self._epoch and picker_revision == self._picker_revision
                    and self._active):
                self._picking = False
                self._sync_values()

    async def choose_cookie(self) -> None:
        value = await self._choose(self.pick_file)
        if value is not None:
            self.draft_cookie = value
            self._auth_revision += 1
            self._sync_values()

    async def choose_folder(self) -> None:
        value = await self._choose(self.pick_folder)
        if value is not None:
            self.draft_parent = value
            self._sync_values()

    async def choose_database(self) -> None:
        value = await self._choose(self.pick_database)
        if value is not None:
            self.draft_database = value
            self._sync_values()

    async def choose_images(self) -> None:
        value = await self._choose(self.pick_images)
        if value is not None:
            self.draft_images = value
            self._sync_values()

    async def choose_model(self) -> None:
        value = await self._choose(self.pick_model)
        if value is not None:
            self.draft_model = value
            self._sync_values()

    def clear_model(self) -> None:
        if self.snapshot is not None and self._active:
            self.draft_model = None
            # モデルを外すと自動解析は動かない（設定の入切は残るが何もしない）。
            # 保存時に旧 profile の「自動」印も外す（save を参照）。
            self._sync_values()

    def clear_cookie(self) -> None:
        if self.snapshot is not None and self._active:
            self.draft_cookie = None
            self._auth_revision += 1
            self._busy = False
            self._sync_values()

    def clear_folder(self) -> None:
        if self.snapshot is not None and self._active:
            self.draft_parent = None
            self._sync_values()

    def clear_library(self) -> None:
        if self.snapshot is not None and self._active:
            self.draft_database = None
            self.draft_images = None
            self._sync_values()

    def save(self) -> bool:
        if self.snapshot is None or self._busy or self._picking or not self._active:
            return False
        try:
            snapshot = self.store.update(cookie_file=self.draft_cookie,
                save_parent=self.draft_parent, expected_revision=self.snapshot.revision,
                library_readonly_db=self.draft_database,
                library_image_root=self.draft_images,
                similarity_model_file=self.draft_model,
                similarity_provider=self.provider_select.value if self.provider_select else 'auto',
                # 自動実行の入切は設定の embed_auto_on_download が持つ。モデルを外すときだけ、
                # 旧 profile の「自動」印も外す（モデル無しの自動は profile として不正なため）。
                **({'similarity_auto': False} if self.draft_model is None else {}))
        except NativeProfileConflict:
            self.status.set_text('別の画面で設定が変わりました。取消して開き直してください。')
            return False
        except NativeProfileError as exc:
            self.status.set_text(str(exc))
            return False
        except Exception:
            self.status.set_text('保存できませんでした。旧設定は保持されています。')
            return False
        self.snapshot = snapshot
        self.status.set_text('保存しました。再起動後に反映されます。' + (
            '再起動後、借用プレミアムをONにして解除用パスワードを入力できます。'
            if snapshot.cookie_file is not None and snapshot.save_parent is not None else
            '利用するにはCookieと保存先の両方を設定してください。'))
        if self.on_saved is not None and snapshot.cookie_file is not None:
            try:
                self.on_saved()
            except Exception:
                pass  # The header check must never turn a saved setting into a failure.
        return True

    def cancel(self) -> None:
        if not self._active:
            return
        # Cancel touches neither the profile database nor the current runtime.
        # Inline controls must keep their mounted handlers' _epoch. Invalidate
        # only pending picker/auth work, so a delayed OS result cannot restore
        # a discarded draft or overwrite a new selection.
        self._picker_revision += 1
        self._auth_revision += 1
        self._busy = False
        self._picking = False
        self.draft_cookie = self.snapshot.cookie_file if self.snapshot else None
        self.draft_parent = self.snapshot.save_parent if self.snapshot else None
        self.draft_database = self.snapshot.library_readonly_db if self.snapshot else None
        self.draft_images = self.snapshot.library_image_root if self.snapshot else None
        self.draft_model = self.snapshot.similarity_model_file if self.snapshot else None
        if self.provider_select is not None:
            self.provider_select.set_value(self.snapshot.similarity_provider if self.snapshot else 'auto')
        self._sync_values()
        if self.status is not None:
            self.status.set_text('変更を破棄しました。')
        if self._standalone:
            # Standalone has no containing PreferencesView; closing invalidates
            # all old event handlers until the next explicit open()/build().
            self.deactivate()
            self.dialog.close()

    async def check_auth(self) -> bool:
        """Only the explicit button invokes the supplied bounded GET callback."""
        if (self.verify_auth is None or self.draft_cookie is None or
                self.snapshot is None or self._busy or not self._active):
            return False
        self._busy = True
        epoch = self._epoch
        revision = self._auth_revision
        chosen = self.draft_cookie
        if self.auth_button is not None:
            self.auth_button.set_enabled(False)
        self.status.set_text('認証を確認しています…')
        ok = False
        from ..native_auth_result import NativeAuthResult
        result = None
        try:
            if inspect.iscoroutinefunction(self.verify_auth):
                result = await asyncio.wait_for(self.verify_auth(chosen), timeout=10)
            else:
                result = await asyncio.wait_for(asyncio.to_thread(self.verify_auth, chosen), timeout=10)
            ok = result.ok if type(result) is NativeAuthResult else result is True
        except asyncio.TimeoutError:
            result = NativeAuthResult('communication_failed')
        except Exception:
            ok = False  # Transport exceptions/headers may contain credentials.
        finally:
            if (epoch == self._epoch and self._active and
                    revision == self._auth_revision and chosen == self.draft_cookie):
                self._busy = False
                self.status.set_text(result.message if type(result) is NativeAuthResult else
                    '認証を確認できました。' if ok else '認証を確認できませんでした。')
                self._sync_values()
        return ok

    def deactivate(self) -> None:
        """The outer PreferencesView has closed/rebuilt; discard late callbacks."""
        self._epoch += 1
        self._picker_revision += 1
        self._auth_revision += 1
        self._active = False
        self._busy = False
        self._picking = False

    def close(self) -> None:
        """Permanently detach this client's controls; the store stays owned by services."""
        if not self._closed:
            self._closed = True
            status=getattr(self,'_request_status',None)
            if status is not None:status.close()
            if self.migration_view is not None:self.migration_view.close()
            self.deactivate()
            self.dialog.close()
