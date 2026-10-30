"""Native adapters are injected; never modify OS settings or launch an app."""
from types import SimpleNamespace
from pathlib import Path
import pytest
from pixivvault_nicegui.desktop_integration import DesktopIntegration,DesktopActionRejected,DesktopUnavailable,TrayController,protocol_command,WindowsProtocolRegistration


class Launcher:
    def __init__(self):self.calls=[]
    def open_default(self,target):self.calls.append(('default',target))
    def open_with(self,exe,target):self.calls.append(('with',exe,target))


def test_zip_prefers_honeyview_original_lookup_order_and_plain_images_default(tmp_path):
    archive=tmp_path/'saved.zip';archive.write_bytes(b'fixture')
    image=tmp_path/'image.jpg';image.write_bytes(b'fixture')
    launcher=Launcher()
    desktop=DesktopIntegration(allowed_roots=[tmp_path],launcher=launcher,environment={'ProgramFiles':str(tmp_path/'first'),'ProgramFiles(x86)':str(tmp_path/'second')},exists=lambda p:True)
    desktop.open_saved(archive);desktop.open_saved(image)
    assert launcher.calls==[('with',tmp_path/'first'/'Honeyview'/'Honeyview.exe',archive),('default',image)]


def test_default_fallback_directory_and_target_rejection(tmp_path):
    root=tmp_path/'saved';root.mkdir();archive=root/'saved.zip';archive.write_bytes(b'fixture')
    launcher=Launcher();desktop=DesktopIntegration(allowed_roots=[root],launcher=launcher,environment={})
    desktop.open_saved(archive);desktop.open_saved(root)
    assert [x[0] for x in launcher.calls]==['default','default']
    outside=tmp_path/'outside.jpg';outside.write_bytes(b'fixture')
    executable=root/'file.exe';executable.write_bytes(b'not executed')
    for path in (outside,executable,root/'missing.jpg'):
        with pytest.raises(DesktopActionRejected):desktop.open_saved(path)
    assert len(launcher.calls)==2


@pytest.mark.parametrize("position", ["root", "nested-target"])
def test_windows_reparse_attribute_is_checked_without_following_target(monkeypatch, tmp_path, position):
    from pixivvault_nicegui.fs_paths import logical_path
    root = tmp_path / "saved"
    nested = root / "nested"
    nested.mkdir(parents=True)
    target = nested / "page.png"
    target.write_bytes(b"synthetic")
    launcher = Launcher()
    desktop = None if position == "root" else DesktopIntegration(
        allowed_roots=[root], launcher=launcher, environment={})
    guarded = root if position == "root" else nested
    original_lstat = Path.lstat
    inspected = []

    def lstat(path, *args, **kwargs):
        result = original_lstat(path, *args, **kwargs)
        if logical_path(path) == logical_path(guarded):
            inspected.append(path)
            return SimpleNamespace(st_mode=result.st_mode, st_file_attributes=0x400)
        return result

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(DesktopActionRejected, match="リンク"):
        if position == "root":
            DesktopIntegration(allowed_roots=[root], launcher=launcher, environment={})
        else:
            desktop.open_saved(target)
    assert launcher.calls == []
    assert inspected


def test_construction_and_unconfirmed_uri_have_no_side_effects():
    calls=[]
    protocol=SimpleNamespace(register=lambda:calls.append('register'),unregister=lambda:calls.append('unregister'),is_registered=lambda:bool(calls and calls[-1]=='register'))
    desktop=DesktopIntegration(protocol=protocol)
    assert calls==[]
    with pytest.raises(DesktopActionRejected):desktop.set_uri_registration(True)
    assert calls==[]
    desktop.set_uri_registration(True,confirmed=True)
    desktop.set_uri_registration(False,confirmed=True)
    assert calls==['register','unregister']
    with pytest.raises(DesktopUnavailable):DesktopIntegration().set_uri_registration(True,confirmed=True)


def test_failed_external_launch_safe_error(tmp_path):
    target=tmp_path/'image.png';target.write_bytes(b'x')
    def fail(_):raise RuntimeError('sensitive path')
    with pytest.raises(DesktopActionRejected,match='ファイルを開けませんでした'):
        DesktopIntegration(allowed_roots=[tmp_path],launcher=SimpleNamespace(open_default=fail)).open_saved(target)


