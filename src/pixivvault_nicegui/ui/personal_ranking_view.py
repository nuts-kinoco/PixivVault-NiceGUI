"""Original personal ranking operations over the isolated vault search adapter."""
import asyncio
from pathlib import Path
from .manual_toolbar import manual_toolbar
from .navigation_labels import PERSONAL_RANKING_LABEL

from nicegui import ui
from ..personal_ranking import (PersonalRankingSource,normalize_personal_filters,apply_personal_filters,
    active_filter_descriptors,_DATE_PRESETS,_SORT_PRESETS,_BOOKMARK_PRESETS)
from ..external_vault import RANKING_CANDIDATES_OPEN_KEY
from .action_controls import refresh_button
from .library_filter_panel import LibraryFilterPanel
from .ranking_view import RankingView,_ViewServices,_RankingGallery

# 除外条件のキーと、文脈（読み込んだ集合）のキー。集合が無い条件は適用できない（元 ranking_tab.py:1436-1456）。
EXCLUSION_CONTEXT=(('exclude_hated','hated_ids'),('exclude_downloaded','downloaded_ids'),('exclude_hidden_authors','hidden_author_ids'),('exclude_hidden_works','hidden_work_ids'))
_CANDIDATE_TAGS = Path(__file__).with_name('candidate_tags.js').read_text(encoding='utf-8')
EXCLUSION_WARNING='　⚠️一部の除外条件を適用できませんでした'

