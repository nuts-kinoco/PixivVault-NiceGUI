"""Original author both/illust/novel selection over explicit media owners."""
import asyncio
from dataclasses import replace
from uuid import uuid4
from nicegui import ui
from ..author_media import AuthorMediaCandidate
from .job_views import _accepted_call, _browser_event


class AuthorMediaView:
    def __init__(self, parent):
        self.parent=parent
        self.services=parent.services
        self.active=True
        self.closed=False
        self.busy=False
        self.generation=0
        self.rows=()
        self.selected=set()
        self.operations={}
        self.selection_snapshot=None
        self.target_setting=None
        self._target_setting_edited=False
        self.shown=50
        preferences=getattr(self.services,'preferences',None)
        novel_default=(preferences.get_setting('novel_save_format','epub') if preferences else 'epub').upper()
        ugoira_default=preferences.get_setting('ugoira_save_format','gif') if preferences else 'gif'
        if ugoira_default=='folder': ugoira_default='frames'
        with ui.column().classes('w-full gap-2').props('data-testid=author-media-view') as self.root:
            ui.label('選択作者の全媒体保存').classes('pv-subtitle')
            with ui.row().classes('w-full items-center'):
                self.target=ui.select({'author':'作者別設定に従う','both':'全作者: イラストとうごイラ・小説','illust':'全作者: イラストとうごイラ','novel':'全作者: 小説'},value='author',label='今回の差分確認対象').props('dense outlined data-testid=author-media-target-mode')
                self.novel_format=ui.select({'EPUB':'EPUB','TXT':'TXT','BOTH':'EPUBとTXT'},value=novel_default,label='小説形式').props('dense outlined')
                self.ugoira_format=ui.select({'gif':'GIF','mp4':'MP4','frames':'フォルダー'},value=ugoira_default,label='うごイラ形式').props('dense outlined')
                self.preview_button=ui.button('全媒体の差分を確認').props('flat no-caps data-testid=author-media-preview')
            for control in (self.target,self.novel_format,self.ugoira_format):
                control.on_value_change(self._changed)
            with ui.row().classes('w-full items-center'):
                self.setting_author=ui.select({},value=None,label='設定する作者').props('dense outlined data-testid=author-media-setting-author')
                self.setting_author.on_value_change(self._setting_author_changed)
                self.target_setting_choice=ui.select({'both':'両方','illust':'イラスト・うごイラ','novel':'小説'},value='both',label='この作者の媒体').props('dense outlined data-testid=author-media-setting-target')
                self.target_setting_choice.on_value_change(lambda _e: setattr(self, '_target_setting_edited', True))
                self.setting_load=ui.button('作者個別設定を読込').props('flat no-caps data-testid=author-media-setting-load')
                self.setting_save=ui.button('この作者の対象を保存').props('flat no-caps data-testid=author-media-setting-save')
                self.setting_clear=ui.button('作者個別設定を解除').props('flat no-caps data-testid=author-media-setting-clear')
            self.zip_revision=None
            with ui.row():
                self.zip_choice=ui.select({'inherit':'ZIP: 全体設定に従う','on':'ZIP: この作者を有効','off':'ZIP: この作者を無効'},value='inherit').props('data-testid=author-zip-choice')
                self.zip_load=ui.button('ZIP設定を読込').props('data-testid=author-zip-load')
                self.zip_save=ui.button('ZIP設定を保存').props('data-testid=author-zip-save')
                self.zip_load.set_enabled(getattr(self.services,'save_exports',None) is not None)
                self.zip_save.set_enabled(getattr(self.services,'save_exports',None) is not None)
            self.setting_status=ui.label('設定する作者を選んで読み込んでください。個別設定は次の差分確認に反映されます').classes('pv-subtitle').props('role=status data-testid=author-media-setting-status')
            self.status=ui.label().props('role=status data-testid=author-media-status')
            with ui.row().classes('w-full items-center'):
                self.select_all=ui.button('差分の保存対象を全選択',color=None).props(
                    'flat no-caps data-testid=author-media-select-all')
                self.select_none=ui.button('差分の保存対象を全解除',color=None).props(
                    'flat no-caps data-testid=author-media-select-none')
            self.list=ui.column().classes('w-full gap-1')
            self.submit_button=ui.button('選択した全媒体をキューへ追加').props('flat no-caps data-testid=author-media-submit')
        for button, callback in (
                (self.preview_button, self.preview),
                (self.setting_load, self.load_setting),
                (self.setting_save, lambda: self.save_setting(False)),
                (self.setting_clear, lambda: self.save_setting(True)),
                (self.zip_load, lambda: self.zip_setting(False)),
                (self.zip_save, lambda: self.zip_setting(True)),
                (self.select_all, lambda: self._select_all(True)),
                (self.select_none, lambda: self._select_all(False)),
                (self.submit_button, self.submit)):
            self._bind_epoch_guarded_click(button, callback)
        self.render()

    def _bind_epoch_guarded_click(self, button, callback):
        # Capture the DOM state at the time of the click. A queued click from
        # a hidden author panel must not become a new action after mode return.
        parent_root=getattr(self.parent,'root',None)
        epoch_js=(f"document.getElementById('{parent_root.html_id}').dataset.epoch"
                  if parent_root is not None else "'0'")
        generation_js=f"document.getElementById('{self.root.html_id}').dataset.viewGeneration"

        async def guarded(event):
            if not (self._valid() and _browser_event(event,
                    epoch=getattr(self.parent,'epoch',0),view_generation=self.generation)):
                return False
            result=callback()
            return await result if asyncio.iscoroutine(result) else result

        button.on('click',guarded,js_handler=(
            f"() => emit({{epoch:{epoch_js},viewGeneration:{generation_js}}})"))

    def _snapshot(self):
        return (self.parent.author_revision, tuple(self.parent.author_targets),self.target.value,
                self.novel_format.value,self.ugoira_format.value,
                getattr(self.parent,'target_mode','author'),getattr(self.parent,'epoch',0))

    def _valid(self):
        return (self.active and not self.closed and self.parent._valid()
                and getattr(self.parent,'target_mode','author')=='author')

    def _changed(self,event=None):
        self.generation+=1
        self.rows=(); self.selected.clear(); self.selection_snapshot=None
        self.render()

    def _setting_author(self):
        ids={a.user_id for a in self.parent.author_targets}
        chosen=self.setting_author.value
        if chosen in ids:return chosen
        return next(iter(ids)) if len(ids)==1 else None

    def _setting_author_changed(self,event=None):
        self.target_setting=None
        self.zip_revision=None
        if self._valid():
            self.setting_status.set_text('設定する作者が変わりました。現在の個別設定を読み込んでください')
        self.render()

    async def load_setting(self,event=None):
        aid=self._setting_author()
        source=getattr(self.services,'author_media',None)
        if not self._valid() or self.busy or aid is None or source is None: return False
        self.busy=True; generation=self.generation
        self.render()
        try:
            setting=await asyncio.to_thread(source.author_target,aid)
            if not self._valid() or generation!=self.generation or aid!=self._setting_author(): return False
            self.target_setting=setting
            self.target_setting_choice.set_value(setting.target_type or 'both')
            self._target_setting_edited=False
            self.zip_revision=None
            self.setting_status.set_text('作者個別対象: '+(setting.target_type or '解除済み（both）'))
            return True
        except Exception:
            if self._valid(): self.setting_status.set_text('個別設定を読み込めませんでした')
            return False
        finally:
            self.busy=False
            if not self.closed:self.render()

    async def save_setting(self,clear=False):
        aid=self._setting_author(); setting=self.target_setting
        if (not self._valid() or self.busy or setting is None or setting.author_id!=aid): return False
        source=self.services.author_media
        # The original single-author UI used the preview target as its edit
        # control. Preserve that path while allowing an independent per-author
        # choice when several authors are selected.
        target=(None if clear else self.target_setting_choice.value
                if self._target_setting_edited or self.target.value=='author'
                else self.target.value)
        self.busy=True; self.render()
        try:
            updated=await _accepted_call(source.configure_author_target,aid,target,expected_revision=setting.revision)
            if self._valid() and aid==self._setting_author():
                self.target_setting=updated
                self.zip_revision=None
                self._changed()
                self.setting_status.set_text('作者個別設定を解除しました（次の差分確認は両方）' if clear else '作者個別対象を保存しました（次の差分確認から適用）')
            return True
        except Exception:
            self.target_setting=None
            if self._valid(): self.setting_status.set_text('設定を保存できませんでした。別画面の更新を確認するため再読込してください')
            return False
        finally:
            self.busy=False
            if not self.closed:self.render()

    async def preview(self,event=None):
        if not self._valid() or self.busy or not self.parent.author_targets:
            return False
        self.busy=True; self.generation+=1
        generation=self.generation
        snapshot=self._snapshot()
        self.rows=(); self.selected.clear(); self.selection_snapshot=None
        self.render()
        try:
            rows=[]
            setting_source=getattr(getattr(self.services,'author_media',None),'author_target',None)
            for author in snapshot[1]:
                setting_before=(await asyncio.to_thread(setting_source,author.user_id)
                                if snapshot[2]=='author' and callable(setting_source) else None)
                found=await asyncio.to_thread(self.services.author_media_preview,author.user_id,
                    target_type=None if snapshot[2]=='author' else snapshot[2],
                    novel_format=snapshot[3],ugoira_format=snapshot[4])
                if not self._valid() or self.generation!=generation or self._snapshot()!=snapshot:
                    return False
                if setting_before is not None:
                    setting_after=await asyncio.to_thread(setting_source,author.user_id)
                    if (not self._valid() or self.generation!=generation or self._snapshot()!=snapshot):
                        return False
                    if setting_after!=setting_before:
                        self.status.set_text('別画面で作者設定が変更されました。差分を再確認してください')
                        return False
                    found=tuple(replace(c,author_setting_revision=setting_before.revision,
                        author_setting_target=setting_before.target_type) for c in found)
                rows.extend(found)
            if any(type(c) is not AuthorMediaCandidate for c in rows):
                raise ValueError('invalid preview')
            self.rows=tuple(rows); self.shown=50
            self.selected={c.key for c in rows if c.eligible}
            self.selection_snapshot=snapshot
            self.status.set_text(('作者別設定で' if snapshot[2]=='author' else '今回指定した共通対象で')
                +f'差分 {len(rows)} 件・選択 {len(self.selected)} 件。表示は50件ずつ、受付は全選択分を対象にします')
            return True
        except Exception:
            if self._valid(): self.status.set_text('全件確認に失敗しました。受付はしていません。再確認できます')
            return False
        finally:
            self.busy=False
            if not self.closed: self.render()

    def _select(self,key,value,generation):
        if not self._valid() or self.busy or generation!=self.generation or self.selection_snapshot!=self._snapshot(): return
        if value and any(c.key==key and c.eligible for c in self.rows): self.selected.add(key)
        elif not value: self.selected.discard(key)
        self.status.set_text(f'保存対象 {len(self.selected)} 件を選択中。キューへの追加は別操作です')
        self.render()

    def _select_all(self,enabled):
        if (not self._valid() or self.busy or
                self.selection_snapshot!=self._snapshot()):
            return False
        self.selected={c.key for c in self.rows if c.eligible} if enabled else set()
        self.status.set_text(f'保存対象 {len(self.selected)} 件を選択中。キューへの追加は別操作です')
        self.render()
        return True

    def _more(self,generation):
        if self._valid() and not self.busy and generation==self.generation:
            self.shown+=50; self.render()

    def render(self):
        if self.closed: return
        self.root.props['data-view-generation']=str(self.generation)
        self.root.update()
        choices={a.user_id: f'{a.display_name} · {a.user_id}' for a in self.parent.author_targets}
        self.setting_author.set_options(choices)
        if self.setting_author.value not in choices:
            only=next(iter(choices)) if len(choices)==1 else None
            if self.setting_author.value!=only:
                self.setting_author.set_value(only)
        self.setting_author.set_enabled(self._valid() and not self.busy and bool(choices))
        self.list.clear()
        labels={'new':'新規','updated':'更新','restored':'復帰','missing':'実物欠損','unknown':'判定保留','skipped':'保存済み'}
        kinds={'illustration':'イラスト','ugoira':'うごイラ','novel':'小説'}
        with self.list:
            for c in self.rows[:self.shown]:
                with ui.row().classes('w-full items-center'):
                    choice=ui.checkbox(f'{kinds[c.kind]} · {c.title} · {c.work_id}',value=c.key in self.selected)
                    choice.on_value_change(lambda e,key=c.key,gen=self.generation:self._select(key,e.value,gen))
                    choice.set_enabled(self._valid() and not self.busy and c.eligible and self.selection_snapshot==self._snapshot())
                    ui.label(labels[c.difference.value]).classes('pv-counter')
            if len(self.rows)>self.shown:
                ui.button('差分をもっと見る',on_click=lambda gen=self.generation:self._more(gen)).props('flat no-caps')
        settings_ready=(self._valid() and not self.busy and self._setting_author() is not None
                        and getattr(self.services,'author_media',None) is not None)
        self.setting_load.set_enabled(settings_ready)
        settings_loaded=settings_ready and self.target_setting is not None and self.target_setting.author_id==self._setting_author()
        self.setting_save.set_enabled(settings_loaded)
        self.setting_clear.set_enabled(settings_loaded)
        self.target_setting_choice.set_enabled(settings_loaded)
        self.preview_button.set_enabled(self._valid() and not self.busy)
        self.select_all.set_enabled(self._valid() and not self.busy
            and self.selection_snapshot==self._snapshot() and any(c.eligible for c in self.rows))
        self.select_none.set_enabled(self._valid() and not self.busy
            and self.selection_snapshot==self._snapshot() and bool(self.selected))
        self.submit_button.set_enabled(self._valid() and not self.busy and bool(self.selected)
                                      and self.selection_snapshot==self._snapshot())
        for control in (self.target,self.novel_format,self.ugoira_format):
            control.set_enabled(self._valid() and not self.busy)

    async def submit(self,event=None):
        if (not self._valid() or self.busy or not self.selected
                or self.selection_snapshot!=self._snapshot()): return False
        self.busy=True
        accepted=0; failed=False
        generation=self.generation
        origins={a.user_id:a.origin for a in self.selection_snapshot[1]}
        self.render()
        try:
            for candidate in self.rows:
                if candidate.key not in self.selected: continue
                # Close/tab changes prevent NEW admissions, while an already
                # accepted call completes through the existing shield helper.
                if (not self._valid() or generation!=self.generation
                        or self.selection_snapshot!=self._snapshot()): break
                signature=(candidate.key,str(candidate.admission()))
                operation=self.operations.setdefault(signature,uuid4().hex)
                try:
                    await _accepted_call(self.services.enqueue_author_media,candidate,operation,
                                         origin=origins[candidate.author_id])
                except Exception:
                    failed=True; break
                self.selected.discard(candidate.key); accepted+=1
                self.operations.pop(signature,None)
            if self._valid():
                remaining=len(self.selected)
                self.status.set_text(f'{accepted} 件を受付しました。残り {remaining} 件は未受付です。'
                    + ('未受付分はキュー容量と状態を確認して同じ操作で再試行できます' if remaining else
                       '保存状態はキューで確認できます'))
            return not failed and not self.selected
        finally:
            self.busy=False
            if not self.closed: self.render()

    def set_active(self,active):
        if self.closed: return
        if self.active!=bool(active): self.generation+=1
        self.active=bool(active); self.render()

    def close(self):
        self.closed=True; self.active=False; self.generation+=1


    async def zip_setting(self,save):
        aid=self._setting_author()
        if not self._valid() or self.busy or aid is None or self.services.save_exports is None:return False
        source=self.services.author_media
        self.busy=True;generation=self.generation
        try:
            if save:
                if self.zip_revision is None or self.zip_revision[0]!=aid:raise ValueError('reload ZIP settings')
                value={'inherit':None,'on':True,'off':False}[self.zip_choice.value]
                revision=await asyncio.to_thread(source.configure_author_zip,aid,value,expected_revision=self.zip_revision[1])
            else:
                setting=await asyncio.to_thread(source.author_target,aid)
                value=await asyncio.to_thread(source.author_zip_setting,aid)
                revision=setting.revision
            if self._valid() and self.generation==generation:
                self.zip_revision=(aid,revision)
                self.zip_choice.set_value('inherit' if value is None else 'on' if value else 'off')
                if save:self._changed()
                self.setting_status.set_text('ZIP設定を保存しました' if save else 'ZIP設定を読み込みました')
            return True
        except Exception:
            if self._valid():self.setting_status.set_text('ZIP設定を確認できません。再読込してください。')
            return False
        finally:
            self.busy=False
            # _changed() renders while busy, disabling the author selector.
            # Re-render after the accepted ZIP setting call has finished so a
            # second author can be configured without a tab/mode roundtrip.
            if not self.closed:self.render()
