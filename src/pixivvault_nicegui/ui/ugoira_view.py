"""Existing tabs for explicit ugoira save and verified frame playback."""
import asyncio
import json
from uuid import uuid4
from nicegui import ui
from ..ugoira_practical import FORMATS

TIMING='GIFは10ms単位へ切捨て。MP4は原版どおり平均FPS（1〜60）、色はmp4v圧縮・アルファ除去。元のdelayと色を保つ保存はフォルダーを選択。'

class UgoiraDownloadView:
    def __init__(self,services,*,navigate):
        self.services,self.navigate=services,navigate
        self.active,self.closed,self.busy=True,False,False
        self.epoch=self.revision=0
        self.selection=None
        self.operation=None
        preferences=getattr(services,'preferences',None)
        selected_format=preferences.get_setting('ugoira_save_format','gif') if preferences else 'gif'
        if selected_format=='folder':selected_format='frames'
        with ui.column().classes('pv-jobs-view w-full').props('data-testid=ugoira-download') as self.root:
            ui.label('うごイラを保存').classes('pv-library-heading')
            with ui.row().classes('pv-toolbar w-full'):
                self.work=ui.input('作品ID').props('outlined dense data-testid=ugoira-work-id')
                self.author=ui.input('作者ID').props('outlined dense data-testid=ugoira-author-id')
                self.format=ui.select(FORMATS,value=selected_format,label='保存形式').props('outlined dense data-testid=ugoira-format').classes('pv-ugoira-format')
                self.lookup_button=ui.button('作品を確認',on_click=self.lookup).props('flat data-testid=ugoira-preview-button')
            ui.label(TIMING).classes('pv-subtitle')
            self.notice=ui.label().props('role=status data-testid=ugoira-notice')
            self.preview=ui.label().classes('pv-novel-text').props('data-testid=ugoira-preview')
            with ui.row().classes('pv-toolbar'):
                self.submit_button=ui.button('キューへ追加',on_click=self.submit).props('flat data-testid=ugoira-submit')
                ui.button('キューを開く',on_click=lambda:navigate('queue')).props('flat')
            for control in (self.work,self.author,self.format): control.on_value_change(lambda _:self.invalidate())
        self.sync()
    def valid(self,epoch=None): return self.active and not self.closed and (epoch is None or epoch==self.epoch)
    def invalidate(self):
        self.revision+=1
        self.selection=self.operation=None
        self.preview.set_text('')
        self.sync()
    def sync(self):
        self.lookup_button.set_enabled(self.valid() and not self.busy)
        self.submit_button.set_enabled(self.valid() and not self.busy and self.selection is not None and self.selection.saved is None)
    async def lookup(self):
        if not self.valid() or self.busy: return
        self.busy=True; self.sync()
        captured=self.epoch,self.revision
        wid,aid,fmt=str(self.work.value or '').strip(),str(self.author.value or '').strip(),self.format.value
        try:
            selection=await asyncio.to_thread(self.services.ugoira.preview,wid,aid,fmt)
            if not self.valid(captured[0]) or self.revision!=captured[1]: return
            self.selection,self.operation=selection,uuid4().hex
            spec=selection.spec
            self.preview.set_text(f"{spec['title']} · {spec['author']} · ID {wid} · {FORMATS[fmt]} · {len(spec['frames'])} コマ · {sum(f['delay'] for f in spec['frames'])} ms")
            self.notice.set_text('保存済み。ライブラリから確認できます。' if selection.saved else '確認済み。追加後にZIP取得・保存します。')
        except Exception:
            if self.valid(captured[0]): self.notice.set_text('確認できません。ID・作者・認証・MP4用OpenCV環境を確認してください。')
        finally:
            self.busy=False; self.sync()
    async def submit(self):
        if not self.valid() or self.busy or self.selection is None or self.selection.saved is not None: return
        self.busy=True; self.sync()
        selection,operation,epoch=self.selection,self.operation,self.epoch
        try:
            job=await asyncio.to_thread(self.services.enqueue_ugoira_selection,selection,operation)
            if self.valid(epoch):
                self.selection=None
                self.notice.set_text('キューに追加しました。保存結果はキューとライブラリで確認できます。')
        except Exception:
            if self.valid(epoch): self.notice.set_text('受付を確認できません。キューを確認し、同じ操作を再確認してください。')
        finally:
            self.busy=False; self.sync()
    def set_active(self,active):
        if self.active!=bool(active): self.epoch+=1
        self.active=bool(active)
        self.sync()
    def close(self): self.closed=True; self.active=False; self.epoch+=1

