"""Exercise owned, isolated startup locks without constructing a live transport."""
import asyncio
from contextlib import closing, contextmanager
import errno
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Event, Thread
from time import monotonic

from nicegui import Client, app, ui
from nicegui.page import page as Page
import pytest

from pixivvault_nicegui import bootstrap, native_startup
from pixivvault_nicegui import managed_network_stop_startup as startup
from pixivvault_nicegui.managed_downloads import IDENTITY, MARKER
from pixivvault_nicegui.managed_network_stop_gate import (
    ManagedNetworkGateBusy, ManagedNetworkGateStorageError, ManagedNetworkStopGate,
)
from pixivvault_nicegui.managed_stop_migration import _FileLock
from pixivvault_nicegui.native_profile import NativeProfileStore
from pixivvault_nicegui.test_owner_runtime import isolated_test_workspace
from test_stage5_r1_native_first_draft_shared import _stub_native
from test_stage5_r1_shared_stop_transport import generated_owners


@pytest.fixture
def isolated(tmp_path):
    (tmp_path / '.pixivvault-isolated-test-workspace').write_bytes(
        b'PixivVault isolated source test workspace v1\n')
    with isolated_test_workspace(tmp_path):
        yield tmp_path


def selected_history(root):
    root.mkdir()
    (root / MARKER).write_text(json.dumps(IDENTITY), encoding='ascii')
    return ManagedNetworkStopGate(root / startup._LOCAL_GATE)


@contextmanager
def child_holds_lock(path):
    ready = path.parent / 'child-ready'
    source = (
        'from pathlib import Path\n'
        'from threading import Event\n'
        'from pixivvault_nicegui.managed_stop_migration import _FileLock\n'
        'import sys\n'
        'ticket = _FileLock(sys.argv[1], create=True)\n'
        'Path(sys.argv[2]).touch()\n'
        'Event().wait(12)\n'
        'ticket.close()\n'
    )
    with subprocess.Popen([sys.executable, '-B', '-c', source, str(path), str(ready)],
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as child:
        try:
            deadline = monotonic() + 8
            while not ready.is_file():
                assert child.poll() is None and monotonic() < deadline
                Event().wait(.01)
            yield child
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=3)


def test_startup_waits_for_owned_process_to_exit(isolated, monkeypatch):
    path = isolated / '.pixivvault-user-stop-startup.lock'
    contended, released = Event(), Event()
    from pixivvault_nicegui import managed_stop_migration

    def observe(*args, **kwargs):
        try:
            return _FileLock(*args, **kwargs)
        except OSError:
            contended.set()
            raise

    monkeypatch.setattr(managed_stop_migration, '_FileLock', observe)
    with child_holds_lock(path) as child:
        def kill_owned_child():
            if contended.wait(3):
                child.kill()
                child.wait(timeout=3)
                released.set()

        worker = Thread(target=kill_owned_child)
        worker.start()
        try:
            assert not startup.open_or_initialize_windows_user_stop().gate.snapshot().blocked
            assert released.is_set()
        finally:
            contended.set()
            worker.join(timeout=5)
            assert not worker.is_alive()
    assert path.read_bytes() == b'0'


def test_selected_history_waits_for_thread_and_keeps_stop(isolated, monkeypatch):
    root = isolated / 'images'
    gate = selected_history(root)
    gate.trip(429)
    before = gate.path.read_bytes()
    ready, release = Event(), Event()

    def hold():
        ticket = _FileLock(gate.lock_path)
        try:
            with closing(sqlite3.connect(gate.path)) as connection:
                connection.execute('BEGIN IMMEDIATE')
                connection.execute('UPDATE gate SET revision=revision+1')
                ready.set()
                release.wait(4)
                connection.rollback()
        finally:
            ticket.close()

    worker = Thread(target=hold)
    worker.start()
    try:
        assert ready.wait(3)
        from pixivvault_nicegui import managed_stop_migration

        def observe(*args, **kwargs):
            try:
                return _FileLock(*args, **kwargs)
            except OSError:
                release.set()
                raise

        monkeypatch.setattr(managed_stop_migration, '_FileLock', observe)
        assert gate.path.with_name(gate.path.name + '-journal').is_file()
        owner = startup.open_or_initialize_windows_user_stop(selected_roots=(('images', root),))
        assert release.is_set()
        assert owner.gate.snapshot().status_code == 429
        assert gate.path.read_bytes() == before
    finally:
        release.set()
        worker.join(timeout=5)
        assert not worker.is_alive()


