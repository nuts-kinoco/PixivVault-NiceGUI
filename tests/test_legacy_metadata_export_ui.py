"""E01 settings entry and application wiring with isolated, synthetic old DB."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
import time
import zipfile

from nicegui import Client, ui
import pytest

from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.preferences import PreferencesStore
from pixivvault_nicegui.ui.preferences_view import PreferencesView
from pixivvault_nicegui.ui.shell import DemoShell
from pixivvault_nicegui.legacy_metadata_export import LegacyMetadataExportError
from pixivvault_nicegui import legacy_metadata_export
from test_legacy_backup_history import legacy_fixture


@pytest.mark.asyncio
async def test_settings_explicit_export_bounded_status_and_reopen(tmp_path):
    store = PreferencesStore(tmp_path/'prefs.sqlite3')
    client = Client(ui.page('/_legacy_export_ui'))
    started, release = Event(), Event()
    calls = []

    def slow_export():
        calls.append('click')
        started.set()
        assert release.wait(4)
        return SimpleNamespace(path=tmp_path/'owned'/'finished.zip', works=3)

    task = None
    try:
        with client:
            view = PreferencesView(store, export_legacy=slow_export)
            view.open()
            assert view.export_button.enabled
            task = asyncio.create_task(view.run_legacy_export())
            assert await asyncio.to_thread(started.wait, 3)
            assert view.export_busy and not view.export_button.enabled
            await view.run_legacy_export()
            assert calls == ['click']
            view.close_dialog()
            release.set()
            await task
            assert '[完了]' in view.export_message and '画像は含まれません' in view.export_message
            view.open()
            assert view.export_button.enabled and view.export_status.text == view.export_message
            view.close()
    finally:
        release.set()
        if task is not None and not task.done():
            await task
        client.delete()
        store.close()


@pytest.mark.asyncio
async def test_settings_export_refuses_errors_and_recovers_button(tmp_path):
    store = PreferencesStore(tmp_path/'prefs.sqlite3')
    client = Client(ui.page('/_legacy_export_failed'))
    try:
        with client:
            def fail():
                raise LegacyMetadataExportError('独立コピーが必要です。')
            view = PreferencesView(store, export_legacy=fail)
            view.open()
            await view.run_legacy_export()
            assert view.export_button.enabled and not view.export_busy
            assert view.export_status.text == '[失敗] 独立コピーが必要です。'
            view.close()
            unavailable = PreferencesView(store)
            unavailable.open()
            assert not unavailable.export_button.enabled
            unavailable.close()
    finally:
        client.delete()
        store.close()


def test_old_readonly_service_connects_e01_without_source_or_image_mutation(tmp_path):
    source_dir=tmp_path/'frozen';source_dir.mkdir()
    old, images = legacy_fixture(source_dir, [
        ('901', 'sample', 1, '[]', '99', 0, 'illust', '2026-10-28'),
    ], [('99', 'author')])
    separate_images=tmp_path/'frozen-images'
    images.rename(separate_images)
    images=separate_images
    original = old.read_bytes()
    media = images/'user-image.png'
    media.write_bytes(b'original media untouched')
    pref = tmp_path/'owned-prefs'
    service = ApplicationServices(library_readonly_db=old, library_image_root=images,
                                  preferences_root=pref)
    try:
        assert service.legacy_metadata_export_available()
        report = service.export_legacy_metadata()
        assert report.path.parent == pref/'exports'
        with zipfile.ZipFile(report.path) as archive:
            assert set(archive.namelist()) == {'pixiv_vault.db', 'metadata.json'}
        assert old.read_bytes() == original and media.read_bytes() == b'original media untouched'
        assert not list(old.parent.glob(old.name + '-*'))
    finally:
        service.close()


@pytest.mark.asyncio
async def test_shell_passes_e01_to_settings_when_old_db_and_owned_settings_exist(tmp_path, monkeypatch):
    from nicegui import core
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    source_dir=tmp_path/'frozen';source_dir.mkdir()
    old, images = legacy_fixture(source_dir, [
        ('901', 'sample', 1, '[]', '99', 0, 'illust', '2026-10-28'),
    ], [('99', 'author')])
    separate_images=tmp_path/'frozen-images'
    images.rename(separate_images)
    images=separate_images
    service = ApplicationServices(library_readonly_db=old, library_image_root=images,
                                  preferences_root=tmp_path/'owned-prefs')
    client = Client(ui.page('/_legacy_export_shell'))
    try:
        with client:
            shell = DemoShell(service)
            shell.open_preferences()
            await shell.following_view._initial_task
            assert shell.preferences_view is not None
            assert shell.preferences_view.export_legacy is not None
            assert shell.preferences_view.export_button.enabled
            shell.close()
    finally:
        client.delete()
        service.close()


@pytest.mark.asyncio
async def test_post_publish_stage_cleanup_warning_keeps_completed_zip_status(tmp_path):
    store=PreferencesStore(tmp_path/'prefs.sqlite3')
    client=Client(ui.page('/_e01_cleanup_warning'))
    report=SimpleNamespace(path=tmp_path/'synthetic-complete.zip',works=3,cleanup_warning=True)
    try:
        with client:
            view=PreferencesView(store,export_legacy=lambda:report)
            view.open()
            await view.run_legacy_export()
            assert view.export_message.startswith('[完了・一時フォルダーの後始末要確認]')
            assert '[失敗]' not in view.export_message
            assert '画像は含まれません' in view.export_status.text
            assert view.export_button.enabled
            view.close()
    finally:
        client.delete()
        store.close()


def test_application_close_cancels_e01_before_closing_owned_resources(tmp_path, monkeypatch):
    source_dir=tmp_path/'frozen';source_dir.mkdir()
    old, images=legacy_fixture(source_dir, [
        ('901','sample',1,'[]','99',0,'illust','2026-10-28'),
    ], [('99','author')])
    independent_images=tmp_path/'frozen-images'
    images.rename(independent_images)
    service=ApplicationServices(library_readonly_db=old,library_image_root=independent_images,
                                preferences_root=tmp_path/'owned-prefs')
    entered=Event()
    observed=[]

    def fake_export(*args, cancelled):
        entered.set()
        deadline=time.monotonic()+5
        while not cancelled() and time.monotonic()<deadline:
            time.sleep(.01)
        observed.append('stop' if cancelled() else 'timeout')
        raise LegacyMetadataExportError('終了要求によりエクスポートを中止しました。')

    monkeypatch.setattr(legacy_metadata_export, 'export_frozen_legacy_metadata', fake_export)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            export_future=pool.submit(service.export_legacy_metadata)
            assert entered.wait(2)
            close_future=pool.submit(service.close)
            assert close_future.result(timeout=6) is None
            with pytest.raises(LegacyMetadataExportError,match='中止しました'):
                export_future.result(timeout=2)
        assert observed == ['stop']
        assert service.closed and service.closing
    finally:
        if not service.closed:
            service.close()
