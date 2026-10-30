"""Ranking controls reuse the existing card/viewer and lifetime contracts."""
import asyncio
from datetime import date as calendar_date, datetime
import json
from uuid import uuid4
from nicegui import ui
from ..ranking_source import RankingQuery, RankingUnavailable, MODES, CONTENTS, SORTS, merge_pages, sorted_items
from ..state import GalleryState
from .gallery import GalleryView

class _ViewServices:
    def __init__(self,parent):self.parent=parent;self.artworks=();self.images=parent.images
    def __getattr__(self,name):return getattr(self.parent,name)

class _RankingGallery(GalleryView):
    def render_gallery(self):
        super().render_gallery()
        owner=getattr(self,'owner',None)
        if owner is not None and owner.page is not None:self.more_cell.set_visibility(not owner.page.exhausted)
    def more(self):
        if self.owner.active and not self.owner.busy:asyncio.create_task(self.owner.load(append=True))
    def close_viewer(self, *, restore_focus=True):
        owner=getattr(self,'owner',None)
        if owner is not None:owner._open_generation+=1
        return super().close_viewer(restore_focus=restore_focus)
    def open(self,work_id,page_no=0):
        self.owner._open_generation+=1
        request=self.owner._open_generation
        if not self.services.images.can_open(work_id):
            self.owner.notice.set_text('この形式のビューアは未接続です。保存後のライブラリで確認してください。')
            return
        if self.services.images.needs_metadata(work_id):
            asyncio.create_task(self.owner.open_unknown(work_id,self.services.images,self.owner.epoch,request))
            return
        super().open(work_id,page_no)

