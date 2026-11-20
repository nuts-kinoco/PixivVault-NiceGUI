"""Recommendation shelves with explicit refresh, paging and sidecar preferences."""
from __future__ import annotations
import asyncio
from uuid import uuid4
from nicegui import background_tasks, core, ui
from ..recommendation_state import LABELS, ORDER, RecommendationWork
from ..recommendation_media import RecommendationMediaSnapshot
from ..viewer_metadata import pixiv_url
from .card_size_control import size_buttons, update_size_buttons
from .grid_layout import MAX_COLUMNS
from .recommendation_viewer import RecommendationViewer
from .card_actions import CardActions
from .card_context_menu import MenuEntry, build_context_menu, open_from_keyboard

_TREND_TIERS=((16,2.0),(13.5,1.5),(12,1),(11,1))
_SECTION_DESCRIPTIONS={
    'trend_added':'傾向ピルをクリックして追加したタグの候補',
    'trend':'好みの学習結果に基づく候補',
    'certain':'あなたが好んで見ているタグの作品',
    'boosted':'個別に登録した作者の新着',
    'superlike':'自分が強く反応した作品',
}

class RecommendView:
    def __init__(self,services,*,on_open=None,on_enqueue=None,card_size="large",on_size_change=None,on_navigate_to_learning=None,on_search_tag=None):
        self.services=services
        self._on_search_tag=on_search_tag
        self._on_navigate_to_learning=on_navigate_to_learning
        self.card_size=card_size;self._on_size_change=on_size_change
        self.controller=getattr(services,'recommendations',None)
        self.on_open=on_open;self.on_enqueue=on_enqueue
        self.active=False;self.closed=False;self.busy=False;self.epoch=0
        # Original section expansion uses 6 -> 12 -> 18, independently of
        # explicit subsequent remote page acquisition.
        self.data=None;self.undo=None;self.visible={};self.step=6
        self._certain_buttons={}
        self._pending_hide=None
        self._save_operations={};self._saving=set();self._busy_buttons=[];self._refresh_pending=False
        self._action_bindings=[];self._render_generation=0
        self._media=None;self._selection_media=None;self._open_generation=0;self._opening=None
        self._author_browser=None
        self.client=ui.context.client
        self._card_actions=CardActions(services,lambda text:self.notice.set_text(text),lambda:self.active and not self.closed)
        with ui.column().classes('pv-metadata-view w-full gap-3').props('data-testid=recommend-view data-busy=false') as self.root:
            with ui.row().classes('items-center w-full'):
                ui.label('おすすめ').classes('pv-view-title')
                self.size_buttons=size_buttons(self.change_card_size, prefix='recommend')
                update_size_buttons(self.size_buttons,self.card_size)
                self.refresh_button=ui.button('保存済み結果を再読込',on_click=self.refresh).props('flat data-testid=recommend-refresh')
                self.update_button=ui.button('おすすめを更新',on_click=self.update).props('outline data-testid=recommend-update')
                self.reset_button=ui.button('順序を既定に戻す',on_click=self.reset_order).props('flat data-testid=recommend-order-reset')
            ui.label('初回は保存済み結果を表示します。更新・追加取得は明示操作時のみ行います。').classes('pv-subtitle')
            self.notice=ui.label().props('role=status data-testid=recommend-notice')
            self.busy_notice=ui.label('処理中…').props('role=status data-testid=recommend-busy-status')
            self.busy_notice.set_visibility(False)
            self.undo_button=ui.button('出さないを取り消す',on_click=self.undo_hide).props('flat data-testid=recommend-undo')
            self.undo_button.set_visibility(False)
            self.body=ui.column().classes('w-full gap-4')
            with ui.dialog().props('data-testid=recommend-hide-reason-dialog') as self.hide_dialog:
                with ui.card():
                    ui.label('出さない理由（任意）')
                    ui.label('次回からこの作品を表示しません。理由があれば教えてください。').classes('pv-subtitle')
                    for label,reason in (('ジャンルが好みでない','genre'),
                                         ('絵柄が好みでない','style'),
                                         ('理由を選ばない',None)):
                        ui.button(label,on_click=lambda e,r=reason:self.choose_hide_reason(r)).props('flat')
                    ui.button('キャンセル',on_click=self.hide_dialog.close).props('flat')
        # Like ranking/library, the overlay is a sibling in the shell content;
        # it is never deleted when a shelf's body is re-rendered.
        self._recommend_viewer=RecommendationViewer(services,self.root,
            on_close=self._viewer_closed,on_hidden=self._viewer_hidden,on_author=self._show_author_page)
        self.client.on_delete(self.close)
        self.refresh_button.set_enabled(self.controller is not None)
        self.update_button.disable()
        self.reset_button.disable()
        if self.controller is None:self.notice.set_text('おすすめの保存結果が未接続です。')
    def _lock_rendered_buttons(self):
        # Preserve capability restrictions when restoring after a mutation.
        self._busy_buttons=[(b,b.enabled) for b in self.body.descendants() if isinstance(b,ui.button)]
        for button,_ in self._busy_buttons:button.disable()

    def _set_busy(self,value, *, text='処理中…'):
        self.busy=value
        if self.closed:return
        self.root.props['data-busy']='true' if value else 'false'
        self.root.update()
        self.busy_notice.set_visibility(value)
        if value:self.busy_notice.set_text(text)
        if value:
            self._lock_rendered_buttons()
            self.update_button.disable()
            self.refresh_button.disable()
            self.undo_button.disable()
            self.reset_button.disable()
        elif not self.closed:
            for button,enabled in self._busy_buttons:button.set_enabled(enabled)
            self._busy_buttons=[]
            self.undo_button.set_enabled(self.undo is not None)
            self.refresh_button.set_enabled(self.controller is not None)
            self.update_button.set_enabled(bool(self.data and self.data.get('can_acquire',False)))
            self.reset_button.set_enabled(bool(self.data and self.data.get('writable')
                                               and tuple(self.data['order'])!=ORDER))
        for controls in self._action_bindings:controls.refresh()

    def _finish_operation(self):
        try:self._set_busy(False)
        finally:
            # A tab re-entered during a worker must reload its committed result.
            if self._refresh_pending and self.active and not self.closed:
                self._refresh_pending=False
                background_tasks.create(self.refresh())

    def set_active(self,active):
        active=bool(active) and not self.closed
        if active==self.active:return
        self.active=active;self.epoch+=1
        self._recommend_viewer.set_viewer_active(active)
        if not active:
            if self._author_browser is not None:self._author_browser.hide()
            self._pending_hide=None
            self.hide_dialog.close()
        if self.active and self.controller is not None:
            if self.busy:self._refresh_pending=True
            else:background_tasks.create(self.refresh())

    async def refresh(self, *, preserve_viewer=False):
        if not self.active or self.busy or self.controller is None:return
        epoch=self.epoch
        try:
            self._set_busy(True, text='保存済み結果を読み込んでいます…')
            data=await asyncio.to_thread(self.controller.refresh)
            if self.active and self.epoch==epoch:
                self.data=data;self.render(preserve_viewer=preserve_viewer)
                unavailable=data.get('unavailable', ())
                self.notice.set_text(
                    '保存結果を確認できない項目があります：'
                    + '・'.join(LABELS.get(k,k) for k in unavailable) if unavailable
                    else '保存済み結果を読み込みました。')
        except Exception:
            if self.active and self.epoch==epoch:self.notice.set_text('保存済み結果を確認できません。前の一覧を保持しました。')
        finally:
            self._finish_operation()
    async def _change(self,callback, *, busy_text='処理中…', show_count=False):
        if not self.active or self.busy or self.controller is None:return
        epoch=self.epoch
        try:
            self._set_busy(True, text=busy_text)
            data=await asyncio.to_thread(callback)
            if self.active and self.epoch==epoch:
                self.data=data;self.render()
                message = '一部を取得できませんでした。保存済み結果を保持しています。再操作で再試行できます。' if data.get('errors') else '専用設定に保存しました。'
                if show_count:
                    groups = [group for rows in data['sections'].values() for group in rows]
                    message += f" {'保持中' if data.get('errors') else '表示中'}: {len(groups)}群・{sum(len(group.works) for group in groups)}件。"
                self.notice.set_text(message)
                return True
            return False
        except Exception:
            if self.active and self.epoch==epoch:
                try:
                    data=await asyncio.to_thread(self.controller.snapshot)
                    if self.active and not self.closed and self.epoch==epoch:
                        self.data=data;self.render()
                except Exception:pass
                if self.active and not self.closed and self.epoch==epoch:
                    self.notice.set_text('取得または保存に失敗しました。前の結果とページを保持しています。再読込後に再試行できます。')
            return False
        finally:
            self._finish_operation()
    async def dismiss_superlike(self,work_id,*,generation):
        if (self.closed or not self.active or self.busy or self.data is None
                or generation!=self._render_generation):return
        if not any(w.work_id==work_id for g in self.data['sections'].get('superlike',()) for w in g.works):return
        revision=self.data['revision']
        if await self._change(lambda:self.controller.dismiss_superlike(work_id,revision),
                busy_text='SuperLikeのおすすめから取り下げています…'):
            self.notice.set_text('おすすめから取り下げました。作品の判定は変更していません。')
            if self.data is not None and self.data['revision']!=revision:
                self.undo=('superlike',work_id,self.data['revision'])
                self.undo_button.set_text('SuperLikeの取り下げを取り消す')
                self.undo_button.set_visibility(True);self.undo_button.set_enabled(True)
    async def update(self):
        if self.data is None:return
        revision=self.data['revision']
        if await self._change(lambda:self.controller.update(revision), busy_text='おすすめを読み込んでいます…') and self.data is not None:
            data=self.data
            sections=len([k for k in data['order'] if k not in data['unavailable']])
            works=sum(len(g.works) for rows in data['sections'].values() for g in rows)
            count=f'（{sections}節・{works}作品）。' if works else f'（{sections}節・0作品）。表示できる作品はありません。'
            if data.get('errors'):
                self.notice.set_text(f'おすすめを更新しました{count}一部を取得できませんでした。保存済み結果を保持しています。再操作で再試行できます。')
            else:self.notice.set_text(f'おすすめを更新しました{count}専用設定に保存しました。')
    async def toggle_trend(self,tag,display):
        if self.data is None:return
        revision=self.data['revision']
        exists=any(r['tag']==tag for r in self.data['added_tags'])
        await self._change(lambda:self.controller.remove_trend(tag,revision) if exists else self.controller.add_trend(tag,display,revision), show_count=not exists)
    async def next_page(self,kind,tag):
        if self.data is None:return
        revision=self.data['revision']
        await self._change(lambda:self.controller.next_page(kind,tag,revision), show_count=True)
    async def more_author(self,author_id,count):
        if self.data is None:return
        revision=self.data['revision']
        await self._change(lambda:self.controller.more_author(author_id,count,revision), show_count=True)
        self.visible[('boosted',author_id)]=count;self.render()
    async def boost(self,author_id,name,enabled):
        if self.data is None:return
        revision=self.data['revision']
        await self._change(lambda:self.controller.boost(author_id,name,enabled,revision))
    def ask_hide_reason(self,work_id,*,card_current=None):
        if not self.active or self.closed or self.busy or self.data is None:return
        if not self._has_action_card(work_id,card_current):return
        self._pending_hide=(work_id,self.epoch,card_current)
        self.hide_dialog.open()
    async def choose_hide_reason(self,reason):
        pending=self._pending_hide;self._pending_hide=None
        self.hide_dialog.close()
        if pending is None or not self.active or self.closed or self.epoch!=pending[1]:return
        if pending[2] is not None and not pending[2]():return
        await self.hide('work',pending[0],True,reason=reason)
    async def hide(self,kind,target,hidden=True,*,reason=None):
        if self.data is None:return
        revision=self.data['revision']
        await self._change(lambda:self.controller.hide(kind,target,hidden,revision,reason=reason))
        if self.data is not None and self.data['revision']!=revision and hidden:
            self.undo=(kind,target,self.data['revision'])
            self.undo_button.set_text('出さないを取り消す')
            self.undo_button.set_visibility(True)
            self.undo_button.set_enabled(True)
    async def undo_hide(self):
        if self.undo is None or self.busy or self.closed or not self.active:return
        kind,target,revision=self.undo
        accepted=await self._change(lambda:self.controller.hide(kind,target,False,revision))
        if accepted and self.data is not None and self.data["revision"]!=revision:
            self.undo=None;self.undo_button.set_visibility(False);self.undo_button.set_text('出さないを取り消す')
    async def collapse(self,kind):
        if self.data is None:return
        values=set(self.data['collapsed'])
        values.remove(kind) if kind in values else values.add(kind)
        revision=self.data['revision']
        await self._change(lambda:self.controller.configure('recommend_section_collapsed',sorted(values),revision))
    async def move(self,kind,delta):
        if self.data is None:return
        order=list(self.data['order']);index=order.index(kind);target=index+delta
        if not 0<=target<len(order):return
        order[index],order[target]=order[target],order[index]
        revision=self.data['revision']
        await self._change(lambda:self.controller.configure('recommend_section_order',order,revision))
    async def collapse_certain_group(self,tag):
        if self.data is None:return
        values=set(self.data['certain_group_collapsed'])
        values.remove(tag) if tag in values else values.add(tag)
        revision=self.data['revision']
        if await self._change(lambda:self.controller.configure('recommend_certain_group_collapsed',sorted(values),revision)):
            button=self._certain_buttons.get(tag)
            if button is not None and self.active and not self.closed and self.client.has_socket_connection:
                self.client.run_javascript(f"requestAnimationFrame(() => {{ const n=document.getElementById('{button.html_id}'); if(n?.isConnected && n.getClientRects().length && !n.closest('[inert], [aria-hidden=\"true\"]')) n.focus({{preventScroll:true}}); }});")
    async def reset_order(self):
        """Restore the original five shelf order using the same revisioned settings."""
        if (self.data is None or not self.data.get('writable')
                or tuple(self.data['order'])==ORDER):return
        revision=self.data['revision']
        await self._change(lambda:self.controller.configure('recommend_section_order',list(ORDER),revision))
    def more(self,key):
        if not self.active:return
        self.visible[key]=self.visible.get(key,self.step)+self.step;self.render()
    def fold(self,key):
        if not self.active or self.closed:return
        self.visible.pop(key,None)
        self.render()

    def _invalidate_open(self):
        self._open_generation+=1
        self._opening=None

    def _viewer_closed(self):
        self._invalidate_open()
        media=self._selection_media
        self._selection_media=None
        if media is not None and media is not self._media:media.close()

    def _visible_works(self):
        if self.data is None:return ()
        result=[]
        for kind in self.data['order']:
            if (kind=='trend' or kind in self.data['collapsed']
                    or kind in self.data['unavailable']):continue
            for group in self.data['sections'].get(kind,()):
                if kind=='certain' and group.key in self.data.get('certain_group_collapsed',()):continue
                count=self.visible.get((kind,group.key),self.step)
                result.extend(group.works[:count])
        return tuple(result)

    def open_saved_work(self,work_id,*,generation):
        if (self.closed or not self.active or self.busy or self.on_open is None
                or generation!=self._render_generation
                or not any(w.work_id==work_id for w in self._visible_works())):return
        self.on_open(work_id)

    async def open_author_page(self,work_id,*,generation):
        if (self.closed or not self.active or self.busy
                or generation!=self._render_generation):return
        work=next((w for w in self._visible_works() if w.work_id==work_id),None)
        if work is None or not work.author_id:return
        await self._show_author_page(work.author_id,work.author_name)

    async def _show_author_page(self,author_id,author_name):
        reader=self._recommend_viewer._viewer_reader()
        if reader is None:
            self.notice.set_text('作者ページには読取接続の設定が必要です。');return
        if self._author_browser is not None and self._author_browser.reader is not reader:
            self._author_browser.close();self._author_browser=None
        if self._author_browser is None:
            from .author_browse_view import AuthorBrowseView
            with self.client:
                self._author_browser=AuthorBrowseView(self.services,reader,card_size=self.card_size,
                    build_card_menu=self._author_card_menu)
        await self._author_browser.show(author_id,author_name)

    def _author_card_menu(self,art,card,browser):
        def current():
            return (browser.visible and not browser.closed and browser.reader is self._recommend_viewer._viewer_reader()
                    and browser.gallery._viewer_active and browser.gallery.cards.get(art.work_id) is card)
        work=RecommendationWork(art.work_id,art.title,art.author_id,art.author,tags=art.tags)
        self._card_menu('author-overlay',work,None,card,self.data,card_current=current)

    async def open_work(self,work_id,*,generation):
        if (self.closed or not self.active or self.busy
                or generation!=self._render_generation or self._media is None
                or not any(w.work_id==work_id for w in self._visible_works())):return
        opening=(work_id,generation,self.epoch)
        if self._opening==opening:return
        self._recommend_viewer.close_viewer(restore_focus=False)
        self._opening=opening
        request=self._open_generation
        epoch=self.epoch;media=self._media
        def current():
            return (self.active and not self.closed and epoch==self.epoch
                and generation==self._render_generation and request==self._open_generation
                and self._media is media)
        selection=None
        try:
            resolved=None
            if media.needs_detail(work_id):
                self.notice.set_text('作品のページ情報を確認しています…')
                resolved=await asyncio.to_thread(media.read_detail,work_id)   # original recommend_tab.py:1025: default (background) lane
                if not current():return
            selection=media.select(work_id,resolved)
            if selection.images.needs_metadata(work_id):
                self.notice.set_text('作品のページ情報を確認しています…')
                artwork=await asyncio.to_thread(selection.images.prepare_artwork,work_id)
                if not current():return
                if artwork is None or artwork.work_id!=work_id:
                    raise ValueError('recommendation page identity changed')
                from dataclasses import replace
                selection.artwork=replace(artwork,x_restrict=selection.artwork.x_restrict)
            if not current():return
            media.ensure_source(work_id)
            self._recommend_viewer.show(selection,card_size=self.card_size)
            self._selection_media=media
            selection=None  # Viewer owns this lease until close/replacement.
            self.notice.set_text('')
        except Exception:
            if current():
                self.notice.set_text('作品情報や画像を準備できません。保存済み結果を再読込するか、カードを押して再試行してください。')
        finally:
            if selection is not None:selection.close()
            if self._opening==opening and request==self._open_generation:self._opening=None

    @staticmethod
    def _all_empty(data):
        # 元: trend / superlikes / certain / trend_added / 登録中の作者 がすべて空のときだけ。
        # 取得元が未接続・未更新の節がある間は「材料が無い」とは言えないので出さない。
        return (not data.get('unavailable') and not data.get('trends') and not data.get('added_tags') and not data.get('boosted')
                and not any(g.works for groups in data['sections'].values() for g in groups))

    def render(self, *, preserve_viewer=False):
        if self.data is None or self.closed:return
        if not preserve_viewer:self._recommend_viewer.close_viewer(restore_focus=False)
        old_media=self._media
        try:
            visible=self._visible_works()
            if old_media is None or not old_media.matches(visible):
                self._media=RecommendationMediaSnapshot(self.services,visible)
        except Exception:
            # An unavailable catalog must not authorize remote fallback for an
            # identity whose saved/excluded state could not be inspected.
            self._media=None
        if old_media is not None and old_media is not self._media and old_media is not self._selection_media:
            old_media.close()
        for controls in self._action_bindings:controls.close()
        self._action_bindings.clear()
        self._render_generation+=1
        generation=self._render_generation
        data=self.data;self.body.clear();self._certain_buttons={}
        self.update_button.set_enabled(data.get('can_acquire',False))
        self.reset_button.set_enabled(bool(data.get('writable') and tuple(data['order'])!=ORDER and not self.busy))
        with self.body:
            if not data['writable']:ui.label('おすすめの登録設定は閲覧のみです。専用設定の保存先が未指定です。').classes('pv-subtitle')
            elif not data.get('can_acquire'):ui.label('取得元が未接続です。保存済み結果と登録設定を操作できます。').classes('pv-subtitle')
            if self._all_empty(data):  # render() は読み込み完了後だけ呼ばれる（元の「読込中は出さない」と同じ）
                # 元 recommend_tab.py:2894-2900 empty_all_panel（節は出さずこの案内だけ）。
                with ui.column().classes('gap-2').props('data-testid=recommend-empty-all'):
                    ui.label('おすすめの材料がまだありません').classes('pv-view-title')
                    ui.label('学習タブで好み・嫌いを判定すると、ここにおすすめが表示されます。').classes('pv-subtitle')
                    if self._on_navigate_to_learning is not None:
                        ui.button('学習タブへ',on_click=lambda e=None:self._on_navigate_to_learning()).props(
                            'outline data-testid=recommend-go-learning')
                return
            for kind in data['order']:
                with ui.column().classes('w-full gap-2').props(f'data-testid=recommend-section-{kind}'):
                    with ui.row().classes('items-center'):
                        ui.label(LABELS[kind]).classes('pv-library-heading')
                        if kind=='trend' and len(data.get('trends',()))>40:
                            ui.label(f"+{len(data['trends'])-40}").classes('pv-subtitle')
                        button=ui.button('展開' if kind in data['collapsed'] else '折りたたむ',on_click=lambda e,k=kind:self.collapse(k)).props('flat')
                        button.set_enabled(data['writable'])
                        for label,delta in (('上へ',-1),('下へ',1)):
                            b=ui.button(label,on_click=lambda e,k=kind,d=delta:self.move(k,d)).props('flat')
                            b.set_enabled(data['writable'] and 0<=list(data['order']).index(kind)+delta<len(data['order']))
                    if kind in data['collapsed']:continue
                    ui.label(_SECTION_DESCRIPTIONS[kind]).classes('pv-subtitle')
                    if kind=='trend':
                        trends=data.get('trends',())
                        with ui.row().classes('gap-2 w-full').style('max-height: 80px; overflow: hidden; align-content: flex-start') as trend_box:
                            added={r['tag'] for r in data.get('added_tags',())}
                            for index,trend in enumerate(trends[:40]):
                                tag=trend['tag'];display=trend.get('display') or tag
                                size,border=_TREND_TIERS[min(index,3)]
                                selected=tag in added
                                b=ui.button(display,on_click=lambda e,t=tag,d=display:self.toggle_trend(t,d),color=None).props(f'unelevated data-testid=recommend-trend-pill-{index}')
                                if selected:b.props('icon-right=close')
                                b.style(f'font-size: {size}px; border: {1.5 if selected else border}px solid var(--pv-pixiv-blue)' if selected else f'font-size: {size}px; border: {border}px solid var(--pv-border-vault)')
                                b.style('border-radius: 999px; color: var(--pv-text-main) !important; background: var(--pv-pixiv-blue-glow) !important' if selected else 'border-radius: 999px; color: var(--pv-text-main) !important; background: var(--pv-bg-card) !important')
                                b.set_enabled(data['writable'] if tag in added else data.get('can_acquire',False))
                        if trends and core.loop is not None:self.client.run_javascript(f"""requestAnimationFrame(() => {{
                              const box=document.getElementById('{trend_box.html_id}'); if(!box) return;
                              const update=() => {{ if(!box.isConnected) {{ observer.disconnect(); return; }}
                                const bottom=box.getBoundingClientRect().bottom;
                                for(const b of box.querySelectorAll('button'))
                                  b.tabIndex=b.disabled || b.getBoundingClientRect().bottom > bottom + 0.5 ? -1 : 0;
                              }};
                              const observer=new ResizeObserver(update); observer.observe(box); update();
                            }});""")
                        if not data.get('trends'):ui.label('表示できる傾向がまだありません。学習済みの判定から計算します。')
                        continue
                    if kind=='trend_added':
                        for row in data.get('added_tags',()):
                            b=ui.button(row['display']+' を外す',on_click=lambda e,t=row['tag'],d=row['display']:self.toggle_trend(t,d)).props('flat')
                            b.set_enabled(data['writable'])
                    if kind=='boosted':
                        for aid,name in data.get('boosted',()):
                            b=ui.button((name or aid)+' のもっと見る登録を解除',on_click=lambda e,a=aid,n=name:self.boost(a,n,False)).props('flat')
                            b.set_enabled(data['writable'])
                    if kind in data['unavailable']:
                        ui.label('この節の保存済み結果がありません。').classes('pv-subtitle')
                        continue
                    groups=data['sections'].get(kind,())
                    if not any(g.works for g in groups):ui.label('表示対象がありません。').classes('pv-subtitle')
                    for group in groups:
                        if kind in ('certain','trend_added','boosted'):
                            ui.label(group.title+('（傾向から追加）' if kind=='trend_added' else '')).classes('pv-library-heading')
                        subtitle=(f'あなたが好んで見ている『{group.title}』の作品' if kind=='certain'
                                  else f'『{group.title}』の作品' if kind=='trend_added'
                                  else f'「{group.title}」の新着' if kind=='boosted' else group.title)
                        ui.label(subtitle).classes('pv-subtitle')
                        if kind=='certain':
                            closed=group.key in data.get('certain_group_collapsed',())
                            b=ui.button('このタグを開く' if closed else 'このタグを閉じる',on_click=lambda e,t=group.key:self.collapse_certain_group(t)).props('flat')
                            self._certain_buttons[group.key]=b
                            b.set_enabled(data['writable'])
                            if closed:continue
                        key=(kind,group.key);count=self.visible.get(key,self.step)
                        with ui.element("div").classes("pv-grid w-full").style(f"--pv-max-cols:{MAX_COLUMNS[self.card_size]}"):
                            for work in group.works[:count]:
                                with ui.column().classes('w-full gap-1').style('min-width:0').props(f'data-testid=recommend-work-{kind}-{work.work_id}'):
                                    with ui.element('div').classes('pv-card w-full').props(
                                            f'tabindex=0 role=button data-testid=recommend-open-{kind}-{work.work_id}') as preview:
                                        if not work.deleted:
                                            preview.props['aria-label']='画集ビューアで開く: '+(work.title or 'ID '+work.work_id)
                                            preview.on('click',lambda e,w=work.work_id,g=generation:self.open_work(w,generation=g))
                                            preview.on('keydown.enter',lambda e,w=work.work_id,g=generation:self.open_work(w,generation=g),
                                                js_handler='event => {event.preventDefault();emit();}')
                                        else:preview.props['aria-label']='表示できません: '+(work.title or 'ID '+work.work_id)
                                        thumbnail=None
                                        caption='サムネイル未取得'
                                        try:
                                            if self._media is not None:
                                                thumbnail=self._media.thumbnail_url(work.work_id)
                                        except Exception:
                                            caption='画像を表示できません'
                                        if thumbnail:
                                            ui.image(thumbnail).props(
                                                f'fit=contain loading=lazy no-transition data-testid=recommend-thumb-{kind}-{work.work_id}').classes('pv-thumb')
                                        else:
                                            with ui.element('div').classes('pv-thumb flex flex-col items-center justify-center'):
                                                ui.icon('image_not_supported')
                                                ui.label(caption).classes('pv-subtitle')
                                        with ui.element('div').classes('pv-card-meta'):
                                            ui.label(work.title or 'ID '+work.work_id).classes('pv-card-title')
                                            ui.label(work.author_name or work.author_id).classes('pv-card-author')
                                            if work.deleted:ui.label('⚠ 表示できません').classes('pv-subtitle').props(f'data-testid=recommend-unavailable-{work.work_id}')
                                    self._card_menu(kind,work,generation,preview,data)
                                    if self.on_open is not None:ui.button('保存済みを開く',
                                        on_click=lambda e,w=work.work_id,g=generation:self.open_saved_work(w,generation=g)).props('flat dense')
                                    if self.on_enqueue is not None:ui.button('キューに追加',on_click=lambda e,w=work:self.save_work(w.work_id)).props('flat')
                                    from .pixiv_action_buttons import PixivActionButtons
                                    controls=PixivActionButtons(self.services,
                                        prefix=f'recommend-action-{kind}-{work.work_id}')
                                    controls.bind(work.work_id,work.author_id,author_name=work.author_name,
                                        is_current=lambda g=generation: (self.active and not self.closed
                                            and not self.busy and self._render_generation==g))
                                    self._action_bindings.append(controls)
                                    if kind=='superlike':
                                        dismiss=ui.button('SuperLikeを取り下げる',on_click=lambda e,w=work.work_id,g=generation:
                                            self.dismiss_superlike(w,generation=g)).props(
                                            f'flat data-testid=recommend-dismiss-superlike-{work.work_id}')
                                        dismiss.set_enabled(data['writable'])
                                    b=ui.button('出さない',on_click=lambda e,w=work:self.ask_hide_reason(w.work_id)).props('flat');b.set_enabled(data['writable'])
                                    if work.author_id:
                                        b=ui.button('作者を出さない',on_click=lambda e,w=work:self.hide('author',w.author_id)).props('flat');b.set_enabled(data['writable'])
                                        registered=work.author_id in dict(data.get('boosted',()))
                                        b=ui.button('登録解除' if registered else '作者をもっと見る',on_click=lambda e,w=work,v=not registered:self.boost(w.author_id,w.author_name,v)).props('flat');b.set_enabled(data['writable'])
                        if count>self.step:
                            ui.button('畳む',on_click=lambda e,k=key:self.fold(k)).props(
                                'flat data-testid=recommend-collapse')
                        if len(group.works)>count:
                            ui.button('もっと見る',on_click=lambda e,k=key:self.more(k)).props(
                                'outline data-testid=recommend-expand')
                        elif kind=='boosted' and count < self.controller.acquisition.visible_limit*3:
                            limit=self.controller.acquisition.visible_limit
                            next_limit=limit*2 if count<limit*2 else limit*3
                            b=ui.button('もっと見る',on_click=lambda e,a=group.key,n=next_limit:self.more_author(a,n)).props('outline')
                            b.set_enabled(data.get('can_acquire',False))
                        elif kind in ('certain','trend_added') and not group.exhausted:
                            failed=data.get('errors',{}).get(kind+':'+group.key)=='fetch_failed_retry_same_page'
                            b=ui.button('取得に失敗しました。再試行' if failed else 'もっと見る',on_click=lambda e,k=kind,t=group.key:self.next_page(k,t)).props('outline')
                            b.set_enabled(data.get('can_acquire',False))
            with ui.expansion('出さない一覧').classes('w-full'):
                if not data['hidden_works'] and not data['hidden_authors']:
                    ui.label('出さない登録された作品・作者はありません。').classes('pv-subtitle')
                for kind,targets in (('work',data['hidden_works']),('author',data['hidden_authors'])):
                    for target in targets:
                        with ui.row().classes('items-center'):
                            ui.label(('作品 ' if kind=='work' else '作者 ')+target)
                            b=ui.button('元に戻す',on_click=lambda e,k=kind,t=target:self.hide(k,t,False)).props('flat');b.set_enabled(data['writable'])
        if self.busy:
            self._lock_rendered_buttons()
            self.update_button.disable()

    def _viewer_hidden(self,work_id):
        # ビューアの右クリックで出さないにした。保存済みの結果を読み直して棚から外す。
        if self.active and not self.closed and not self.busy:
            background_tasks.create(self.refresh(preserve_viewer=True))
    def _card_menu(self,kind,work,generation,card,data,*,card_current=None):
        """元 recommend_tab.py:815-876 の右クリック 12 項目（取り下げるは SuperLike の棚だけ）。"""
        wid=work.work_id;aid=work.author_id;name=work.author_name
        writable=bool(data['writable']);actions=self._card_actions
        tag=work.tags[0] if work.tags else None
        target=pixiv_url(wid)
        def guard(action):
            def invoke():
                if self.closed or not self.active:return None
                if card_current is not None:
                    if not card_current():return None
                elif generation!=self._render_generation:return None
                return action()
            return invoke
        def open_author():
            if card_current is not None:return self._show_author_page(aid,name)
            return self.open_author_page(wid,generation=generation)
        entries=[
            MenuEntry('bookmark','ブックマークする','bookmark_add',guard(lambda:actions.bookmark(wid,aid,name)),
                      enabled=actions.can_write() and bool(aid)),
            MenuEntry('prefer','好み（保存する）','favorite_border',guard(lambda:self.save_preferred(wid,kind,card_current=card_current)),
                      enabled=self.on_enqueue is not None),
        ]
        if kind=='superlike':
            entries.append(MenuEntry('dismiss','取り下げる','undo',
                lambda:self.dismiss_superlike(wid,generation=generation),enabled=writable,
                testid=f'recommend-ctx-dismiss-superlike-{wid}'))
        entries+=[
            MenuEntry('hide','出さない','visibility_off',guard(lambda:self.hide('work',wid)),enabled=writable),
            MenuEntry('hide-reason','出さない（理由をつける）','edit_note',guard(lambda:self.ask_hide_reason(wid,card_current=card_current)),enabled=writable),
            MenuEntry('queue','ダウンロードキューへ積む','download_for_offline',guard(lambda:self.save_work(wid,card_current=card_current)),
                      enabled=self.on_enqueue is not None),
            MenuEntry('boost','この作者をもっと見る','person_add_alt',guard(lambda:self.boost(aid,name,True)),
                      enabled=writable and bool(aid)),
            MenuEntry('hide-author','この作者を出さない','person_off',guard(lambda:self.hide('author',aid)),
                      enabled=writable and bool(aid)),
            MenuEntry('tag','このタグで検索','search',guard(lambda:self._search_tag(tag)),
                      enabled=bool(tag) and self._on_search_tag is not None),
            MenuEntry('author','作者の作品一覧へ飛ぶ','person_outline',
                      guard(open_author),enabled=bool(aid),
                      testid=f'recommend-author-{kind}-{wid}'),
            MenuEntry('follow','pixivでフォローする','person_add_alt_1',guard(lambda:actions.follow(aid,name)),
                      enabled=actions.can_write() and bool(aid)),
            MenuEntry('pixiv','pixivで作品ページを開く','open_in_new',href=target,enabled=target is not None,
                      testid=f'recommend-pixiv-link-{kind}-{wid}'),
        ]
        menu,_=build_context_menu(entries,testid=f'recommend-pixiv-context-{kind}-{wid}')
        open_from_keyboard(card,menu)
    def _search_tag(self,tag):
        if tag and self._on_search_tag is not None and self.active and not self.closed:self._on_search_tag(tag)
    async def save_preferred(self,work_id,kind,*,card_current=None):
        """元の「好み（保存する）」: 保存して、SuperLike の棚からは取り下げる（保存が受理されたときだけ）。"""
        generation=self._render_generation
        if not await self.save_work(work_id,card_current=card_current):return
        if kind=='superlike' and self.data is not None and generation==self._render_generation:
            await self.dismiss_superlike(work_id,generation=generation)
    def _has_action_card(self,work_id,card_current):
        if card_current is not None:return card_current()
        return self.data is not None and any(w.work_id==work_id for groups in self.data['sections'].values()
                                            for group in groups for w in group.works)

    async def save_work(self,work_id,*,card_current=None):
        if not self.active or self.closed or self.on_enqueue is None or work_id in self._saving:return False
        if not self._has_action_card(work_id,card_current):return False
        operation=self._save_operations.setdefault(work_id,uuid4().hex)
        epoch=self.epoch;self._saving.add(work_id)
        try:
            job=await self.on_enqueue(work_id,operation,
                is_current=lambda:self.active and not self.closed and self.epoch==epoch
                    and (card_current is None or card_current()))
            if job is not None and self.active and not self.closed and self.epoch==epoch:
                self.notice.set_text('保存をキューに追加しました。完了・失敗・再試行はダウンロード画面で確認できます。')
                return True
        except Exception:
            if self.active and not self.closed and self.epoch==epoch:
                self.notice.set_text('保存受付を確認できません。同じ操作で再確認できます。キューも確認してください。')
        finally:self._saving.discard(work_id)
        return False
    def change_card_size(self,size):
        if self.closed or not self.active:return
        if self._on_size_change is not None:self._on_size_change(size)
        else:self.apply_card_size(size)
    def apply_card_size(self,size):
        if size not in MAX_COLUMNS:raise ValueError("invalid card size")
        self.card_size=size
        update_size_buttons(self.size_buttons,size)
        self.render()
    def close(self):
        if self.closed:return
        self.closed=True;self.active=False;self.epoch+=1;self._pending_hide=None
        self._recommend_viewer.close()
        if self._author_browser is not None:self._author_browser.close()
        if self._media is not None:self._media.close();self._media=None
        for controls in self._action_bindings:controls.close()
        self._action_bindings.clear()
        self.hide_dialog.close()
