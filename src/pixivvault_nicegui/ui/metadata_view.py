"""Explicit managed-only metadata settings; no GET or background runner yet."""
from __future__ import annotations

import asyncio

from nicegui import ui


class MetadataView:
    """Per-client CAS form backed by an application-owned managed side-ledger.

    Merely opening this tab never initializes storage, reads credentials or
    contacts Pixiv. Provisioning requires the dedicated button. Enabling a
    setting is *not* an implicit authorization to start an unconnected worker.
    """

    def __init__(self, services):
        self.services = services
        self.closed = False
        self._busy = False
        self.revision = None
        self._settings_dirty = False
        self._loading_settings = False
        with ui.column().classes('w-full gap-3').props('data-testid=metadata-view') as self.root:
            ui.label('メタデータ後埋め').classes('text-h6')
            ui.label('専用管理フォルダーにのみ設定を保存します。元DBや既存画像は変更しません。')
            ui.label('取得・自動実行はまだ接続していません。設定をONにしても通信は始まりません。')
            self.open_button = ui.button('専用メタデータ台帳を開く', on_click=self.open).props(
                'data-testid=metadata-open')
            self.enabled = ui.checkbox('後埋め設定をONにする（取得は未接続）').props(
                'data-testid=metadata-enabled')
            self.enabled.on_value_change(lambda _: self._mark_settings_dirty())
            with ui.row().classes('items-center'):
                self.save_button = ui.button('設定を保存', on_click=self.save).props(
                    'data-testid=metadata-save')
                self.discard_button = ui.button('編集を破棄して再読込', on_click=self.discard_changes).props(
                    'data-testid=metadata-discard')
                self.refresh_button = ui.button('状態更新', on_click=self.refresh).props(
                    'data-testid=metadata-refresh')
            self.status = ui.label('専用メタデータ台帳は未起動です').props(
                'data-testid=metadata-status role=status')
            self.progress_label = ui.label('台帳の作品状態: 未読込').props(
                'data-testid=metadata-progress role=status')
        self._set_controls(False)
        self._timer = ui.timer(5.0, self.refresh)

    def _set_controls(self, enabled: bool):
        for widget in (self.enabled, self.save_button, self.discard_button,
                       self.refresh_button):
            widget.set_enabled(enabled)

    def _mark_settings_dirty(self):
        if not self._loading_settings:
            self._settings_dirty = True

    def _load(self, settings):
        if not self._settings_dirty:
            # While a local form is dirty, keep its CAS revision pinned.
            self.revision = settings.revision
            self._loading_settings = True
            try:
                self.enabled.set_value(settings.enabled)
            finally:
                self._loading_settings = False
        mode = 'ON' if settings.enabled else 'OFF'
        self.status.set_text(f'保存済み設定: {mode} / 取得機能は未接続（通信なし）')

    def _load_progress(self, counts):
        if counts is None:
            return
        self.progress_label.set_text(
            f'台帳登録 {counts.total}件 / 待機 {counts.pending} / 処理中の記録 {counts.in_flight} / '
            f'完了 {counts.done} / 保留 {counts.blocked} / 中断不明 {counts.unknown} / '
            f'打切り {counts.gave_up}（現在世代との照合件数ではありません）')

    async def open(self):
        if self.closed or self._busy:
            return
        self._busy = True
        try:
            ledger = await asyncio.to_thread(self.services.enable_managed_metadata)
            if self.closed:
                return
            settings = await asyncio.to_thread(ledger.settings)
            if self.closed:
                return
            self.open_button.set_enabled(False)
            self._set_controls(True)
            self._load(settings)
            counts = await asyncio.to_thread(self.services.managed_metadata_progress)
            if not self.closed:
                self._load_progress(counts)
        except Exception:
            if not self.closed:
                self.status.set_text('専用メタデータ台帳を開けませんでした')
        finally:
            self._busy = False

    async def refresh(self):
        if self.closed or self._busy or self.revision is None:
            return
        self._busy = True
        try:
            settings = await asyncio.to_thread(self.services.managed_metadata_settings)
            if not self.closed and settings is not None:
                self._load(settings)
                counts = await asyncio.to_thread(self.services.managed_metadata_progress)
                if not self.closed:
                    self._load_progress(counts)
        except Exception:
            if not self.closed:
                self.status.set_text('後埋め設定の状態を取得できませんでした')
        finally:
            self._busy = False

    async def save(self):
        if self.closed or self._busy or self.revision is None:
            return
        self._busy = True
        self._set_controls(False)
        try:
            settings = await asyncio.to_thread(
                self.services.configure_managed_metadata,
                enabled=bool(self.enabled.value), expected_revision=self.revision)
            if self.closed:
                return
            self._settings_dirty = False
            self._load(settings)
        except Exception:
            if not self.closed:
                self.status.set_text('設定が変更されたか保存に失敗しました。再読込で確認してください')
        finally:
            self._busy = False
            if not self.closed:
                self._set_controls(True)

    async def discard_changes(self):
        if self.closed or self._busy or self.revision is None:
            return
        self._busy = True
        self._settings_dirty = False
        try:
            settings = await asyncio.to_thread(self.services.managed_metadata_settings)
            if not self.closed and settings is not None:
                self._load(settings)
        except Exception:
            if not self.closed:
                self._settings_dirty = True
                self.status.set_text('設定を再読込できませんでした')
        finally:
            self._busy = False

    def close(self):
        self.closed = True
        self._timer.cancel()
