"""CoS-only automatic legacy capacity fixture; no real DB/media/network."""
import argparse
import io
import json
from pathlib import Path
import sqlite3
from threading import Event, Lock
import zipfile
from nicegui import app, ui
from PIL import Image
from pixivvault_nicegui.fs_paths import native_io_path
from pixivvault_nicegui.managed_downloads import _safe_path
from pixivvault_nicegui.media_routes import register_media_routes
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.shell import DemoShell


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--port',type=int,required=True)
    parser.add_argument('--pause-first',action='store_true')
    parser.add_argument('--fail-author',choices=['1003'])
    args=parser.parse_args()
    allowed=Path(__file__).resolve().parents[1]/'artifacts/p3-capacity-auto-20261028'
    root=args.root.absolute()
    if '..' in root.parts or root==allowed or not root.is_relative_to(allowed) or not 1024<=args.port<=65535 or args.port==8089:
        parser.error('new isolated artifact subdirectory and dedicated free port required')
    root=native_io_path(root);_safe_path(root);root.mkdir(parents=True,exist_ok=True)
    marker=root/'.capacity-auto-fixture.json'
    identity={'kind':'synthetic-capacity-auto','version':1}
    if marker.exists():
        _safe_path(marker,file=True)
        if json.loads(marker.read_bytes())!=identity:parser.error('unknown fixture root')
    else:
        if any(root.iterdir()):parser.error('unknown nonempty fixture root')
        dbroot=root/'db';dbroot.mkdir();images=root/'media';images.mkdir()
        db=sqlite3.connect(str(dbroot/'library.sqlite3'))
        with db:
            db.executescript("CREATE TABLE works(work_id TEXT PRIMARY KEY,title TEXT,page_count INTEGER,tags TEXT,user_id TEXT,is_deleted INTEGER,last_backup TEXT); CREATE TABLE following_users(user_id TEXT PRIMARY KEY,name TEXT);")
            for i in range(1,63):
                aid=str(1000+i);wid=str(9000+i)
                db.execute('INSERT INTO following_users VALUES(?,?)',(aid,'合成作者 '+aid))
                db.execute('INSERT INTO works VALUES(?,?,?,?,?,?,?)',(wid,'合成作品 '+wid,1,'[]',aid,0,'2026-10-28'))
                if i==2:continue  # successfully missing store = measured zero
                body=io.BytesIO();Image.new('RGB',(8,8),(i*3%255,90,170)).save(body,format='PNG')
                folder=images/f'合成作者({aid})';folder.mkdir()
                (folder/(f'Fixture work({wid}).png')).write_bytes(body.getvalue())
                (folder/'extra.bin').write_bytes(b'x'*i)
                if i==1:
                    with zipfile.ZipFile(images/f'合成作者({aid}).zip','x') as archive:
                        archive.writestr('capacity-only.bin',body.getvalue())
        db.close()
        with marker.open('x',encoding='utf-8') as stream:json.dump(identity,stream)
    services=ApplicationServices(library_readonly_db=root/'db/library.sqlite3',
        library_image_root=root/'media',preferences_root=root/'preferences')
    register_media_routes(app,services.images)
    reader=services.legacy_capacity.reader;measure=reader.measure
    gate=Event();lock=Lock();state={'calls':[],'active':0,'max_active':0,'fail_author':args.fail_author}
    if not args.pause_first:gate.set()
    def measured(aid):
        with lock:
            state['calls'].append(aid);first=len(state['calls'])==1
            state['active']+=1;state['max_active']=max(state['max_active'],state['active'])
        try:
            if first and args.pause_first:gate.wait()
            if aid==state['fail_author']:raise OSError('synthetic author measurement failure')
            return measure(aid)
        finally:
            with lock:state['active']-=1
    reader.measure=measured
    @app.get('/_fixture/state')
    def inspect():
        # Observation only. Normal LibraryView actions own measurement/publication.
        with lock:snapshot={k:(list(v) if isinstance(v,list) else v) for k,v in state.items()}
        snapshot['cache']={str(1000+i):services.legacy_capacity.cached(str(1000+i)) for i in range(1,63)}
        return snapshot
    @ui.page('/_fixture/control')
    def control():
        ui.label('合成検証の故障注入だけを制御。製品の保存APIを代行しません。')
        ui.button('合成計測の停止を解除',on_click=gate.set).props('data-testid=fixture-release-capacity')
        ui.checkbox('合成作者1003を失敗させる',value=state['fail_author']=='1003',
            on_change=lambda e:state.update(fail_author='1003' if e.value else None))
    shells=[]
    @ui.page('/')
    def page():shells.append(DemoShell(services))
    def shutdown():
        for shell in shells:shell.close()
        gate.set();services.close()
    app.on_shutdown(shutdown)
    ui.run(host='127.0.0.1',port=args.port,show=False,reload=False,title='PV automatic capacity fixture')


if __name__=='__main__':main()
