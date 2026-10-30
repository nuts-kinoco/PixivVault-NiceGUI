"""L18: real root selection and queued header actions; generated paths, fake OS launch."""
from __future__ import annotations

import asyncio
from hashlib import sha256
from pathlib import Path
import sqlite3
from threading import Event, get_ident
from types import SimpleNamespace

from nicegui import Client, background_tasks, core, ui
from nicegui.events import GenericEventArguments, handle_event
import pytest
import pytest_asyncio

from pixivvault_nicegui.desktop_integration import DesktopActionRejected, DesktopIntegration
from pixivvault_nicegui.fs_paths import logical_path
from pixivvault_nicegui.native_profile import NativeProfileStore, STORAGE_FOLDER, STORAGE_MARKER, provision_storage
from pixivvault_nicegui.native_startup import NativeProfileBinding
from pixivvault_nicegui.services import ApplicationServices


class Launcher:
    def __init__(self):
        self.calls = []

    def open_default(self, path):
        self.calls.append((logical_path(path), get_ident()))


def desktop(*roots):
    launcher = Launcher()
    return DesktopIntegration(allowed_roots=roots, launcher=launcher), launcher


def profile(tmp_path):
    parent = tmp_path / 'selected-parent'
    parent.mkdir()
    store = NativeProfileStore(tmp_path / 'profile')
    launched = store.update(save_parent=parent, expected_revision=0)
    provision_storage(launched, store.root)
    return parent, store, NativeProfileBinding(store)


@pytest_asyncio.fixture
async def ui_runtime(monkeypatch):
    from nicegui.app.app import State
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    monkeypatch.setattr(core.app, '_state', State.STARTED)


def action(button):
    callbacks = [event.handler for event in button._event_listeners.values()
                 if event.type == 'click']
    assert len(callbacks) == 1
    return callbacks[0], {'token': button.props['data-folder-token']}


async def click(button, client, *, captured=None):
    callback, payload = action(button) if captured is None else captured
    before = set(background_tasks.running_tasks)
    with client:
        handle_event(callback, GenericEventArguments(sender=button, client=client, args=payload))
    pending = set(background_tasks.running_tasks) - before
    if pending:
        await asyncio.gather(*pending)


def by_testid(client, testid):
    return next(element for element in client.elements.values()
                if element.props.get('data-testid') == testid)


@pytest.mark.asyncio
async def test_native_header_opens_only_marked_startup_storage_once(tmp_path, ui_runtime):
    from pixivvault_nicegui.ui.shell import DemoShell
    parent, store, binding = profile(tmp_path)
    base = parent / STORAGE_FOLDER
    unrelated = tmp_path / 'unrelated-allowed-root'
    unrelated.mkdir()
    integration, launcher = desktop(unrelated, base)
    services = ApplicationServices(8, desktop=integration)
    services.native_profile = binding
    client = Client(ui.page('/_l18_native_header'))
    marker = base / STORAGE_MARKER
    before = (marker.read_bytes(), marker.stat().st_mtime_ns, tuple(parent.iterdir()))
    try:
        choices = services.saved_folder_choices()
        assert len(choices) == 1 and choices[0].key == 'native'
        assert choices[0].path == logical_path(base)
        assert logical_path(parent) not in integration.allowed_roots
        with client:
            shell = DemoShell(services)
        button = by_testid(client, 'saved-folder-open')
        assert not button.props.get('disable')
        assert launcher.calls == [], 'building the real header must never open Explorer'
        captured = action(button)
        await click(button, client, captured=captured)
        assert [value[0] for value in launcher.calls] == [logical_path(base)]
        assert launcher.calls[0][1] != get_ident(), 'OS launch must not block the UI thread'
        await click(button, client, captured=captured)
        assert len(launcher.calls) == 1, 'one queued DOM token must never launch twice'
        assert (marker.read_bytes(), marker.stat().st_mtime_ns, tuple(parent.iterdir())) == before
        shell.close()
    finally:
        client.delete()
        services.close()
        binding.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['settings', 'marker'])
