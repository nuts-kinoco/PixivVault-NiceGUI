"""Original personal ranking operations over the isolated vault search adapter."""
import asyncio
from nicegui import ui
from ..personal_ranking import (PersonalRankingSource,normalize_personal_filters,apply_personal_filters,
    _DATE_PRESETS,_SORT_PRESETS,_BOOKMARK_PRESETS)
from ..state import GalleryState
from .ranking_view import RankingView,_ViewServices,_RankingGallery

class PersonalRankingView(RankingView):
    def __init__(self,services,*,card_size='large',on_size_change=None):
        self.services=services;self.proxy=_ViewServices(services);self.vault=getattr(services,'external_vault',None)
        self.source=PersonalRankingSource(self.vault) if self.vault else None
        self.active=False;self.closed=False;self.busy=False;self.epoch=0;self._open_generation=0;self.page=None;self.pin=None
        # This subclass bypasses RankingView.__init__ but inherits its lifetime cleanup.
        self._condition_task=None;self._condition_pending=False
        self._calendar_token=None;self.calendar_dialog=None;self.calendar_picker=None
        self.client=ui.context.client
        self._context_snapshot={};self._context_request=0;self._context_task=None
        saved=self.vault.ranking_preferences() if self.vault else {}
        self.saved_filters=normalize_personal_filters(saved.get('filters'));self.filters=dict(self.saved_filters)
        with ui.column().classes('pv-gallery w-full').props('data-testid=personal-ranking-view') as self.root:
            with ui.row().classes('pv-toolbar'):
                self.tags=ui.textarea('選択タグ（1 行に 1 件・AND 検索）',value='\n'.join(saved.get('tags',[]))).props('outlined dense data-testid=personal-tags')
                self.bookmarks=ui.select(dict(_BOOKMARK_PRESETS),value=self.filters['bookmark_min'],label='ブクマ数下限').props('outlined dense data-testid=personal-bookmarks')
                self.sort=ui.select(dict(_SORT_PRESETS),value=self.filters['sort'],label='並び順').props('outlined dense data-testid=personal-sort')
                self.r18=ui.checkbox('R-18 指定（原 API 未対応）',value=False).props('data-testid=personal-r18');self.r18.disable()
                self.ai=ui.checkbox('AI を除外',value=False).props('data-testid=personal-ai')
                self.fetch_button=ui.button('取得・更新',on_click=self.load).props('flat data-testid=personal-fetch')
            ui.label('借用アカウントの原検索 API は R-18 指定を消費しないため、この検索では R-18 絞り込みを適用しません。').classes('text-caption')
            with ui.expansion('よく効くタグ', value=True).classes('w-full').props(
                    'data-testid=personal-candidates-toggle') as self.candidates:
                self.trend_row=ui.row()
            with ui.expansion('絞り込み（取得済みの結果へ適用）').classes('w-full'):
                with ui.row():
                    self.date=ui.select({k:label for k,label,_ in _DATE_PRESETS},value=self.filters['date_preset'],label='投稿日').props('outlined dense data-testid=personal-date-preset')
                    self.include=ui.textarea('含むタグ（1 件ずつ改行）',value='\n'.join(self.filters['tags_include'])).props('outlined dense data-testid=personal-tags-include')
                    self.exclude=ui.textarea('除外タグ（1 件ずつ改行）',value='\n'.join(self.filters['tags_exclude'])).props('outlined dense data-testid=personal-tags-exclude')
                    self.author_max=ui.number('1 作者の上限（0 は無制限）',value=self.filters['per_author_max'],min=0,precision=0).props('outlined dense data-testid=personal-author-max')
                ui.label('含む・除外タグ、投稿日、作者上限は取得済み結果に即時反映します。ブクマ数と並び順の変更は「取得・更新」で再検索します。').classes('pv-subtitle')
                self.exclusions={}
                with ui.row():
                    for key,label in (('exclude_downloaded','保存済みを除外'),('exclude_hidden_authors','非表示作者を除外'),('exclude_hidden_works','非表示作品を除外'),('exclude_hated','嫌いな作品を除外')):
                        self.exclusions[key]=ui.checkbox(label,value=self.filters[key])
                    ui.button('この条件を保存',on_click=self.save).props('flat data-testid=personal-save-filters')
                    ui.button('条件をクリア',on_click=self.clear_filters).props('flat data-testid=personal-clear-filters')
            self.notice=ui.label('').props('role=status data-testid=personal-notice')
            self.gallery=_RankingGallery(self.proxy,card_size=card_size,on_size_change=on_size_change);self.gallery.owner=self
            # No personal search has occurred yet. Never call the demo gallery
            # an empty search result or leave its unrelated placeholder visible.
            self.gallery.empty.set_visibility(False)
            self.gallery.counter.set_text('未取得')
        for control in (self.tags,self.bookmarks,self.sort,self.r18,self.ai):control.on_value_change(lambda _:self.changed())
        for control in (self.date,self.include,self.exclude,self.author_max,*self.exclusions.values()):control.on_value_change(lambda _:self.local_changed())
        self.client.on_delete(self.close);self.availability()
    def add_tag(self,tag):
        self.tags.set_value('\n'.join(dict.fromkeys((*self.selected_tags(),tag))))
        # set_value emits the same changed() callback as keyboard editing.
        # Do not persist preferences twice for one suggested-tag click.
    def selected_tags(self):return tuple(dict.fromkeys(t.strip() for t in str(self.tags.value or '').splitlines() if t.strip()))
    def _server_key(self):
        filters=self.current_filters()
        return (self.selected_tags(),bool(self.r18.value),bool(self.ai.value),
                filters['bookmark_min'],filters['sort'])
    def _current_page(self):
        return self.page is not None and self.page.key==self._server_key()
    def _search_available(self):
        try:
            status=self.vault.status() if self.vault is not None else {}
        except Exception:
            status={}
        return bool(status.get('open') and status.get('mode')=='nana7mi'
                    and status.get('transport_available'))
    def _show_unfetched(self):
        """Do not present old server results under changed search conditions."""
        self.gallery.close_viewer(restore_focus=False)
        old=self.gallery.state
        state=GalleryState((),page_size=50)
        state.set_card_size(old.card_size)
        state.set_query(old.query)
        state.generation=old.generation+1
        old.close()
        self.gallery.state=self.gallery._viewer_state=state
        self.gallery.render_gallery()
        self.gallery.empty.set_visibility(False)
        self.gallery.more_cell.set_visibility(False)
        self.gallery.counter.set_text('未取得')
    def current_filters(self):
        return normalize_personal_filters(dict(bookmark_min=self.bookmarks.value,sort=self.sort.value,date_preset=self.date.value,
            tags_include=str(self.include.value or '').splitlines(),tags_exclude=str(self.exclude.value or '').splitlines(),per_author_max=self.author_max.value,
            **{k:bool(v.value) for k,v in self.exclusions.items()}))
    def context(self):
        return self._context_snapshot
    def _read_context(self):
        callback=getattr(self.services,'personal_ranking_context',None)
        if callback:return callback()
        context={}
        rec=getattr(self.services,'recommendations',None)
        if rec is not None:
            snapshot=rec.snapshot();context.update(hidden_work_ids=set(snapshot['hidden_works']),hidden_author_ids=set(snapshot['hidden_authors']))
        library=getattr(self.services,'library',None)
        if library is not None:context['downloaded_ids']={w.work_id for w in library.artworks}
        return context
    def _start_context_refresh(self):
        if self._context_task is not None and not self._context_task.done():return
        from nicegui import background_tasks
        task=background_tasks.create(self._refresh_context());self._context_task=task
        def finished(done):
            if self._context_task is not done:return
            self._context_task=None
            if done.cancelled() or done.exception() is not None:return
            if done.result() is None and self.active and not self.closed:self._start_context_refresh()
        task.add_done_callback(finished)
    async def _refresh_context(self):
        self._context_request+=1;request=self._context_request;epoch=self.epoch
        try:context=await asyncio.to_thread(self._read_context)
        except Exception:context={}
        if self.closed or not self.active or self.epoch!=epoch or request!=self._context_request:return None
        self._context_snapshot=context
        self.trend_row.clear()
        with self.trend_row:
            for candidate in context.get('trend_rows',())[:24]:
                if not isinstance(candidate,dict):continue
                tag=candidate.get('tag');label=candidate.get('display') or candidate.get('name') or tag
                if isinstance(tag,str) and tag.strip():ui.button(str(label),on_click=lambda _=None,t=tag:self.add_tag(t)).props('flat dense')
        self.availability()
        if self.page is not None:self.render()
        return context
    def availability(self,*,update_notice=True):
        try:status=self.vault.status() if self.vault else {}
        except Exception:status={}
        enabled=bool(status.get('open') and status.get('mode')=='nana7mi'
                     and status.get('transport_available'))
        has_tags=bool(self.selected_tags())
        usable=enabled and has_tags and self.active and not self.closed and not self.busy
        self.fetch_button.set_enabled(usable)
        # When exhausted the original gallery hides the entire more-cell.
        # Retain its existing enabled state for a future replacement page;
        # forged append is still rejected by load()'s exhausted guard.
        self.gallery.more_button.set_enabled(usable and self._current_page())
        if update_notice:
            if not enabled:
                self.notice.set_text('外部連携で保管庫を解除し、借用アカウントを更新してください。' if status.get('crypto_available') else '借用アカウント検索は未設定、または暗号化依存が利用できません。')
            elif not has_tags:
                self.notice.set_text('タグを選び、取得してください。')
            elif self.page is None:
                self.notice.set_text('取得で検索します。まだ結果はありません。')
        context=self.context()
        for key,context_key in (('exclude_hated','hated_ids'),('exclude_downloaded','downloaded_ids'),('exclude_hidden_authors','hidden_author_ids'),('exclude_hidden_works','hidden_work_ids')):
            self.exclusions[key].set_enabled(context_key in context)
    def changed(self):
        self.epoch+=1
        if self.page is not None and not self._current_page():self._show_unfetched()
        self.availability(update_notice=False)
        if self.vault is not None:
            try:self.vault.save_ranking_preferences(self.selected_tags(),self.saved_filters)
            except Exception:self.notice.set_text('タグ選択を保存できませんでした。');return
        self.notice.set_text('検索条件が変わりました。「取得・更新」で新しい条件を反映します。' if self.selected_tags() else 'タグを選択してください。')
    def local_changed(self):
        if self.page is not None:self.render()
        elif not self.closed:self.notice.set_text('取得済み結果がありません。タグを選んで取得してください。')
    def clear_filters(self):
        defaults=normalize_personal_filters({})
        self.bookmarks.set_value(defaults['bookmark_min']);self.sort.set_value(defaults['sort'])
        self.date.set_value(defaults['date_preset']);self.include.set_value('');self.exclude.set_value('');self.author_max.set_value(0)
        for key,control in self.exclusions.items():control.set_value(defaults[key])
        self.local_changed()
    def save(self):
        if self.vault is None:return
        try:
            candidate=self.current_filters()
            self.vault.save_ranking_preferences(self.selected_tags(),candidate)
            self.saved_filters=candidate
            self.notice.set_text('現在の条件を次回起動用に保存しました。')
        except Exception:self.notice.set_text('条件を保存できませんでした。')
    async def load(self,*,append=False):
        if not self.active or self.closed or self.busy or self.source is None:return
        if append and (not self._current_page() or self.page.exhausted):return
        epoch=self.epoch;tags=self.selected_tags();filters=self.current_filters()
        if not tags:self.notice.set_text('タグを選択してください。');return
        if not self._search_available():
            self.availability()
            return
        self.busy=True;self.fetch_button.disable();self.gallery.more_button.disable();self.notice.set_text('取得中…')
        provider=None;old_pin,old_page,old_images=self.pin,self.page,self.proxy.images
        try:
            pending=self._context_task
            context=await pending if pending is not None and not pending.done() else await self._refresh_context()
            if context is None:return
            candidate=await asyncio.to_thread(self.source.fetch,tags,filters,r18=bool(self.r18.value),exclude_ai=bool(self.ai.value),
                previous=self.page if append else None,author_counts=context.get('author_counts'))
            if (self.closed or not self.active or epoch!=self.epoch
                    or not self._search_available()):return
            provider=self.services.acquire_ranking_images(tuple(r['item'].record for r in candidate.rows))
            self.pin=provider;self.proxy.images=provider;self.page=candidate
            if not append:
                self.gallery.grid.clear();self.gallery.more_cell=None;self.gallery.cards.clear();self.gallery.images.clear();self.gallery.ranks.clear();self.gallery._rendered_ids=()
            self.render()
            if old_pin is not None:old_pin.close()
            provider=None
        except Exception:
            if not self.closed and self.active and epoch==self.epoch:self.notice.set_text('検索を完了できませんでした。外部連携の状態を確認し、再試行してください。')
        finally:
            if provider is not None:
                self.pin,self.page,self.proxy.images=old_pin,old_page,old_images;provider.close()
            self.busy=False
            if not self.closed:self.availability(update_notice=False)
    def render(self):
        if self.closed or self.page is None:return
        if not self._current_page():
            self._show_unfetched()
            self.notice.set_text('検索条件が変わりました。「取得・更新」で新しい条件を反映します。')
            self.availability(update_notice=False)
            return
        context=self.context();filters=self.current_filters()
        rows,dropped=apply_personal_filters(self.page.rows,filters,**{k:context.get(k) for k in ('hidden_work_ids','hidden_author_ids','downloaded_ids','hated_ids')})
        self.gallery.close_viewer(restore_focus=False);old=self.gallery.state
        state=GalleryState(tuple(r['item'].record.as_artwork() for r in rows),page_size=max(50,len(rows)))
        state.set_card_size(old.card_size);state.set_query(old.query);state.generation=old.generation+1;old.close()
        self.gallery.state=self.gallery._viewer_state=state;self.gallery.render_gallery()
        for wid,image in self.gallery.images.items():image.set_source(self.proxy.images.url(wid,0,'thumb'))
        self.gallery.more_cell.set_visibility(not self.page.exhausted)
        self.gallery.empty.set_text('条件に一致する作品は0件です。条件を変更してください。')
        self.notice.set_text(f'借用アカウント・{len(rows)}件表示 / {dropped}件を条件で除外'+('・これで全部' if self.page.exhausted else ''))
        self.availability(update_notice=False)
    def set_active(self,active):
        super().set_active(active)
        # A hidden tab may still receive forged or late events; keep visible
        # button state aligned with the existing load() lifetime guard.
        self.availability(update_notice=self.active)
        if self.active:
            self._start_context_refresh()
