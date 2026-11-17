import sys, io, threading
from pathlib import Path
from types import SimpleNamespace
from nicegui import Client, ui
from email.message import Message
import pixivvault_nicegui.extension_bridge as bridge
from pixivvault_nicegui.ui.preferences_view import PreferencesView
from pixivvault_nicegui.preferences import SPECS
from pixivvault_nicegui.ui.scheduler_view import check_failure_message
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopped

class Server:
    def __init__(self, address, handler): self.handler=handler; self.server_port=12345
    def serve_forever(self, **kw): pass
    def shutdown(self): pass
    def server_close(self): pass

def test_routes_snapshot_preferences(monkeypatch):
    monkeypatch.setattr(bridge,'_Server',Server)
    b=bridge.ExtensionBridge(enqueue=lambda c:'job',work_status=lambda x: (_ for _ in ()).throw(ValueError()),author_status=lambda x:{},port=0)
    b.start(); h=b._server.handler
    def run(method,path,body=b''):
        obj=h.__new__(h); obj.server=b._server; obj.path=path; obj.client_address=('127.0.0.1',5)
        obj.headers=Message(); obj.headers['Origin']='chrome-extension://'+'a'*32; obj.headers['Host']='127.0.0.1:12345'
        obj.headers['Content-Length']=str(len(body)); obj.headers['Content-Type']='application/json'
        obj.rfile=io.BytesIO(body); obj.wfile=io.BytesIO(); obj.request_version='HTTP/1.1'; obj.command=method; obj.requestline=method+' '+path
        getattr(obj,'do_'+method)()
    run('GET','/ping'); run('POST','/download',b'{}'); run('GET','/api/work/1')
    counts,reasons=b.response_snapshot()
    assert counts=={200:1,400:1,503:1}
    assert reasons=={(200,'accepted'):1,(400,'invalid_command'):1,(503,'status_unavailable'):1}
    store=SimpleNamespace(snapshot=lambda:SimpleNamespace(values={s.key:s.default for s in SPECS}))
    # Client の外で UI 要素を作ると NiceGUI がグローバルな script mode と擬似 Client を
    # 残し、後続の試験の ui.timer が起動しなくなる。専用 Client の中で作って後始末する。
    client=Client(ui.page('/_snapshot_preferences'))
    try:
        with client:
            v=PreferencesView(store,extension_status=lambda:'running',extension_snapshot=b.response_snapshot)
            v.open()
            labels=[getattr(e,'text','') for e in v.dialog.descendants()]
            assert any('200 1 / 400 1 / 503 1' in t for t in labels if isinstance(t,str))
            assert any('正常応答 1' in t and 'コマンド不正 1' in t and '状態取得不可 1' in t for t in labels if isinstance(t,str))
            v.close()
    finally:
        client.delete(); b.close()

def test_concurrent_snapshot():
    b=bridge.ExtensionBridge(enqueue=lambda c:'job',work_status=lambda x:{},author_status=lambda x:{})
    def writer():
        for _ in range(2000):
            with b._lock:
                b._response_counts[200]+=1; b._reason_counts[(200,'accepted')]=b._reason_counts.get((200,'accepted'),0)+1
    t=threading.Thread(target=writer); t.start()
    while t.is_alive():
        c,r=b.response_snapshot(); assert c[200]==r.get((200,'accepted'),0)
    t.join(); c,r=b.response_snapshot(); assert c[200]==r[(200,'accepted')]==2000
    c[200]=0; assert b.response_snapshot()[0][200]==2000

def test_final_b3():
    for code in (401,403,429):
        assert '発生時点は未確認' in check_failure_message(HTTPStatusRejected(code))
        assert '記録' in check_failure_message(ManagedNetworkStopped(code))