def test_malformed_startup_lock_is_storage_error_and_is_preserved(isolated):
    lock = isolated / '.pixivvault-user-stop-startup.lock'
    lock.write_bytes(b'invalid')
    start = monotonic()
    with pytest.raises(ManagedNetworkGateStorageError) as error:
        startup.open_or_initialize_windows_user_stop()
    assert not isinstance(error.value, ManagedNetworkGateBusy)
    assert monotonic() - start < 1
    assert lock.read_bytes() == b'invalid'
    assert not (isolated / startup._ANCHOR).exists()


def test_open_permission_error_is_not_lock_contention(isolated, monkeypatch):
    lock = isolated / '.pixivvault-user-stop-startup.lock'
    lock.write_bytes(b'0')
    from pixivvault_nicegui import managed_stop_migration as migration
    real_open = migration._open_existing_stop_lock

    def open_path(path):
        if path == lock:
            raise PermissionError(errno.EACCES, 'generated ACL rejection')
        return real_open(path)

    def unexpected_wait(_):
        pytest.fail('a file open failure must not wait for a byte lock retry')

    monkeypatch.setattr(migration, '_open_existing_stop_lock', open_path)
    monkeypatch.setattr(startup, 'sleep', unexpected_wait)
    with pytest.raises(ManagedNetworkGateStorageError) as error:
        startup.open_or_initialize_windows_user_stop()
    assert not isinstance(error.value, ManagedNetworkGateBusy)
    assert not (isolated / startup._ANCHOR).exists()


def test_startup_ticket_is_published_after_sentinel_is_written(isolated, monkeypatch):
    lock = isolated / '.pixivvault-user-stop-startup.lock'
    real_open = Path.open
    observations = []

    def open_path(path, *args, **kwargs):
        stream = real_open(path, *args, **kwargs)
        if path.name.startswith(lock.name) and args in (('x+b',), ('xb',)):
            observations.append(lock.exists())
        return stream

    monkeypatch.setattr(Path, 'open', open_path)
    assert not startup.open_or_initialize_windows_user_stop().gate.snapshot().blocked
    assert observations == [False]
    assert lock.read_bytes() == b'0'


def test_uncontended_startup_never_waits_for_retry(isolated, monkeypatch):
    def unexpected_wait(_):
        pytest.fail('uncontended startup must not wait for a retry')

    monkeypatch.setattr(startup, 'sleep', unexpected_wait, raising=False)
    for _ in range(2):
        start = monotonic()
        assert not startup.open_or_initialize_windows_user_stop().gate.snapshot().blocked
        print(f'Uncontended local startup: {monotonic() - start:.3f}s')


def test_missing_established_owner_returns_without_wait(isolated, monkeypatch):
    owner = startup.open_or_initialize_windows_user_stop()
    owner.root.rename(isolated / 'saved-owner')
    anchor = isolated / startup._ANCHOR
    before = anchor.read_bytes()

    def unexpected_wait(_):
        pytest.fail('an absent established owner must not wait for a retry')

    monkeypatch.setattr(startup, 'sleep', unexpected_wait, raising=False)
    with pytest.raises(ManagedNetworkGateStorageError) as error:
        startup.open_or_initialize_windows_user_stop()
    assert not isinstance(error.value, ManagedNetworkGateBusy)
    assert anchor.read_bytes() == before and not owner.root.exists()


