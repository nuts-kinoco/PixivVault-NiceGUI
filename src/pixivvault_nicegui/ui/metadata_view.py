"""Explicit managed-only metadata settings and bounded one-work GET; no scheduler."""
from __future__ import annotations

import asyncio
import re

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
            ui.label('設定をONにしても自動通信はしません。明示実行時のみ保存済み1作品のPixiv詳細をGETします。')
            ui.label('取得中に新規ダウンロード受付が来た場合、実行中のGETが終わるまで受付が待つことがあります。停止は通信完了待ちです。')
            self.open_button = ui.button('専用メタデータ台帳を開く', on_click=self.open).props(
                'data-testid=metadata-open')
            self.enabled = ui.checkbox('後埋め設定をONにする（実行は別操作）').props(
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
            ui.label('保留・中断作品の確認（操作は指定した1作品のみ、通信・自動再試行なし）')
            self.work_id = ui.input(label='台帳の作品ID（数字）').props(
                'data-testid=metadata-work-id')
            with ui.row().classes('items-center'):
                self.inspect_button = ui.button('作品の台帳状態を確認', on_click=self.inspect).props(
                    'data-testid=metadata-inspect')
                self.ack_button = ui.button('以前の中断を確認済みにする', on_click=self.acknowledge).props(
                    'data-testid=metadata-ack-interrupted')
                self.resume_interrupted_button = ui.button('中断不明から待機へ戻す',
                    on_click=self.resume_interrupted).props('data-testid=metadata-resume-interrupted')
                self.resume_blocked_button = ui.button('保留から待機へ戻す',
                    on_click=self.resume_blocked).props('data-testid=metadata-resume-blocked')
            self.entry_status = ui.label('指定した作品の状態: 未確認').props(
                'data-testid=metadata-entry-status role=status')
            with ui.row().classes('items-center'):
                self.run_button = ui.button('指定した保存済み1作品を取得', on_click=self.run_one).props(
                    'data-testid=metadata-run-one')
                self.stop_button = ui.button('取得の停止を要求', on_click=self.stop_run).props(
                    'data-testid=metadata-stop')
            self.run_status = ui.label('取得処理: 未実行').props(
                'data-testid=metadata-run-status role=status')
            self.stop_button.set_enabled(False)
        self._set_controls(False)
        self._timer = ui.timer(5.0, self.refresh)

    def _set_controls(self, enabled: bool):
        for widget in (self.enabled, self.save_button, self.discard_button,
                       self.refresh_button, self.work_id, self.inspect_button,
                       self.ack_button, self.resume_interrupted_button,
                       self.resume_blocked_button, self.run_button):
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
        self.status.set_text(f'保存済み設定: {mode} / 自動取得なし、1作品GETは明示操作のみ')

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

    def _selected_work_id(self):
        value = (self.work_id.value or '').strip()
        if re.fullmatch(r'[1-9][0-9]{0,19}', value) is None:
            raise ValueError('invalid metadata work id')
        return value

    async def inspect(self):
        if self.closed or self._busy or self.revision is None:
            return
        self._busy = True
        try:
            wid = self._selected_work_id()
            entry = await asyncio.to_thread(self.services.managed_metadata_entry, wid)
            if not self.closed:
                if entry is None:
                    self.entry_status.set_text('台帳に指定作品の記録はありません')
                else:
                    self.entry_status.set_text(
                        f'台帳状態: {entry.state} / 試行回数: {entry.attempts} '
                        '（現在の保存世代と照合済みではありません）')
        except Exception:
            if not self.closed:
                self.entry_status.set_text('作品IDが無効か、台帳を読み取れませんでした')
        finally:
            self._busy = False

    async def _apply_recovery(self, action: str):
        if self.closed or self._busy or self.revision is None:
            return
        self._busy = True
        self._set_controls(False)
        try:
            if self._settings_dirty:
                raise ValueError('discard unsaved settings first')
            wid = self._selected_work_id()
            if action == 'ack':
                await asyncio.to_thread(
                    self.services.acknowledge_managed_metadata_interrupted, wid)
            elif action == 'interrupted':
                await asyncio.to_thread(
                    self.services.resume_managed_metadata_interrupted,
                    wid, expected_revision=self.revision)
            elif action == 'blocked':
                await asyncio.to_thread(
                    self.services.resume_managed_metadata_blocked,
                    wid, expected_revision=self.revision)
            else:
                raise ValueError('unsupported metadata recovery action')
            if self.closed:
                return
            settings = await asyncio.to_thread(self.services.managed_metadata_settings)
            if self.closed:
                return
            self._load(settings)
            counts = await asyncio.to_thread(self.services.managed_metadata_progress)
            if self.closed:
                return
            self._load_progress(counts)
            self.entry_status.set_text('指定作品の操作を記録しました。自動取得は開始しません。')
        except Exception:
            if not self.closed:
                self.entry_status.set_text(
                    '処理できませんでした。未保存の設定、古い改訂番号、現在の保存世代、'
                    'または中断の状態を確認してください')
        finally:
            self._busy = False
            if not self.closed:
                self._set_controls(True)

    async def acknowledge(self):
        await self._apply_recovery('ack')

    async def resume_interrupted(self):
        await self._apply_recovery('interrupted')

    async def resume_blocked(self):
        await self._apply_recovery('blocked')

    async def run_one(self):
        if self.closed or self._busy or self.revision is None:
            return
        self._busy = True
        self._set_controls(False)
        try:
            if self._settings_dirty or not self.enabled.value:
                raise ValueError('metadata settings need explicit save and enable')
            wid = self._selected_work_id()
            self.stop_button.set_enabled(True)
            self.run_status.set_text('明示取得の処理中（新規ダウンロードが優先されます）')
            summary = await asyncio.to_thread(
                self.services.run_managed_metadata_one, wid,
                expected_revision=self.revision)
            if self.closed:
                return
            # Stop reasons and counts contain no artwork titles, URLs, or credentials.
            self.run_status.set_text(
                f'取得結果: 完了 {summary.filled} / 対象 {summary.target} / '
                f'保留理由 {summary.stopped or "なし"}')
            counts = await asyncio.to_thread(self.services.managed_metadata_progress)
            if not self.closed:
                self._load_progress(counts)
        except Exception:
            if not self.closed:
                self.run_status.set_text('取得不可。設定、保存済み作品とキューの状態を確認してください')
        finally:
            self._busy = False
            if not self.closed:
                self.stop_button.set_enabled(False)
                self._set_controls(True)

    def stop_run(self):
        if self.closed:
            return
        # Does not close an in-flight socket or tamper with the durable receipt;
        # bounded GET is allowed to drain before service shutdown.
        self.services.stop_managed_metadata()
        self.run_status.set_text('停止を要求しました。通信中のGETは完了待ちです')

    def close(self):
        self.closed = True
        self._timer.cancel()
