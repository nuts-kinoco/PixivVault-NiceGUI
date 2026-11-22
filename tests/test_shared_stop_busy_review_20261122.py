"""Neon review regressions with isolated owners and no network."""
import asyncio
import os
import sys
from threading import Event
from types import SimpleNamespace

import pytest

from pixivvault_nicegui import bootstrap, native_startup
from pixivvault_nicegui import managed_network_stop_startup as startup
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkGateBusy
from pixivvault_nicegui.managed_stop_migration import StopMigrationService, _FileLock
from pixivvault_nicegui.native_profile import NativeProfileStore
from test_shared_stop_busy_recovery_20261122 import isolated, selected_history
from test_stage5_r1_native_first_draft_shared import _stub_native
from test_stage5_r1_shared_stop_transport import generated_owners


def stub_launch(monkeypatch):
    from pixivvault_nicegui import native_instance
    if sys.platform == 'win32':
        monkeypatch.setattr(native_instance, 'claim_or_restore',
                            lambda _: SimpleNamespace(close=lambda: None))
    return _stub_native(monkeypatch)


@pytest.mark.parametrize('keep_busy', [False, True])
def test_profile_lock_recovery_precedes_shared_owner(isolated, monkeypatch, keep_busy):
    from pixivvault_nicegui import managed_network_stop_gate as gate_module
    from pixivvault_nicegui import managed_network_stop_shared_scope as shared
    profile, _, _, _ = generated_owners(isolated)
    ticket = _FileLock(profile.gate.lock_path)
    _, recovery, registrations = stub_launch(monkeypatch)
    monkeypatch.setattr(startup, '_STARTUP_LOCK_RETRY_SECONDS', .15)
    monkeypatch.setattr(startup, '_STARTUP_LOCK_POLL_SECONDS', .025)
    open_existing = shared.open_existing_windows_user_stop
    lock_byte = gate_module._set_stop_byte_lock
    shared_calls, refused = [], []

    def observe_shared():
        shared_calls.append(True)
        return open_existing()

    def observe_lock(stream, *, lock):
        try:
            return lock_byte(stream, lock=lock)
        except OSError:
            info = os.fstat(stream.fileno())
            if lock and gate_module._file_identity(profile.gate.lock_path) == (info.st_dev, info.st_ino):
                refused.append(True)
                if not keep_busy:
                    ticket.close()
            raise

    monkeypatch.setattr(shared, 'open_existing_windows_user_stop', observe_shared)
    monkeypatch.setattr(gate_module, '_set_stop_byte_lock', observe_lock)
    try:
        assert bootstrap.main(['--native', '--profile-root', str(profile.root)]) == 0
        assert refused
        if keep_busy:
            binding, flags = recovery[0]
            assert not shared_calls and not registrations
            assert flags == {'stop_gate_busy': True}
            assert binding.recheck_startup_stop is not None
        else:
            assert shared_calls and not recovery and len(registrations) == 1
    finally:
        ticket.close()


def test_startup_and_formal_migration_cannot_publish_over_each_other(isolated, monkeypatch):
    store = NativeProfileStore(isolated / 'migration-profile')
    service = StopMigrationService(store)
    plan = service.preview()
    write_new = startup._write_new
    results = []

    def interrupt(path, data):
        write_new(path, data)
        if path.name == startup._ANCHOR:
            results.append(service.execute(plan, confirmed=True))

    monkeypatch.setattr(startup, '_write_new', interrupt)
    try:
        owner = startup.open_or_initialize_windows_user_stop()
        assert len(results) == 1 and results[0].reason == 'source_busy'
        assert startup.open_or_initialize_windows_user_stop().root == owner.root
    finally:
        store.close()


def test_startup_during_formal_publication_preserves_the_migrated_stop(isolated):
    store = NativeProfileStore(isolated / 'migration-profile')
    images = isolated / 'images'
    selected_history(images).trip(429)
    service = StopMigrationService(store)
    plan = service.preview((('images', images),))
    checked = []

    def checkpoint(phase):
        if phase == 'before-publish':
            with pytest.raises(ManagedNetworkGateBusy):
                startup.open_or_initialize_windows_user_stop(deadline=startup.monotonic())
            checked.append(True)

    service._checkpoint = checkpoint
    try:
        assert service.execute(plan, confirmed=True).status == 'completed'
        assert checked == [True]
        assert not (service.target_root.parent / startup._ANCHOR).exists()
        owner = startup.open_or_initialize_windows_user_stop()
        assert owner.gate.snapshot().status_code == 429
    finally:
        store.close()


