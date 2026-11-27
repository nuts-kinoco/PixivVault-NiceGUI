"""Learning-only view history; verdict badges always come from the current DB join."""
import asyncio
import csv
from io import StringIO
from types import SimpleNamespace
from .manual_toolbar import manual_toolbar

from nicegui import ui

from .management_dialog import managed_dialog

from .action_controls import dialog_actions, notify_action, outlined_input, secondary_button

LABELS = {'like':'いいね','superLike':'SuperLike','skip':'見送り','hate':'合わない'}

class _NoticeStatus:
    """親の status ラベルの代わり。ヘッダー側ではトーストで知らせる。"""

    @staticmethod
    def set_text(text):
        notify_action(text, type='warning')


class HeaderHistoryHost:
    """学習タブの外（ヘッダーの共通部品）から閲覧履歴を出すための親。

    ViewHistoryView が親に求める面（services / root / _images / active / state / discovery / status）だけを
    持つ。学習タブを作らないので、タブは変わらず、取得や自動通信も起きない。
    """

    def __init__(self, shell, storage):
        self._shell = shell
        self.services = shell.services
        self.root = shell.content
        self._images = shell.services.images
        self.state = SimpleNamespace(generation=0)
        self.discovery = getattr(shell.services, 'discovery', None)
        self.status = _NoticeStatus()
        self.storage = storage
        # ダイアログ（Quasar のオーバーレイ）はビューアーより前面に出るので、作品を開くときは閉じる。
        self.close_dialog_on_open = True

    @property
    def active(self):
        return not self._shell.closed


