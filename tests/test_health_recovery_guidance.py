import io
import json
from types import SimpleNamespace
import pytest
from tools import check_native_window as checker


def fake_http(monkeypatch, bodies, status=200):
    calls = []
    class Reply(io.BytesIO):
        def __init__(self, body):
            super().__init__(json.dumps(body).encode())
            self.status = status
    def open_response(*args, **kwargs):
        calls.append('GET')
        return Reply(bodies[min(len(calls)-1,len(bodies)-1)])
    monkeypatch.setattr(checker.urllib.request, 'build_opener',
                        lambda *_: SimpleNamespace(open=open_response))
    return calls


CONFIG = {'status':'configuration-required','mode':'configuration','data':'none'}
READY = {'status':'ok','mode':'demo','data':'synthetic'}


def test_configuration_response_stops_polling_on_first_sample(monkeypatch):
    calls = fake_http(monkeypatch, [CONFIG])
    now = [0.]
    monkeypatch.setattr(checker, 'time', SimpleNamespace(
        monotonic=lambda: now[0], sleep=lambda delay: now.__setitem__(0, now[0]+delay)))
    with pytest.raises(RuntimeError, match='configuration-required'):
        checker.wait_until(lambda: checker.health_ready(12345), 10.)
    assert calls == ['GET']
    assert now[0] == 0


@pytest.mark.parametrize('body,status,expected', [(READY,200,True),(READY,503,False)])
def test_health_samples_keep_response_per_attempt(monkeypatch,body,status,expected):
    calls = fake_http(monkeypatch,[body],status)
    samples=[]
    assert checker.health_ready(12345, 'synthetic', samples) is expected
    assert len(samples)==1 and samples[0]['http_status']==status
    assert samples[0]['body']==body


def test_native_checker_reports_recovery_without_timeout_or_pass(tmp_path,monkeypatch):
    calls = fake_http(monkeypatch,[CONFIG])
    target=tmp_path/'generated.exe';target.write_bytes(b'not executed')
    output=tmp_path/'result.json'
    process=SimpleNamespace(pid=10,poll=lambda:None)
    monkeypatch.setattr(checker.subprocess,'Popen',lambda *a,**kw:process)
    monkeypatch.setattr(checker,'available_port',lambda:12345)
    monkeypatch.setattr(checker,'process_identity',lambda pid:1000)
    monkeypatch.setattr(checker,'process_pairs',lambda:[(10,1)])
    monkeypatch.setattr(checker,'valid_owned',lambda values:set(values))
    monkeypatch.setattr(checker,'living_pids',lambda values:set())
    monkeypatch.setattr(checker,'port_released',lambda port:True)
    assert checker.main(['--exe',str(target),'--output',str(output)])==2
    state=json.loads(output.read_text('utf-8'))
    assert state['status']=='configuration-required'
    assert state['recovery_required'] is True
    assert state['health_ready'] is False
    assert 'health_timeout' not in state['errors']
    assert calls==['GET']
    assert state['health_samples'][0]['body']==CONFIG


def test_first_install_without_shared_owner_opens_local_settings(tmp_path,monkeypatch):
    from test_stage5_r1_native_first_draft_shared import _stub_native
    from pixivvault_nicegui import bootstrap, managed_network_stop_shared_scope as shared
    from pixivvault_nicegui.native_profile import NativeProfileStore
    local=tmp_path/'generated-local';local.mkdir()
    monkeypatch.setattr(shared,'_windows_local_app_data',lambda:local)
    calls,recovered,registered=_stub_native(monkeypatch)
    store=NativeProfileStore(tmp_path/'generated-profile');store.close()
    assert bootstrap.main(['--native','--profile-root',str(store.root)])==0
    assert len(calls)==len(registered)==1 and not recovered
    assert registered[0]['native_profile'].auth_shared_stop_required is True
    owner = registered[0]['native_profile'].shared_network_stop_scope
    assert owner is not None
    assert owner.root == local/'PixivVault-NiceGUI'/'managed-pixiv-get-stop-v1'
    assert not owner.gate.snapshot().blocked
    assert shared.open_existing_windows_user_stop().gate.path == owner.gate.path