def test_retry_gets_one_final_attempt_after_wait_overshoots(monkeypatch):
    now = [0.0]
    attempts = []

    def operation():
        attempts.append(now[0])
        if len(attempts) == 1:
            raise ManagedNetworkGateBusy()
        return True

    def oversleep(_):
        now[0] = .26

    monkeypatch.setattr(startup, 'monotonic', lambda: now[0])
    monkeypatch.setattr(startup, 'sleep', oversleep)
    assert startup.retry_startup_lock(operation, deadline=.255) is True
    assert attempts == [0, .26]


def test_recheck_single_worker_and_shutdown_cancellation(isolated):
    binding = native_startup.NativeProfileBinding(NativeProfileStore(isolated / 'profile'))
    entered, returned, release = Event(), Event(), Event()
    calls = []

    def open_scopes(*, deadline, cancel):
        calls.append(True)
        entered.set()
        try:
            startup.retry_startup_lock(lambda: busy_until_released(), deadline=deadline, cancel=cancel)
            pytest.fail('shutdown must prevent successful publication')
        finally:
            returned.set()

    def busy_until_released():
        assert not release.is_set()
        raise ManagedNetworkGateBusy()

    binding.recheck_startup_stop = open_scopes

    async def exercise():
        first = asyncio.create_task(binding.check_startup_stop(Event()))
        assert await asyncio.to_thread(entered.wait, 2)
        assert (await binding.check_startup_stop(Event()))[1] == 'busy'
        await asyncio.wait_for(asyncio.to_thread(binding.close), 2)
        assert returned.is_set()
        assert (await first)[1] == 'closed'
        assert calls == [True]
        assert binding.shared_network_stop_scope is binding.managed_network_stop_scope is None

    try:
        asyncio.run(exercise())
    finally:
        release.set()
        binding.close()


@pytest.mark.skipif(os.name != 'nt', reason='Win32 share modes')
def test_windows_exclusive_open_is_busy_until_external_handle_closes(isolated):
    import ctypes
    from ctypes import wintypes
    from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopGate

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    open_file = kernel.CreateFileW
    open_file.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                          wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    open_file.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    owner = startup.open_or_initialize_windows_user_stop()
    lock_path = owner.gate.lock_path
    handle = open_file(str(lock_path), 0x80000000, 0, None, 3, 0x80, None)
    assert handle != ctypes.c_void_p(-1).value
    try:
        with pytest.raises(ManagedNetworkGateBusy):
            ManagedNetworkStopGate(owner.gate.path)
        with pytest.raises(ManagedNetworkGateBusy):
            startup._acquire_file_lock(lock_path)
    finally:
        assert kernel.CloseHandle(handle)
    assert not ManagedNetworkStopGate(owner.gate.path).snapshot().blocked


def test_file_ticket_releases_byte_before_closing(isolated, monkeypatch):
    from pixivvault_nicegui import managed_stop_migration as migration
    real_lock = migration._set_stop_byte_lock
    calls = []

    def observe(stream, *, lock):
        assert not stream.closed
        calls.append(lock)
        return real_lock(stream, lock=lock)

    monkeypatch.setattr(migration, '_set_stop_byte_lock', observe)
    ticket = _FileLock(isolated / 'test.lock', create=True)
    ticket.close()
    ticket.close()
    assert calls == [True, False]


def test_ticket_close_releases_lock_when_explicit_unlock_fails(isolated, monkeypatch):
    from pixivvault_nicegui import managed_stop_migration as migration
    real_lock = migration._set_stop_byte_lock
    path = isolated / 'test.lock'
    ticket = _FileLock(path, create=True)

    def refuse_unlock(stream, *, lock):
        if not lock:
            raise OSError('generated explicit unlock error')
        return real_lock(stream, lock=lock)

    with monkeypatch.context() as context:
        context.setattr(migration, '_set_stop_byte_lock', refuse_unlock)
        ticket.close()
    _FileLock(path).close()


