"""Ranking controls reuse the existing card/viewer and lifetime contracts."""
import asyncio
from datetime import date as calendar_date, datetime
import json
from uuid import uuid4
from nicegui import ui
from ..ranking_source import RankingQuery, RankingUnavailable, MODES, CONTENTS, SORTS, merge_pages, sorted_items
from ..state import GalleryState
from .gallery import GalleryView
from .ranking_card_menus import RankingCardMenus

class _ViewServices:
    def __init__(self,parent):self.parent=parent;self.artworks=();self.images=parent.images
    def __getattr__(self,name):return getattr(self.parent,name)

class _RankingGallery(GalleryView):
    def _rank_label(self,art):
        owner=getattr(self,'owner',None)
        if owner is None or not owner.preferred_tags_in(art.tags):return super()._rank_label(art)
        # 元 ranking_tab.py:822-824: 順位の横に青ハート。印が無いカードの構造は他の一覧と同じに保つ。
        with ui.row().classes('items-center no-wrap gap-1 pv-card-head'):
            label=ui.label().classes('pv-card-rank')
            heart=ui.icon('favorite').classes('pv-card-heart').props(f'size=12px aria-label=好みのタグを含む作品')
            heart._props['data-testid']=f'ranking-heart-{art.work_id}'
        return label
    def _card_menu(self,art,card):
        owner=getattr(self,'owner',None)
        if owner is not None:owner.build_card_menu(art,card)
    def _after_viewer_hide(self,art,hidden):
        owner=getattr(self,'owner',None)
        if owner is None:return super()._after_viewer_hide(art,hidden)
        owner.adopt_hidden(art.work_id,art.title,hidden)
    def render_gallery(self):
        super().render_gallery()
        owner=getattr(self,'owner',None)
        if owner is not None and owner.page is not None:self.more_cell.set_visibility(not owner.page.exhausted)
    def more(self):
        if self.owner.active and not self.owner.busy:asyncio.create_task(self.owner.load(append=True))
    def close_viewer(self, *, restore_focus=True):
        owner=getattr(self,'owner',None)
        if owner is not None:owner._open_generation+=1
        super().close_viewer(restore_focus=restore_focus)
        if self._viewer_state is not self.state:
            self._viewer_state.close()
            self._viewer_state = self.state
    def search(self,value=None):
        if self._viewer_state is not self.state:self.close_viewer(restore_focus=False)
        super().search(value)
    def open(self,work_id,page_no=0):
        if self._viewer_state is not self.state:self.close_viewer(restore_focus=False)
        self.owner._open_generation+=1
        request=self.owner._open_generation
        if not self.services.images.can_open(work_id):
            self.owner.notice.set_text('この形式のビューアは未接続です。保存後のライブラリで確認してください。')
            return
        if self.services.images.needs_metadata(work_id):
            asyncio.create_task(self.owner.open_unknown(work_id,self.services.images,self.owner.epoch,request))
            return
        super().open(work_id,page_no)