def test_stop_diagnostics_is_readonly_and_never_authenticates(tmp_path):
    from pixivvault_nicegui.native_profile import NativeProfileStore
    from pixivvault_nicegui.native_startup import NativeProfileBinding
    from pixivvault_nicegui.managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
    from hashlib import sha256
    store=NativeProfileStore(tmp_path/'profile')
    scope=ManagedProfileNetworkStopScope(store.root)
    scope.gate.trip(403)
    binding=NativeProfileBinding(store)
    before={p.name:sha256(p.read_bytes()).hexdigest() for p in store.root.iterdir() if p.is_file()}
    try:
        observations=binding.inspect_stop_history()
        assert any(x['state']=='observed_stopped' and x['status_code']==403 for x in observations)
        assert all(x['authorizes_get'] is False for x in observations)
        assert {p.name:sha256(p.read_bytes()).hexdigest() for p in store.root.iterdir() if p.is_file()}==before
    finally:binding.close()


@pytest.mark.asyncio
async def test_recovery_panel_controls_work_without_auth(monkeypatch):
    from pixivvault_nicegui.ui.stop_recovery_view import StopRecoveryPanel
    from nicegui import Client
    from nicegui.page import page as Page
    calls=[]
    binding=SimpleNamespace(inspect_stop_history=lambda:(calls.append('local-only') or
        ({'owner':'native','state':'observed_stopped','status_code':403,'authorizes_get':False},)))
    client=Client(Page('/generated-recovery-controls'))
    panel=None
    try:
        with client:
            panel=StopRecoveryPanel(binding)
            assert panel.entry.enabled
            panel.open()
            assert panel.dialog.value
            await panel.check()
            assert calls==['local-only']
            assert '403' in panel.result.text
            assert '認証' in panel.help_text and 'すべての保存元' in panel.help_text and '未実装' not in panel.help_text
    finally:
        if panel is not None:
            panel.close()
            panel.root.delete()
        client.delete()


def test_offline_recovery_page_wires_enabled_guidance_with_auth_disabled(monkeypatch):
    from nicegui import app, ui
    from pixivvault_nicegui import native_startup
    from pixivvault_nicegui.ui import stop_recovery_view
    pages, built, allowed = {}, [], []
    actual = stop_recovery_view.StopRecoveryPanel
    class Capture(actual):
        def __init__(self, binding):
            super().__init__(binding)
            built.append(self)
    monkeypatch.setattr(stop_recovery_view, 'StopRecoveryPanel', Capture)
    monkeypatch.setattr(ui, 'page', lambda route: lambda fn: pages.setdefault(route, fn))
    monkeypatch.setattr(app, 'get', lambda *a, **kw: lambda fn: fn)
    monkeypatch.setattr(app, 'on_shutdown', lambda fn: None)
    def create_view(*, allow_auth=True):
        allowed.append(allow_auth)
        return SimpleNamespace(build=lambda:None, close=lambda:None)
    binding=SimpleNamespace(create_view=create_view,close=lambda:None,
                            inspect_stop_history=lambda:())
    native_startup.register_profile_recovery(binding,stop_migration_required=True)
    from nicegui import Client
    from nicegui.page import page as Page
    client = Client(Page('/generated-offline-recovery'))
    with client:
        pages['/']()
    try:
        assert allowed==[False]
        assert len(built)==1 and built[0].entry.enabled
        built[0].open()
        assert built[0].dialog.value is True
    finally:
        for panel in built:
            panel.close()
            panel.root.delete()
        client.delete()


def test_stop_diagnostics_lists_configured_media_without_creating_history(tmp_path):
    from pixivvault_nicegui.native_profile import NativeProfileStore
    from pixivvault_nicegui.native_startup import NativeProfileBinding
    parent=tmp_path/'save-parent'
    parent.mkdir()
    store=NativeProfileStore(tmp_path/'profile')
    store.update(save_parent=parent,expected_revision=0)
    binding=NativeProfileBinding(store)
    try:
        observed=binding.inspect_stop_history()
        assert {row['owner'] for row in observed}=={'native','images','novel','ugoira'}
        assert all(row['authorizes_get'] is False for row in observed)
        assert list(parent.iterdir())==[]
    finally:
        binding.close()