def test_reader_creation_retries_busy_stop_aggregation(isolated, monkeypatch):
    profile, _, _, cookie = generated_owners(isolated)
    owner = startup.open_or_initialize_windows_user_stop()
    profile.gate.trip(403)
    ticket = _FileLock(owner.gate.lock_path)
    real_trip = owner.gate.trip
    contended = []

    def observe_trip(status):
        try:
            return real_trip(status)
        except ManagedNetworkGateBusy:
            contended.append(True)
            ticket.close()
            raise

    monkeypatch.setattr(owner.gate, 'trip', observe_trip)
    try:
        reader, *_ = bootstrap._build_owned_media_readers(
            pixiv_cookie_file=cookie, pixiv_download_root=None, novel_download_root=None,
            novel_fixture_root=None, novel_source=None, ugoira_root=None, ugoira_fixture_root=None,
            live_client=None, managed_network_stop_scope=profile, shared_network_stop_scope=owner,
            startup_stop_deadline=startup.startup_lock_deadline())
        try:
            assert contended == [True]
            assert owner.gate.snapshot().status_code == 403
        finally:
            reader.close()
    finally:
        ticket.close()


def test_recovered_auth_refuses_a_changed_profile_revision(isolated, monkeypatch):
    profile, _, _, cookie = generated_owners(isolated)
    owner = startup.open_or_initialize_windows_user_stop(profile_scope=profile)
    binding = native_startup.NativeProfileBinding(NativeProfileStore(profile.root))
    try:
        binding.auth_stop_required = binding.auth_shared_stop_required = True
        binding.shared_network_stop_scope = owner
        binding.managed_network_stop_scope = profile
        binding.recheck_startup_stop = startup.open_or_initialize_windows_user_stop
        from pixivvault_nicegui import managed_network_stop_shared_transport

        def unexpected_reader(*args, **kwargs):
            pytest.fail('an old recovery page must not construct a reader after settings change')

        monkeypatch.setattr(managed_network_stop_shared_transport, 'create_shared_draft_auth_reader',
                            unexpected_reader)
        new_parent = isolated / 'new-save-parent'
        new_parent.mkdir()
        binding.store.update(save_parent=new_parent, expected_revision=binding.launch_snapshot.revision)
        result = asyncio.run(binding.check_authentication(cookie, _publish=False))
        assert result.code == 'profile_changed'
    finally:
        binding.close()


