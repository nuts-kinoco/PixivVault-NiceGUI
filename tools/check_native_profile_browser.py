"""Explicit browser acceptance for profile picks/cancel/restart with fake callbacks.

Run with pytest on this file after installing the browser extra/runtime. The
CPU-only tests do not launch a browser or require a desktop browser install.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.run_browser_checks import (
    ROOT, child_environment, free_port, port_released, stop_child, wait_for_health,
)


def _serve(port: int, fixture: Path):
    from nicegui import app, ui
    from pixivvault_nicegui.native_profile import NativeProfileStore
    from pixivvault_nicegui.ui.native_profile_view import NativeProfileView

    root=fixture/'profile'
    store=NativeProfileStore(root)
    inline_store=NativeProfileStore(fixture/'inline-profile')
    calls={'verify':0}

    async def fake_file_picker():
        return fixture/'credentials'/'synthetic-cookies.txt'

    async def fake_folder_picker():
        return fixture/'save-parent'

    async def fake_database_picker():
        return fixture/'replacement-frozen'/'works.db'

    async def fake_images_picker():
        return fixture/'replacement-images'

    async def fake_verify(_cookie):
        calls['verify']+=1
        return True

    @app.get('/healthz')
    def health():
        return {'status':'ok','mode':'demo','data':'synthetic'}

    @app.get('/_fixture/state')
    def state():
        return {'revision':store.snapshot().revision,'verify_calls':calls['verify']}

    @app.post('/_fixture/shutdown')
    def shutdown():
        app.shutdown()
        return {'stopping':True}

    @ui.page('/')
    def page():
        view=NativeProfileView(store,pick_file=fake_file_picker,
                               pick_folder=fake_folder_picker,
                               pick_database=fake_database_picker,
                               pick_images=fake_images_picker,verify_auth=fake_verify)
        ui.button('設定',on_click=view.open).props('data-testid=native-fixture-settings')

    @ui.page('/inline')
    def inline_page():
        # Mirrors PreferencesView calling build() inside its own mounted card:
        # cancel must leave all of these existing button handlers operational.
        with ui.card():
            inline_view=NativeProfileView(inline_store,pick_file=fake_file_picker,
                                          pick_folder=fake_folder_picker)
            inline_view.build()

    app.on_shutdown(store.close)
    app.on_shutdown(inline_store.close)
    ui.run(host='127.0.0.1',port=port,show=False,reload=False,
           binding_refresh_interval=None,timeout_graceful_shutdown=5)


def test_native_profile_real_edge_ui_cancel_save_and_restart(tmp_path):
    credentials=tmp_path/'credentials'
    credentials.mkdir()
    source=credentials/'synthetic-cookies.txt'
    source.write_bytes(b'only fake local generated data')
    save_parent=tmp_path/'save-parent'
    save_parent.mkdir()
    frozen=tmp_path/'frozen'
    frozen.mkdir()
    old_db=frozen/'works.db'
    old_db.write_bytes(b'old generated frozen metadata')
    old_images=tmp_path/'old-images'
    old_images.mkdir()
    replacement=tmp_path/'replacement-frozen'
    replacement.mkdir()
    new_db=replacement/'works.db'
    new_db.write_bytes(b'new generated frozen metadata')
    new_images=tmp_path/'replacement-images'
    new_images.mkdir()
    baseline=source.read_bytes()
    failures=[]
    external=[]
    with sync_playwright() as playwright:
        channel=os.environ.get('PIXIVVAULT_BROWSER_CHANNEL', 'msedge' if os.name=='nt' else 'chromium')
        browser=playwright.chromium.launch(channel=channel,headless=True)
        try:
            for phase in ('initial','restart','readonly-repair'):
                if phase=='readonly-repair':
                    # Keep source data immutable; simulate an explicitly saved
                    # readonly profile whose original DB path later vanished.
                    from pixivvault_nicegui.native_profile import NativeProfileStore
                    profile=NativeProfileStore(tmp_path/'profile')
                    profile.update(cookie_file=None,library_readonly_db=old_db,
                                   library_image_root=old_images,expected_revision=1)
                    profile.close()
                    old_db.unlink()
                port=free_port()
                url=f'http://127.0.0.1:{port}'
                context=browser.new_context(viewport={'width':1280,'height':720})
                page=context.new_page()
                page.on('pageerror',lambda exc:failures.append(str(exc)))
                def route(request_route):
                    parsed=urlsplit(request_route.request.url)
                    if (parsed.scheme,parsed.netloc)==('http',f'127.0.0.1:{port}'):
                        request_route.continue_()
                    else:
                        external.append(parsed.netloc)
                        request_route.abort()
                context.route('**/*',route)
                process=None
                try:
                    process=subprocess.Popen(
                        [sys.executable,'-B',str(Path(__file__).resolve()),
                         '--serve',str(port),'--fixture',str(tmp_path)],
                        cwd=ROOT,env=child_environment(),stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                    wait_for_health(url,process)
                    if phase=='initial':
                        page.goto(url+'/inline',wait_until='domcontentloaded')
                        inline_cookie=page.get_by_test_id('native-profile-cookie-value')
                        inline_folder=page.get_by_test_id('native-profile-folder-value')
                        inline_cancel=page.get_by_test_id('native-profile-cancel')
                        expect(inline_cookie).to_have_text('未設定')
                        page.get_by_test_id('native-profile-pick-cookie').click()
                        page.get_by_test_id('native-profile-pick-folder').click()
                        expect(inline_cookie).to_contain_text('synthetic-cookies.txt')
                        expect(inline_folder).to_contain_text('save-parent')
                        inline_cancel.click()
                        expect(inline_cookie).to_have_text('未設定')
                        expect(inline_folder).to_have_text('未設定')
                        # The same live controls must work after inline cancel.
                        page.get_by_test_id('native-profile-pick-cookie').click()
                        expect(inline_cookie).to_contain_text('synthetic-cookies.txt')
                        page.get_by_test_id('native-profile-pick-folder').click()
                        page.get_by_test_id('native-profile-save').click()
                        expect(page.get_by_test_id('native-profile-status')).to_contain_text('次回起動')
                    page.goto(url,wait_until='domcontentloaded')
                    page.get_by_test_id('native-fixture-settings').click()
                    dialog=page.get_by_test_id('native-profile-dialog')
                    cookie=dialog.get_by_test_id('native-profile-cookie-value')
                    folder=dialog.get_by_test_id('native-profile-folder-value')
                    expect(dialog).to_be_visible()
                    if phase=='initial':
                        expect(cookie).to_have_text('未設定')
                        expect(folder).to_have_text('未設定')
                        dialog.get_by_test_id('native-profile-pick-cookie').click()
                        expect(cookie).to_contain_text('synthetic-cookies.txt')
                        dialog.get_by_test_id('native-profile-pick-folder').click()
                        expect(folder).to_contain_text('save-parent')
                        dialog.get_by_test_id('native-profile-cancel').click()
                        expect(dialog).not_to_be_visible()
                        page.get_by_test_id('native-fixture-settings').click()
                        expect(cookie).to_have_text('未設定')
                        expect(folder).to_have_text('未設定')
                        dialog.get_by_test_id('native-profile-pick-cookie').click()
                        dialog.get_by_test_id('native-profile-pick-folder').click()
                        expect(cookie).to_contain_text('synthetic-cookies.txt')
                        expect(folder).to_contain_text('save-parent')
                        assert page.request.get(url+'/_fixture/state').json()=={
                            'revision':0,'verify_calls':0}
                        dialog.get_by_test_id('native-profile-save').click()
                        expect(dialog.get_by_test_id('native-profile-status')).to_contain_text('次回起動')
                        assert page.request.get(url+'/_fixture/state').json()=={
                            'revision':1,'verify_calls':0}
                        dialog.get_by_test_id('native-profile-auth-check').click()
                        expect(dialog.get_by_test_id('native-profile-status')).to_have_text('認証を確認できました。')
                        assert page.request.get(url+'/_fixture/state').json()=={
                            'revision':1,'verify_calls':1}
                    elif phase=='restart':
                        expect(cookie).to_contain_text('synthetic-cookies.txt')
                        expect(folder).to_contain_text('save-parent')
                        assert page.request.get(url+'/_fixture/state').json()=={
                            'revision':1,'verify_calls':0}
                    else:
                        readonly=dialog.get_by_test_id('native-profile-library-section')
                        db=dialog.get_by_test_id('native-profile-database-value')
                        images_value=dialog.get_by_test_id('native-profile-images-value')
                        expect(readonly).to_be_visible()
                        expect(db).to_contain_text('frozen')
                        expect(images_value).to_contain_text('old-images')
                        dialog.get_by_test_id('native-profile-pick-database').click()
                        dialog.get_by_test_id('native-profile-pick-images').click()
                        expect(db).to_contain_text('replacement-frozen')
                        expect(images_value).to_contain_text('replacement-images')
                        dialog.get_by_test_id('native-profile-cancel').click()
                        page.get_by_test_id('native-fixture-settings').click()
                        expect(db).to_contain_text('frozen')
                        expect(images_value).to_contain_text('old-images')
                        dialog.get_by_test_id('native-profile-pick-database').click()
                        dialog.get_by_test_id('native-profile-pick-images').click()
                        dialog.get_by_test_id('native-profile-save').click()
                        expect(dialog.get_by_test_id('native-profile-status')).to_contain_text('次回起動')
                        assert page.request.get(url+'/_fixture/state').json()=={
                            'revision':3,'verify_calls':0}
                    assert source.read_bytes()==baseline
                    assert list(save_parent.iterdir())==[]
                finally:
                    if process is not None and process.poll() is None:
                        try:
                            page.request.post(url+'/_fixture/shutdown',timeout=5000)
                            process.wait(timeout=15)
                        except Exception:
                            pass
                    context.close()
                    if process is not None:
                        stop_child(process)
                        assert process.returncode==0
                    assert port_released(port)
        finally:
            browser.close()
    assert not failures and not external
    assert not old_db.exists() and new_db.read_bytes()==b'new generated frozen metadata'
    assert list(save_parent.iterdir())==[]
    from pixivvault_nicegui.native_profile import NativeProfileStore
    assert NativeProfileStore.peek(tmp_path/'inline-profile').revision==1


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--serve',type=int,required=True)
    parser.add_argument('--fixture',type=Path,required=True)
    args=parser.parse_args()
    _serve(args.serve,args.fixture)