def test_tray_minimize_restore_close_confirmation_and_exit_once():
    calls=[]
    settings={'minimize_to_tray':'1'}
    icon=SimpleNamespace(stop=lambda:calls.append('stop'))
    def factory(show,exit):calls.append('start');return icon
    controller=TrayController(SimpleNamespace(get_setting=lambda key,default:settings[key]),show=lambda:calls.append('show'),hide=lambda:calls.append('hide'),request_shutdown=lambda:calls.append('request') or True,factory=factory)
    assert not controller.minimize()  # no invisible window without a usable tray
    assert controller.start();assert controller.start()
    assert controller.minimize()
    settings['minimize_to_tray']='0';assert not controller.minimize()
    controller.restore()
    assert not controller.close_requested()
    assert controller.close_requested(confirmed=True)
    controller.exit_from_tray();controller.restore()
    assert calls==['start','hide','show','request','show']
    controller.complete_close();controller.complete_close()
    assert calls==['start','hide','show','request','show','stop']


def test_tray_stop_failure_retains_owner_after_native_exit_request():
    calls=[]
    def fail():
        calls.append('stop')
        if calls.count('stop')==1:raise RuntimeError('fixture stop failed')
    icon=SimpleNamespace(stop=fail)
    controller=TrayController(None,show=lambda:None,hide=lambda:None,request_shutdown=lambda:calls.append('request') or True,factory=lambda *_:icon)
    controller.start()
    assert controller.exit_from_tray()
    with pytest.raises(RuntimeError):controller.complete_close()
    assert calls==['request','stop'] and controller.icon is icon
    controller.complete_close()
    assert calls==['request','stop','stop'] and controller.icon is None


def test_protocol_command_quotes_current_entry_without_shell_execution():
    assert protocol_command('S:/Test App/app.exe')=='"S:/Test App/app.exe" "%1"'
    assert protocol_command('S:/python.exe','S:/App/main.py')=='"S:/python.exe" "S:/App/main.py" "%1"'
    with pytest.raises(DesktopActionRejected):protocol_command('bad"path')


from test_protocol_registration_restore import Registry


def test_registry_adapter_only_injected_hkcu_and_foreign_command_preserved():
    registry=Registry();adapter=WindowsProtocolRegistration('"S:/candidate/app.exe" "%1"',registry=registry)
    assert not adapter.is_registered()
    adapter.register();assert adapter.is_registered()
    key=adapter.KEY+r'\shell\open\command'
    registry.values[key]['']='foreign install'
    with pytest.raises(DesktopActionRejected):adapter.unregister()
    assert registry.deleted==[]
    registry.values[key]['']=adapter.command
    adapter.unregister()
    assert len([path for path in registry.deleted if path.startswith(adapter.KEY)])==4
    assert not adapter.is_registered()

def test_local_external_target_resolves_zip_without_extracting(tmp_path):
    from zipfile import ZipFile
    from pixivvault_nicegui.local_media_adapter import ReadonlyLocalMediaCatalog,MediaMissing
    from pixivvault_nicegui.library_models import LibrarySnapshot,LibraryWork
    from pixivvault_nicegui.models import Artwork
    from PIL import Image
    from io import BytesIO
    stream=BytesIO();Image.new('RGB',(12,10),'red').save(stream,'PNG')
    archive=tmp_path/'artist(12).zip'
    with ZipFile(archive,'w') as z:z.writestr('title(101)_1.png',stream.getvalue())
    catalog=ReadonlyLocalMediaCatalog(tmp_path,LibrarySnapshot((LibraryWork(Artwork('101','title','artist'),'12'),)))
    before=archive.read_bytes()
    assert catalog.external_target('101',0)==archive
    assert archive.read_bytes()==before and len(list(tmp_path.iterdir()))==1
    with pytest.raises(MediaMissing):catalog.external_target('999',0)
    catalog.close()


@pytest.mark.asyncio
async def test_external_page_click_uses_exact_pin_and_ignores_stale_generation():
    from nicegui import Client,ui
    from pixivvault_nicegui.services import ApplicationServices
    from pixivvault_nicegui.ui.library_view import LibraryView
    services=ApplicationServices(120);services.desktop=object();calls=[]
    async def fake_open(work_id,page=1,*,catalog_token=None):calls.append((work_id,page,catalog_token))
    services.open_external_saved=fake_open
    client=Client(ui.page('/_external_saved'))
    try:
        with client:
            view=LibraryView(services)
            view._image_provider.can_open_external=lambda _:True
            view.open_author('demo-author-01')
            work_id,page_no=next(iter(view.page_cards))
            epoch=view.state.generation;session=view._page_session
            await view.open_external(work_id,page_no,epoch-1,session)
            assert calls==[]
            await view.open_external(work_id,page_no,epoch,session)
            assert calls==[(work_id,page_no+1,view._image_provider.token)]
            view.set_active(False)
            await view.open_external(work_id,page_no,epoch,session)
            assert len(calls)==1
    finally:client.delete();services.close()