class UgoiraLibraryView:
    def __init__(self,services):
        self.services=services
        self.client=ui.context.client
        self.active=self.closed=self.busy=False
        self.epoch=0
        self.entries=()
        with ui.column().classes('pv-jobs-view w-full').props('data-testid=ugoira-library') as self.root:
            with ui.row().classes('pv-toolbar'):
                ui.label('うごイラライブラリ').classes('pv-library-heading')
                ui.button('保存結果を確認',on_click=self.refresh).props('flat data-testid=ugoira-library-refresh')
            self.notice=ui.label().props('role=status data-testid=ugoira-library-notice')
            self.rows=ui.column().classes('pv-jobs-scroll w-full')
        with ui.dialog().props('data-testid=ugoira-player') as self.dialog,ui.card().classes('w-full').style('max-width:900px;background:var(--pv-surface)'):
            self.title=ui.label().classes('pv-library-heading')
            ui.label('保存形式の検証済みフレームを再生します。GIFは10ms単位、MP4は変換後の色・平均FPSです。全コマ0msのGIFは静止表示します。').classes('pv-subtitle')
            self.canvas=ui.element('canvas').style('width:100%;max-height:60vh;object-fit:contain').props('data-testid=ugoira-canvas')
            with ui.row():
                ui.button('再生 / 一時停止',on_click=self.toggle).props('flat data-testid=ugoira-play-toggle')
                ui.button('閉じる',on_click=self.dialog.close).props('flat data-testid=ugoira-player-close')
        self.dialog.on('hide',js_handler=f"() => {{const c=document.getElementById('{self.canvas.html_id}');if(c?.pv){{c.pv.stop();delete c.pv;}}const r=document.getElementById('{self.root.html_id}');const b=document.getElementById(r?.dataset.trigger);if(r?.dataset.active==='true'&&b?.isConnected)b.focus({{preventScroll:true}});}}")
    async def refresh(self):
        if not self.active or self.closed or self.busy:return
        self.busy=True; epoch=self.epoch
        self.rows.set_visibility(False)
        try:
            entries=await asyncio.to_thread(self.services.ugoira.library)
            if not self.active or self.epoch!=epoch:return
            self.entries=entries
            self.rows.clear()
            with self.rows:
                for entry in entries:
                    spec=entry['spec']
                    with ui.row().classes('pv-job-row w-full items-center'):
                        ui.label(f"{spec['title']} · {FORMATS[spec['output']]} · ID {spec['work_id']}").classes('pv-job-title grow')
                        button=ui.button('再生').props(f"flat data-testid=ugoira-read-{spec['work_id']}-{spec['output']}")
                        button.on_click(lambda e,entry=entry,button=button:self.read(entry,button.html_id))
            self.notice.set_text(f'保存済み {len(entries)} 件')
        except Exception:
            if self.active and self.epoch==epoch:self.notice.set_text('保存結果の整合性を確認できません。')
        finally:
            self.busy=False
            if not self.closed:
                self.rows.set_visibility(self.active and self.epoch==epoch)
                if self.active and self.epoch!=epoch:asyncio.create_task(self.refresh())
    async def read(self,entry,trigger):
        if not self.active or self.closed or self.busy or entry not in self.entries:return
        self.busy=True; epoch=self.epoch
        try:
            playback=await asyncio.to_thread(self.services.ugoira.playback,entry)
            if not self.active or self.epoch!=epoch:return
            self.title.set_text(entry['spec']['title']+' · '+FORMATS[entry['spec']['output']])
            self.root.props['data-trigger']=trigger; self.root.update()
            self.dialog.open()
            packet=json.dumps(playback,separators=(',',':'))
            await self.client.run_javascript(f'''return await (async()=>{{
                const c=document.getElementById('{self.canvas.html_id}');
                if(!c)return false;
                if(c.pv)c.pv.stop();
                const data={packet}; let stopped=false,paused=false,elapsed=0,last=null,raf=null;
                const state={{stop:()=>{{stopped=true;cancelAnimationFrame(raf);}},toggle:()=>{{paused=!paused;last=null;c.dataset.paused=String(paused);}}}};
                c.pv=state;c.dataset.paused='false';c.width=data.width;c.height=data.height;
                const images=await Promise.all(data.frames.map(f=>new Promise((resolve,reject)=>{{const i=new Image();i.onload=()=>resolve(i);i.onerror=reject;i.src=f.src;}})));
                if(stopped||c.pv!==state)return false;
                const ctx=c.getContext('2d');
                function tick(now){{
                    if(stopped||!c.isConnected)return;
                    if(!paused&&last!==null)elapsed+=now-last;
                    last=now;let position=(data.duration_ms>0?elapsed%data.duration_ms:0),index=0;
                    while(index<data.frames.length-1&&position>=data.frames[index].delay)position-=data.frames[index++].delay;
                    ctx.clearRect(0,0,c.width,c.height);ctx.drawImage(images[index],0,0);
                    c.dataset.frame=String(index);c.dataset.elapsed=String(Math.floor(elapsed));
                    raf=requestAnimationFrame(tick);
                }}
                raf=requestAnimationFrame(tick);return true;
            }})()''',timeout=15)
        except Exception:
            if self.active and self.epoch==epoch:self.notice.set_text('保存フレームを再生できません。整合性を再確認してください。')
        finally:
            self.busy=False
            if not self.closed and self.active and self.epoch!=epoch:
                asyncio.create_task(self.refresh())
    def toggle(self):
        if self.active and not self.closed:self.client.run_javascript(f"document.getElementById('{self.canvas.html_id}')?.pv?.toggle()")
    def set_active(self,active):
        if self.closed or self.active==bool(active):return
        self.epoch+=1
        self.active=bool(active)
        self.root.props['data-active']=str(self.active).lower();self.root.update()
        self.rows.set_visibility(False)
        if not self.active:self.dialog.close()
        elif not self.busy:asyncio.create_task(self.refresh())
    def apply_card_size(self,_):pass
    def close(self):
        self.closed=True;self.active=False;self.epoch+=1;self.dialog.close()