class ViewHistoryView:
    def __init__(self, parent, storage):
        self.parent, self.storage = parent, storage
        self.closed = self.busy = self.opened = False
        self.error = ''
        self.rows = []
        self.shown = 50
        self.generation = 0
        # 入口はヘッダー右端の VISIBILITY ボタン（shell.open_view_history）。ヘッダー側は HeaderHistoryHost を親に
        # この部品を単独で持ち、学習タブは開かない。学習タブの部品は記録（record_view）と件数の更新に使う。
        self.on_count = None
        self.button = ui.button('閲覧履歴', on_click=self.open).props('flat data-testid=view-history-inner')
        self.button.set_visibility(False)
        self.button.set_enabled(storage is not None)
        with managed_dialog().props('data-testid=view-history-dialog') as self.dialog, ui.card().classes('pv-tone-b-history-card w-full gap-2') as card:
            heading = ui.label('閲覧履歴（学習タブ）').classes('pv-dialog-title text-base font-semibold')
            self.dialog.props['aria-labelledby'] = heading.html_id
            ui.label('学習で開いた作品の記録です。判定とは別に回数を記録します。').classes('pv-subtitle')
            self.notice = ui.label().classes('pv-subtitle pv-tone-b-notice').props('role=status data-testid=view-history-status')
            self.body = ui.column().classes('pv-tone-b-history-list pv-tone-b-list w-full gap-2')
            self.more = secondary_button('もっと見る', self.load_more, testid='view-history-more')
            with ui.column().classes('w-full').props('data-testid=view-history-danger') as danger:
                self.delete_summary = ui.label().classes('pv-subtitle')
                ui.label('削除すると元に戻せません。件数を入力すると「全件削除」が押せます。').classes('pv-subtitle')
                with ui.row().classes('w-full items-center gap-2'):
                    self.confirm = outlined_input('削除する履歴の件数を入力').props(
                        'inputmode=numeric data-testid=view-history-confirm')
                    self.clear_button = ui.button('全件削除', on_click=self.clear, color='amber').classes('pv-hit28').props(
                        'unelevated dense no-caps text-color=black data-testid=view-history-clear')
                    self.confirm.on_value_change(lambda _: self.sync())
            manual_toolbar(card, 'view-history', (danger,), title='履歴の削除')
            with dialog_actions().classes('justify-end'):
                secondary_button('CSV出力', self.export, icon='file_download', outline=True, testid='view-history-export')
                secondary_button('閉じる', self.dialog.close, testid='view-history-close').props('autofocus')
        self.dialog.on('hide', self.hide)
        from .recommendation_viewer import RecommendationViewer
        self.viewer = RecommendationViewer(parent.services, parent.root, on_close=lambda:None,
                                           test_id='view-history-viewer')
        self.timer = ui.timer(1, self.poll)
        self.initial = ui.timer(.1, self.refresh, once=True)
        self.sync()

    def hide(self):
        self.opened = False
        self.generation += 1

    def sync(self):
        self.delete_summary.set_text(f'閲覧履歴{len(self.rows)}件が対象です。作品や判定は削除しません。')
        self.clear_button.set_enabled(not self.busy and bool(self.rows) and self.confirm.value == str(len(self.rows)))
        self.more.set_visibility(self.shown < len(self.rows))

    def _show_notice(self, text, *, error=False):
        self.notice.set_text(text)
        self.notice.classes(add='pv-tone-b-error' if error else None, remove=None if error else 'pv-tone-b-error')

    async def open(self):
        self.error = ''
        self.opened = True
        self.shown = 50
        self.dialog.open()
        await self.refresh()

    async def poll(self):
        if self.opened and self.parent.active and not self.closed and not self.busy:
            await self.refresh()

    async def refresh(self):
        if self.closed or self.busy or self.storage is None:return
        self.busy = True
        epoch = self.generation
        try:
            rows = await self.storage.view_history()
            if self.closed or epoch != self.generation:return
            self.button.set_text(f'閲覧履歴 · {len(rows)}件')
            if self.on_count is not None:self.on_count(len(rows))
            if rows != self.rows or not self.body.default_slot.children:
                self.rows = rows
                self.confirm.set_value('')
                self.render()
            self._show_notice(self.error or f'{len(rows)}件', error=bool(self.error))
        except Exception:
            if not self.closed:
                self._show_notice('閲覧履歴を読み込めません。保存先を確認して再度開いてください。', error=True)
        finally:
            self.busy = False
            if not self.closed:self.sync()

    def render(self):
        self.body.clear()
        with self.body:
            if not self.rows:
                ui.label('閲覧履歴はありません。').classes('pv-empty')
            for row in self.rows[:self.shown]:
                with ui.row().classes('pv-tone-b-history-row').props(f'data-testid=view-history-row data-work-id={row["work_id"]}'):
                    try:
                        source = self.parent._images.url(row['work_id'],0,'thumb')
                    except Exception:
                        ui.icon('image_not_supported').classes('pv-tone-b-thumb pv-tone-b-history-placeholder')
                    else:
                        picture = ui.image(source).classes('pv-tone-b-thumb').props('fit=cover').on('click',lambda _,w=row['work_id']:self.open_work(w))
                        with picture.add_slot('error'):
                            ui.icon('image_not_supported').classes('pv-subtitle pv-tone-b-history-placeholder')
                    with secondary_button('', lambda _,w=row['work_id']:self.open_work(w)).props('align=left').classes('pv-tone-b-history-title'):
                        title = ui.label(row['title']).classes('pv-tone-b-history-title-text')
                        title.props['title'] = row['title']
                    ui.label(f'開いた回数: {row["open_count"]}　最終: {row["last_opened_at"]}').classes('pv-subtitle pv-tone-b-history-facts')
                    ui.label(LABELS.get(row.get('discovery_verdict'), '')).props('data-testid=view-history-badge').classes('pv-subtitle pv-tone-b-history-badge')
                    ui.button('Pixivで開く…', color=None, on_click=lambda _,w=row['work_id']:ui.navigate.to(f'https://www.pixiv.net/artworks/{w}',new_tab=True)).props('flat dense no-caps').classes('pv-tone-b-history-external pv-hit28')
        self.sync()

    def load_more(self):
        self.shown += 50
        self.render()

    def export(self):
        output = StringIO(newline='')
        writer = csv.writer(output)
        fields = ('work_id','title','open_count','first_opened_at','last_opened_at')
        writer.writerow(fields)
        for row in self.rows:writer.writerow(row.get(k,'') for k in fields)
        ui.download.content(output.getvalue().encode('utf-8-sig'),'discovery_view_history_export.csv')

    async def clear(self):
        if self.busy or not self.rows or self.confirm.value != str(len(self.rows)):return
        self.busy = True
        self.sync()
        failed = False
        try:
            await self.storage.clear_view_history(self.rows)
        except Exception:
            failed = True
        finally:
            self.busy = False
            self.confirm.set_value('')
        self.error = '閲覧履歴が変更されたか、削除できませんでした。再読込して件数を確認してください。' if failed else ''
        await self.refresh()
        if self.error:
            self._show_notice(self.error, error=True)

    async def record_open(self, wid):
        if self.closed or self.storage is None:
            return
        work_generation = self.parent.state.generation
        try:
            await self.storage.record_view(wid)
        except Exception:
            if (not self.closed and self.parent.state.generation == work_generation
                    and self.parent._can_open_learning_work()):
                self.parent.status.set_text('閲覧履歴を記録できませんでした。')
        else:
            await self.refresh()

    async def open_work(self, wid, record=False):
        if self.closed or not self.parent.active:return
        epoch = self.generation
        work_generation = self.parent.state.generation

        def current():
            return (not self.closed and self.parent.active and epoch == self.generation
                    and (not record or (self.parent.state.generation == work_generation
                                        and self.parent._can_open_learning_work())))

        if record:
            await self.record_open(wid)
            if not current():
                return
        pin = None
        try:
            if self.parent.discovery is not None:
                source = self.parent.discovery
                await source.resolve_history_candidate(wid)
                def resolve():return source._record_for(source._candidates[wid])
                candidate = await source._wait(source._submit(resolve))
                pin = self.parent.services.acquire_ranking_images((candidate,))
                artwork = candidate.as_artwork()
            else:
                artwork = next(a for a in self.parent.services.artworks if a.work_id == wid)
                pin = self.parent.services.images
            if not current():
                if pin is not self.parent.services.images:pin.close()
                return
            selection = SimpleNamespace(closed=False, artwork=artwork, images=pin,
                close=(lambda:None) if pin is self.parent.services.images else pin.close)
            self.viewer.show(selection,card_size='large')
            if getattr(self.parent,'close_dialog_on_open',False):self.dialog.close()
        except Exception:
            if pin is not None and pin is not self.parent.services.images:pin.close()
            if current():
                self.parent.status.set_text('作品を表示できません。取得状態を確認してください。')

    def close(self):
        self.closed = True
        self.generation += 1
        self.timer.cancel()
        self.initial.cancel()
        self.viewer.close()
        self.dialog.close()
