"""Recommendation shelves with explicit refresh, paging and sidecar preferences."""
from __future__ import annotations
import asyncio
from uuid import uuid4
from nicegui import background_tasks, ui
from ..recommendation_state import LABELS, ORDER
from ..recommendation_media import RecommendationMediaSnapshot
from ..viewer_metadata import pixiv_url
from .card_size_control import size_buttons, update_size_buttons
from .grid_layout import MAX_COLUMNS
from .recommendation_viewer import RecommendationViewer

class RecommendView:
    def __init__(self,services,*,on_open=None,on_enqueue=None,card_size="large",on_size_change=None):
        self.services=services
        self.card_size=card_size;self._on_size_change=on_size_change
        self.controller=getattr(services,'recommendations',None)
        self.on_open=on_open;self.on_enqueue=on_enqueue
        self.active=False;self.closed=False;self.busy=False;self.epoch=0
        # Original section expansion uses 6 -> 12 -> 18, independently of
        # explicit subsequent remote page acquisition.
        self.data=None;self.undo=None;self.visible={};self.step=6
        self._pending_hide=None
        self._save_operations={};self._saving=set();self._busy_buttons=[];self._refresh_pending=False
        self._action_bindings=[];self._render_generation=0
        self._media=None;self._open_generation=0;self._opening=None
        self.client=ui.context.client
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
            on_close=self._invalidate_open)
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
            self._pending_hide=None
            self.hide_dialog.close()
        if self.active and self.controller is not None:
            if self.busy:self._refresh_pending=True
            else:background_tasks.create(self.refresh())

    async def refresh(self):
        if not self.active or self.busy or self.controller is None:return
        epoch=self.epoch
        try:
            self._set_busy(True, text='保存済み結果を読み込んでいます…')
            data=await asyncio.to_thread(self.controller.refresh)
            if self.active and self.epoch==epoch:
                self.data=data;self.render();self.notice.set_text('保存済み結果を読み込みました。')
        except Exception:
            if self.active and self.epoch==epoch:self.notice.set_text('保存済み結果を確認できません。前の一覧を保持しました。')
        finally:
            self._finish_operation()
    async def _change(self,callback, *, busy_text='処理中…'):
        if not self.active or self.busy or self.controller is None:return
        epoch=self.epoch
        try:
            self._set_busy(True, text=busy_text)
            data=await asyncio.to_thread(callback)
            if self.active and self.epoch==epoch:
                self.data=data;self.render()
                self.notice.set_text('一部を取得できませんでした。保存済み結果を保持しています。再操作で再試行できます。' if data.get('errors') else '専用設定に保存しました。')
                return True
            return False
        except Exception:
            if self.active and self.epoch==epoch:
                self.notice.set_text('取得または保存に失敗しました。前の結果とページを保持しています。再読込後に再試行できます。')
            return False
        finally:
            self._finish_operation()
    async def update(self):
        if self.data is None:return
        revision=self.data['revision']
        await self._change(lambda:self.controller.update(revision), busy_text='おすすめを取得中…')
    async def toggle_trend(self,tag,display):
        if self.data is None:return
        revision=self.data['revision']
        exists=any(r['tag']==tag for r in self.data['added_tags'])
        await self._change(lambda:self.controller.remove_trend(tag,revision) if exists else self.controller.add_trend(tag,display,revision))
    async def next_page(self,kind,tag):
        if self.data is None:return
        revision=self.data['revision']
        await self._change(lambda:self.controller.next_page(kind,tag,revision))
    async def more_author(self,author_id,count):
        if self.data is None:return
        revision=self.data['revision']
        await self._change(lambda:self.controller.more_author(author_id,count,revision))
        self.visible[('boosted',author_id)]=count;self.render()
    async def boost(self,author_id,name,enabled):
        if self.data is None:return
        revision=self.data['revision']
        await self._change(lambda:self.controller.boost(author_id,name,enabled,revision))
    def ask_hide_reason(self,work_id):
        if not self.active or self.closed or self.busy or self.data is None:return
        if not any(w.work_id==work_id for groups in self.data['sections'].values()
                   for group in groups for w in group.works):return
        self._pending_hide=(work_id,self.epoch)
        self.hide_dialog.open()
    async def choose_hide_reason(self,reason):
        pending=self._pending_hide;self._pending_hide=None
        self.hide_dialog.close()
        if pending is None or not self.active or self.closed or self.epoch!=pending[1]:return
        await self.hide('work',pending[0],True,reason=reason)
    async def hide(self,kind,target,hidden=True,*,reason=None):
        if self.data is None:return
        revision=self.data['revision']
        await self._change(lambda:self.controller.hide(kind,target,hidden,revision,reason=reason))
        if self.data is not None and self.data['revision']!=revision and hidden:
            self.undo=(kind,target,self.data['revision'])
            self.undo_button.set_visibility(True)
            self.undo_button.set_enabled(True)
    async def undo_hide(self):
        if self.undo is None or self.busy or self.closed or not self.active:return
        kind,target,revision=self.undo
        accepted=await self._change(lambda:self.controller.hide(kind,target,False,revision))
        if accepted and self.data is not None and self.data["revision"]!=revision:
            self.undo=None;self.undo_button.set_visibility(False)
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

    def _visible_works(self):
        if self.data is None:return ()
        result=[]
        for kind in self.data['order']:
            if (kind=='trend' or kind in self.data['collapsed']
                    or kind in self.data['unavailable']):continue
            for group in self.data['sections'].get(kind,()):
                count=self.visible.get((kind,group.key),self.step)
                result.extend(group.works[:count])
        return tuple(result)

    def open_saved_work(self,work_id,*,generation):
        if (self.closed or not self.active or self.busy or self.on_open is None
                or generation!=self._render_generation
                or not any(w.work_id==work_id for w in self._visible_works())):return
        self.on_open(work_id)

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
                resolved=await asyncio.to_thread(media.read_detail,work_id)
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
            selection=None  # Viewer owns this lease until close/replacement.
            self.notice.set_text('')
        except Exception:
            if current():
                self.notice.set_text('作品情報や画像を準備できません。保存済み結果を再読込するか、カードを押して再試行してください。')
        finally:
            if selection is not None:selection.close()
            if self._opening==opening and request==self._open_generation:self._opening=None

    def render(self):
        if self.data is None or self.closed:return
        self._recommend_viewer.close_viewer(restore_focus=False)
        old_media=self._media
        try:
            visible=self._visible_works()
            if old_media is None or not old_media.matches(visible):
                self._media=RecommendationMediaSnapshot(self.services,visible)
        except Exception:
            # An unavailable catalog must not authorize remote fallback for an
            # identity whose saved/excluded state could not be inspected.
            self._media=None
        if old_media is not None and old_media is not self._media:old_media.close()
        for controls in self._action_bindings:controls.close()
        self._action_bindings.clear()
        self._render_generation+=1
        generation=self._render_generation
        data=self.data;self.body.clear()
        self.update_button.set_enabled(data.get('can_acquire',False))
        self.reset_button.set_enabled(bool(data.get('writable') and tuple(data['order'])!=ORDER and not self.busy))
        with self.body:
            if not data['writable']:ui.label('おすすめの登録設定は閲覧のみです。専用設定の保存先が未指定です。').classes('pv-subtitle')
            elif not data.get('can_acquire'):ui.label('取得元が未接続です。保存済み結果と登録設定を操作できます。').classes('pv-subtitle')
            for kind in data['order']:
                with ui.column().classes('w-full gap-2').props(f'data-testid=recommend-section-{kind}'):
                    with ui.row().classes('items-center'):
                        ui.label(LABELS[kind]).classes('pv-library-heading')
                        button=ui.button('展開' if kind in data['collapsed'] else '折りたたむ',on_click=lambda e,k=kind:self.collapse(k)).props('flat')
                        button.set_enabled(data['writable'])
                        for label,delta in (('上へ',-1),('下へ',1)):
                            b=ui.button(label,on_click=lambda e,k=kind,d=delta:self.move(k,d)).props('flat')
                            b.set_enabled(data['writable'] and 0<=list(data['order']).index(kind)+delta<len(data['order']))
                    if kind in data['collapsed']:continue
                    if kind=='trend':
                        with ui.row().classes('gap-2'):
                            added={r['tag'] for r in data.get('added_tags',())}
                            for trend in data.get('trends',()):
                                tag=trend['tag'];display=trend.get('display') or tag
                                b=ui.button(('追加済み: ' if tag in added else '')+display,
                                    on_click=lambda e,t=tag,d=display:self.toggle_trend(t,d)).props('outline')
                                b.set_enabled(data.get('can_acquire',False))
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
                        ui.label('この節の保存済み結果がありません。取得元を接続後、更新してください。').classes('pv-subtitle')
                        continue
                    groups=data['sections'].get(kind,())
                    if not any(g.works for g in groups):ui.label('表示対象がありません。').classes('pv-subtitle')
                    for group in groups:
                        ui.label(group.title).classes('pv-subtitle')
                        key=(kind,group.key);count=self.visible.get(key,self.step)
                        with ui.element("div").classes("pv-grid w-full").style(f"--pv-max-cols:{MAX_COLUMNS[self.card_size]}"):
                            for work in group.works[:count]:
                                with ui.column().classes('w-full gap-1').style('min-width:0').props(f'data-testid=recommend-work-{kind}-{work.work_id}'):
                                    with ui.element('div').classes('pv-card w-full').props(
                                            f'tabindex=0 role=button data-testid=recommend-open-{kind}-{work.work_id}') as preview:
                                        preview.props['aria-label']='画集ビューアで開く: '+(work.title or 'ID '+work.work_id)
                                        preview.on('click',lambda e,w=work.work_id,g=generation:self.open_work(w,generation=g))
                                        preview.on('keydown.enter',lambda e,w=work.work_id,g=generation:self.open_work(w,generation=g),
                                            js_handler='event => {event.preventDefault();emit();}')
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
                                    target=pixiv_url(work.work_id)
                                    if target is not None:
                                        with ui.context_menu().props(
                                                f'data-testid=recommend-pixiv-context-{kind}-{work.work_id}'):
                                            link=ui.link('Pixivで作品を開く',target,new_tab=True).classes('p-3').props(
                                                f'data-testid=recommend-pixiv-link-{kind}-{work.work_id}')
                                            link.props['rel']='noopener noreferrer'
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
                        elif kind=='boosted' and count<self.step*3:
                            b=ui.button('作者の作品をさらに取得',on_click=lambda e,a=group.key,n=count+self.step:self.more_author(a,n)).props('outline')
                            b.set_enabled(data.get('can_acquire',False))
                        elif kind in ('certain','trend_added') and not group.exhausted:
                            b=ui.button('さらに取得',on_click=lambda e,k=kind,t=group.key:self.next_page(k,t)).props('outline')
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

    async def save_work(self,work_id):
        if not self.active or self.closed or self.on_enqueue is None or work_id in self._saving:return
        # A visible card is the authority, not an arbitrary event payload.
        if self.data is None or not any(w.work_id==work_id for groups in self.data['sections'].values()
                                       for group in groups for w in group.works):return
        operation=self._save_operations.setdefault(work_id,uuid4().hex)
        epoch=self.epoch;self._saving.add(work_id)
        try:
            job=await self.on_enqueue(work_id,operation,
                is_current=lambda:self.active and not self.closed and self.epoch==epoch)
            if job is not None and self.active and not self.closed and self.epoch==epoch:
                self.notice.set_text('保存をキューに追加しました。完了・失敗・再試行はダウンロード画面で確認できます。')
        except Exception:
            if self.active and not self.closed and self.epoch==epoch:
                self.notice.set_text('保存受付を確認できません。同じ操作で再確認できます。キューも確認してください。')
        finally:self._saving.discard(work_id)
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
        if self._media is not None:self._media.close();self._media=None
        for controls in self._action_bindings:controls.close()
        self._action_bindings.clear()
        self.hide_dialog.close()
