"""Saved-image similarity dialog; request generations reject stale results."""
from __future__ import annotations
import asyncio
import inspect
import secrets
from nicegui import Client, ui

class SimilarView:
    PHASE_LABELS = {'idle':'待機中', 'running':'実行中', 'paused':'一時停止中',
                    'failed':'失敗・再開待ち', 'stopped':'停止'}
    ACTIVE_PROVIDERS = {'cuda': 'GPU (CUDA)', 'cpu': 'CPU'}
    REQUESTED_PROVIDERS = {'auto': '自動（GPU優先）', 'cuda': 'GPU (CUDA)', 'cpu': 'CPU'}
    # Only fixed, public summaries. ORT's exception bodies may contain local
    # paths, GPU device details or untrusted inference input; never echo them.
    FALLBACK_REASONS = {
        'EgaraBackendUnavailable': 'GPUを利用できないためCPUへ切替',
        'RuntimeError': 'GPU実行中の問題によりCPUへ切替',
        'Fail': 'GPUの初期化または実行に失敗したためCPUへ切替',
        'RuntimeException': 'GPUの初期化または実行に失敗したためCPUへ切替',
        'EPFail': 'GPU実行プロバイダーの利用に失敗したためCPUへ切替',
        'InvalidArgument': 'GPU推論の入力を受け付けられずCPUへ切替',
        'ModuleNotFoundError': 'GPU関連モジュールを読み込めずCPUへ切替',
        'ImportError': 'GPU関連モジュールを読み込めずCPUへ切替',
        'OSError': 'GPU関連ライブラリを利用できずCPUへ切替',
    }

    def __init__(self,source,on_select,*,image_url=None,analyze=None,
                 analysis_status=None,start_analysis=None,pause_analysis=None,
                 resume_analysis=None):
        self.source=source;self.on_select=on_select;self.image_url=image_url
        self.analyze=analyze
        self.analysis_status=analysis_status
        self._analysis_controls={'start':start_analysis,'pause':pause_analysis,
                                 'resume':resume_analysis}
        self.active=True;self.closed=False;self.generation=0;self.origin=None
        self._query_candidates=();self._is_current=None
        self._analysis_token=None;self._analysis_busy=False;self._analysis_run=None
        self._analysis_available=False
        self._control_tokens={};self._control_busy=False
        self._status_timer=None;self._status_phase='idle'
        self._bulk_started=False
        self.client=ui.context.client
        with ui.dialog() as self.dialog,ui.card().classes('w-full').style('width:900px;max-width:95vw'):
            self.title=ui.label().classes('pv-library-heading')
            self.notice=ui.label().props('role=status data-testid=similar-status')
            self.results=ui.column().classes('w-full').props('data-testid=similar-results')
            with ui.row():
                self.retry=ui.button('再試行',on_click=self.retry_search).props('flat data-testid=similar-retry')
                self.analyze_button=ui.button('保存済み画像を解析して検索').props(
                    'flat data-testid=similar-analyze')
                self.analyze_button.on('click',self.analyze_current,js_handler="""event => {
                    const button=event.currentTarget;
                    emit({token:button.dataset.analyzeToken,work:button.dataset.analyzeWork,
                          page:Number(button.dataset.analyzePage),
                          generation:Number(button.dataset.analyzeGeneration)});
                }""")
                ui.button('閉じる',on_click=self.invalidate).props('flat data-testid=similar-close')
            ui.label('保存済み全ページの解析').classes('text-subtitle2')
            self.analysis_text=ui.label('未実行（解析ワーカー未接続）').props(
                'role=status data-testid=similar-analysis-status')
            with ui.row():
                self.start_button=ui.button('全保存ページの解析').props('flat data-testid=similar-analysis-start')
                self.pause_button=ui.button('一時停止').props('flat data-testid=similar-analysis-pause')
                self.resume_button=ui.button('再開').props('flat data-testid=similar-analysis-resume')
            for action,button in (('start',self.start_button),('pause',self.pause_button),
                                  ('resume',self.resume_button)):
                button.on('click',lambda event,action=action:self.run_analysis_control(action,event),
                          js_handler="""event => {
                              const button=event.currentTarget;
                              emit({token:button.dataset.controlToken,
                                    generation:Number(button.dataset.controlGeneration)});
                          }""")
                button.set_enabled(False)
        self.dialog.on('hide',self._hide)
        self.client.on_delete(self.close)
    def _stop_status_timer(self):
        if self._status_timer is not None:
            timer=self._status_timer
            timer.cancel()
            # A reusable view opens many times; remove the NiceGUI element
            # rather than leaving canceled timer nodes on a hidden dialog.
            # NiceGUI may have already removed it during timer._cleanup().
            slot=timer.parent_slot
            if not timer.is_deleted and slot is not None and timer in slot.children:
                timer.delete()
            self._status_timer=None
    def _revoke(self):
        self.generation+=1
        self._analysis_token=None
        self._control_tokens.clear()
        self._analysis_available=False
        self._analysis_run=None
        self._stop_status_timer()
    def _hide(self,*_):
        self._revoke()
    def invalidate(self):
        self._revoke()
        self.dialog.close()
    def _valid(self,generation=None):
        return (not self.closed and self.active
                and self.client.id in Client.instances and not self.client.is_deleted
                and self.dialog.value
                and (generation is None or generation==self.generation)
                and (self._is_current is None or self._is_current()))
    def _new_analysis_token(self):
        self._analysis_token=None
        self.analyze_button.set_enabled(False)
        if (not self._analysis_busy and self._analysis_available and self.analyze is not None
                and self.origin is not None and self._valid()):
            work_id,_,page=self.origin
            self._analysis_token=secrets.token_urlsafe(24)
            self.analyze_button.props(
                f'data-analyze-token={self._analysis_token} '
                f'data-analyze-work={work_id} data-analyze-page={page} '
                f'data-analyze-generation={self.generation}')
            self.analyze_button.set_enabled(True)
        self.analyze_button.set_visibility(self._analysis_available and self.analyze is not None)

    def _offer_analysis(self):
        self._analysis_available=True
        self._new_analysis_token()

    @staticmethod
    def _display_number(status,key):
        value=status.get(key)
        return str(value) if type(value) is int and value>=0 else '未確認'
    def _sync_control_buttons(self):
        valid=self._valid() and not self._control_busy and self.analysis_status is not None
        available={'start':self._status_phase=='idle',
                   'pause':self._status_phase=='running' or
                           (self._status_phase=='idle' and self._bulk_started),
                   'resume':self._status_phase in ('paused','failed')}
        self.start_button.set_text('新しい保存を確認' if self._bulk_started else '全保存ページの解析')
        for action,button in (('start',self.start_button),('pause',self.pause_button),
                              ('resume',self.resume_button)):
            enabled=bool(valid and available[action] and self._analysis_controls[action] is not None)
            if not enabled:
                self._control_tokens.pop(action,None)
            elif action not in self._control_tokens:
                self._control_tokens[action]=secrets.token_urlsafe(24)
            button.set_enabled(enabled)
            if enabled:
                button.props(f'data-control-token={self._control_tokens[action]} '
                             f'data-control-generation={self.generation}')

    async def refresh_analysis_status(self):
        """Read status only while visible; no worker or model is started here."""
        if not self._valid():
            return
        generation=self.generation
        if self.analysis_status is None:
            self._status_phase='idle'
            self.analysis_text.set_text('未実行（解析ワーカー未接続）')
            self._sync_control_buttons()
            return
        try:
            status=await asyncio.to_thread(self.analysis_status)
            if type(status) is not dict:
                raise ValueError('invalid status')
        except Exception:
            if self._valid(generation):
                self.analysis_text.set_text('解析状態を確認できません。')
                self._control_tokens.clear()
                for button in (self.start_button,self.pause_button,self.resume_button):
                    button.set_enabled(False)
            return
        if not self._valid(generation):
            return
        phase=status.get('phase')
        self._status_phase=phase if phase in self.PHASE_LABELS else 'failed'
        active_provider=self.ACTIVE_PROVIDERS.get(status.get('provider')) if type(status.get('provider')) is str else None
        requested=self.REQUESTED_PROVIDERS.get(status.get('requested')) if type(status.get('requested')) is str else None
        activity=any(type(status.get(key)) is int and status[key]>0
                     for key in ('examined','indexed','resumed','errors'))
        # Idle can also mean a completed scan waiting for newly saved pages.
        # Local explicit start is remembered even if the initial scan was empty.
        if type(status.get('started')) is bool:
            self._bulk_started=status['started']
        elif (activity or self._status_phase in ('running','paused','failed')):
            self._bulk_started=True
        not_started=self._status_phase=='idle' and not self._bulk_started
        phase_text=('未実行' if active_provider is None else '全ページ解析は未開始') if not_started else self.PHASE_LABELS[self._status_phase]
        # Only provider_active proves which engine has actually executed.
        # Requested is a preference, never an execution claim.
        provider_text='実行先 '+(active_provider or '未確定（モデル未ロード）')
        if requested is not None:
            provider_text+=' / 希望 '+requested
        # SimilarityWorker counters persist across wake/rescan during its
        # lifetime. They are not a single scan's progress.
        detail=(f"{phase_text} / "
                f"ワーカー累計: 解析済み {self._display_number(status,'indexed')} / "
                f"再開 {self._display_number(status,'resumed')} / "
                f"エラー {self._display_number(status,'errors')} / "
                f"確認 {self._display_number(status,'examined')} / {provider_text}")
        if (status.get('provider')=='cpu' and status.get('requested')=='auto'
                and type(status.get('fallback_reason')) is str
                and status.get('fallback_reason')):
            reason=self.FALLBACK_REASONS.get(status['fallback_reason'],
                                             'GPUからCPUへ切替（詳細な理由は非表示）')
            detail+=' / フォールバック: '+reason
        self.analysis_text.set_text(detail)
        self._sync_control_buttons()

    def _ensure_status_timer(self):
        if self.analysis_status is None or self._status_timer is not None:
            return
        with self.dialog:
            self._status_timer=ui.timer(1.0,self.refresh_analysis_status)

    def control_event(self,action):
        """Current control event shape; useful for synthetic UI assertions."""
        from types import SimpleNamespace
        return SimpleNamespace(args={'token':self._control_tokens.get(action),
                                     'generation':self.generation})

    async def run_analysis_control(self,action,event):
        """Only an explicit, once-issued browser click may change worker state."""
        callback=self._analysis_controls.get(action)
        payload=getattr(event,'args',None)
        if (callback is None or not self._valid() or self._control_busy
                or not isinstance(payload,dict) or
                type(payload.get('generation')) is not int or
                payload['generation']!=self.generation or
                not isinstance(payload.get('token'),str) or
                payload['token']!=self._control_tokens.get(action)):
            return False
        self._control_tokens.pop(action,None)
        self._control_busy=True
        self._sync_control_buttons()
        generation=self.generation
        try:
            await asyncio.to_thread(callback)
            if action=='start' and self._valid(generation):
                self._bulk_started=True
        except Exception:
            if self._valid(generation):
                self.analysis_text.set_text('解析操作に失敗しました。状態を確認して再試行してください。')
            return False
        finally:
            self._control_busy=False
            if self._valid(generation):
                await self.refresh_analysis_status()
        return self._valid(generation)
    async def show(self,work_id,title='',page=1,*,query_candidates=(),is_current=None):
        if self.closed or not self.active or (is_current is not None and not is_current()):return
        candidates=tuple(query_candidates)
        self._analysis_token=None
        self._analysis_available=False
        self._control_tokens.clear()
        self.origin=(work_id,title,page);self._query_candidates=candidates;self._is_current=is_current
        self.generation+=1;generation=self.generation
        self.title.set_text(f'「{title or work_id}」に似た絵')
        self.results.clear();self.notice.set_text('似た絵を探しています…');self.retry.set_visibility(False)
        self.analyze_button.set_visibility(False)
        self.dialog.open()
        await self.refresh_analysis_status()
        if self._valid(generation):self._ensure_status_timer()
        def search():
            if candidates:
                return self.source.search_author(tuple(wid for wid,_ in candidates),work_id)
            return work_id,self.source.search(work_id,page)
        try:
            chosen,result=await asyncio.to_thread(search)
            if not self._valid(generation):
                if not self.closed and generation==self.generation:self.invalidate()
                return
            if candidates:self.title.set_text(f'「{dict(candidates).get(chosen) or chosen}」に似た絵')
            if result['status']=='empty_pool':
                self.notice.set_text('保存済みの解析データがありません。')
                self._offer_analysis()
            elif result['status']=='no_query_embedding':
                self.notice.set_text('このページの保存済み解析データがありません。')
                self._offer_analysis()
            elif not result['results']:self.notice.set_text(f"似た絵は見つかりませんでした（解析済み{result['pool_size']}枚・類似度0.50以上）。")
            else:self.notice.set_text(f"{len(result['results'])}枚（解析済み{result['pool_size']}枚・類似度0.50以上）")
            with self.results:
                for (wid,pageno),score in result['results']:
                    with ui.row().classes('items-center'):
                        if self.image_url is not None:
                            url=self.image_url(wid,pageno)
                            if url:ui.image(url).style('width:96px;height:96px;object-fit:contain')
                        ui.button(f'{wid} · {pageno}ページ · {score:.2f}',on_click=lambda _,wid=wid,pageno=pageno,generation=generation:self.select(wid,pageno,generation)).props('flat data-testid=similar-result')
        except Exception:
            if self._valid(generation):
                self.notice.set_text('保存済み解析データを確認できません。再試行してください。');self.retry.set_visibility(True)
    async def retry_search(self):
        if self.origin and self._valid():
            await self.show(*self.origin,query_candidates=self._query_candidates,is_current=self._is_current)
    async def analyze_current(self,event):
        payload=getattr(event,'args',None)
        if (self.analyze is None or not self.origin or not self._valid()
                or self._analysis_busy or not self._analysis_available
                or not isinstance(payload,dict)
                or type(payload.get('generation')) is not int
                or type(payload.get('page')) is not int
                or payload.get('generation')!=self.generation
                or payload.get('work')!=self.origin[0]
                or payload.get('page')!=self.origin[2]
                or not isinstance(payload.get('token'),str)
                or payload['token']!=self._analysis_token):
            return False
        self._analysis_token=None  # One-use consumption precedes any await.
        self._analysis_busy=True
        run=object();self._analysis_run=run
        work_id,title,page=self.origin
        generation=self.generation
        candidates=self._query_candidates
        is_current=self._is_current
        origin=self.origin
        self.analyze_button.set_enabled(False)
        self.notice.set_text('保存済みの原画像を解析しています…')
        def current():
            return (self._analysis_run is run and self._valid(generation)
                    and self.origin==origin)
        try:
            # Explicit local job only. No Pixiv GET is initiated by this view.
            await asyncio.to_thread(self.analyze,work_id,page,is_current=current)
        except Exception:
            if self._valid(generation):
                self.notice.set_text('保存済み画像を解析できません。明示的に再試行してください。')
                self._analysis_available=True
            return False
        finally:
            self._analysis_busy=False
            if self._analysis_run is run:self._analysis_run=None
            # A different page can have been opened during the worker call.
            # Offer a *new* token for that current page after the old worker
            # drains; never reuse the old page's token or publish its result.
            if self._valid() and self._analysis_available:
                self._new_analysis_token()
        if self._valid(generation) and self.origin==origin:
            await self.show(work_id,title,page,query_candidates=candidates,is_current=is_current)
            return True
        return False
    async def select(self,work_id,page,generation=None):
        if not self._valid(generation):return
        self.invalidate()
        result=self.on_select(work_id,page)
        if inspect.isawaitable(result):await result
    def set_active(self,active):
        self.active=bool(active) and not self.closed
        if not self.active:self.invalidate()
    def close(self):
        if self.closed:return
        self.closed=True;self.active=False;self._is_current=None
        self.invalidate()