class PersonalRankingView(RankingView):
    def preferred_tags_in(self,tags):return False  # 青ハート印は世間ランキングだけ（元 ranking_tab.py:822）
    def _schedule_first_load(self):pass  # original auto-fetch is only the public ranking tab
    def __init__(self,services,*,card_size='large',on_size_change=None):
        self.services=services;self.proxy=_ViewServices(services);self.vault=getattr(services,'external_vault',None)
        from ..premium_read import PremiumSearch
        reader=getattr(services,'live_client',None)
        self.source=PersonalRankingSource(PremiumSearch(self.vault,reader,preserve_expiry=True)) if self.vault or reader else None
        self.active=False;self.closed=False;self.busy=False;self.epoch=0;self._open_generation=0;self.page=None;self.pin=None
        self._image_records = {}
        self._image_reader = None
        self._failed_vault_epoch=None
        self._search_failure_notice = None
        # This subclass bypasses RankingView.__init__ but inherits its lifetime cleanup.
        self._condition_task=None;self._condition_pending=False
        self._calendar_token=None;self.calendar_dialog=None;self.calendar_picker=None
        self.client=ui.context.client
        if not getattr(self.client, '_pv_candidate_tags', False):
            self.client._pv_candidate_tags = True
            if self.client.has_socket_connection:
                self.client.run_javascript(_CANDIDATE_TAGS)
            else:
                ui.add_head_html(f'<script>{_CANDIDATE_TAGS}</script>')

        self._context_snapshot = {}
        self._context_base = {}
        self._context_revision = None
        self._context_request = 0
        self._context_task = None
        self._context_refresh_pending = False
        saved=self.vault.ranking_preferences() if self.vault else {}
        self.saved_filters=normalize_personal_filters(saved.get('filters'));self.filters=dict(self.saved_filters)
        self._selected_tags = tuple(saved.get('tags', ()))
        with ui.column().classes('pv-gallery pv-ranking-view pv-personal-ranking w-full').props('data-testid=personal-ranking-view') as self.root:
            with ui.row().classes('pv-nav-fixed-tools w-full') as fixed:
                ui.label(PERSONAL_RANKING_LABEL).classes('pv-library-heading pv-serif-heading')
            with ui.row().classes('pv-toolbar pv-tone-toolbar') as tools:
                with ui.row().classes('pv-personal-tag-entry items-center no-wrap gap-1') as tag_entry:
                    self.tag_chips = ui.row().classes('items-center no-wrap gap-1')
                    self.tags = ui.input('選択タグ', placeholder='タグを追加してEnter（AND 検索）').classes('pv-personal-tag-input').props(
                        'outlined dense data-testid=personal-tags aria-label="追加するタグ"')
                    self.tags.on('keydown.enter', self._submit_tag, js_handler=(
                        '(event) => { if (!event.isComposing && event.keyCode !== 229) emit(event.target.value); }'))
                self._render_selected_tags()
                # 元 ranking_tab.py:2135: 既定 ON。借用検索が使えるときだけ操作できる（availability）。
                # 元の借用検索は R-18 を API にも結果の絞り込みにも使わないので、切替で結果は変わらない。
                self.r18=ui.switch('R-18',value=True).classes('pv-filter-switch pv-focus-ring').props('data-testid=personal-r18');self.r18.tooltip('R-18を含める')
                self.ai=ui.switch('AI を除外',value=False).classes('pv-filter-switch pv-focus-ring').props('data-testid=personal-ai')
                self.fetch_button=refresh_button(self.refresh,label='取得・更新',testid='personal-fetch')
                self.fetch_button.set_text('取得・更新').props('no-caps')
                self.fetch_button.props['aria-label'] = '取得・更新'
                self.filter_toggle=ui.button('絞り込み',icon='filter_list',on_click=self._toggle_filters).props(
                    'flat dense no-caps aria-expanded=false data-testid=personal-filter-toggle')
                self._build_hide_manager()
            # 副行（元 ranking_tab.py:2142-2166）。隠すのはラベルとチップだけで、トグルは常に出す（戻す導線を残す）。
            # 開閉は設定 ranking_personal_candidates_open へ保存する。既定は開き。
            self._candidates_open=saved.get(RANKING_CANDIDATES_OPEN_KEY,True) is not False
            self.fetch_button.move(fixed)
            self.tag_chips.move(fixed)
            self._candidate_count = 0
            with ui.row().classes('pv-personal-candidates items-center no-wrap w-full gap-2').props('data-testid=personal-candidates-row') as candidates:
                self.candidates_label=ui.label('よく効くタグ:').classes('pv-subtitle').props('data-testid=personal-candidates-label')
                with ui.element('div').classes('pv-candidates-box').props('data-testid=personal-candidates-box') as self.candidates_box:
                    self.trend_row = ui.row().classes('pv-candidate-row no-wrap items-center gap-1')
                    with ui.element('button').classes('pv-candidate-more pv-hit28 pv-focus-ring').props(
                            'type=button hidden aria-expanded=false data-testid=personal-candidates-more') as self.candidates_more:
                        ui.label('').classes('pv-candidate-count')
                    self.candidates_menu = ui.column().classes('pv-candidate-menu').props(
                        'hidden popover=manual role=group aria-label=ほかのよく効くタグ data-testid=personal-candidates-menu')
                    self.candidates_more.props['aria-controls'] = self.candidates_menu.html_id
            self.candidates_toolbar, self.candidates_toggle, panel = manual_toolbar(
                self.root, 'personal', (*tools.default_slot.children, candidates), opened=self._candidates_open,
                index=1, title='絞り込み')
            tools.delete()
            self.candidates_toggle.props['data-testid'] = 'personal-candidates-toggle'
            self.candidates_closed_label, self.candidates_open_label = self.candidates_toggle.default_slot.children
            self.candidates_toolbar.on('toggle', self._candidate_toolbar_changed,
                                       js_handler='event => emit(event.target.open)')
            self._apply_candidates_open()
            # 有効条件(NOW)チップ行（元 ranking_tab.py:2315-2356・filter_panel.py:350-379）。
            self.chips_row=ui.row().classes('items-center gap-2 w-full').props('data-testid=personal-chips')
            with ui.row().classes('pv-personal-status items-center gap-2 w-full'):
                self.notice=ui.label('').classes('pv-subtitle').props('role=status data-testid=personal-notice')
                self._build_hide_status()
            with ui.row().classes('pv-library-body pv-personal-body w-full no-wrap'):
                with LibraryFilterPanel.container(testid='personal-filter-panel',label=f'{PERSONAL_RANKING_LABEL}の絞り込み') as self.filter_panel:
                    ui.label('絞り込み').classes('pv-library-heading pv-serif-heading')
                    self.bookmarks=ui.select(dict(_BOOKMARK_PRESETS),value=self.filters['bookmark_min'],label='ブクマ数下限').props('outlined dense data-testid=personal-bookmarks')
                    self.sort=ui.select(dict(_SORT_PRESETS),value=self.filters['sort'],label='並び順').props('outlined dense data-testid=personal-sort')
                    self.start_date=ui.input('検索開始日 YYYY-MM-DD').props('outlined dense data-testid=personal-start-date')
                    self.end_date=ui.input('検索終了日 YYYY-MM-DD').props('outlined dense data-testid=personal-end-date')
                    self.date=ui.select({k:label for k,label,_ in _DATE_PRESETS},value=self.filters['date_preset'],label='投稿日').props('outlined dense data-testid=personal-date-preset')
                    self.include=ui.textarea('含むタグ（1 件ずつ改行）',value='\n'.join(self.filters['tags_include'])).props('outlined dense data-testid=personal-tags-include')
                    self.exclude=ui.textarea('除外タグ（1 件ずつ改行）',value='\n'.join(self.filters['tags_exclude'])).props('outlined dense data-testid=personal-tags-exclude')
                    self.author_max=ui.number('1 作者の上限（0 は無制限）',value=self.filters['per_author_max'],min=0,precision=0).props('outlined dense data-testid=personal-author-max')
                    ui.label('タグ・投稿日・作者上限は取得済み結果に反映します。ブクマ数と並び順を変えると再検索します。').classes('pv-subtitle')
                    self.exclusions={}
                    for key,label in (('exclude_downloaded','保存済みを除外'),('exclude_hidden_authors','非表示作者を除外'),('exclude_hidden_works','非表示作品を除外'),('exclude_hated','嫌いな作品を除外')):
                        testid = 'personal-' + key.replace('_', '-')
                        self.exclusions[key]=ui.checkbox(label,value=self.filters[key]).props(f'dense data-testid={testid}')
                    with ui.row().classes('gap-1'):
                        ui.button('この条件を保存',on_click=self.save).props('outline dense no-caps data-testid=personal-save-filters')
                        ui.button('条件をクリア',on_click=self.clear_filters).props('flat dense no-caps data-testid=personal-clear-filters')
                self.filter_panel.set_visibility(False)
                self.filter_toggle.props['aria-controls'] = self.filter_panel.html_id
                self.gallery=_RankingGallery(self.proxy,card_size=card_size,on_size_change=on_size_change);self.gallery.owner=self
                for control in tuple(self.gallery.toolbar.default_slot.children):
                    control.move(panel)
                self.gallery.toolbar.delete()
                self.gallery.empty.set_text('検索結果はまだありません。')
                self.gallery.empty.set_visibility(True)
                self.gallery.counter.set_text('未取得')
        for control in (self.start_date,self.end_date):control.on_value_change(lambda _:self.changed())
        # 元 ranking_tab.py:1935-1947, 2012-2022, 2036-2038: サーバー側条件（ブクマ数下限・並び順・R-18）と条件クリアは再検索する。
        for control in (self.bookmarks,self.sort,self.r18,self.ai):control.on_value_change(lambda _:self.server_changed())
        for control in (self.date,self.include,self.exclude,self.author_max,*self.exclusions.values()):control.on_value_change(lambda _:self.local_changed())
        self._local_revision_timer=ui.timer(1.0, self._sync_local_context) if getattr(services, 'local_tag_view', None) is not None else None
        self.client.on_delete(self.close);self.availability();self._refresh_chips()

    def _toggle_filters(self):
        if self.closed or not self.active:
            return
        opened = not self.filter_panel.visible
        self.filter_panel.set_visibility(opened)
        self.filter_toggle.props['aria-expanded'] = str(opened).lower()

    def local_tags_changed(self):
        self._context_revision = None
        self._start_context_refresh()

    def _sync_local_context(self):
        local = getattr(self.services, 'local_tag_view', None)
        if self.closed or not self.active or local is None or self._context_revision == local.revision():
            return
        # A passive poll during a read is covered by its revision check.
        if self._context_task is None or self._context_task.done():
            self._start_context_refresh()
    def _apply_candidates_open(self):
        opened=self._candidates_open
        self.candidates_label.set_visibility(opened);self.candidates_box.set_visibility(opened)
        count = self._candidate_count
        self.candidates_closed_label.set_text(f'よく効くタグ（{count}件）' if count else '絞り込みを開く')
        self.candidates_toolbar.props['open'] = opened
        self.candidates_toggle.props['aria-expanded'] = str(opened).lower()

    def _candidate_toolbar_changed(self, event):
        if isinstance(event.args, bool) and event.args != self._candidates_open:
            self.toggle_candidates()

    def toggle_candidates(self):
        self._candidates_open=not self._candidates_open
        if not self._candidates_open and self.filter_panel.visible:
            self._toggle_filters()
        # 保存は画面反映の前（元 ranking_tab.py:1861-1863）。保存に失敗しても、いまの画面は切り替える。
        save=getattr(self.vault,'save_ranking_candidates_open',None)
        if save is not None:
            try:save(self._candidates_open)
            except Exception:
                import logging
                logging.getLogger(__name__).warning('よく効くタグの開閉を保存できませんでした。',exc_info=True)
        self._apply_candidates_open()
    def _render_candidate_tags(self, context):
        self.trend_row.clear()
        self.candidates_menu.clear()
        tags = []
        for candidate in context.get('trend_rows', ())[:24]:
            if isinstance(candidate, dict):
                tag = candidate.get('tag')
                if isinstance(tag, str) and tag.strip():
                    tags.append((tag, candidate.get('display') or candidate.get('name') or tag))
        self._candidate_count = len(tags)
        for host in (self.trend_row, self.candidates_menu):
            with host:
                for index, (tag, label) in enumerate(tags):
                    ui.button(str(label), color=None, on_click=lambda _, tag=tag: self.add_tag(tag)).classes(
                        'pv-hit28 pv-focus-ring').props(f'flat dense no-caps data-candidate-index={index}')
        self._apply_candidates_open()

    def _submit_tag(self, event):
        if self.closed or not self.tags.enabled or not isinstance(event.args, str):
            return
        self.add_tag(event.args)
        self.tags.set_value('')

    def add_tag(self, tag):
        tag = str(tag or '').strip()
        if tag and tag not in self._selected_tags:
            self._commit_tags((*self._selected_tags, tag))

    def remove_tag(self, tag):
        if tag in self._selected_tags:
            self._commit_tags(tuple(value for value in self._selected_tags if value != tag))

    def open_with_tag(self, tag):
        """Preselect an explicitly opened search without starting a request."""
        self._condition_pending = False
        self.tags.set_value('')
        self._selected_tags = (tag,)
        self._render_selected_tags()
        self.changed()

    def _commit_tags(self, tags):
        self._selected_tags = tags
        self._render_selected_tags()
        self.server_changed()

    def _render_selected_tags(self):
        self.tag_chips.clear()
        with self.tag_chips:
            for tag in self._selected_tags:
                with ui.row().classes('pv-chip items-center no-wrap gap-1'):
                    ui.label(tag)
                    button = ui.button(icon='close', on_click=lambda _, value=tag: self.remove_tag(value))
                    button.props('flat dense round size=xs data-testid=personal-remove-tag')
                    button.classes('pv-hit28')
                    button._props['aria-label'] = f'{tag} を解除'

    def selected_tags(self):
        return self._selected_tags
    def _server_dates(self):
        return tuple(str(getattr(getattr(self,name,None),'value','') or '').strip() for name in ('start_date','end_date'))
    def _server_key(self):
        filters=self.current_filters()
        return (self.selected_tags(),bool(self.r18.value),bool(self.ai.value),
                filters['bookmark_min'],filters['sort'])+((self._server_dates(),) if any(self._server_dates()) else ())
    def _current_page(self):
        return self.page is not None and self.page.key==self._server_key()
    def _search_available(self):
        if self._failed_vault_epoch is not None:return False
        if self.source is not None and hasattr(self.source.vault,'available'):
            return self.source.vault.available()
        try:
            status=self.vault.status() if self.vault is not None else {}
        except Exception:
            status={}
        return bool(status.get('open') and status.get('mode')=='nana7mi'
                    and status.get('transport_available'))
    def _show_unfetched(self):
        """Reset the display when there is no usable search context."""
        self.gallery.replace_artworks(())
        self.gallery.empty.set_text('検索結果はまだありません。')
        self.gallery.empty.set_visibility(True)
        self.gallery.more_cell.set_visibility(False)
        self.gallery.counter.set_text('未取得')
    def current_filters(self):
        return normalize_personal_filters(dict(bookmark_min=self.bookmarks.value,sort=self.sort.value,date_preset=self.date.value,
            tags_include=str(self.include.value or '').splitlines(),tags_exclude=str(self.exclude.value or '').splitlines(),per_author_max=self.author_max.value,
            **{k:bool(v.value) for k,v in self.exclusions.items()}))
    def context(self):
        local = getattr(self.services, 'local_tag_view', None) or getattr(getattr(self.services, 'discovery', None), 'local_tag_view', None)
        if local is not None and self._context_revision != local.revision():
            return self._context_base
        return self._context_snapshot
    def _after_hidden_change(self):
        # 除外集合は文脈（保存済みの読み込み）から来る。読み直してから描き直す。
        self._refresh_hide_ui()
        self._start_context_refresh()
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
        if self.closed or not self.active:
            return None
        self._context_refresh_pending = True
        if self._context_task is None or self._context_task.done():
            from nicegui import background_tasks
            self._context_task = background_tasks.create(self._drain_context_refresh())
        return self._context_task

    async def _drain_context_refresh(self):
        while self.active and not self.closed:
            self._context_refresh_pending = False
            context = await self._refresh_context()
            if context is not None and not self._context_refresh_pending:
                return context
        return None

    async def _refresh_context(self):
        self._context_request+=1;request=self._context_request;epoch=self.epoch
        local = getattr(self.services, 'local_tag_view', None) or getattr(getattr(self.services, 'discovery', None), 'local_tag_view', None)
        base_reader = getattr(self.services, 'personal_ranking_context_base', None)
        try:
            before = local.revision() if local is not None else None
            base = await asyncio.to_thread(base_reader) if local is not None and callable(base_reader) else None
            context = await asyncio.to_thread(self._read_context)
            after = local.revision() if local is not None else None
            if before != after:
                return None
            revision = after
        except Exception:
            context, base, revision = {}, {}, None
        if (self.closed or not self.active or self.epoch != epoch
                or request != self._context_request or self._context_refresh_pending):
            return None
        self._context_base = context if base is None else base
        self._context_revision = revision
        self._context_snapshot=context
        context=self.context()
        self._render_candidate_tags(context)
        self.availability()
        if self.page is not None:self.render()
        if self.busy:self.notice.set_text('取得中…')
        return context
    @staticmethod
    def _missing_exclusions(filters,context):
        return any(filters.get(key) and context_key not in context for key,context_key in EXCLUSION_CONTEXT)
    def availability(self,*,update_notice=True):
        try:status=self.vault.status() if self.vault else {}
        except Exception:status={}
        if self._failed_vault_epoch is not None and status.get('auto_unlock_epoch')!=self._failed_vault_epoch:
            self._failed_vault_epoch=None
        enabled=bool(status.get('open') and status.get('mode')=='nana7mi'
                     and status.get('transport_available') and self._failed_vault_epoch is None)
        self.tags.set_enabled(self._failed_vault_epoch is None)
        self.r18.set_enabled(enabled)  # 元 ranking_tab.py:2062: 借用検索が使えるときだけ操作できる
        has_tags=bool(self.selected_tags())
        usable=enabled and has_tags and self.active and not self.closed and not self.busy
        self.fetch_button.set_enabled(usable)
        # When exhausted the original gallery hides the entire more-cell.
        # Retain its existing enabled state for a future replacement page;
        # forged append is still rejected by load()'s exhausted guard.
        self.gallery.more_button.set_enabled(usable and self._current_page() and self._search_failure_notice is None)
        if update_notice:
            if self._search_failure_notice is not None:
                self._render_search_failure()
            elif not enabled:
                self.notice.set_text('借用プレミアムをONにして、借用アカウントを更新してください。' if status.get('crypto_available') else '借用アカウント検索は未設定、または暗号化依存が利用できません。')
            elif not has_tags:
                self.notice.set_text('タグを選び、取得してください。')
            elif self.page is None:
                self.notice.set_text('取得で検索します。まだ結果はありません。')
        self.notice.props.set_bool('data-error', self._search_failure_notice is not None or (
            self.page is not None and bool(self.page.remote_error) and self._current_page()))
        context=self.context()
        for key,context_key in EXCLUSION_CONTEXT:
            self.exclusions[key].set_enabled(context_key in context)
    def changed(self):
        self.epoch+=1;self._refresh_chips()
        self._search_failure_notice = None
        if self.page is not None:
            self.render()
        self.availability(update_notice=False)
        if self.vault is not None:
            try:self.vault.save_ranking_preferences(self.selected_tags(),self.saved_filters)
            except Exception:self.notice.set_text('タグ選択を保存できませんでした。');return
        self.notice.set_text('検索条件が変わりました。「取得・更新」で新しい条件を反映します。' if self.selected_tags() else 'タグを選択してください。')
    def server_changed(self):
        self.changed();self._research()
    def _research(self):
        """サーバー側条件が変わったら、取得ボタンと同じ load() で検索し直す（元 _ensure_personal_loaded）。

        タグが無い・借用検索が使えない（取得停止中など）ときは通信しない。連続した変更は
        継承した _load_changed_conditions が 1 本にまとめる。load() の中で取得停止ゲートを通る。
        """
        if self.closed or not self.active or not self.selected_tags() or not self._search_available():return
        self._condition_pending=True
        self.notice.set_text('条件が変わりました。最新の一覧を取得します。')
        if self._condition_task is None or self._condition_task.done():
            try:self._condition_task=asyncio.get_running_loop().create_task(self._load_changed_conditions())
            except RuntimeError:self._condition_pending=False
    def _refresh_chips(self):
        row=self.chips_row;row.clear()
        found=active_filter_descriptors(self.current_filters())
        self.filter_toggle.set_text(f'絞り込み ({len(found)})' if found else '絞り込み')
        with row:
            for key,label in found:
                with ui.element('div').classes('pv-chip pv-focus-ring').props('role=button tabindex=0') as chip:
                    ui.label(label);ui.label('✕').classes('pv-chip-x')
                chip._props['data-chip-key']=key;chip._props['aria-label']=f'{label} を解除'
                chip.on('click',lambda _,k=key:self.remove_condition(k));chip.on('keydown.enter',lambda _,k=key:self.remove_condition(k))
            if found:ui.button('すべて解除',on_click=self.clear_filters).props('flat dense no-caps data-testid=personal-chips-clear')
        row.set_visibility(bool(found))
    def remove_condition(self,key):
        """チップ 1 つぶんの条件だけを既定へ戻す（元 ranking_tab.py:2323-2354）。"""
        if self.closed:return
        if key=='bookmark_min':self.bookmarks.set_value(0)
        elif key=='date_preset':self.date.set_value('all')
        elif key=='per_author_max':self.author_max.set_value(0)
        elif key.startswith(('tags_include:','tags_exclude:')):
            which,tag=key.split(':',1);control=self.include if which=='tags_include' else self.exclude
            control.set_value('\n'.join(t for t in str(control.value or '').splitlines() if t.strip() and t.strip()!=tag))
        elif key in self.exclusions:self.exclusions[key].set_value(False)
    def local_changed(self):
        self._refresh_chips()
        if self.page is not None:self.render()
        elif not self.closed:self.notice.set_text('取得済み結果がありません。タグを選んで取得してください。')
    def clear_filters(self):
        defaults=normalize_personal_filters({})
        self.bookmarks.set_value(defaults['bookmark_min']);self.sort.set_value(defaults['sort'])
        self.date.set_value(defaults['date_preset']);self.include.set_value('');self.exclude.set_value('');self.author_max.set_value(0)
        for key,control in self.exclusions.items():control.set_value(defaults[key])
        self.local_changed()
        self._research()  # 元 ranking_tab.py:2022: ブクマ数下限も既定へ戻るので再検索が要る
    def save(self):
        if self.vault is None:return
        try:
            candidate=self.current_filters()
            self.vault.save_ranking_preferences(self.selected_tags(),candidate)
            self.saved_filters=candidate
            self.notice.set_text('現在の条件を次回起動用に保存しました。')
        except Exception:self.notice.set_text('条件を保存できませんでした。')
    async def refresh(self):
        # The original personal search replaces its card cache on explicit refresh.
        await self.load(retry_thumbnails=True)

    async def load(self,*,append=False,retry_thumbnails=False):
        if not self.active or self.closed or self.busy or self.source is None:return
        if append and (not self._current_page() or self.page.exhausted or self._search_failure_notice is not None):return
        epoch=self.epoch;tags=self.selected_tags();filters=self.current_filters()
        if not tags:self.notice.set_text('タグを選択してください。');return
        if not self._search_available():
            self.availability()
            return
        self._condition_pending = False  # An explicit fetch satisfies the same queued conditions.
        self._search_failure_notice = None
        r18, exclude_ai = bool(self.r18.value), bool(self.ai.value)
        dates = {key: value for key, value in zip(('start_date','end_date'), self._server_dates()) if value}
        self.busy=True;self.fetch_button.disable();self.gallery.more_button.disable();self.notice.set_text('取得中…')
        provider=None;old_pin,old_page,old_images=self.pin,self.page,self.proxy.images
        published = False
        try:
            pending = self._context_task
            if pending is None or pending.done():
                pending = self._start_context_refresh()
            context = await asyncio.shield(pending)
            if context is None or self.closed or not self.active or epoch != self.epoch:
                return
            if not self._search_available():
                self._show_search_failure('検索中に借用アカウント検索を利用できなくなりました。接続状態を確認してください。')
                return
            candidate=await asyncio.to_thread(self.source.fetch,tags,filters,r18=r18,exclude_ai=exclude_ai,
                previous=self.page if append else None,author_counts=context.get('author_counts'), **dates)
            if self.closed or not self.active or epoch != self.epoch:
                return
            available = self._search_available()
            if candidate.remote_error and (not available or (old_page is not None and not old_page.remote_error)):
                from ..premium_read import unavailable_message
                message = unavailable_message(candidate.remote_error) or '通信障害のため検索を完了できませんでした。'
                if candidate.source_warning:
                    message = unavailable_message(candidate.source_warning) + ' ' + message
                self._show_search_failure(message)
                return
            if not available:
                self._show_search_failure('検索中に借用アカウント検索を利用できなくなりました。接続状態を確認してください。')
                return
            records = {r['item'].record.work_id: r['item'].record for r in candidate.rows}
            provider, reader = self._ranking_image_candidate(records)
            if provider is not None:
                self.pin = self.proxy.images = provider
            self.page = candidate
            self.render()
            if retry_thumbnails:
                self.gallery.grid.retry_failed_images()
            if provider is not None and old_pin is not None:
                old_pin.close()
            self._image_records, self._image_reader = records, reader
            provider = None
            published = True
        except Exception as error:
            from ..premium_read import unavailable_message
            if not self.closed and self.active and epoch==self.epoch:
                code=getattr(error,'code',None)
                import logging
                logging.getLogger(__name__).warning('personal search failed: %s code=%s reason=%s',type(error).__name__,code,getattr(error,'reason',None))
                if code in ('invalid-grant','token-unavailable') and self.vault is not None:
                    self._failed_vault_epoch=self.vault.status().get('auto_unlock_epoch',0)
                    self.notice.set_text('借用アカウントを利用できません。借用プレミアムを一度OFFにしてから再度ONにし、アカウントを確認してください。')
                else:
                    message=unavailable_message(code,reason=getattr(error,'reason',None))
                    if message is None:
                        message='\u691c\u7d22\u3092\u5b8c\u4e86\u3067\u304d\u307e\u305b\u3093\u3067\u3057\u305f\u3002\u5916\u90e8\u9023\u643a\u306e\u72b6\u614b\u3092\u78ba\u8a8d\u3057\u3001\u518d\u8a66\u884c\u3057\u3066\u304f\u3060\u3055\u3044\u3002'
                        # Display only fixed error codes, never arbitrary exception data.
                        if code in {'search-failed','transport-failed','invalid-response','stale-session',
                                    'closed','keeper-stopped','transport-fence-unavailable',
                                    'socket-shutdown-unverified','generation-conflict','vault-persistence-failed',
                                    'pixiv-network-busy','fallback-page-unavailable','invalid-search-result',
                                    'search-page-metadata-unavailable','refresh-failed'}:
                            safe_code=code
                        else:safe_code='unknown' if code else None
                        if safe_code:message+=f'\uff08\u7406\u7531: {safe_code}\uff09'
                    self.notice.set_text(message)
                if getattr(error,'warning',None)=='expired-payout':
                    self.notice.set_text(unavailable_message('expired-payout')+' '+self.notice.text)
                self._show_search_failure(self.notice.text)
        finally:
            if provider is not None:
                self.pin,self.proxy.images=old_pin,old_images;provider.close()
            if not published:
                self.page = old_page
            self.busy=False
            if not self.closed:self.availability(update_notice=False)
    def _show_search_failure(self, message):
        self._search_failure_notice = message
        self._render_search_failure()

    def _render_search_failure(self):
        message = self._search_failure_notice
        if self.page is not None and not self.page.remote_error:
            message += ' 直前の検索結果を表示しています。'
            self.gallery.counter.set_text('前の検索結果')
            self.gallery.more_cell.set_visibility(False)
        self.notice.set_text(message)

    def render(self):
        if self.closed or self.page is None:return
        current = self._current_page()
        if not self.selected_tags() or (not current and self.page.remote_error):
            self._show_unfetched()
            self.availability(update_notice=False)
            return
        context=self.context();filters=self.current_filters()
        rows,dropped=apply_personal_filters(self.page.rows,filters,**{k:context.get(k) for k in ('hidden_work_ids','hidden_author_ids','downloaded_ids','hated_ids')})
        self.gallery.replace_artworks(tuple(r['item'].record.as_artwork() for r in rows))
        self.gallery.more_cell.set_visibility(current and not self.page.exhausted)
        self.gallery.empty.set_text('条件に一致する作品は0件です。条件を変更してください。')
        self.notice.set_text(f'{self.page.order_label or "借用アカウント"}・{len(rows)}件表示 / {dropped}件を条件で除外'+('・これで全部' if self.page.exhausted else ''))
        from ..premium_read import unavailable_message
        if self.page.remote_error:
            self.notice.set_text((unavailable_message(self.page.remote_error) or 'pixivの検索結果を取得できませんでした。')
                +' pixiv側は0件として扱います。ローカルライブラリは引き続き利用できます。')
        if self.page.source_warning:
            self.notice.set_text(unavailable_message(self.page.source_warning)+' '+self.notice.text)
        # 元 ranking_tab.py:1640-1642: ON の除外条件の集合が読めていないときは、読めた分だけ適用して警告を添える。
        if self._missing_exclusions(filters,context):self.notice.set_text(self.notice.text+EXCLUSION_WARNING)
        if not current:
            self.gallery.counter.set_text('前の検索結果')
            self.notice.set_text('検索条件が変わりました。「取得・更新」で新しい条件を反映します。')
        if self._search_failure_notice is not None:
            self._render_search_failure()
        self.availability(update_notice=False)
    def set_active(self,active):
        super().set_active(active)
        # A hidden tab may still receive forged or late events; keep visible
        # button state aligned with the existing load() lifetime guard.
        self.availability(update_notice=self.active)
        if self.active:
            self._start_context_refresh()
