"""U17 original download detail: stable, accessible, per-client expansion.

The old Flet tab's shared log panel was permanently hidden. This view uses
the *actual* local job snapshot and current selection instead of inventing
old log messages; no network, HTTP, destructive action or worker start.
"""
from __future__ import annotations

from collections import Counter

from nicegui import ui


STATE_LABELS = {
    'queued': '待機中', 'running': '実行中', 'paused': '一時停止',
    'stopping': '停止中', 'done': '完了', 'error': 'エラー', 'stopped': '停止',
}
MIN_DETAIL_HEIGHT = 540


class DownloadStatusDetails:
    """One owner/client. A tab roundtrip never recreates or hides the panel."""

    def __init__(self, owner, *, managed: bool):
        self.owner = owner
        self.managed = bool(managed)
        self.opened = False
        with ui.column().classes('w-full gap-1').props('data-testid=download-details') as self.root:
            with ui.row().classes('w-full items-center gap-2 rounded px-2 py-1').props(
                    'data-testid=download-detail-bar aria-label=ダウンロード詳細バー') as self.bar:
                self.state_label = ui.label('待機中').classes('pv-subtitle').props(
                    'data-testid=download-detail-summary')
                ui.label('管理保存・差分確認' if managed else '合成データ・専用保存').classes('pv-subtitle')
                self.button = ui.button('詳細を開く', icon='expand_more').props(
                    'flat dense no-caps data-testid=download-detail-toggle aria-expanded=false')
            self.notice = ui.label('').props('role=status data-testid=download-detail-notice')
            with ui.column().classes('w-full gap-1 rounded px-3 py-2').style(
                    'max-height: 210px; overflow-y: auto; border: 1px solid var(--q-separator-color, #8b8b8b)') as self.panel:
                self.selection_label = ui.label().props('data-testid=download-detail-selection')
                self.queue_label = ui.label().props('data-testid=download-detail-queue')
                self.destination_label = ui.label(
                    '保存先：専用管理フォルダー（元の画像・SQLiteは変更しません）' if managed else
                    '保存先：合成試験の専用フォルダー（本物のPixiv作品ではありません）').props(
                    'data-testid=download-detail-destination')
                ui.label('個別ジョブの進捗と操作は「キュー」タブで確認できます。').classes('pv-subtitle')
                self.refresh_button = ui.button('状態を更新', icon='refresh',
                    on_click=self.refresh).props('flat dense data-testid=download-detail-refresh')
            self.panel.set_visibility(False)
        self.root.props['data-expanded'] = 'false'
        self.button.props(f'aria-controls={self.panel.html_id}')
        payload = (f"{{epoch:document.getElementById('{self.owner.root.html_id}').dataset.epoch,"
                   "viewportHeight:window.innerHeight}")
        self.button.on('click', self.handle_event, js_handler=f'() => emit({payload})')
        self.bar.on('dblclick', self.handle_event,
                    js_handler=("event => { if (event.target?.closest('button')) return; "
                                f'emit({payload}); }}'))
        self.refresh()

    def handle_event(self, event):
        args = getattr(event, 'args', None)
        if not isinstance(args, dict) or type(args.get('epoch')) is not str:
            return False
        if args['epoch'] != str(self.owner.epoch) or type(args.get('viewportHeight')) is not int:
            return False
        height = args['viewportHeight']
        if not 100 <= height <= 32768:
            return False
        return self.toggle(viewport_height=height, epoch=args['epoch'])

    def toggle(self, *, viewport_height: int, epoch=None) -> bool:
        if not self.owner._valid(epoch):
            return False
        if (not self.opened and
                (type(viewport_height) is not int or viewport_height < MIN_DETAIL_HEIGHT)):
            self.notice.set_text('ウィンドウの高さが足りないため詳細を開けません。縦に広げて再度お試しください。')
            return False
        self.opened = not self.opened
        self.root.props['data-expanded'] = str(self.opened).lower()
        self.panel.set_visibility(self.opened)
        self.button.props['aria-expanded'] = str(self.opened).lower()
        self.button.text = '詳細を閉じる' if self.opened else '詳細を開く'
        self.button.icon = 'expand_less' if self.opened else 'expand_more'
        self.notice.set_text('')
        if self.opened:
            self.refresh()
        return True

    def refresh(self):
        if self.owner.closed:
            return
        selected = getattr(self.owner, 'selected', ())
        count = len(selected)
        author_targets = len(getattr(self.owner, 'author_targets', ())) if self.managed else 0
        self.selection_label.set_text(
            f'選択中：作品 {count}件' + (f'・作者 {author_targets}件' if self.managed else ''))
        jobs = getattr(self.owner.services, 'jobs', None)
        snapshot = getattr(jobs, 'snapshot', None) if jobs is not None else None
        if not callable(snapshot):
            self.state_label.set_text('待機中：0件')
            self.queue_label.set_text('ジョブ：0件（キューは未接続です）')
            return
        try:
            rows = tuple(snapshot())
            counts = Counter(getattr(row, 'state', None) for row in rows)
            summary = '・'.join(f'{STATE_LABELS[state]} {counts[state]}件'
                               for state in STATE_LABELS if counts[state])
            if len(counts) == 0:
                summary = '現在のキューは0件です'
            self.queue_label.set_text('キュー：' + summary)
            active = sum(counts[s] for s in ('queued','running','paused','stopping'))
            self.state_label.set_text(f'進行中 {active}件・選択中 {count}件')
        except Exception:
            self.state_label.set_text('キュー確認待ち')
            self.queue_label.set_text('現在のキュー状態を読み取れません。キュー画面で確認してください。')

    def set_active(self, active):
        # Root visibility is owned by DemoShell. State is preserved, never
        # toggled/auto-collapsed while switching tabs.
        self.button.set_enabled(bool(active) and not self.owner.closed)
        self.refresh_button.set_enabled(bool(active) and not self.owner.closed)
        if active:
            self.refresh()
