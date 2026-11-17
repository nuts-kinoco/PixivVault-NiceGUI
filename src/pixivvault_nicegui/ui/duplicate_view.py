"""Duplicate review with explicit single/group-batch quarantine confirmation."""
from __future__ import annotations
import asyncio
import inspect
from uuid import uuid4
from nicegui import ui
from ..duplicate_inspection import (
    visible_groups, visible_undecidable_groups,
    low_quality_keys, validate_selection, best_item_index,
)
from .card_size_control import update_size_buttons

THUMB_SIZES = {'small':88, 'medium':144, 'large':200}

def format_trash_size(n):
    """Original gui.py:5599 `_format_size`: 95B / 1.5KB / 2.0MB (1024 based)."""
    for unit in ('B','KB','MB','GB'):
        if n<1024 or unit=='GB':
            return f'{n:.0f}{unit}' if unit=='B' else f'{n:.1f}{unit}'
        n/=1024
    return f'{n:.1f}TB'

class DuplicateView:
    def __init__(self,source,*,open_trash=None):
        self.source=source;self.active=False;self.closed=False;self.busy=False
        self.generation=0;self.selection_revision=0;self.scan_result=None;self.selections={};self.query=''
        self.thumb_size='medium';self.thumbnails=[]
        self.group_move_buttons={};self._confirmation=None;self._last_result=None
        self.group_low_buttons={}
        self._pending_quarantines=set()
        self._open_trash_callback=open_trash
        self._trash_epoch=0;self._trash_state='unloaded';self._trash_snapshot=None
        self._trash_reading=False;self._trash_opening=False;self._trash_emptying=False
        self._trash_moves=();self._trash_restore_buttons=[]
        self._restore_candidate=None
        self.mode='undecidable'  # Original gui.py:5022, 5445-5469.
        self.undecidable_groups=None
        self.undecidable_state='unloaded'
        self.undecidable_epoch=0
        self._undecidable_task=None
        self.client=ui.context.client
        with ui.column().classes('pv-metadata-view w-full').props('data-testid=duplicate-view') as self.root:
            ui.label('重複候補の確認').classes('pv-library-heading')
            with ui.row().classes('pv-toolbar'):
                self.scan_button=ui.button('スキャンして更新',on_click=self.scan).props('flat data-testid=duplicate-scan')
                self.search=ui.input(placeholder='作者名・IDで絞り込む',on_change=lambda e:self.filter(e.value)).props('outlined dense clearable debounce=200 data-testid=duplicate-search')
                self.mode_buttons={}
                for label,key in (('決められないもの','undecidable'),('近似重複の候補','near')):
                    self.mode_buttons[key]=ui.button(label,on_click=lambda _,value=key:self.set_mode(value)).props(
                        f'flat no-caps aria-pressed=false data-testid=duplicate-mode-{key}')
                self.trash_button=ui.button('ごみ箱',on_click=self.open_trash).props('flat data-testid=duplicate-trash')
            with ui.row().classes('pv-toolbar'):
                self.low_button=ui.button('画質の低い方を一括で選ぶ',on_click=self.select_low).props('flat data-testid=duplicate-bulk-low')
                ui.label('サムネイルサイズ').classes('pv-subtitle')
                self.size_buttons={}
                for label,size in (('小','small'),('中','medium'),('大','large')):
                    button=ui.button(label,on_click=lambda _,value=size:self.change_thumb_size(value)).props(
                        f'flat dense no-caps aria-label=サムネイル{label} data-testid=duplicate-size-{size}').classes('pv-size-button')
                    self.size_buttons[size]=button
                update_size_buttons(self.size_buttons,self.thumb_size)
            self.notice=ui.label('まだスキャンしていません。').props('role=status data-testid=duplicate-status')
            self.scan_progress=ui.label('').props('role=status data-testid=duplicate-progress')
            self.scan_bar=ui.linear_progress(value=0).props('data-testid=duplicate-progress-bar')
            self.skipped_notice=ui.label('').classes('whitespace-pre-line').props('role=alert data-testid=duplicate-skipped-authors')
            self.scan_progress.set_visibility(False);self.scan_bar.set_visibility(False);self.skipped_notice.set_visibility(False)
            self.recovery_notice=ui.label('').props('role=status data-testid=duplicate-recovery-status')
            self.legacy_notice=ui.label(
                '旧フォルダ・ZIPも走査します。確認後は画像を実移動し、元ZIPを更新します。'
                '操作前の退避をImagesの兄弟quarantine内に保持します。退避分の容量が必要です。'
                if getattr(source,'legacy',None) is not None else '').classes('pv-subtitle')
            with ui.column().classes('w-full').props('data-testid=duplicate-undecidable-panel') as self.undecidable_panel:
                ui.label('同じ作者の作品で、元のファイルをどの作品に対応付けるか決められないものです。').classes('pv-subtitle')
                self.undecidable_notice=ui.label('未判定の台帳を確認しています…').props(
                    'role=status data-testid=duplicate-undecidable-status')
                self.undecidable_refresh=ui.button('未判定の台帳を再読込',on_click=self.refresh_undecidable).props(
                    'flat data-testid=duplicate-undecidable-refresh')
                self.undecidable_rows=ui.column().classes('w-full').props('data-testid=duplicate-undecidable-rows')
                ui.label('作品への対応付け確定は、この読取画面からは実行できません。').classes('pv-subtitle')
            with ui.column().classes('w-full').props('data-testid=duplicate-near-panel') as self.near_panel:
                ui.label('近似は候補です。作者ごとに比較し、各組に1枚以上残します。').classes('pv-subtitle')
                self.rows=ui.column().classes('w-full')
                self.summary=ui.label('0枚選択中').props('data-testid=duplicate-selection')
                self.plan_button=ui.button('隔離計画を確認',on_click=self.plan).props('flat data-testid=duplicate-plan')
                self.move_button=ui.button('選んだものをごみ箱へ移す',on_click=lambda _:self.confirm()).props('flat data-testid=duplicate-move')
                self.result_rows=ui.column().classes('w-full').props('data-testid=duplicate-item-results')
                self.legacy_recover_button=ui.button('未完了の退避操作を復旧・再確認',on_click=self.recover_legacy).props('flat data-testid=duplicate-legacy-recover')
                self.legacy_recover_button.set_visibility(False)
                ui.label('隔離方法と退避対象を実行前に表示します。退避と保存証拠を保持するため、空き容量は増えません。').classes('pv-subtitle')
        with ui.dialog().props('persistent data-testid=duplicate-confirm-dialog') as self.confirm_dialog:
            with ui.card().classes('w-full').style('max-width:640px'):
                self.confirm_title=ui.label().classes('text-lg').props('data-testid=duplicate-confirm-title')
                self.confirm_impact=ui.label().props('data-testid=duplicate-confirm-impact')
                self.confirm_method=ui.label().classes('pv-subtitle')
                self.relocation_choice=ui.checkbox(
                    'この管理領域の元ページを隔離先へ物理移動する（元保存場所から除き、二重保存を避ける）',
                    value=False).props('data-testid=duplicate-physical-move')
                ui.label('物理移動後も隔離先と退避先にデータを保持します。ドライブ全体の空き容量が増えるとは限りません。').classes('pv-subtitle')
                self.confirm_rows=ui.column().classes('w-full').style('max-height:320px;overflow:auto')
                with ui.row().classes('w-full justify-end'):
                    ui.button('キャンセル',on_click=self.cancel_confirmation).props('flat data-testid=duplicate-confirm-cancel')
                    self.confirm_button=ui.button('ごみ箱へ隔離する',on_click=self.execute_confirmation).props('data-testid=duplicate-confirm-execute')
                self.relocation_choice.on_value_change(lambda _:self._sync_relocation_choice())
        with ui.dialog().props('data-testid=duplicate-trash-dialog') as self.trash_dialog:
            with ui.card().classes('w-full').style('max-width:640px'):
                self.trash_title=ui.label('ごみ箱').classes('text-lg').props('data-testid=duplicate-trash-title')
                self.trash_status=ui.label('ごみ箱の内容は未確認です。').props(
                    'role=status data-testid=duplicate-trash-status')
                self.trash_copy_count=ui.label('公開済みの隔離コピー: 未確認').props(
                    'data-testid=duplicate-trash-copy-count')
                self.trash_copy_bytes=ui.label('隔離コピーの容量: 未確認').props(
                    'data-testid=duplicate-trash-copy-bytes')
                self.trash_excluded=ui.label('通常の一覧から除外中: 未確認').props(
                    'data-testid=duplicate-trash-excluded')
                ui.label('件数と容量は公開済みの隔離コピーです。未完了の作業用コピーは含みません。'
                         '元の保存証拠と一覧の除外記録は保持されます。').classes('pv-subtitle')
                # Original gui.py:5640-5669: type the count to enable the empty button.
                ui.label('空にすると元に戻せません。件数を入力すると「空にする」が押せます。').classes('pv-subtitle')
                self.trash_confirm_count=ui.input(label='件数を入力',on_change=lambda _:self.sync()).props(
                    'outlined dense inputmode=numeric data-testid=duplicate-trash-confirm-count').style('width:140px')
                self.trash_restore_rows=ui.column().classes('w-full').props(
                    'data-testid=duplicate-trash-move-recovery')
                with ui.row().classes('w-full justify-end'):
                    self.trash_refresh_button=ui.button('再読込',on_click=self.refresh_trash).props(
                        'flat data-testid=duplicate-trash-refresh')
                    self.trash_open_button=ui.button('保存先を開く',on_click=self.open_trash_location).props(
                        'flat data-testid=duplicate-trash-open')
                    self.trash_open_button.tooltip('このアプリ専用のごみ箱フォルダをOSで開きます。'
                        if self._open_trash_callback is not None else
                        'この起動ではOSで開く操作を利用できません。')
                    ui.button('閉じる',on_click=self.close_trash).props(
                        'flat data-testid=duplicate-trash-close')
                    self.trash_empty_button=ui.button('空にする',on_click=self.empty_trash,color='amber').props(
                        'text-color=black data-testid=duplicate-trash-empty')
        with ui.dialog().props('persistent data-testid=duplicate-restore-dialog') as self.restore_dialog:
            with ui.card().classes('w-full').style('max-width:560px'):
                self.restore_title=ui.label().props('data-testid=duplicate-restore-title')
                ui.label('管理領域の検証済み隔離データから元ページを復元し、通常一覧の除外を解除します。'
                         '復元先が変更されている場合は上書きせず停止します。').classes('pv-subtitle')
                with ui.row().classes('w-full justify-end'):
                    ui.button('キャンセル',on_click=self.cancel_restore).props(
                        'flat data-testid=duplicate-restore-cancel')
                    self.restore_button=ui.button('復元を確認して実行',on_click=self.execute_restore).props(
                        'data-testid=duplicate-restore-execute')
        self.client.on_delete(self.close)
        self._progress_timer=ui.timer(.2,self.refresh_scan_progress)
        self.render_undecidable()
        self.sync()
    def refresh_scan_progress(self):
        if self.closed:return
        self.scan_progress.set_visibility(self.busy);self.scan_bar.set_visibility(self.busy)
        if not self.busy:return
        read=getattr(self.source,'progress_snapshot',None)
        value=read() if callable(read) else dict(phase='スキャンしています',done=0,total=0,author_id='')
        total=value['total'];done=value['done'];author=value.get('author_id','')
        text=value['phase']+(f' · 作者ID {author}' if author else '')+(f' · {done}/{total}' if total else '')
        self.scan_progress.set_text(text)
        self.scan_bar.set_value(min(1,done/total) if total else 0)
        self.scan_bar.props('indeterminate' if not total else 'indeterminate=false')
    def sync(self):
        legacy=getattr(self.source,'legacy',None)
        notice=getattr(legacy,'recovery_notice',None)
        self.recovery_notice.set_text(notice() if callable(notice) else '')
        enabled=self.active and not self.closed and not self.busy
        self.scan_button.set_enabled(enabled)
        self.trash_button.set_enabled(enabled and not self._trash_reading
            and callable(getattr(self.source,'quarantine_snapshot',None)))
        self.trash_refresh_button.set_enabled(enabled and self.trash_dialog.value
            and not self._trash_reading and not self._trash_opening)
        self.trash_open_button.set_enabled(enabled and self.trash_dialog.value
            and not self._trash_reading and not self._trash_opening
            and self._trash_state in ('ready','uncreated') and self._trash_snapshot is not None
            and (self._trash_state=='uncreated' or self._trash_snapshot.location is not None)
            and self._open_trash_callback is not None)
        self.low_button.set_enabled(enabled and self.mode=='near' and self.scan_result is not None)
        for button in self.size_buttons.values():button.set_enabled(enabled)
        self.plan_button.set_enabled(enabled and self.mode=='near' and any(self.selections.values()))
        self.move_button.set_enabled(enabled and self.mode=='near' and any(self.selections.values()))
        for gid,button in self.group_move_buttons.items():
            button.set_enabled(enabled and self.mode=='near' and bool(self.selections.get(gid)))
        for button in self.group_low_buttons.values():button.set_enabled(enabled and self.mode=='near')
        self.confirm_button.set_enabled(enabled and self._confirmation is not None)
        self.restore_button.set_enabled(enabled and self._restore_candidate is not None)
        can_empty=(enabled and self.trash_dialog.value and not self._trash_reading
            and not self._trash_opening and self._trash_state=='ready'
            and callable(getattr(self.source,'empty_trash',None))
            and getattr(self._trash_snapshot,'purgeable_count',0)>0)
        self.trash_confirm_count.set_enabled(bool(can_empty))
        self.trash_empty_button.set_enabled(bool(can_empty and self._trash_count_confirmed()))
        for button in self._trash_restore_buttons:
            button.set_enabled(enabled and self.trash_dialog.value and not self._trash_reading)
        self.undecidable_refresh.set_enabled(enabled and self._has_undecidable_source()
            and self.undecidable_state!='loading')
        for name,button in self.mode_buttons.items():
            count=(len(self.undecidable_groups) if self.undecidable_state=='ready'
                   else None) if name=='undecidable' else (
                       len(self.scan_result.groups) if self.scan_result is not None else None)
            text='決められないもの' if name=='undecidable' else '近似重複の候補'
            button.set_text(f'{text}（{count if count is not None else "–"}）')
            button.props(f'aria-pressed={str(name==self.mode).lower()}')
            button.classes(add='pv-size-selected' if name==self.mode else 'pv-size-button',
                           remove='pv-size-button' if name==self.mode else 'pv-size-selected')
            button.set_enabled(enabled and (name!='near' or self.scan_result is not None))
        self.undecidable_panel.set_visibility(self.mode=='undecidable')
        self.near_panel.set_visibility(self.mode=='near')

    def _trash_current(self,epoch,generation):
        return (not self.closed and self.active and self.trash_dialog.value
            and epoch==self._trash_epoch and generation==self.generation)

    def _set_trash_link(self,snapshot):
        """Entry link like the original (gui.py:5612): ごみ箱 N件・容量."""
        text='ごみ箱'
        if snapshot is not None and getattr(snapshot,'state',None)=='ready' and hasattr(snapshot,'purgeable_count'):
            text=f'ごみ箱 {snapshot.purgeable_count}件・{format_trash_size(snapshot.purgeable_bytes)}'
        self.trash_button.set_text(text)

    async def refresh_trash_summary(self):
        reader=getattr(self.source,'quarantine_snapshot',None)
        if self.closed or not callable(reader):return
        try:snapshot=await asyncio.to_thread(reader)
        except Exception:return
        if not self.closed:self._set_trash_link(snapshot)

    def _schedule_trash_summary(self):
        task=getattr(self,'_summary_task',None)
        if self.closed or not self.active or (task is not None and not task.done()):return
        if not callable(getattr(self.source,'quarantine_snapshot',None)):return
        try:self._summary_task=asyncio.get_running_loop().create_task(
            self.refresh_trash_summary(),name='duplicate trash summary read')
        except RuntimeError:pass

    def _trash_count_confirmed(self):
        count=getattr(self._trash_snapshot,'purgeable_count',0)
        try:return count>0 and int(self.trash_confirm_count.value)==count
        except (TypeError,ValueError):return False

    def _render_trash(self):
        snapshot=self._trash_snapshot
        if snapshot is not None and hasattr(snapshot,'purgeable_count'):
            self.trash_title.set_text(f'ごみ箱 {snapshot.purgeable_count}件・{format_trash_size(snapshot.purgeable_bytes)}')
        else:
            self.trash_title.set_text('ごみ箱')
        if snapshot is not None:self._set_trash_link(snapshot)
        if snapshot is None:
            self.trash_copy_count.set_text('公開済みの隔離コピー: 未確認')
            self.trash_copy_bytes.set_text('隔離コピーの容量: 未確認')
            self.trash_excluded.set_text('通常の一覧から除外中: 未確認')
        else:
            self.trash_copy_count.set_text(f'公開済みの隔離コピー: {snapshot.copy_count}件')
            self.trash_copy_bytes.set_text(f'隔離コピーの容量: {format_trash_size(snapshot.copy_bytes)}')
            if (getattr(snapshot,'corrupt_count',0) or getattr(snapshot,'missing_count',0)
                    or getattr(snapshot,'unconfirmed_count',0)):
                self.trash_copy_count.set_text(f'検証できた健全な隔離コピー: {snapshot.copy_count}件')
                self.trash_copy_bytes.set_text(f'健全な隔離コピーの実測容量: {format_trash_size(snapshot.copy_bytes)}')
            self.trash_excluded.set_text(f'通常の一覧から除外中: {snapshot.excluded_pages}ページ')
        messages={
            'unloaded':'ごみ箱の内容は未確認です。',
            'loading':'ごみ箱を確認しています…',
            'uncreated':'ごみ箱はまだ作成されていません。初めて隔離すると作成されます。保存先を開くと親フォルダを表示します。',
            'error':'ごみ箱の内容を読み取れませんでした。再読込してください。',
        }
        self.trash_status.set_text(messages.get(self._trash_state,
            'ごみ箱に隔離コピーはありません。' if snapshot is not None
            and snapshot.copy_count==0 else 'ごみ箱の内容を確認しました。'))
        if (snapshot is not None and snapshot.copy_count==0
                and (getattr(snapshot,'missing_count',0) or getattr(snapshot,'corrupt_count',0)
                     or getattr(snapshot,'unconfirmed_count',0))):
            self.trash_status.set_text('健全な隔離コピーはありません。')
        if snapshot is not None and getattr(snapshot,'missing_count',0):
            self.trash_status.set_text(self.trash_status.text
                + f' 実体が見つからない隔離コピー: {snapshot.missing_count}件（除外状態は維持しています）。')
        if snapshot is not None and getattr(snapshot,'corrupt_count',0):
            self.trash_status.set_text(self.trash_status.text
                + f' サイズまたは内容が一致しない隔離コピー: {snapshot.corrupt_count}件（件数・容量には含めず、除外状態は維持しています）。')
        if snapshot is not None and getattr(snapshot,'unconfirmed_count',0):
            self.trash_status.set_text(self.trash_status.text
                + f'（確認できない項目: {snapshot.unconfirmed_count}件）')
        if (snapshot is not None and hasattr(snapshot,'purgeable_count')
                and snapshot.purgeable_count!=snapshot.copy_count):
            # The title and the typed count are what "empty" deletes (like the original,
            # every copy still present); the healthy-copy lines below count fewer.
            self.trash_status.set_text(self.trash_status.text
                + f' ごみ箱の件数（{snapshot.purgeable_count}件）には、壊れた隔離コピーや復元済みの控えも含み、空にするとすべて削除されます。')
        self.trash_restore_rows.clear()
        self._trash_restore_buttons=[]
        if self._trash_state=='ready' and self._trash_moves:
            with self.trash_restore_rows:
                ui.label('管理領域の物理移動を復元').classes('pv-subtitle')
                for record in self._trash_moves:
                    operation_id,state,count,revision=record
                    with ui.row():
                        ui.label(f'{count}ページ · {state} · 操作 {operation_id[:8]}')
                        button=ui.button('復元を確認',on_click=lambda _,v=record:self.confirm_restore(v)).props(
                            'flat data-testid=duplicate-restore-choice')
                        self._trash_restore_buttons.append(button)

    async def open_trash(self):
        if self.closed or not self.active or self.busy or self._trash_reading:return
        self.trash_dialog.open()
        await self.refresh_trash()

    def close_trash(self):
        self._trash_epoch+=1
        if not self.closed:self.trash_confirm_count.set_value('')
        self.trash_dialog.close()
        self.cancel_restore()
        self._trash_snapshot=None
        self._trash_moves=()
        self._trash_state='unloaded'
        if not self.closed:self.sync()

    async def refresh_trash(self):
        if (self.closed or not self.active or self.busy or self._trash_reading
                or not self.trash_dialog.value):return
        self._trash_epoch+=1
        epoch,generation=self._trash_epoch,self.generation
        self._trash_reading=True
        self._trash_snapshot=None
        self._trash_moves=()
        self._trash_state='loading'
        self.trash_confirm_count.set_value('')
        self._render_trash();self.sync()
        try:
            reader=getattr(self.source,'quarantine_snapshot',None)
            if not callable(reader):raise RuntimeError('quarantine overview unavailable')
            snapshot=await asyncio.to_thread(reader)
            recoverable=getattr(self.source,'recoverable_moves',None)
            moves=await asyncio.to_thread(recoverable) if callable(recoverable) else ()
            if self._trash_current(epoch,generation):
                self._trash_snapshot=snapshot
                self._trash_moves=moves
                self._trash_state=snapshot.state
                self._render_trash()
        except Exception:
            if self._trash_current(epoch,generation):
                self._trash_snapshot=None
                self._trash_moves=()
                self._trash_state='error'
                self._render_trash()
        finally:
            self._trash_reading=False
            if not self.closed:self.sync()

    async def empty_trash(self):
        """Original gui.py:5640-5669: permanent deletion after the typed count matched."""
        emptier=getattr(self.source,'empty_trash',None)
        if (self.closed or not self.active or self.busy or self._trash_reading or not self.trash_dialog.value
                or self._trash_state!='ready' or not callable(emptier)
                or not self._trash_count_confirmed()):return
        count=self._trash_snapshot.purgeable_count
        self.busy=True;self.sync()
        result=None
        try:
            result=await asyncio.to_thread(lambda:emptier(expected_count=count,confirmed=True))
        except Exception:
            result=None
        finally:
            self.busy=False
        if self.closed:return
        self.close_trash()
        if result is None:
            message='ごみ箱を空にできませんでした。内容が変わった可能性があります。ごみ箱を開き直して件数を確認してください。'
            ui.notify(message,type='warning')
        else:
            self.scan_result=getattr(self.source,'latest_scan',None);self.selections={};self.render()
            if not result.failed:
                message=f'ごみ箱: {result.removed}件を完全に削除しました（除外の記録は残ります）'
            elif result.removed:
                # Partial progress is real and permanent: say both numbers.
                message=(f'ごみ箱: {result.removed}件を空にしました。残り{result.failed}件は空にできませんでした'
                         '（除外の記録は残ります。もう一度「空にする」で続きを実行できます）')
            else:
                message=(f'ごみ箱を空にできませんでした。{result.failed}件が残っています。'
                         'ごみ箱を開き直してもう一度お試しください。')
            ui.notify(message,type='warning' if result.failed else 'positive')
        self._schedule_trash_summary()
        self.notice.set_text(message)
        self.sync()

    def confirm_restore(self,record):
        if (self.closed or not self.active or self.busy or not self.trash_dialog.value
                or self._trash_reading or record not in self._trash_moves):return
        self._restore_candidate=record
        self.restore_title.set_text(f'{record[2]}ページを管理領域のごみ箱から復元しますか？')
        self.restore_dialog.open()
        self.sync()

    def cancel_restore(self):
        self._restore_candidate=None
        self.restore_dialog.close()
        if not self.closed:self.sync()

    async def execute_restore(self):
        record=self._restore_candidate
        if (record is None or self.closed or not self.active or self.busy
                or record not in self._trash_moves):return
        self._restore_candidate=None
        self.restore_dialog.close()
        self.busy=True;self.sync()
        completed=False;refresh_warning=False
        try:
            operation_id,_,_,revision=record
            restorer=getattr(self.source,'restore_move',None)
            if not callable(restorer):raise RuntimeError('managed restore unavailable')
            await asyncio.to_thread(restorer,operation_id,expected_revision=revision)
            completed=True
            callback=getattr(self.source,'on_changed',None)
            if callable(callback):
                try:
                    value=callback()
                    if inspect.isawaitable(value):await value
                except Exception:
                    refresh_warning=True
            if not self.closed:
                self.scan_result=None;self.selections={};self.render()
                self._schedule_trash_summary()
                self.notice.set_text('管理領域の復元が完了しました。再スキャンしてください。'
                                     +(' ライブラリ表示も再読込してください。' if refresh_warning else ''))
        except Exception:
            if not completed and not self.closed:
                self.notice.set_text('復元を確定できませんでした。ごみ箱を再読込して状態を確認してください。')
        finally:
            self.busy=False
            if not self.closed:
                self.sync()
                if self.trash_dialog.value:await self.refresh_trash()

    async def open_trash_location(self):
        if (self.closed or not self.active or self.busy or self._trash_reading
                or self._trash_opening or not self.trash_dialog.value
                or self._trash_state not in ('ready','uncreated') or self._trash_snapshot is None
                or (self._trash_state=='ready' and self._trash_snapshot.location is None) or self._open_trash_callback is None):return
        epoch,generation=self._trash_epoch,self.generation
        self._trash_opening=True;self.sync()
        try:
            await self._open_trash_callback(parent_if_uncreated=True) if self._trash_state=='uncreated' else await self._open_trash_callback()
        except Exception:
            if self._trash_current(epoch,generation):
                self.trash_status.set_text('ごみ箱の保存先を開けませんでした。再読込して確認してください。')
        finally:
            self._trash_opening=False
            if not self.closed:self.sync()

    def _has_undecidable_source(self):
        reader=getattr(self.source,'undecidable_groups',None)
        return callable(reader) and bool(getattr(self.source,'undecidable_available',True))

    def set_mode(self,mode):
        if mode not in ('undecidable','near'):
            raise ValueError('invalid duplicate review mode')
        if self.closed or not self.active or self.busy or (
                mode=='near' and self.scan_result is None) or mode==self.mode:return
        self.mode=mode
        self.sync()  # Original switches mounted sections without rebuilding cards.

    async def refresh_undecidable(self):
        if self.closed or not self.active or self.undecidable_state=='loading':return
        if not self._has_undecidable_source():
            self.undecidable_state='unavailable';self.undecidable_groups=None
            self.render_undecidable();self.sync();return
        self.undecidable_epoch+=1;epoch=self.undecidable_epoch
        self.undecidable_state='loading';self.render_undecidable();self.sync()
        try:
            groups=await asyncio.to_thread(self.source.undecidable_groups)
            if self.closed or not self.active or epoch!=self.undecidable_epoch:return
            if groups is None:
                self.undecidable_groups=None;self.undecidable_state='unavailable'
            else:
                self.undecidable_groups=tuple(groups);self.undecidable_state='ready'
        except Exception:
            if self.closed or not self.active or epoch!=self.undecidable_epoch:return
            self.undecidable_groups=None;self.undecidable_state='error'
        finally:
            if not self.closed and self.active and epoch==self.undecidable_epoch:
                self.render_undecidable();self.sync()

    def render_undecidable(self):
        self.undecidable_rows.clear()
        if self.undecidable_state=='unavailable':
            self.undecidable_notice.set_text(
                '旧file_ledgerは未接続です。管理保存先の作品IDから未判定グループを推測しません。')
            return
        if self.undecidable_state=='error':
            self.undecidable_notice.set_text('未判定の台帳を読み込めませんでした。再読込できます。')
            return
        if self.undecidable_state in ('unloaded','loading'):
            self.undecidable_notice.set_text('未判定の台帳を読み込んでいます…')
            return
        groups=visible_undecidable_groups(self.undecidable_groups,self.query,limit=None)
        shown=groups[:50]
        self.undecidable_notice.set_text(
            f'{len(shown)} / {len(groups)} 組を表示（台帳全体 {len(self.undecidable_groups)} 組）')
        with self.undecidable_rows:
            if not groups:
                if self.query:
                    ui.label('一致する候補はありません').props('data-testid=duplicate-undecidable-empty')
                else:
                    ui.label('決められないものはありません').props('data-testid=duplicate-undecidable-empty')
                    ui.label('手で判断が要る重複はありません').classes('pv-subtitle')
            for group in shown:
                candidates=group['candidates']
                sample=candidates[0] if candidates else {'author_name':'','title':''}
                with ui.card().classes('w-full').props('data-testid=duplicate-undecidable-group'):
                    ui.label(f"{sample.get('author_name') or ''} / {sample.get('title') or ''}")
                    ui.label('候補: '+' / '.join(str(c['work_id']) for c in candidates)).classes('pv-subtitle')
                    for candidate in candidates:
                        ui.label(f"{candidate.get('title') or ''}（{candidate['work_id']}）")
            if len(groups)>len(shown):
                ui.label(f'先頭{len(shown)}件のみ表示・残り{len(groups)-len(shown)}件').classes('pv-subtitle')
    async def scan(self):
        if self.closed or not self.active or self.busy:return
        self.busy=True;self.generation+=1;generation=self.generation;self.sync();self.notice.set_text('スキャンしています…')
        self.skipped_notice.set_text('');self.skipped_notice.set_visibility(False)
        self.refresh_scan_progress()
        try:
            result=await asyncio.to_thread(self.source.scan)
            if self.closed or not self.active or generation!=self.generation:return
            self.scan_result=result;self.selections={}
            self.skipped_notice.set_text('\n'.join(f'上限超過: 作者ID {aid} · {count}枚（上限2000枚）'
                                                  for aid,count in result.skipped_authors))
            self.skipped_notice.set_visibility(bool(result.skipped_authors))
            self.notice.set_text(f'{len(result.groups)}組・{result.scanned_files}枚を確認／読取失敗{len(result.unreadable)}枚／上限超過{len(result.skipped_authors)}作者')
            self.render()
            # The two sections have distinct sources. A managed scan cannot
            # synthesize undecidable file_ledger rows; refresh only if supplied.
            if self._has_undecidable_source():
                await self.refresh_undecidable()
        except Exception:
            if not self.closed and self.active and generation==self.generation:
                self.scan_result=None;self.selections={};self.render();self.notice.set_text('保存済み画像の整合性を確認できません。再試行してください。')
        finally:self.busy=False;self.refresh_scan_progress();self.sync()
    def filter(self,value):
        self.query=str(value or '')
        self.render()
        self.render_undecidable()
    def render(self):
        self.selection_revision+=1;revision=self.selection_revision
        groups=visible_groups(self.scan_result,self.query) if self.scan_result is not None else ()
        self.selections={g.group_id:self.selections.get(g.group_id,set()) for g in groups}
        self.rows.clear();self.thumbnails=[];self.group_move_buttons={};self.group_low_buttons={}
        with self.rows:
            if not groups:
                if self.scan_result is None:
                    ui.label('近似重複はまだスキャンしていません。').props('data-testid=duplicate-near-empty')
                elif self.query and self.scan_result.groups:
                    ui.label('一致する候補はありません').props('data-testid=duplicate-near-empty')
                else:
                    ui.label('近似重複の候補はありません').props('data-testid=duplicate-near-empty')
            for group in groups:
                with ui.card().classes('w-full').props('data-testid=duplicate-near-group') as card:
                    card.props['data-author-id'] = str(group.author_id)
                    ui.label(f'{group.author_name or group.author_id} · {len(group.items)}枚')
                    ui.label('距離 '+ '/'.join(str(d) for _,_,d in group.edges)).classes('pv-subtitle')
                    with ui.row().classes('w-full'):
                        for item in group.items[:5]:
                            with ui.column():
                                if item.thumb_b64:thumb=ui.image('data:image/jpeg;base64,'+item.thumb_b64).style('object-fit:contain')
                                else:thumb=ui.label('サムネイルを表示できません')
                                pixels=THUMB_SIZES[self.thumb_size]
                                thumb.style(f'width:{pixels}px;height:{pixels}px').props('data-testid=duplicate-thumbnail')
                                self.thumbnails.append(thumb)
                                ui.label(item.filename or f'作品 {item.work_id}・{item.page}ページ')
                                ui.label(f'{item.width}×{item.height} · {item.file_size} bytes')
                                ui.label('この組で最大' if group.items.index(item)==best_item_index(group.items) else '低画質候補').classes('pv-subtitle')
                                cb=ui.checkbox('隔離候補',value=item.key in self.selections[group.group_id]).props(
                                    f'data-testid=duplicate-select-{group.author_id}-{item.work_id}-{item.page}')
                                cb.on_value_change(lambda e,gid=group.group_id,key=item.key,revision=revision:self.toggle(gid,key,e.value,revision))
                        if len(group.items)>5:ui.label(f'+{len(group.items)-5}枚（非表示の画像は選択しません）')
                    self.group_move_buttons[group.group_id]=ui.button(
                        'この組の選択画像をごみ箱へ移す',
                        on_click=lambda _,gid=group.group_id,rev=revision:self.confirm(gid,rev),
                    ).props('flat data-testid=duplicate-group-move')
                    self.group_low_buttons[group.group_id]=ui.button('この組の画質の低い方を選ぶ',
                        on_click=lambda _,gid=group.group_id,rev=revision:self.select_group_low(gid,rev)).props('flat data-testid=duplicate-group-low')
            total=len(visible_groups(self.scan_result,self.query,limit=None)) if self.scan_result is not None else 0
            if total>len(groups):ui.label(f'先頭{len(groups)}件のみ表示・残り{total-len(groups)}件')
        self.update_summary()
    def change_thumb_size(self,size):
        if size not in THUMB_SIZES:raise ValueError('invalid thumbnail size')
        if self.closed or not self.active or self.busy or size==self.thumb_size:return
        self.thumb_size=size
        pixels=THUMB_SIZES[size]
        for thumb in self.thumbnails:thumb.style(f'width:{pixels}px;height:{pixels}px')
        update_size_buttons(self.size_buttons,size)
    def update_summary(self):
        selected={key for keys in self.selections.values() for key in keys}
        items=tuple(p for g in self.scan_result.groups for p in g.items if p.key in selected) if self.scan_result else ()
        groups=sum(bool(keys) for keys in self.selections.values())
        self.summary.set_text(f'{groups}組・{len(items)}枚選択中・{sum(p.file_size for p in items)} bytes');self.sync()
    async def recover_legacy(self):
        if self.closed or not self.active or self.busy:return
        callback=getattr(self.source,'recover_legacy',None)
        if not callable(callback):return
        generation=self.generation;self.busy=True;self.sync()
        try:
            result=await asyncio.to_thread(callback)
            if self.closed or not self.active or generation!=self.generation:return
            self.scan_result=result;self.render()
            self.legacy_recover_button.set_visibility(False)
            self.notice.set_text('退避操作の保存状態を確認しました。残る項目を確認してから操作してください。新しい移動は実行していません。')
        except Exception:
            if not self.closed:self.notice.set_text('復旧を確認できません。退避を保持しています。保存先の状態を確認してください。')
        finally:self.busy=False;self.sync()
    def toggle(self,gid,key,value,revision):
        if self.closed or not self.active or self.busy or revision!=self.selection_revision or self.scan_result is None:return
        changed={g:set(v) for g,v in self.selections.items()};chosen=changed.setdefault(gid,set())
        chosen.add(key) if value else chosen.discard(key)
        try:validate_selection(self.scan_result,changed,self.query)
        except ValueError:self.notice.set_text('全選択を戻しました。各組に1枚以上残します。');self.render();return
        self.selections=changed;self.update_summary()
    def select_low(self):
        if self.closed or not self.active or self.busy or self.scan_result is None:return
        self.selections={g.group_id:set(low_quality_keys(g)) for g in visible_groups(self.scan_result,self.query)};self.render()
    def select_group_low(self,gid,revision):
        if self.closed or not self.active or self.busy or revision!=self.selection_revision or self.scan_result is None:return
        group=next((g for g in visible_groups(self.scan_result,self.query) if g.group_id==gid),None)
        if group is None:return
        self.selections[gid]=set(low_quality_keys(group));self.render()
    async def plan(self):
        if self.closed or not self.active or self.busy or self.scan_result is None:return
        self.busy=True;generation=self.generation;revision=self.selection_revision;self.sync()
        try:
            plan=await asyncio.to_thread(self.source.plan,self.scan_result,{g:set(v) for g,v in self.selections.items()},self.query)
            if self.closed or not self.active or generation!=self.generation or revision!=self.selection_revision:return
            self.summary.set_text(f'{len(plan.remaining)}組・{len(plan.items)}枚・{plan.bytes_count} bytes の隔離候補。ファイルは移動していません。')
        except Exception:
            if not self.closed and self.active and generation==self.generation:self.notice.set_text('選択または保存結果が変わりました。再スキャンしてください。')
        finally:self.busy=False;self.sync()

    def _selected(self,group_id=None):
        return {gid:frozenset(keys) for gid,keys in self.selections.items()
                if keys and (group_id is None or gid==group_id)}

    def cancel_confirmation(self):
        self._confirmation=None
        self.confirm_dialog.close()
        self.relocation_choice.set_value(False)
        if not self.closed:self.sync()
    def _sync_relocation_choice(self):
        if self.relocation_choice.value is True:
            self.confirm_button.set_text('確認済みの元ページを物理移動する')
        else:
            self.confirm_button.set_text('管理領域へ隔離コピーを保存する')

    async def confirm(self,group_id=None,expected_revision=None):
        if (self.closed or not self.active or self.busy or self.mode!='near'
                or self.scan_result is None
                or expected_revision is not None and expected_revision!=self.selection_revision):return
        selected=self._selected(group_id)
        if not selected:return
        generation=self.generation;revision=self.selection_revision;scan=self.scan_result
        self.busy=True;self.sync()
        try:
            plan=await asyncio.to_thread(self.source.plan,scan,selected,self.query)
            if (self.closed or not self.active or generation!=self.generation
                    or revision!=self.selection_revision or self._selected(group_id)!=selected):return
            if not plan.mutation_allowed:
                self.notice.set_text(plan.blocked_reason)
                return
            self._confirmation=(plan,generation,revision,uuid4().hex,group_id,selected)
            legacy=all(item.source=='legacy' for item in plan.items)
            self.confirm_method.set_text(
                '元フォルダから実移動し、元ZIPから対象画像を除いて更新します。操作前のデータは退避先に保持します。'
                if legacy else '管理領域では隔離コピーと一覧除外を行います。物理移動を選んだ場合は元ページの保存場所から移します。')
            self.relocation_choice.set_text('旧フォルダの実移動・元ZIPの更新を行う'
                if legacy else 'この管理領域の元ページを隔離先へ物理移動する')
            self.relocation_choice.set_value(legacy)
            self._sync_relocation_choice()
            self.relocation_choice.set_enabled(all(item.source=='managed' for item in plan.items))
            self.confirm_title.set_text(f'{len(plan.items)}枚をごみ箱へ隔離しますか？（{len(plan.remaining)}組）')
            self.confirm_impact.set_text(
                f'対象 {plan.bytes_count} bytes。元データの退避対象 {plan.backup_bytes} bytes。'
                '旧ZIPは対象画像だけを除いて更新し、フォルダ画像は実移動します。'
                '退避・再構築・検証コピー分の追加容量が必要です。失敗項目は元へ戻し、未処理候補を残します。'
                if legacy else f'対象 {plan.bytes_count} bytes。選択した管理画像だけに適用します。')
            self.confirm_rows.clear()
            chosen={item.key for item in plan.items}
            with self.confirm_rows:
                for group in plan.groups:
                    with ui.column().classes('w-full'):
                        ui.label(group.author_name or f'作者ID {group.author_id}').classes('text-weight-medium')
                        for item in group.items:
                            if item.key in chosen:
                                ui.label(f'{item.filename or item.work_id} · 元の{item.page}ページ · {item.file_size} bytes')
                                if item.store_name:ui.label(f'対象の保存ファイル: {item.store_name}')
            self.confirm_dialog.open()
        except Exception:
            if not self.closed and self.active and generation==self.generation:
                self.notice.set_text('選択や保存世代が変わりました。再スキャンして確認してください。')
        finally:
            self.busy=False
            if not self.closed:self.sync()

    async def _execute_accepted(self,plan,operation_id,physical_move=False):
        if physical_move:
            result=await asyncio.to_thread(self.source.execute,plan,operation_id=operation_id,
                                           mode='move',confirmed_move=True)
        else:
            result=await asyncio.to_thread(self.source.execute,plan,operation_id=operation_id)
        self._last_result=result
        refresh_warning=False
        if result.committed:
            callback=getattr(self.source,'on_changed',None)
            if callable(callback):
                try:
                    update=callback()
                    if inspect.isawaitable(update):await update
                except Exception:
                    refresh_warning=True
        return result,refresh_warning

    async def execute_confirmation(self):
        pending=self._confirmation
        if pending is None or self.closed or not self.active or self.busy:return
        physical_move=self.relocation_choice.value is True
        plan,generation,revision,operation_id,group_id,selected=pending
        if (self.mode!='near' or self.scan_result is None
                or generation!=self.generation or revision!=self.selection_revision
                or self.scan_result.scan_id!=plan.scan_id or self._selected(group_id)!=selected):
            self.cancel_confirmation()
            self.notice.set_text('選択が変わりました。確認を開き直してください。')
            return
        self._confirmation=None;self.confirm_dialog.close();self.busy=True;self.sync()
        result=None;cancelled=False
        accepted=asyncio.create_task(self._execute_accepted(plan,operation_id,physical_move),
                                     name='accepted managed quarantine')
        self._pending_quarantines.add(accepted)
        accepted.add_done_callback(self._pending_quarantines.discard)
        try:
            while True:
                try:
                    result,refresh_warning=await asyncio.shield(accepted)
                    break
                except asyncio.CancelledError:
                    if accepted.cancelled():raise
                    cancelled=True
                    # Another cancel may arrive while draining. Keep every
                    # await shielded, including the post-commit refresh.
                    continue
            if not cancelled and not self.closed and self.active and generation==self.generation:
                self.scan_result=self.source.latest_scan
                remaining={p.key for p in (*result.failed,*getattr(result,'unprocessed',()))}
                self.selections={g.group_id:{p.key for p in g.items if p.key in remaining} for g in self.scan_result.groups}
                self.render();self.result_rows.clear()
                self.legacy_recover_button.set_visibility(getattr(result,'recovery_required',False))
                with self.result_rows:
                    for item in getattr(result,'item_results',()):
                        label={'success':'成功','failed':'失敗','unprocessed':'未処理'}[item.status]
                        ui.label(f'{item.filename or item.key}: {label} {item.reason}')
                message=f'{len(result.committed)}枚を隔離しました・失敗{len(result.failed)}枚。'
                message+=('元ZIPを更新し、画像を移動しました。操作前の退避を保持しています。未処理候補は一覧に残しています。'
                          if any(item.source=='legacy' for item in result.committed) else
                          '元ページを隔離先へ物理移動しました。' if physical_move and result.committed
                          else '隔離コピーを保存して一覧から除外しました。' if result.committed
                          else '完了した項目はありません。失敗・未処理の項目を確認してください。')
                if getattr(result,'unprocessed',()):message+=f' 未処理{len(result.unprocessed)}枚。復旧状態を確認してください。'
                if getattr(result,'recovery_required',False):message+=' 保存状態の再確認が必要です。復旧ボタンから確認してください。'
                if result.cleanup_warning:message+=' 一時領域の後始末を確認してください。'
                if refresh_warning:message+=' ライブラリ表示を再読込してください。'
                self.notice.set_text(message)
                self._schedule_trash_summary()
                ui.notify(message,type='warning' if result.failed or getattr(result,'unprocessed',())
                          or result.cleanup_warning or refresh_warning else 'positive')
            if cancelled:raise asyncio.CancelledError()
        except asyncio.CancelledError:
            raise
        except Exception:
            if cancelled:raise asyncio.CancelledError()
            if not self.closed and self.active and generation==self.generation:
                if result is not None and result.committed:
                    self.notice.set_text(f'{len(result.committed)}枚の隔離は完了しています。表示を再読込してください。')
                else:
                    self.notice.set_text('隔離対象の状態を確認できません。再スキャンして確認してください。')
        finally:
            self.busy=False
            if not self.closed:self.sync()
    def set_active(self,active):
        next_active=bool(active) and not self.closed
        if self.active!=next_active:
            self.generation+=1
            self.undecidable_epoch+=1
            if not next_active and self.undecidable_state=='loading':
                self.undecidable_state='unloaded'
        self.active=next_active
        if not self.active:
            self.cancel_confirmation()
            self.close_trash()
        if self.active:
            latest=getattr(self.source,'latest_scan',None)
            if latest is not None and latest is not self.scan_result:
                self.scan_result=latest;self.selections={};self.render()
        if self.active and self.undecidable_state=='unloaded':
            if self._has_undecidable_source():
                self._undecidable_task=asyncio.create_task(
                    self.refresh_undecidable(),name='duplicate undecidable ledger read')
            else:
                self.undecidable_state='unavailable'
            self.render_undecidable()
        if self.active:self._schedule_trash_summary()
        self.sync()
    def close(self):
        self._progress_timer.deactivate()
        self.closed=True;self.active=False;self.generation+=1;self.undecidable_epoch+=1
        # Accepted tasks retain their own source/result and remain tracked until
        # copying and refresh finish. Deleting the client must not cancel them.
        self._confirmation=None;self.confirm_dialog.close();self.close_trash();self.sync()