async def test_native_captured_location_rejects_new_settings_or_lost_ownership(tmp_path, change):
    parent, store, binding = profile(tmp_path)
    base = parent / STORAGE_FOLDER
    integration, launcher = desktop(base)
    services = ApplicationServices(8, desktop=integration)
    services.native_profile = binding
    replacement = tmp_path / 'new-parent'
    replacement.mkdir()
    try:
        selected, = services.saved_folder_choices()
        if change == 'settings':
            store.update(save_parent=replacement, expected_revision=store.snapshot().revision)
            reason = '再起動'
        else:
            (base / STORAGE_MARKER).write_bytes(b'generated invalid ownership marker')
            reason = '所有'
        with pytest.raises(DesktopActionRejected, match=reason):
            await services.open_saved_folder(selected)
        assert launcher.calls == []
        assert list(replacement.iterdir()) == [], 'header actions never provision a new root'
        assert base.is_dir()
    finally:
        services.close()
        binding.close()


@pytest.mark.asyncio
async def test_cli_readonly_location_is_explicit_and_never_the_first_allowed_root(tmp_path):
    source = tmp_path / 'frozen'
    source.mkdir()
    path = source / 'library.sqlite3'
    with sqlite3.connect(path) as connection:
        connection.executescript('''
            CREATE TABLE works (work_id TEXT PRIMARY KEY, title TEXT, page_count INTEGER,
                tags TEXT, user_id TEXT, is_deleted INTEGER DEFAULT 0);
            CREATE TABLE following_users (user_id TEXT PRIMARY KEY, name TEXT);
            INSERT INTO works VALUES ('11','generated',1,'[]','91',0);
            INSERT INTO following_users VALUES ('91','generated author');
        ''')
    images = tmp_path / 'explicit-old-images'
    images.mkdir()
    payload = images / 'preserved.bin'
    payload.write_bytes(b'generated source bytes; never moved or rewritten')
    unrelated = tmp_path / 'other-allowed'
    unrelated.mkdir()
    integration, launcher = desktop(unrelated, images)
    before = [(sha256(item.read_bytes()).hexdigest(), item.stat().st_mtime_ns)
              for item in (path, payload)]
    services = ApplicationServices(library_readonly_db=path, library_image_root=images,
                                   desktop=integration)
    try:
        selected, = services.saved_folder_choices()
        assert selected.key == 'readonly' and '読取専用' in selected.label
        await services.open_saved_folder(selected)
        assert [value[0] for value in launcher.calls] == [logical_path(images)]
    finally:
        services.close()
    assert [(sha256(item.read_bytes()).hexdigest(), item.stat().st_mtime_ns)
            for item in (path, payload)] == before
    assert all(not Path(str(path) + suffix).exists() for suffix in ('-wal', '-shm', '-journal'))


@pytest.mark.asyncio
async def test_multiple_cli_media_require_a_named_choice_before_os_open(tmp_path, ui_runtime):
    from pixivvault_nicegui.novel_save_presentation import build_novel_ui_fixture
    from pixivvault_nicegui.ugoira_fixtures import build_ugoira_fixture
    from pixivvault_nicegui.ui.saved_folder_button import SavedFolderButton
    novel, ugoira = tmp_path / 'novels', tmp_path / 'animation'
    integration, launcher = desktop(ugoira, novel)
    services = ApplicationServices(8, desktop=integration,
        novel_fixture_root=novel, novel_fixture_source=build_novel_ui_fixture(),
        ugoira_root=ugoira, ugoira_source=build_ugoira_fixture())
    client = Client(ui.page('/_l18_cli_choice'))
    try:
        assert {item.key: item.label for item in services.saved_folder_choices()} == {
            'novel': '小説', 'ugoira': 'うごイラ'}
        with client:
            view = SavedFolderButton(services)
        assert launcher.calls == []
        await click(view.button, client)
        assert view.menu.value is True and launcher.calls == []
        selected = by_testid(client, 'saved-folder-choice-novel')
        old = action(selected)
        await click(selected, client, captured=old)
        assert [value[0] for value in launcher.calls] == [logical_path(novel)]
        await click(selected, client, captured=old)
        assert len(launcher.calls) == 1
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['owner', 'desktop'])
async def test_cli_old_selection_does_not_follow_replaced_owner_or_desktop(tmp_path, change):
    root = tmp_path / 'generated-downloads'
    integration, launcher = desktop(root)
    services = ApplicationServices(8, download_sandbox=root, desktop=integration)
    original = services.download_writer
    replacement = tmp_path / 'replacement-downloads'
    replacement.mkdir()
    replacement_desktop, other_launcher = desktop(replacement)
    try:
        selected, = services.saved_folder_choices()
        if change == 'owner':
            services.download_writer = SimpleNamespace(root=replacement)
        else:
            services.desktop = replacement_desktop
        with pytest.raises(DesktopActionRejected):
            await services.open_saved_folder(selected)
        assert launcher.calls == [] and other_launcher.calls == []
    finally:
        services.download_writer = original
        services.desktop = integration
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stop', ['client', 'service'])
async def test_queued_launch_rechecks_close_and_client_generation_inside_close_fence(tmp_path, stop):
    root = tmp_path / 'generated-downloads'
    integration, launcher = desktop(root)
    services = ApplicationServices(8, download_sandbox=root, desktop=integration)
    entered = Event()
    active = True
    pending = None
    locked = False
    def current():
        entered.set()
        return active
    try:
        selected, = services.saved_folder_choices()
        services._close_lock.acquire()
        locked = True
        pending = asyncio.create_task(services.open_saved_folder(selected, is_current=current))
        assert await asyncio.to_thread(entered.wait, 2)
        if stop == 'client':
            active = False
        else:
            services.closing = True
        services._close_lock.release()
        locked = False
        with pytest.raises(DesktopActionRejected):
            await pending
        assert launcher.calls == []
    finally:
        if locked:
            services._close_lock.release()
        if pending is not None and not pending.done():
            await asyncio.gather(pending, return_exceptions=True)
        services.close()