def test_recheck_profile_change_while_waiting_does_not_provision_old_parent(isolated, monkeypatch):
    from nicegui import ui
    from pixivvault_nicegui import managed_network_stop_gate as gate_module
    from pixivvault_nicegui.native_profile import STORAGE_FOLDER

    profile, _, _, _ = generated_owners(isolated)
    old_parent, new_parent = isolated / 'old-parent', isolated / 'new-parent'
    old_parent.mkdir()
    new_parent.mkdir()
    store = NativeProfileStore(profile.root)
    store.update(save_parent=old_parent, expected_revision=store.snapshot().revision)
    ticket = _FileLock(profile.gate.lock_path)
    _, recovery, _ = stub_launch(monkeypatch)
    monkeypatch.setattr(startup, '_STARTUP_LOCK_RETRY_SECONDS', .15)
    monkeypatch.setattr(startup, '_STARTUP_LOCK_POLL_SECONDS', .025)
    real_lock = gate_module._set_stop_byte_lock
    rechecking = False

    def release_after_change(stream, *, lock):
        try:
            return real_lock(stream, lock=lock)
        except OSError:
            if rechecking:
                store.update(save_parent=new_parent, expected_revision=store.snapshot().revision)
                ticket.close()
            raise

    def serve(**_):
        nonlocal rechecking
        binding, _ = recovery[0]
        rechecking = True
        assert asyncio.run(binding.check_startup_stop(Event()))[1] == 'profile_changed'
        assert not (old_parent / STORAGE_FOLDER).exists()

    monkeypatch.setattr(gate_module, '_set_stop_byte_lock', release_after_change)
    monkeypatch.setattr(ui, 'run', serve)
    try:
        assert bootstrap.main(['--native', '--profile-root', str(profile.root)]) == 0
    finally:
        ticket.close()
        store.close()


def test_migrated_storage_preserves_shared_busy(isolated):
    from pixivvault_nicegui.managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
    from pixivvault_nicegui.managed_network_stop_storage_binding import prepare_native_storage_binding
    from test_migration_connections import configured

    store, parent, _ = configured(isolated, True)
    try:
        migration = StopMigrationService(store)
        assert migration.execute(migration.preview(), confirmed=True).status == 'completed'
        profile = ManagedProfileNetworkStopScope(store.root)
        owner = startup.open_or_initialize_windows_user_stop()
        ticket = _FileLock(owner.gate.lock_path)
        try:
            with pytest.raises(ManagedNetworkGateBusy):
                prepare_native_storage_binding(profile, parent)
        finally:
            ticket.close()
        prepare_native_storage_binding(profile, parent)
    finally:
        store.close()


def test_migrated_media_validation_preserves_busy(isolated):
    from pixivvault_nicegui.managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
    from pixivvault_nicegui.managed_stop_migration_binding import verified_media
    from test_migration_connections import configured

    store, _, paths = configured(isolated, True)
    try:
        migration = StopMigrationService(store)
        assert migration.execute(migration.preview(), confirmed=True).status == 'completed'
        profile = ManagedProfileNetworkStopScope(store.root)
        profile.shared_owner = startup.open_or_initialize_windows_user_stop()
        images = paths['pixiv_download_root']
        ticket = _FileLock(images / (startup._LOCAL_GATE + '.lock'))
        try:
            with pytest.raises(ManagedNetworkGateBusy):
                verified_media(profile, images)
        finally:
            ticket.close()
        assert verified_media(profile, images)
    finally:
        store.close()


def test_cli_reports_late_busy_with_exit_two(isolated, monkeypatch, capsys):
    _, _, _, credential = generated_owners(isolated)

    def register(**_):
        raise ManagedNetworkGateBusy()

    monkeypatch.setattr(bootstrap, 'register_pages', register)
    assert bootstrap.main(['--pixiv-cookie-file', str(credential)]) == 2
    output = capsys.readouterr().err
    assert '実行中' in output and str(credential) not in output


@pytest.mark.skipif(os.name != 'nt', reason='Win32 error distinctions')
@pytest.mark.parametrize('code', [5, 32, 33])
def test_windows_lock_syscall_only_retries_lock_violation(isolated, monkeypatch, code):
    import ctypes
    from pixivvault_nicegui import managed_network_stop_gate as gate_module

    owner = startup.open_or_initialize_windows_user_stop()

    def refuse(*args, **kwargs):
        raise ctypes.WinError(code)

    monkeypatch.setattr(gate_module, '_set_stop_byte_lock', refuse)
    with pytest.raises(gate_module.ManagedNetworkGateStorageError) as error:
        owner.gate.snapshot()
    assert isinstance(error.value, ManagedNetworkGateBusy) == (code == 33)
