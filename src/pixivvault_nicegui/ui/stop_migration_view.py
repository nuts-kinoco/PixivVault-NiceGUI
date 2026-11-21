"""Explicit, offline three-step STOP migration flow."""
from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path
from threading import Event
from nicegui import ui
from .action_controls import danger_button, dialog_actions, dialog_card, outlined_input, outlined_select, primary_button, secondary_button
from ..managed_stop_migration import MigrationRejected, MigrationResult

KINDS = {'native':'起動設定', 'images':'画像の保存元', 'novel':'小説の保存元', 'ugoira':'うごいらの保存元'}
REASONS = {
    'source_unverified':'所有情報や停止履歴を確認できません。正しい保存元を指定してください。履歴を作り直さないでください。',
    'source_changed':'確認後に保存元が変わりました。ほかの処理を終了し、内容の確認からやり直してください。',
    'source_busy':'保存元が使用中です。ほかのアプリや取得処理を終了してから、内容を確認してください。',
    'copy_mismatch':'コピーの照合が一致しません。元データと移行記録を保持し、保存先を確認してください。',
    'target_exists':'共通の所有情報が既にあります。上書きせず、下の「中断した移行を確認」で実態を確認してください。',
    'recovery_required':'中断した移行があります。保存元を保持し、下の「中断した移行を確認」へ進んでください。',
    'history_unverified':'過去の移行記録と所有情報が一致しません。ファイルを消さず、移行記録を添えて確認を依頼してください。',
    'recovery_conflict':'中断した移行を元に戻せません。ファイルを消さず、使用中のアプリを終了してから、移行記録の確認を依頼してください。',
    'publication_unverified':'切替後の読み返しを確認できません。成功とは扱いません。データを保持し、中断した移行を確認してください。',
    'io_failed':'ファイルを扱えません。空き容量とアクセス権を確認してください。元データと移行記録は保持しています。',
    'cancelled':'切替前に中止しました。元データは保持しています。',
    'preview_required':'保存元の内容をもう一度確認してください。',
    'confirmation_required':'全保存元を列挙したことを確認してから開始してください。',
    'atomic_publish_unavailable':'安全な切替を利用できません。元データを保持したまま確認を依頼してください。',
}


