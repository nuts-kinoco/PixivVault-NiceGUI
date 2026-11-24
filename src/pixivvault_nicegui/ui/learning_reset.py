"""学習状況のリセット（元 gui.py:2183-2297）。判定履歴の全削除＋好みカウントの初期化。

元と同じく、件数を入力するまで実行ボタンは押せない。CSV出力は同じ列・同じファイル名。
"""
import csv
from io import StringIO
from nicegui import ui

from .management_dialog import managed_dialog

DESCRIPTION_EMPTY = 'まだ判定がありません。'
EXPORT_FIELDS = ('work_id', 'title', 'verdict', 'reason', 'tags', 'author_id', 'source',
                 'opened', 'bookmarked', 'judged_at')


def description(judged):
    if not judged:
        return DESCRIPTION_EMPTY
    return (f'これまでの判定（{judged}枚）と、そこから学んだ好みの傾向を消します。'
            'おすすめタブの「あなたがSuperLikeした作品」の一覧と、閲覧履歴に付いている判定バッジ、'
            'ランキングの「合わない を除外」も一緒に空になります。閲覧履歴そのものと、'
            '「出さない」に登録した作品や作者は消えません。')


class LearningResetView:
    def __init__(self, parent, storage, *, is_open=None):
        self.parent, self.storage = parent, storage
        self.is_open = is_open if is_open is not None else lambda: parent.active
        self.closed = self.busy = False
        self.judged = 0
        self.generation = 0
        with ui.column().classes('w-full gap-1').props('data-testid=learning-reset'):
            self.item_label = ui.label('学習状況のリセット').classes('pv-card-title')
            self.desc = ui.label(DESCRIPTION_EMPTY).classes('pv-subtitle').props('data-testid=learning-reset-desc')
            self.button = ui.button('学習状況をリセット', icon='restart_alt', on_click=self.open).props(
                'flat no-caps data-testid=learning-reset-open')
            self.notice = ui.label().props('role=status data-testid=learning-reset-status')
        self.button.set_enabled(False)
        with managed_dialog(return_focus=self.button).props(
            'no-backdrop-dismiss transition-duration=150 data-testid=learning-reset-dialog '
            'aria-label=学習状況のリセット') as self.dialog, \
                ui.card().classes('pv-dialog-card w-full').style('max-width:520px'):
            self.title = ui.label().classes('pv-view-title')
            self.warning = ui.label(
                '削除すると元に戻せません。件数を入力すると「学習状況をリセット」が押せます。').classes('pv-subtitle')
            self.confirm = ui.input(label='件数を入力').props('outlined dense autofocus inputmode=numeric data-testid=learning-reset-confirm')
            with ui.row().classes('w-full justify-end'):
                self.export_button = ui.button('CSV出力', icon='file_download', on_click=self.export).props(
                    'flat no-caps data-testid=learning-reset-export')
                self.close_button = ui.button('閉じる', on_click=self.dialog.close).props(
                    'flat no-caps data-testid=learning-reset-close')
                self.execute_button = ui.button('学習状況をリセット', on_click=self.execute, color='amber').props(
                    'no-caps text-color=black data-testid=learning-reset-execute')
        self.execute_button.set_enabled(False)
        self.confirm.on_value_change(lambda _: self.sync())
        self.dialog.on('hide', self.hide)
        self.initial = ui.timer(.1, self.refresh, once=True)

    def hide(self):
        self.generation += 1

    def _confirmed(self):
        try:
            return self.judged > 0 and int(self.confirm.value) == self.judged
        except (TypeError, ValueError):
            return False

    def sync(self):
        self.execute_button.set_enabled(not self.busy and self._confirmed())
        self.export_button.set_enabled(not self.busy)
        self.close_button.set_enabled(not self.busy)

    def _can_open(self):
        p = self.parent
        return (not self.closed and not self.busy and self.is_open()
                and not p.state.closed and not p.services.closing)

    def _available(self):
        p = self.parent
        return (self._can_open() and p._storage_ready and not p._storage_busy
                and not p.state.saving and not p._review_busy and not getattr(p, '_review_mutating', False))

    async def refresh(self):
        """開くたびに数え直す（元 §3-1）。古い件数のままだと解錠の期待値がずれる。"""
        if self.closed:
            return 0
        try:
            judged = await self.storage.judged_count()
        except Exception:
            judged = 0
        if self.closed:
            return 0
        self.judged = judged
        self.desc.set_text(description(judged))
        self.button.set_enabled(judged > 0 and not self.busy)
        self.sync()
        return judged

    async def open(self):
        # The count and CSV are available while the inactive Learning view
        # is still restoring its session. Execution still requires that session.
        if not self._can_open():
            return
        judged = await self.refresh()
        if judged <= 0 or not self._can_open():
            return
        self.notice.set_text('')
        self.title.set_text(f'学習状況のリセット（判定 {judged}枚）')
        self.confirm.set_value('')
        self.sync()
        self.dialog.open()

    async def export(self):
        try:
            rows = await self.storage.judgment_export_rows()
        except Exception:
            self.notice.set_text('CSV出力に失敗しました。')
            return
        output = StringIO(newline='')
        writer = csv.writer(output)
        writer.writerow(EXPORT_FIELDS)
        for row in rows:
            writer.writerow([row['work_id'], row.get('title') or '', row.get('verdict'),
                             row.get('reason') or '', row.get('tags'), row.get('author_id') or '',
                             row.get('source') or '', row.get('opened'), row.get('bookmarked'),
                             row.get('judged_at')])
        ui.download.content(output.getvalue().encode('utf-8-sig'), 'discovery_judgment_history_export.csv')

    async def execute(self):
        if not self._available() or not self._confirmed():
            return
        self.busy = True
        self.dialog.close()
        parent = self.parent
        parent._storage_busy = True      # 完了まで判定を締める（元 §3-3-8）
        if parent._review_open:
            parent.review_dialog.close()
        parent._update_buttons()
        self.sync()
        failed = False
        try:
            await self.storage._wait(self.storage.submit_reset())
        except Exception:
            failed = True
        finally:
            self.busy = False
            parent._storage_busy = False
        if self.closed or parent.state.closed:
            return
        if failed:
            self.notice.set_text('学習状況: リセットに失敗しました。保存先を確認して、もう一度お試しください。')
            parent._update_buttons()
            await self.refresh()
            return
        self.notice.set_text('学習状況: リセットしました')
        parent.reset_superlike_count()
        parent.reload_storage(notice='学習状況: リセットしました')
        await self.refresh()

    def close(self):
        self.closed = True
        self.generation += 1
        self.initial.cancel()
        self.dialog.close()