class RankingView:
    def __init__(self,services,*,card_size='large',on_size_change=None):
        self.services=services;self.proxy=_ViewServices(services)
        self.active=False;self.closed=False;self.busy=False;self.epoch=0;self._open_generation=0;self.page=None;self.pin=None
        self._condition_task=None;self._condition_pending=False;self._syncing_controls=False
        self._calendar_token=None;self.calendar_dialog=None;self.calendar_picker=None
        self.client=ui.context.client
        with ui.column().classes('pv-gallery w-full').props('data-testid=ranking-view') as self.root:
            with ui.row().classes('pv-toolbar'):
                self.mode=ui.select(MODES,value='daily',label='ランキング').props(
                    'outlined dense data-testid=ranking-mode').style('min-width:145px')
                self.content=ui.select(CONTENTS,value='illust',label='作品種別').props(
                    'outlined dense data-testid=ranking-content').style('min-width:165px')
                self.date_button=ui.button('今日',icon='calendar_month',on_click=self.open_calendar).props(
                    'flat no-caps data-testid=ranking-date-calendar aria-label=ランキングの日付を選ぶ')
                self.date=ui.input('日付 YYYYMMDD（空欄は今日）').props(
                    'outlined dense data-testid=ranking-date').style('min-width:270px')
                self.r18=ui.checkbox('R-18',value=False).props('data-testid=ranking-r18')
                self.ai=ui.checkbox('AIを除外',value=False).props('data-testid=ranking-exclude-ai')
                self.tag=ui.input('タグ検索').props('outlined dense data-testid=ranking-tag')
                self.fetch_button=ui.button('取得・更新',on_click=self.load).props('flat data-testid=ranking-fetch')
                self.sort=ui.select(SORTS,value='rank',label='並び順',on_change=lambda _:self.render()).props('outlined dense data-testid=ranking-sort')
            self.notice=ui.label('条件を選び、取得してください。').props('role=status data-testid=ranking-notice')
            self.gallery=_RankingGallery(self.proxy,card_size=card_size,on_size_change=on_size_change)
            self.gallery.owner=self
            # Initial no-request state is not a successful empty response.
            self.gallery.empty.set_text('ランキングは未取得です。条件を選び「取得・更新」を押してください。')
        for control in (self.mode,self.content,self.date,self.r18,self.ai):
            control.on_value_change(lambda _:self.changed(refresh=True))
        # Original tag entry is a submit operation, unlike selecting a date,
        # category or checkbox. Do not send a request for every typed letter.
        self.tag.on_value_change(lambda _:self.changed())
        self.tag.on('keydown.enter',lambda _:self.changed(refresh=True))
        self.client.on_delete(self.close)
    def changed(self,*,refresh=False):
        if self.closed or self._syncing_controls:return
        self.epoch+=1
        self._condition_pending=False
        self._close_calendar()
        self._sync_date_button()
        self.notice.set_text('条件を変更しました。最新の一覧を取得します。' if refresh else
                             '条件が変わりました。Enterまたは取得で反映します。現在の一覧は前の条件です。')
        supported=self.mode.value in ('daily','weekly') or bool(self.tag.value)
        vault=getattr(self.services.ranking_source,'vault',None)
        status=vault.status() if vault is not None else {}
        premium=bool(self.tag.value) and status.get('open') and status.get('mode')=='nana7mi'
        self.r18.set_enabled(supported and not premium)
        self._syncing_controls=True
        try:
            if not supported or premium:self.r18.set_value(False)
        finally:
            self._syncing_controls=False
        if premium:self.notice.set_text('人気順の借用検索では原 API が R-18 指定を消費しないため、R-18 絞り込みを適用しません。')
        elif not supported:self.notice.set_text('このランキングでは R-18 指定を利用できません。日間・週間またはタグ検索で指定してください。')
        self._sync_result_controls()
        if refresh and self.active:
            self._condition_pending=True
            if self._condition_task is None or self._condition_task.done():
                self._condition_task=asyncio.create_task(self._load_changed_conditions())
    async def _load_changed_conditions(self):
        # One serializer per view. Canceling a to_thread coroutine would not
        # cancel its GET and could let a second request overlap it; instead
        # retain the active request until its stale result is discarded.
        while self.active and not self.closed and self._condition_pending:
            epoch=self.epoch
            await asyncio.sleep(.12)
            if self.closed or not self.active or not self._condition_pending:return
            if epoch!=self.epoch:continue
            if self.busy:
                await asyncio.sleep(.025)
                continue
            self._condition_pending=False
            await self.load()
    def _selected_date(self):
        value=str(self.date.value or '').strip()
        if not value:return None
        if len(value)!=8 or not value.isascii() or not value.isdecimal():
            raise ValueError('invalid ranking date')
        selected=datetime.strptime(value,'%Y%m%d').date()
        if not calendar_date(2007,1,1)<=selected<=calendar_date.today():
            raise ValueError('ranking date outside original calendar range')
        return selected
    def _sync_date_button(self):
        try:selected=self._selected_date()
        except ValueError:
            self.date_button.set_text('日付を確認');return
        self.date_button.set_text(selected.isoformat() if selected else '今日')
    def _close_calendar(self):
        dialog=self.calendar_dialog
        self._calendar_token=None
        self.calendar_dialog=self.calendar_picker=None
        if dialog is not None and not dialog.is_deleted:
            dialog.close();dialog.delete()
    def _dismiss_calendar(self,token):
        if token==self._calendar_token:self._close_calendar()
    def open_calendar(self):
        if self.closed or not self.active:return
        self._close_calendar()
        token=(self.epoch,uuid4().hex);self._calendar_token=token
        try:initial=self._selected_date()
        except ValueError:initial=None
        today=calendar_date.today()
        with self.root:
            dialog=ui.dialog().props('data-testid=ranking-date-dialog')
        self.calendar_dialog=dialog
        with dialog,ui.card():
            ui.label('ランキングの日付を選択')
            picker=ui.date(initial.isoformat() if initial else None,mask='YYYY-MM-DD').props(
                'data-testid=ranking-date-picker')
            self.calendar_picker=picker
            picker.props['navigation-min-year-month']='2007/01'
            picker.props['navigation-max-year-month']=today.strftime('%Y/%m')
            picker.props['default-year-month']=(initial or today).strftime('%Y/%m')
            picker.props[':options']=("value => {const day=String(value).replaceAll('/', '-');"
                f"return day>='2007-01-01' && day<='{today.isoformat()}';}}")
            with ui.row():
                ui.button('今日',on_click=lambda _e,t=token:self._calendar_pick(t,
                    {'epoch':t[0],'nonce':t[1],'value':None})).props('flat data-testid=ranking-date-today')
                ui.button('キャンセル',on_click=lambda _e,t=token:self._dismiss_calendar(t)).props(
                    'flat data-testid=ranking-date-cancel')
        pinned=json.dumps(dict(epoch=token[0],nonce=token[1]),separators=(',',':'))
        picker.on('update:model-value',lambda event,t=token:self._calendar_pick(t,event.args),
            js_handler=f'value => {{const opened={pinned};emit({{...opened,value}});}}')
        dialog.on('hide',lambda _e,t=token:self._dismiss_calendar(t))
        dialog.open()
    def _calendar_pick(self,token,args):
        if (self.closed or not self.active or token!=self._calendar_token or token[0]!=self.epoch
                or not isinstance(args,dict) or args.get('epoch')!=token[0]
                or args.get('nonce')!=token[1] or self.calendar_dialog is None
                or self.calendar_dialog.is_deleted or not self.calendar_dialog.value):return
        value=args.get('value')
        if value is None:raw=''
        else:
            if type(value) is not str or len(value)!=10:return
            try:selected=calendar_date.fromisoformat(value)
            except ValueError:return
            if selected.isoformat()!=value or not calendar_date(2007,1,1)<=selected<=calendar_date.today():return
            raw=selected.strftime('%Y%m%d')
        self._close_calendar()
        previous=self.date.value
        self.date.set_value(raw)
        if previous==raw:self.changed(refresh=True)
    def query(self):
        selected=self._selected_date()
        return RankingQuery(self.mode.value,self.content.value,selected.strftime('%Y%m%d') if selected else None,
                            bool(self.r18.value),bool(self.ai.value),str(self.tag.value or '').strip())
    def _result_matches_conditions(self):
        if self.page is None:return False
        try:return self.page.query==self.query()
        except ValueError:return False
    def _sync_result_controls(self):
        # Keep the last successful cards on a failed request, but never offer
        # append or sorting as though those cards matched the new conditions.
        matches=not self.closed and not self.busy and self._result_matches_conditions()
        self.gallery.more_button.set_enabled(matches)
        self.sort.set_enabled(matches and not self.page.query.tag)
    def _retained_notice(self,text):
        return text+(' 前の一覧を保持しています。' if self.page is not None else '')
    async def load(self,*,append=False):
        if not self.active or self.closed or self.busy:return
        epoch=self.epoch
        try:query=self.query()
        except ValueError:
            self.notice.set_text(self._retained_notice('日付・モード・検索条件を確認してください。'))
            self._sync_result_controls();return
        if append and (self.page is None or self.page.query!=query or self.page.exhausted):return
        self._condition_pending=False
        self.busy=True;self.fetch_button.disable();self.gallery.more_button.disable()
        if append:self.gallery.more_button.set_text('読み込み中…')
        self.notice.set_text('取得中…')
        candidate_provider=None
        old_pin,old_page,old_images=self.pin,self.page,self.proxy.images
        try:
            candidate=await asyncio.to_thread(self.services.ranking_source.fetch,query,self.page.page+1 if append else 1)
            if self.closed or not self.active or epoch!=self.epoch:return
            merged=merge_pages(self.page if append else None,candidate)
            provider=self.services.acquire_ranking_images(tuple(i.record for i in merged.items))
            candidate_provider=provider
            old=self.pin;self.pin=provider;self.proxy.images=provider
            self.page=merged
            if not append:
                self.gallery.grid.clear();self.gallery.more_cell=None
                self.gallery.cards.clear();self.gallery.images.clear();self.gallery.ranks.clear();self.gallery._rendered_ids=()
            self.render()
            if old is not None:old.close()
            candidate_provider=None
            self.notice.set_text(f'{merged.order_label} · {len(merged.items)} 件'+(' · これ以上のページはありません' if merged.exhausted and not query.tag else ''))
        except RankingUnavailable:
            if not self.closed and self.active and epoch==self.epoch:
                self.notice.set_text(self._retained_notice('このモードは利用できません。'))
        except Exception:
            if not self.closed and self.active and epoch==self.epoch:
                self.notice.set_text(self._retained_notice('取得できませんでした。再試行してください。'))
        finally:
            if candidate_provider is not None:
                self.pin,self.page,self.proxy.images=old_pin,old_page,old_images
                candidate_provider.close()
            self.busy=False
            if not self.closed:
                self.gallery.more_button.set_text('もっと見る')
                self.fetch_button.enable();self._sync_result_controls()
    def render(self):
        if self.closed or self.page is None:return
        if not self._result_matches_conditions():
            self._sync_result_controls();return
        self.gallery.close_viewer(restore_focus=False)
        old=self.gallery.state
        items=self.page.items if self.page.query.tag else sorted_items(self.page.items,self.sort.value)
        state=GalleryState(tuple(i.record.as_artwork() for i in items),page_size=max(50,len(items)))
        state.set_card_size(old.card_size);state.set_query(old.query);state.generation=old.generation+1;old.close()
        self.gallery.state=self.gallery._viewer_state=state
        self.gallery.empty.set_text('取得した条件に該当する作品がありません。')
        self.gallery.render_gallery()
        for work_id,image in self.gallery.images.items():image.set_source(self.proxy.images.url(work_id,0,'thumb'))
        self.gallery.more_cell.set_visibility(not self.page.exhausted)
        self._sync_result_controls()
    async def open_unknown(self,work_id,provider,epoch,request):
        try:artwork=await asyncio.to_thread(provider.prepare_artwork,work_id)
        except Exception:
            if self.active and not self.closed and epoch==self.epoch and request==self._open_generation:self.notice.set_text('作品のページ情報を取得できませんでした。')
            return
        if (not self.active or self.closed or epoch!=self.epoch or self.pin is not provider
                or request!=self._open_generation):return
        old=self.gallery.state
        state=GalleryState(tuple(artwork if a.work_id==work_id else a for a in old.artworks),page_size=old.page_size)
        state.set_card_size(old.card_size);state.set_query(old.query);state.generation=old.generation+1;old.close()
        self.gallery.state=self.gallery._viewer_state=state
        self.gallery.open(work_id)
    def set_viewer_active(self,active):self.set_active(active)
    def set_active(self,active):
        if self.active!=bool(active):self.epoch+=1
        self.active=bool(active) and not self.closed;self.gallery.set_viewer_active(self.active)
        if not self.active:
            self._condition_pending=False;self._close_calendar()
    def apply_card_size(self,size):self.gallery.apply_card_size(size)
    def close(self):
        if self.closed:return
        self.closed=True;self.active=False;self.epoch+=1;self._condition_pending=False
        self._close_calendar();self.gallery.close()
        if self.pin is not None:self.pin.close()