class RankingView(RankingCardMenus):
    _auto_loaded=False;_auto_task=None  # first-open fetch state (see _schedule_first_load)
    _preferred=frozenset();_preferred_built=False  # 好みタグ印の集合（元 ranking_tab.py:453, 519-544）
    def __init__(self,services,*,card_size='large',on_size_change=None):
        self.services=services;self.proxy=_ViewServices(services)
        self._auto_loaded=False;self._auto_task=None
        self._preferred=frozenset();self._preferred_base=frozenset();self._preferred_revision=None
        self.active=False;self.closed=False;self.busy=False;self.epoch=0;self._open_generation=0;self.page=None;self.pin=None
        self._condition_task=None;self._condition_pending=False;self._syncing_controls=False
        self._calendar_token=None;self.calendar_dialog=None;self.calendar_picker=None
        self.client=ui.context.client
        with ui.column().classes('pv-gallery pv-ranking-view w-full').props('data-testid=ranking-view') as self.root:
            with ui.row().classes('pv-toolbar'):
                self.mode=ui.toggle(MODES,value='daily').props(
                    'unelevated no-caps rounded data-testid=ranking-mode').classes('pv-period-chips')
                self.content=ui.select(CONTENTS,value='illust',label='作品種別').props(
                    'outlined dense data-testid=ranking-content').style('min-width:165px')
                self.date_button=ui.button('今日',icon='calendar_month',on_click=self.open_calendar).props(
                    'flat no-caps data-testid=ranking-date-calendar aria-label=ランキングの日付を選ぶ')
                self.date=ui.input('日付 YYYYMMDD（空欄は今日）').props(
                    'outlined dense data-testid=ranking-date').style('min-width:270px')
                self.r18=ui.switch('R-18',value=False).props('data-testid=ranking-r18')
                self.ai=ui.switch('AIを除外',value=False).props('data-testid=ranking-exclude-ai')
                self.tag=ui.input('タグ検索').props('outlined dense data-testid=ranking-tag')
                self.fetch_button=ui.button('取得・更新',on_click=self.refresh).props('flat data-testid=ranking-fetch')
                self.sort=ui.select(SORTS,value='rank',label='並び順',on_change=lambda _:self.render()).props('outlined dense data-testid=ranking-sort')
                self._build_hide_manager()
            self.notice=ui.label('条件を選び、取得してください。').props('role=status data-testid=ranking-notice')
            self._build_hide_status()
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
        self._local_revision_task=None
        self._local_revision_timer=ui.timer(1.0, self._sync_local_revision) if getattr(services, 'local_tag_view', None) is not None else None
        self.client.on_delete(self.close)

    def local_tags_changed(self):
        self._preferred_revision = None
        self._sync_local_revision()

    def _sync_local_revision(self):
        local = getattr(self.services, 'local_tag_view', None)
        if self.closed or not self.active or local is None or not self._preferred_built:
            return
        if self._preferred_revision == local.revision():
            return
        if self.page is not None:
            self.render()
        if self._local_revision_task is None or self._local_revision_task.done():
            self._local_revision_task = asyncio.create_task(self._refresh_local_preferred())

    async def _refresh_local_preferred(self):
        await asyncio.to_thread(self._rebuild_preferred)
        if not self.closed and self.active and self.page is not None:
            self.render()

    def changed(self,*,refresh=False):
        if self.closed or self._syncing_controls:return
        self.epoch+=1
        self._condition_pending=False
        self._close_calendar()
        self._sync_date_button()
        self.notice.set_text('条件を変更しました。最新の一覧を取得します。' if refresh else
                             '条件が変わりました。Enterまたは取得で反映します。現在の一覧は前の条件です。')
        supported=self.mode.value in ('daily','weekly') or bool(self.tag.value)
        # 元 ranking_tab.py:1109-1110, 1219-1227: 借用プレミアムのタグ検索でも R-18 切替は操作でき、値も保つ
        # （借用検索は R-18 を API にも結果の絞り込みにも使わないので、結果は変わらない）。
        self.r18.set_enabled(supported)
        self._syncing_controls=True
        try:
            if not supported:self.r18.set_value(False)
        finally:
            self._syncing_controls=False
        if not supported:self.notice.set_text('このランキングでは R-18 指定を利用できません。日間・週間またはタグ検索で指定してください。')
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
    async def refresh(self):
        """「取得・更新」: 好みの集合を引き直してから取得する（元 _on_refresh, ranking_tab.py:1275-1277）。"""
        await self.load(refresh_preferences=True)
    def preferred_tags_in(self,tags):
        from ..library_filters import normalize_tag
        local = getattr(self.services, 'local_tag_view', None) or getattr(getattr(self.services, 'discovery', None), 'local_tag_view', None)
        preferred = self._preferred
        if local is not None and getattr(self, '_preferred_revision', None) != local.revision():
            preferred = getattr(self, '_preferred_base', frozenset())
        return any(normalize_tag(t) in preferred for t in tags or ())
    def preferred_work(self,work_id):
        return False if self.page is None else any(i.record.work_id==work_id and self.preferred_tags_in(i.record.tags) for i in self.page.items)
    async def load(self,*,append=False,refresh_preferences=False):
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
            candidate=await asyncio.to_thread(self._fetch_with_hidden,query,self.page.page+1 if append else 1,
                _rebuild=refresh_preferences or not self._preferred_built,
                **({'previous':self.page} if append and getattr(self.page,'page_session',None) is not None else {}))
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
            # The mode change announces that R-18 cannot be selected for
            # monthly/rookie, but the async fetch used to overwrite that
            # explanation immediately. Keep the reason visible with results.
            r18_note=(' · このランキングでは R-18 指定を利用できません。日間・週間またはタグ検索で指定してください。'
                      if query.mode in ('monthly','rookie') and not query.tag else '')
            self.notice.set_text(f'{merged.order_label} · {len(merged.items)} 件'
                +(' · これ以上のページはありません' if merged.exhausted and not query.tag else '')
                +r18_note)
        except RankingUnavailable:
            if not self.closed and self.active and epoch==self.epoch:
                self.notice.set_text(self._retained_notice('このモードは利用できません。'))
        except Exception as error:
            if not self.closed and self.active and epoch==self.epoch:
                from ..premium_read import unavailable_message
                code = getattr(error, 'code', None)
                if code == 'shared-get-outcome-uncertain':
                    from .shared_get_recovery_view import UNCERTAIN_MESSAGE
                    message = UNCERTAIN_MESSAGE
                else:
                    message = unavailable_message(code, reason=getattr(error, 'reason', None)) or '取得できませんでした。再試行してください。'
                self.notice.set_text(self._retained_notice(message))
        finally:
            if candidate_provider is not None:
                self.pin,self.page,self.proxy.images=old_pin,old_page,old_images
                candidate_provider.close()
            self.busy=False
            if not self.closed:
                self.gallery.more_button.set_text('もっと見る')
                self.fetch_button.enable();self._sync_result_controls()
    def _fetch_with_hidden(self,*args,_rebuild=False,**kwargs):
        # 取得と同じ作業スレッドで、保存済みの「出さない」集合も読む（通信は取得の 1 回だけ）。
        value=self.services.ranking_source.fetch(*args,**kwargs)
        self._load_hidden_now()
        if _rebuild:self._rebuild_preferred()
        return value
    def _rebuild_preferred(self):
        # 失敗しても印を諦めるだけで、ランキング本体は止めない（元 ranking_tab.py:519-530）。作った扱いにして取得ごとに引かない。
        try:
            local = getattr(self.services, 'local_tag_view', None) or getattr(getattr(self.services, 'discovery', None), 'local_tag_view', None)
            base = getattr(self.services, 'preferred_ranking_tags_base', None) or getattr(getattr(self.services, 'discovery', None), 'base_preferred_tags', None)
            self._preferred_base = frozenset(base()) if local is not None and callable(base) else None
            before = local.revision() if local is not None else None
            self._preferred = frozenset(self.services.preferred_ranking_tags())
            if self._preferred_base is None:
                self._preferred_base = self._preferred
            after = local.revision() if local is not None else None
            self._preferred_revision = after if before == after else None
        except Exception:
            import logging
            logging.getLogger(__name__).warning('ランキング: 好みタグの集合を読めませんでした。',exc_info=True)
            self._preferred=frozenset()
        self._preferred_built=True
    def render(self):
        if self.closed or self.page is None:return
        if not self._result_matches_conditions():
            self._sync_result_controls();return
        viewer_state=self.gallery._viewer_state
        hidden=self.hidden_work_ids()
        keep_viewer=viewer_state.viewer_open and viewer_state.selected_id in hidden
        if not keep_viewer:self.gallery.close_viewer(restore_focus=False)
        old=self.gallery.state
        items=self.page.items if self.page.query.tag else sorted_items(self.page.items,self.sort.value)
        if hidden:items=tuple(i for i in items if i.record.work_id not in hidden)
        state=GalleryState(tuple(i.record.as_artwork() for i in items),page_size=max(50,len(items)))
        state.set_card_size(old.card_size);state.set_query(old.query);state.generation=old.generation+1
        # The hidden work leaves the ranking while its open viewer retains the selection.
        if not keep_viewer or old is not viewer_state:old.close()
        self.gallery.state=state
        if not keep_viewer:self.gallery._viewer_state=state
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
        else:
            self._schedule_first_load()
            self._schedule_hidden_refresh()
    def _schedule_hidden_refresh(self):
        # おすすめ側の「出さない」変更を、タブを開いたときに取り込む（保存済みの読み込みだけ）。
        try:self._hm().task=asyncio.get_running_loop().create_task(self.refresh_hidden())
        except RuntimeError:pass
    def _schedule_first_load(self):
        # Original ranking_tab.on_tab_open: the first time the tab becomes
        # visible and nothing is cached, fetch once. Only with a live reader.
        if (self._auto_loaded or self.page is not None or self.closed
                or getattr(self.services,'live_client',None) is None
                or (self._auto_task is not None and not self._auto_task.done())):return
        try:self._auto_task=asyncio.get_running_loop().create_task(self._first_load())
        except RuntimeError:pass
    async def _first_load(self):
        # Header authentication check first, so the two requests are not
        # contending for the shared GET fence. Same load() as the button.
        profile=getattr(self.services,'native_profile',None)
        pending=getattr(profile,'startup_auth_task',None)
        if pending is not None and not pending.done():
            try:await asyncio.wait_for(asyncio.shield(pending),timeout=20)
            except Exception:pass
        # FL-A08: the readers still hold the launch Cookie after a browser
        # replacement. Do not send an automatic GET with the stale one.
        replaced=getattr(profile,'cookie_replaced_since_launch',None)
        if callable(replaced) and replaced():return
        if self.closed or not self.active or self.busy or self.page is not None:return
        self._auto_loaded=True
        await self.load()
    def apply_card_size(self,size):self.gallery.apply_card_size(size)
    def close(self):
        if self.closed:return
        self.closed=True;self.active=False;self.epoch+=1;self._condition_pending=False
        timer=getattr(self, '_local_revision_timer', None)
        if timer is not None:timer.cancel()
        self._close_calendar();self.gallery.close()
        if self.pin is not None:self.pin.close()