class StopMigrationView:
    def __init__(self, service, *, pick_folder=None):
        self.service, self.pick_folder = service, pick_folder
        self.additional = []
        self.plan = None
        self.busy = False
        self.closed = False
        self.cancel_event = Event()
        self.events = deque()
        self.epoch = 0
        with ui.dialog().props('persistent data-testid=stop-migration-dialog') as self.dialog:
            with dialog_card('停止履歴を引き継ぐ', size='lg',
                             subtitle='以前の保存元を確認し、停止履歴を共通の所有情報へ引き継ぎます。通信や認証は行いません。'):
                with ui.column().classes('pv-migration-section'):
                    self.step = ui.label('1. 以前使った保存元をすべて追加').classes('pv-section-heading pv-serif-heading')
                    ui.label('現在の保存元は自動で含まれます。以前使った起動設定や、画像・小説・うごいらの保存元を追加してください。').classes('pv-subtitle')
                    with ui.element('div').classes('pv-settings-fields'):
                        self.kind = outlined_select(KINDS, value='images', label='保存元の種類').props('data-testid=migration-kind')
                        self.path = outlined_input('保存元フォルダの絶対パス').props('data-testid=migration-path')
                    with ui.row().classes('pv-toolbar pv-compact-toolbar'):
                        self.pick_button = secondary_button('フォルダを選ぶ', self.choose, testid='migration-pick')
                        self.pick_button.set_enabled(pick_folder is not None)
                        self.add_button = secondary_button('一覧へ追加', self.add, testid='migration-add', outline=True)
                        self.preview_button = primary_button('内容を確認', self.preview, testid='migration-preview')
                    self.sources = ui.column().classes('w-full gap-1 pv-migration-scroll pv-compact-status')
                with ui.column().classes('pv-migration-section'):
                    ui.label('2. 内容を確認して開始').classes('pv-section-heading pv-serif-heading')
                    self.details = ui.label('まだ確認していません。').classes(
                        'w-full whitespace-pre-wrap break-all pv-migration-scroll').props('data-testid=migration-details')
                    ui.label('停止履歴・所有情報と起動設定を引き継ぎます。元のVault・鍵・Cookieは保持され、記録済みの停止も維持されます。').classes('pv-subtitle')
                    self.confirm = ui.checkbox(
                        '以前使ったすべての保存元を上の一覧に含め、対象と内容を確認しました。',
                        value=False, on_change=self.sync).props('dense data-testid=migration-confirm')
                    with dialog_actions():
                        self.run_button = danger_button('確認した内容で正式移行を実行', self.execute, testid='migration-execute')
                with ui.column().classes('pv-migration-section'):
                    ui.label('3. 移行結果').classes('pv-section-heading pv-serif-heading')
                    self.status = ui.label('移行はまだ始まっていません。').props('role=status aria-live=polite data-testid=migration-status')
                    self.bar = ui.linear_progress(value=0).classes('w-full')
                    self.results = ui.column().classes('w-full gap-1 pv-migration-scroll pv-compact-status')
                with dialog_actions():
                    self.recover_button = secondary_button('中断した移行を確認', self.recover, testid='migration-recover')
                    self.cancel_button = secondary_button('中止する', self.cancel, testid='migration-cancel', outline=True)
                    self.close_button = secondary_button('閉じる', self.dialog.close, testid='migration-close')
                self.timer = ui.timer(0.15, self.drain)
        ui.context.client.on_delete(self.close)
        self.sync()

    def open(self):
        if not self.closed:
            self.dialog.open()

    def sync(self, *_):
        if self.closed: return
        for control in (self.kind,self.path,self.add_button,self.preview_button,self.recover_button,self.confirm,self.close_button):
            control.set_enabled(not self.busy)
        self.pick_button.set_enabled(not self.busy and self.pick_folder is not None)
        self.run_button.set_enabled(not self.busy and self.plan is not None and self.confirm.value is True)
        self.cancel_button.set_enabled(self.busy and not self.cancel_event.is_set())

    def invalidate(self):
        self.plan = None
        self.confirm.set_value(False)
        self.details.set_text('保存元を変更しました。「内容を確認」を押してください。')
        self.sync()

    async def choose(self):
        if self.busy or self.closed or self.pick_folder is None: return
        value = await self.pick_folder()
        if value and not self.busy and not self.closed: self.path.set_value(str(value))

    def add(self):
        if self.busy or self.closed: return
        value = Path((self.path.value or '').strip())
        if not value.is_absolute():
            self.status.set_text('保存元フォルダの絶対パスを指定してください。');return
        item = (self.kind.value, value)
        if item not in self.additional:
            self.additional.append(item)
            self.invalidate()
            self.render_sources()

    def remove(self, item):
        if self.busy or self.closed: return
        self.additional.remove(item);self.invalidate();self.render_sources()

    def render_sources(self):
        self.sources.clear()
        with self.sources:
            for item in self.additional:
                with ui.row().classes('w-full items-center gap-3'):
                    ui.label(KINDS[item[0]]+' · '+str(item[1])).classes('grow break-all')
                    secondary_button('取り除く', lambda _,x=item:self.remove(x))

    async def preview(self):
        if self.busy or self.closed:return
        self.invalidate(); self.busy = True; self.sync()
        epoch = self.epoch
        try:
            plan = await asyncio.to_thread(self.service.preview, tuple(self.additional))
            if self.closed or epoch != self.epoch:return
            self.plan = plan
            lines = []
            for s in plan.sources:
                state = f'停止履歴 HTTP {s.state[1]}（停止を維持）' if s.state[0] else '読取時点でSTOPなし'
                if s.absent:state = '未作成（設定上の新しい保存先）'
                lines.append(f'{KINDS[s.kind]} · {s.root}\n{len(s.files)}ファイル / {sum(f[2] for f in s.files):,} bytes · {state}')
            self.details.set_text('\n\n'.join(lines))
            self.status.set_text('全保存元が含まれていることを確認してください。この確認だけでは通信は再開しません。')
        except Exception as e:
            if not self.closed:
                self.status.set_text(REASONS.get(getattr(e,'reason','source_unverified'),REASONS['source_unverified']))
        finally:
            self.busy = False;self.sync()

    def cancel(self):
        if self.busy:
            self.cancel_event.set()
            self.status.set_text('中止を受け付けました。元の状態を確認しています。切替済みの場合は結果を表示します。')
            self.sync()

    def drain(self):
        if self.closed:return
        names = {'locking':'保存元をロックしています','copying':'停止履歴をコピーしています',
                 'verifying':'コピーと停止状態を照合しています','publishing':'確認済みの所有情報へ切り替えています',
                 'completed':'移行記録を読み返しました'}
        while self.events:
            phase,n,total=self.events.popleft()
            self.status.set_text(names.get(phase,'確認しています'))
            self.bar.set_value(n/max(total,1))

    async def execute(self):
        if self.busy or self.closed or self.plan is None or self.confirm.value is not True:return
        plan,self.plan = self.plan,None
        self.cancel_event = Event();self.busy = True;self.sync()
        try:
            result = await asyncio.to_thread(self.service.execute,plan,confirmed=True,cancel=self.cancel_event,
                                            progress=lambda *args:self.events.append(args))
            if not self.closed:self.show_result(result)
        finally:
            self.busy = False;self.sync()

    async def recover(self):
        if self.busy or self.closed:return
        self.plan = None;self.confirm.set_value(False);self.busy = True;self.sync()
        try:
            result = await asyncio.to_thread(self.service.recover_pending)
            if not self.closed:self.show_result(result)
        finally:
            self.busy = False;self.sync()

    def show_result(self, result):
        self.events.clear()
        if result.status == 'completed':
            message = '移行が完了し、ディスク上の記録とコピーを確認しました。アプリを終了し、起動し直してください。'
            message += ' 停止履歴があるため、通信停止は維持されます。' if result.stopped else ' 起動時の検証が通るまで通信は再開しません。'
            self.bar.set_value(1)
        elif result.status == 'recovered':
            message = '中断した切替を確認し、移行前の状態へ戻しました。元データは保持しています。「内容を確認」からやり直せます。'
        else:
            message = REASONS.get(result.reason,REASONS['io_failed'])
            if result.status == 'uncertain': message = '結果を確定できません。' + message
        self.status.set_text(message)
        self.results.clear()
        with self.results:
            if result.operation_id:ui.label('移行記録: '+result.operation_id).classes('text-sm break-all')
            for item in result.items:
                label={'success':'成功','failed':'失敗','unprocessed':'未処理'}.get(item.status,'未確認')
                ui.label(f'{label} · {KINDS[item.kind]} · {item.root}').classes('font-semibold break-all')
                if item.reason:ui.label(REASONS.get(item.reason,REASONS['io_failed']))

    def close(self):
        self.cancel_event.set()
        self.closed = True
        self.epoch += 1
        self.timer.deactivate()
        self.dialog.close()
