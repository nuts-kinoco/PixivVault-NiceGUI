"""Dialog-local U15 comparison and bounded representative thumbnails."""
import asyncio
import base64
from io import BytesIO
from threading import Event
from PIL import Image
from nicegui import ui

from .management_dialog import managed_dialog
from ..unfollowed_authors import fetch_unfollowed_authors
from .action_controls import dialog_actions, outlined_select, primary_button, secondary_button
from .pixiv_action_buttons import PixivActionButtons


def _thumbnail(reader,url):
    raw=reader.get_image_bytes(url)
    if type(raw) is not bytes or not 0<len(raw)<=24*1024*1024:raise ValueError()
    with Image.open(BytesIO(raw)) as opened:
        if opened.width*opened.height>28_000_000:raise ValueError()
        opened.thumbnail((180,180));image=opened.convert('RGB')
    out=BytesIO();image.save(out,format='JPEG',quality=80)
    return 'data:image/jpeg;base64,'+base64.b64encode(out.getvalue()).decode('ascii')


class UnfollowedAuthorsView:
    def __init__(self,services):
        self.services=services;self.client=ui.context.client
        self.closed=False;self.visible=False;self.busy=False;self.epoch=0
        self.authors=();self.shown=50;self._cancel=Event()
        self._semaphore=asyncio.Semaphore(8)
        self._account=None;self._reader=None
        self.follow_actions=[]
        with managed_dialog().props('data-testid=unfollowed-dialog') as self.dialog:
            with ui.card().classes('pv-tone-b-unfollowed-card w-full gap-2'):
                ui.label('ブックマークの未フォロー作者').classes('pv-dialog-title text-base font-semibold')
                ui.label('選んだ媒体・公開範囲から、公開・非公開のどちらでもフォローしていない作者を確認します。').classes('pv-subtitle')
                with ui.row().classes('pv-toolbar'):
                    self.kind = outlined_select({'both': '全媒体', 'illustration': 'イラスト・漫画・うごいら', 'novel': '小説'},
                                                value='both', label='媒体')
                    self.visibility = outlined_select({'both': '公開・非公開', 'show': '公開', 'hide': '非公開'},
                                                      value='both', label='ブックマーク')
                    self.fetch_button = primary_button('取得して確認', self.refresh, testid='unfollowed-fetch')
                self.notice = ui.label('未取得です。').classes('pv-subtitle pv-empty').props(
                    'role=status data-testid=unfollowed-status')
                self.rows = ui.column().classes('pv-tone-b-unfollowed-list pv-tone-b-list w-full gap-2')
                with dialog_actions():
                    self.more = secondary_button('次の50作者を表示', self.show_more).classes('mr-auto')
                    self.more.set_visibility(False)
                    secondary_button('閉じる', self.hide)
        self.dialog.on('hide',self.hide)
        self.client.on_delete(self.close)

    def _set_notice(self, text, *, empty=False, error=False):
        self.notice.set_text(text)
        self.notice.classes(add='pv-empty' if empty else None, remove=None if empty else 'pv-empty')
        self.notice.classes(add='pv-tone-b-error' if error else None, remove=None if error else 'pv-tone-b-error')

    def show(self):
        if self.closed:return
        self.visible=True;self.dialog.open()

    def hide(self):
        self._cancel.set();self.epoch+=1;self.visible=False;self.busy=False
        self.authors=();self._account=None;self._reader=None
        self._clear_rows();self.more.set_visibility(False)
        self._set_notice('未取得です。', empty=True)
        self.fetch_button.set_enabled(True)
        if self.dialog.value:self.dialog.close()

    def _current(self,epoch,reader,account=None):
        try:
            return (not self.closed and self.visible and self.epoch==epoch
                    and not getattr(self.services,'closing',False)
                    and self.services.live_client is reader
                    and (account is None or reader.require_identity()==account))
        except Exception:
            return False

    def _clear_rows(self):
        for binding in self.follow_actions:
            binding.close()
        self.follow_actions.clear()
        self.rows.clear()

    def _follow_current(self,epoch,reader,account):
        # The shared action hub polls on the UI thread. Its validity check
        # must use cached identity and never wait for a reader's HTTP lock.
        actions=getattr(self.services,'pixiv_actions',None)
        return (self._current(epoch,reader) and self._reader is reader
                and self._account==account and actions is not None
                and actions.cached_verified_account_id==account)

    async def refresh(self):
        if self.closed or not self.visible or self.busy:return False
        reader=getattr(self.services,'live_client',None)
        if reader is None:
            self._set_notice('認証済みの読取接続が必要です。', error=True);return False
        self.epoch+=1;epoch=self.epoch
        self._cancel.set();cancel=Event();self._cancel=cancel
        kinds=('illustration','novel') if self.kind.value=='both' else (self.kind.value,)
        rests=('show','hide') if self.visibility.value=='both' else (self.visibility.value,)
        self.busy=True;self.authors=();self._clear_rows();self.more.set_visibility(False)
        self.fetch_button.set_enabled(False);self._set_notice('取得しています…')
        try:
            result=await asyncio.to_thread(fetch_unfollowed_authors,reader,kinds=kinds,
                visibilities=rests,is_current=lambda:not cancel.is_set() and self.services.live_client is reader)
            if not self._current(epoch,reader,result.account_id):return False
            self.authors=result.authors;self._account=result.account_id;self._reader=reader;self.shown=50
            self._set_notice(f'未フォロー作者 {len(self.authors)} 人', empty=not self.authors)
            await self._render(epoch)
            return self._current(epoch,reader,result.account_id)
        except Exception:
            if self._current(epoch,reader):
                self.authors=();self._clear_rows()
                self._set_notice('全件を取得できませんでした。接続・利用枠を確認してから再度操作してください。',
                                 error=True)
            return False
        finally:
            if self.epoch==epoch:
                self.busy=False;self.fetch_button.set_enabled(True)

    async def _render(self,epoch):
        reader,account=self._reader,self._account
        self._clear_rows();targets=[]
        with self.rows:
            for item in self.authors[:self.shown]:
                with ui.row().classes('pv-tone-b-author-row'):
                    image=ui.image().classes('pv-tone-b-thumb').props('data-testid=unfollowed-thumb')
                    ui.label(f'{item.name} ({item.author_id})').classes('pv-tone-b-author-name')
                    ui.link('作者ページ',f'https://www.pixiv.net/users/{item.author_id}',new_tab=True).classes('pv-tone-b-author-link')
                    binding=PixivActionButtons(self.services,prefix=f'unfollowed-{item.author_id}')
                    binding.follow.classes('pv-tone-b-author-follow')
                    binding.notice.classes('pv-tone-b-author-notice')
                    binding.bookmark.set_visibility(False)
                    binding.bind(None,item.author_id,author_name=item.name,
                        is_current=lambda:self._follow_current(epoch,reader,account))
                    self.follow_actions.append(binding)
                    ui.label('代表: '+('小説' if item.kind=='novel' else '画像')).classes('pv-subtitle pv-tone-b-author-kind')
                    status=ui.label('プレビューなし' if not item.thumbnail else '読込中').classes('pv-subtitle pv-tone-b-author-preview')
                    if item.thumbnail:targets.append((item,image,status))
        self.more.set_visibility(self.shown<len(self.authors))
        async def fill(item,image,status):
            async with self._semaphore:
                try:
                    if not self._current(epoch,reader,account):return
                    data=await asyncio.to_thread(_thumbnail,reader,item.thumbnail)
                    if not self._current(epoch,reader,account):return
                    image.set_source(data);status.set_text('')
                except Exception:
                    if self._current(epoch,reader):status.set_text('プレビューを取得できません')
        await asyncio.gather(*(fill(*target) for target in targets))

    async def show_more(self):
        if self.closed or not self.visible or self.busy or self._reader is None:return
        if not self._current(self.epoch,self._reader,self._account):
            self.hide();return
        epoch=self.epoch
        self.busy=True
        try:
            self.shown+=50;await self._render(epoch)
        finally:
            if self.epoch==epoch:self.busy=False

    def close(self):
        if not self.closed:self.hide();self.closed=True
