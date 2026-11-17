"""メタデータ後埋めの画面（元 gui.py:1700-1814 の「作品メタデータ」相当）。

自動実行の入切は設定画面の「作品メタデータ」（meta_backfill_enabled、既定 ON）にある。
この画面は「いま埋める」「取れなかったものをもう一度」と状態行、台帳の保留・中断の
確認を持つ。通信はすべて services 側の取得停止ゲート・ダウンロード優先を通る。
"""
from __future__ import annotations

import asyncio
import re

from nicegui import ui


class MetadataView:
    """Per-client view backed by an application-owned managed side-ledger.

    Merely opening this tab never initializes storage, reads credentials or
    contacts Pixiv. 「いま埋める」は明示操作、自動実行は設定の入切に従う。
    """

    STOP_TEXTS = {
        'download_busy': 'ダウンロード中のため、いまは走れません。',
        'network_stopped': '取得停止中のため、通信しませんでした。',
        'global_failure': '通信の問題があったため止めました。しばらくしてからもう一度お試しください。',
        'authentication_unverified': 'ログインを確認できなかったため止めました。',
        'authentication_or_state_unavailable': 'ログインまたは状態を確認できなかったため止めました。',
        'five_consecutive_unknown_failures': '原因不明の失敗が続いたため止めました。',
        'cancelled': '中止しました。',
        'already_running': '別の取得が進行中です。',
        'managed_state_changed': '保存状態が変わったため止めました。もう一度お試しください。',
        'unavailable': 'いまは開始できません。',
    }

    def __init__(self, services):
        self.services = services
        self.closed = False
        self._busy = False
        self._status_busy = False
        self.revision = None
        with ui.column().classes('pv-metadata-view w-full gap-3').props('data-testid=metadata-view') as self.root:
            ui.label('メタデータ後埋め').classes('text-h6')
            ui.label('専用管理フォルダーにのみ設定を保存します。元DBや既存画像は変更しません。')
            ui.label('ブックマーク数・いいね数・閲覧数などは、作品ごとに詳細を1回ずつ取りに行かないと分かりません。'
                     '自動で埋めるかどうかは、設定の「作品メタデータ」で切り替えます（既定はON）。')
            ui.label('ダウンロード中・取得停止中は動かず、終わるまで待ちます。停止は通信完了待ちです。')
            self.meta_status = ui.label('メタデータ: 状態を確認しています…').props(
                'data-testid=metadata-status-line role=status')
            with ui.row().classes('items-center'):
                self.run_all_button = ui.button('いま埋める', on_click=self.run_all).props(
                    'data-testid=metadata-run-all')
                self.retry_button = ui.button('取れなかったものをもう一度試す', on_click=self.retry_gave_up).props(
                    'data-testid=metadata-retry')
                self.retry_button.set_visibility(False)
                self.stop_button = ui.button('取得の停止を要求', on_click=self.stop_run).props(
                    'data-testid=metadata-stop')
            self.run_status = ui.label('取得処理: 未実行').props(
                'data-testid=metadata-run-status role=status')
            self.action_notice = ui.label('').props(
                'data-testid=metadata-action-notice role=status')
            with ui.expansion('台帳の詳細・保留と中断の確認', icon='fact_check').classes('w-full'):
                self.open_button = ui.button('専用メタデータ台帳を開く', on_click=self.open).props(
                    'data-testid=metadata-open')
                with ui.row().classes('items-center'):
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
                self.run_button = ui.button('指定した保存済み1作品を取得', on_click=self.run_one).props(
                    'data-testid=metadata-run-one')
            self.stop_button.set_enabled(False)
        self._set_controls(False)
        self._timer = ui.timer(5.0, self._tick)

    async def _tick(self):
        await self.refresh_status()
        await self.refresh()

    def _set_controls(self, enabled: bool):
        for widget in (self.refresh_button, self.work_id, self.inspect_button,
                       self.ack_button, self.resume_interrupted_button,
                       self.resume_blocked_button, self.run_button):
            widget.set_enabled(enabled)

    async def refresh_status(self, extra: str = ''):
        """「N / M 件（残り…）」。局所の台帳と保存済み一覧だけを読む（通信しない）。"""
        if self.closed or self._status_busy:
            return
        self._status_busy = True
        try:
            status = await asyncio.to_thread(self.services.managed_metadata_status)
            if self.closed:
                return
            self.meta_status.set_text(status.text + (f'  {extra}' if extra else ''))
            self.retry_button.set_visibility(bool(status.gave_up) and not self._busy)
        except Exception:
            if not self.closed:
                self.meta_status.set_text('メタデータ: 状態を読めませんでした')
        finally:
            self._status_busy = False

    def _summary_text(self, summary) -> str:
        if summary.stopped:
            return self.STOP_TEXTS.get(summary.stopped, f'止まりました（{summary.stopped}）。')
        if not summary.target:
            return '埋める対象はありません。'
        return (f'完了しました（取得 {summary.filled} 件 / 対象 {summary.target} 件'
                + (f' / 値なし {summary.no_value}' if summary.no_value else '')
                + (f' / 失敗 {summary.work_error}' if summary.work_error else '') + '）')

    async def run_all(self):
        if self.closed:
            return
        if self._busy:
            self._busy_feedback(target='run')
            return
        self._busy = True
        self.action_notice.set_text('')
        self.run_all_button.set_enabled(False)
        self.retry_button.set_visibility(False)
        self.stop_button.set_enabled(True)
        self.run_status.set_text('メタデータを埋めています…（ダウンロード中・取得停止中は動きません）')
        tail = ''
        try:
            summary = await asyncio.to_thread(self.services.run_managed_metadata_all)
            tail = self._summary_text(summary)
        except Exception:
            tail = '取得不可。保存先と通信の状態を確認してください。'
        finally:
            self._busy = False
            if not self.closed:
                self.run_all_button.set_enabled(True)
                self.stop_button.set_enabled(False)
                self.run_status.set_text('取得結果: ' + tail)
        await self.refresh_status()

    async def retry_gave_up(self):
        if self.closed:
            return
        if self._busy:
            self._busy_feedback(target='run')
            return
        self._busy = True
        try:
            count = await asyncio.to_thread(self.services.reset_managed_metadata_gave_up)
            if not self.closed:
                self.run_status.set_text(f'{count} 件を対象へ戻しました。')
        except Exception:
            if not self.closed:
                self.run_status.set_text('対象へ戻せませんでした。')
        finally:
            self._busy = False
        await self.refresh_status()

    def _busy_feedback(self, *, target: str = 'status'):
        """Never silently discard a click during a periodic refresh/GET."""
        message = '処理中です。完了後にもう一度操作してください'
        # A separate notice survives the periodic settings/progress refresh.
        self.action_notice.set_text(message)
        if target == 'entry':
            self.entry_status.set_text(message)
        elif target == 'run':
            self.run_status.set_text(message)
        else:
            self.status.set_text(message)

    def _load(self, settings):
        # 台帳の enabled は内部の門。自動実行の入切は設定画面が持つ。
        self.revision = settings.revision
        self.status.set_text('専用メタデータ台帳: 開いています')

    def _load_progress(self, counts):
        if counts is None:
            return
        self.progress_label.set_text(
            f'台帳登録 {counts.total}件 / 待機 {counts.pending} / 処理中の記録 {counts.in_flight} / '
            f'完了 {counts.done} / 保留 {counts.blocked} / 中断不明 {counts.unknown} / '
            f'打切り {counts.gave_up}（現在世代との照合件数ではありません）')

    async def open(self):
        if self.closed:
            return
        if self._busy:
            self._busy_feedback()
            return
        self._busy = True
        self.action_notice.set_text('')
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

    def _selected_work_id(self):
        value = (self.work_id.value or '').strip()
        if re.fullmatch(r'[1-9][0-9]{0,19}', value) is None:
            raise ValueError('invalid metadata work id')
        return value

    async def inspect(self):
        if self.closed or self.revision is None:
            return
        if self._busy:
            self._busy_feedback(target='entry')
            return
        self._busy = True
        self.action_notice.set_text('')
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
        if self.closed or self.revision is None:
            return
        if self._busy:
            self._busy_feedback(target='entry')
            return
        self._busy = True
        self.action_notice.set_text('')
        self._set_controls(False)
        try:
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
            self.entry_status.set_text('指定作品の操作を記録しました。この操作自体は通信しません。')
        except Exception:
            if not self.closed:
                self.entry_status.set_text(
                    '処理できませんでした。古い改訂番号、現在の保存世代、'
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
        if self.closed or self.revision is None:
            return
        if self._busy:
            self._busy_feedback(target='run')
            return
        self._busy = True
        self.action_notice.set_text('')
        self._set_controls(False)
        try:
            wid = self._selected_work_id()
            self.stop_button.set_enabled(True)
            self.run_all_button.set_enabled(False)
            self.run_status.set_text('明示取得の処理中（新規ダウンロード受付はGET完了まで待つことがあります）')
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
                self.run_all_button.set_enabled(True)
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