@pytest.mark.parametrize('after_release', [
    'allowed', 'stopped', 'uncertain', 'registration', 'profile', 'rollback', 'shared-rollback',
])
def test_busy_native_page_rechecks_locally_without_restarting(isolated, monkeypatch, after_release):
    # Keep the same real shared owner and lock through startup and the UI click.
    owner = startup.open_or_initialize_windows_user_stop()
    profile_root = isolated / 'profile'
    lock = owner.gate.lock_path
    if after_release in {'profile', 'rollback'}:
        profile, _, _, _ = generated_owners(isolated)
        profile_root = profile.root
        if after_release == 'profile':
            lock = profile.gate.lock_path
        else:
            profile.gate.trip(429)
    late_busy = after_release in {'registration', 'shared-rollback'}
    if after_release == 'shared-rollback':
        owner.gate.trip(429)
    ticket = None if late_busy else _FileLock(lock)
    monkeypatch.setattr(startup, '_STARTUP_LOCK_RETRY_SECONDS', .15, raising=False)
    monkeypatch.setattr(startup, '_STARTUP_LOCK_POLL_SECONDS', .025, raising=False)
    register_recovery = native_startup.register_profile_recovery
    calls, recovered, registered = _stub_native(monkeypatch)
    pages, buttons, labels = {}, [], []

    def inspect_recovery(binding, **kwargs):
        if late_busy:
            assert binding.shared_network_stop_scope is None
            assert binding.managed_network_stop_scope.shared_owner is None
        return register_recovery(binding, **kwargs)

    monkeypatch.setattr(native_startup, 'register_profile_recovery', inspect_recovery)
    monkeypatch.setattr(ui, 'page', lambda route: lambda fn: pages.setdefault(route, fn))
    monkeypatch.setattr(app, 'get', lambda *a, **kw: lambda fn: fn)
    monkeypatch.setattr(app, 'on_shutdown', lambda fn: None)
    real_label = ui.label
    real_button = ui.button

    def label(*args, **kwargs):
        element = real_label(*args, **kwargs)
        labels.append(element)
        return element

    def button(label='', on_click=None, **kwargs):
        element = real_button(label, on_click=on_click, **kwargs)
        buttons.append((element, on_click))
        return element

    monkeypatch.setattr(ui, 'label', label)
    monkeypatch.setattr(ui, 'button', button)
    if late_busy:
        def register(**kwargs):
            nonlocal ticket
            ticket = _FileLock(owner.gate.lock_path)
            kwargs['shared_network_stop_scope'].gate.snapshot()

        monkeypatch.setattr(bootstrap, 'register_pages', register)
    if sys.platform == 'win32':
        from pixivvault_nicegui import native_instance
        from types import SimpleNamespace
        monkeypatch.setattr(native_instance, 'claim_or_restore',
                            lambda _: SimpleNamespace(close=lambda: None))

    async def recheck():
        client = Client(Page('/generated-startup-recheck'))
        try:
            with client:
                pages['/']()
                named = {element.props.get('data-testid'): (element, callback)
                         for element, callback in buttons}
                element, callback = named['startup-stop-recheck']
                auth_button, _ = named['native-profile-auth-check']
                assert '再確認' in element.text
                assert not auth_button.visible
                await callback()
                assert element.enabled
                assert not auth_button.visible
                ticket.close()
                if after_release == 'stopped':
                    owner.gate.trip(403)
                if after_release == 'uncertain':
                    owner.gate.inflight_path.write_bytes(b'pixivvault-user-pixiv-get-inflight-v1\n')
                if after_release in {'rollback', 'shared-rollback'}:
                    gate = profile.gate if after_release == 'rollback' else owner.gate
                    with closing(sqlite3.connect(gate.path)) as connection:
                        connection.execute('UPDATE gate SET blocked=0, status=NULL, revision=0')
                        connection.commit()

                def unexpected_wait(_):
                    pytest.fail('recheck must not wait for a retry after a local outcome')

                monkeypatch.setattr(startup, 'sleep', unexpected_wait)
                await callback()
                assert not element.enabled
                assert auth_button.visible == (after_release in {'allowed', 'registration', 'profile'})
                expected = {'allowed': '通信記録を確認できました',
                            'registration': '通信記録を確認できました',
                            'profile': '通信記録を確認できました',
                            'stopped': '既存のSTOP記録', 'uncertain': '前回の通信結果が不明',
                            'rollback': 'STOP保護または設定の保存状態を確認できません',
                            'shared-rollback': 'STOP保護または設定の保存状態を確認できません'}
                assert any(expected[after_release] in element.text for element in labels)
                if after_release == 'allowed':
                    with_reloaded_page()
        finally:
            client.delete()

    def with_reloaded_page():
        new_client = Client(Page('/reloaded-startup-recheck'))
        try:
            with new_client:
                pages['/']()
                named = {el.props.get('data-testid'): el for el, _ in buttons}
                assert named['native-profile-auth-check'].visible
                assert not named['startup-stop-recheck'].enabled
        finally:
            new_client.delete()

    def serve(**kwargs):
        calls.append(kwargs)
        asyncio.run(recheck())

    monkeypatch.setattr(ui, 'run', serve)
    try:
        started = monotonic()
        assert bootstrap.main(['--native', '--profile-root', str(profile_root)]) == 0
        minimum_wait = .1 if late_busy else .25
        assert minimum_wait <= monotonic() - started < 4
        assert len(calls) == 1 and not recovered and not registered
        if after_release == 'stopped':
            assert owner.gate.snapshot().status_code == 403
        if after_release == 'uncertain':
            assert owner.gate.inflight_path.exists()
    finally:
        if ticket is not None:
            ticket.close()