@pytest.mark.asyncio
async def test_header_tokens_are_client_local_and_client_close_revokes_late_clicks(tmp_path, ui_runtime):
    from pixivvault_nicegui.ui.saved_folder_button import SavedFolderButton
    root = tmp_path / 'generated-downloads'
    integration, launcher = desktop(root)
    services = ApplicationServices(8, download_sandbox=root, desktop=integration)
    clients = [Client(ui.page('/_l18_client_a')), Client(ui.page('/_l18_client_b'))]
    try:
        views = []
        for client in clients:
            with client:
                views.append(SavedFolderButton(services))
        old_a, old_b = action(views[0].button), action(views[1].button)
        await click(views[0].button, clients[0], captured=(old_a[0], old_b[1]))
        assert launcher.calls == []
        clients[0].delete()
        # The callback was already queued before the first client was deleted.
        await old_a[0](GenericEventArguments(sender=views[0].button, client=clients[0], args=old_a[1]))
        assert launcher.calls == []
        await click(views[1].button, clients[1], captured=old_b)
        assert len(launcher.calls) == 1
        assert not services.closing and not services.closed
    finally:
        for client in clients:
            if client.id in Client.instances:
                client.delete()
        services.close()


@pytest.mark.asyncio
async def test_unconfigured_header_is_disabled_and_does_not_create_storage(tmp_path, ui_runtime):
    from pixivvault_nicegui.ui.saved_folder_button import SavedFolderButton
    unrelated = tmp_path / 'allowed-but-not-owned-by-a-media-service'
    unrelated.mkdir()
    integration, launcher = desktop(unrelated)
    services = ApplicationServices(8, desktop=integration)
    client = Client(ui.page('/_l18_unconfigured'))
    try:
        assert services.saved_folder_choices() == ()
        with client:
            view = SavedFolderButton(services)
        assert view.button.props.get('disable') is True
        await click(view.button, client)
        assert launcher.calls == [] and list(unrelated.iterdir()) == []
    finally:
        client.delete()
        services.close()


def test_desktop_authority_adds_owned_native_storage_never_the_selected_parent(tmp_path):
    from pixivvault_nicegui.saved_folders import desktop_saved_roots
    parent, store, binding = profile(tmp_path)
    try:
        media = parent / STORAGE_FOLDER / 'novels'
        roots = desktop_saved_roots(binding, media)
        assert set(roots) == {logical_path(parent / STORAGE_FOLDER), logical_path(media)}
        assert logical_path(parent) not in roots
        empty = NativeProfileStore(tmp_path / 'empty-profile')
        empty_binding = NativeProfileBinding(empty)
        try:
            assert desktop_saved_roots(empty_binding) == ()
        finally:
            empty_binding.close()
    finally:
        binding.close()
