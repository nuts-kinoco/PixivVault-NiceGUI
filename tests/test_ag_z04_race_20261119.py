"""Synthetic last-check races; no real credentials, network, or child process."""
from threading import Event, Thread
from contextlib import contextmanager
import os
import sys
from types import SimpleNamespace

import pytest

from pixivvault_nicegui.native_profile import NativeProfileStore
from pixivvault_nicegui.pixiv_write_client import PixivWriteClient, WriteIntent, WriteHTTPResponse
from test_ag_z04_20261119 import setup, reader, _home


@pytest.mark.parametrize('change', ['profile_cas', 'selected_file'])
def test_external_change_after_final_generation_read_cannot_send_post(tmp_path, monkeypatch, change):
    _, cookie, profile, store, coordinator = setup(tmp_path)
    peer = NativeProfileStore(tmp_path / 'profile')
    client, _ = reader(cookie, coordinator, [])
    client._transport = lambda *args: _home()
    posts, results, errors = [], [], []
    writer = PixivWriteClient(client, transport=lambda *args: posts.append(1) or
        WriteHTTPResponse(200, b'{"error":false,"body":{}}'))
    checked, resume, dispatch_marked = Event(), Event(), Event()
    writer_checks = []
    original = coordinator.generation_token

    def generation_token(reader):
        result = original(reader)
        if sys._getframe(1).f_code.co_name == 'execute':
            writer_checks.append(1)
        if len(writer_checks) == 3:
            checked.set()
            if not resume.wait(5):
                raise TimeoutError('test synchronization timed out')
        return result

    monkeypatch.setattr(coordinator, 'generation_token', generation_token)

    def execute():
        try:
            results.append(writer.execute(WriteIntent('synthetic-race', '777', '91', 'follow'),
                                          mark_dispatch=dispatch_marked.set))
        except BaseException as error:
            errors.append(type(error).__name__)

    thread = Thread(target=execute, name='z04-fake-write')
    thread.start()
    try:
        assert checked.wait(5), 'final generation read was not reached'
        if change == 'profile_cas':
            replacement = cookie.parent / 'synthetic-replacement.txt'
            replacement.write_bytes(cookie.read_bytes())
            peer.update(cookie_file=replacement, expected_revision=peer.snapshot().revision)
        else:
            cookie.write_bytes(cookie.read_bytes() + b'\n# synthetic external replacement\n')
        resume.set()
        thread.join(10)
        assert not thread.is_alive()
        assert not errors
        assert posts == [], 'old-generation POST was sent after external change'
        assert results and results[0].status == 'rejected'
    finally:
        resume.set()
        thread.join(10)
        if thread.is_alive():
            pytest.fail('synthetic writer did not terminate')
        writer.close()
        client.close()
        coordinator.close()
        store.close()
        peer.close()
        profile.close()


@pytest.mark.parametrize('connection', ['same', 'peer'])
def test_post_pins_profile_until_transport_returns(tmp_path, connection):
    _, cookie, profile, store, coordinator = setup(tmp_path)
    peer = NativeProfileStore(tmp_path / 'profile')
    client, _ = reader(cookie, coordinator, [])
    client._transport = lambda *args: _home()
    started, release, update_started, update_done = (Event() for _ in range(4))
    result, update_errors = [], []
    def transport(*args):
        started.set()
        if not release.wait(5): raise TimeoutError('test synchronization timed out')
        return WriteHTTPResponse(200, b'{"error":false,"body":{}}')
    writer = PixivWriteClient(client, transport=transport)
    replacement = cookie.parent / 'synthetic-next.txt'
    replacement.write_bytes(cookie.read_bytes())
    target = profile if connection == 'same' else peer
    revision = target.snapshot().revision
    def update():
        update_started.set()
        try:
            target.update(cookie_file=replacement, expected_revision=revision)
        except BaseException as error:
            update_errors.append(type(error).__name__)
        finally:
            update_done.set()
    writing = Thread(target=lambda: result.append(writer.execute(
        WriteIntent('synthetic-pinned', '777', '91', 'follow'))))
    changing = Thread(target=update)
    writing.start()
    try:
        assert started.wait(5)
        changing.start()
        assert update_started.wait(5)
        assert not update_done.wait(.25)
        release.set()
        writing.join(10)
        changing.join(10)
        assert not writing.is_alive() and not changing.is_alive()
        assert not update_errors and update_done.is_set()
        assert len(result) == 1 and result[0].status == 'confirmed'
        assert peer.snapshot().cookie_file == replacement
    finally:
        release.set()
        writing.join(10)
        if changing.ident is not None: changing.join(10)
        assert not writing.is_alive() and not changing.is_alive()
        writer.close(); client.close(); coordinator.close(); store.close(); peer.close(); profile.close()


@pytest.mark.skipif(os.name != 'nt', reason='Windows mandatory sharing semantics')
def test_post_denies_cookie_write_and_replace_until_transport_returns(tmp_path):
    _, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    client._transport = lambda *args: _home()
    started, release = Event(), Event()
    result = []
    def transport(*args):
        started.set()
        if not release.wait(5): raise TimeoutError('test synchronization timed out')
        return WriteHTTPResponse(200, b'{"error":false,"body":{}}')
    writer = PixivWriteClient(client, transport=transport)
    replacement = cookie.parent / 'synthetic-replace.txt'
    replacement.write_bytes(cookie.read_bytes())
    writing = Thread(target=lambda: result.append(writer.execute(
        WriteIntent('synthetic-file-pin', '777', '91', 'follow'))))
    writing.start()
    try:
        assert started.wait(5)
        with pytest.raises(PermissionError): cookie.write_bytes(b'synthetic')
        with pytest.raises(PermissionError): os.replace(replacement, cookie)
        release.set()
        writing.join(10)
        assert not writing.is_alive()
        assert len(result) == 1 and result[0].status == 'confirmed'
        os.replace(replacement, cookie)
    finally:
        release.set(); writing.join(10)
        assert not writing.is_alive()
        writer.close(); client.close(); coordinator.close(); store.close(); profile.close()


@pytest.mark.parametrize('phase', ['pin', 'transport'])
def test_post_pin_failure_rejects_and_transport_failure_is_uncertain(tmp_path, monkeypatch, phase):
    _, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    client._transport = lambda *args: _home()
    posts = []
    def transport(*args):
        posts.append(1)
        raise TimeoutError('synthetic-private')
    if phase == 'pin':
        def fail(*args): raise PermissionError('synthetic-private')
        monkeypatch.setattr('pixivvault_nicegui.frozen_source_pin._open_held', fail)
    writer = PixivWriteClient(client, transport=transport)
    try:
        result = writer.execute(WriteIntent('synthetic-failure', '777', '91', 'follow'))
        assert result.status == ('rejected' if phase == 'pin' else 'indeterminate')
        assert len(posts) == (0 if phase == 'pin' else 1)
    finally:
        writer.close(); client.close(); coordinator.close(); store.close(); profile.close()


@pytest.mark.skipif(os.name != 'nt', reason='Windows mandatory sharing semantics')
def test_preexisting_cookie_writer_prevents_post_admission(tmp_path):
    _, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    client._transport = lambda *args: _home()
    posts = []
    writer = PixivWriteClient(client, transport=lambda *args: posts.append(1) or
        WriteHTTPResponse(200, b'{"error":false,"body":{}}'))
    try:
        with cookie.open('r+b'):
            result = writer.execute(WriteIntent('synthetic-existing-writer', '777', '91', 'follow'))
        assert result.status == 'rejected' and posts == []
    finally:
        writer.close(); client.close(); coordinator.close(); store.close(); profile.close()


@pytest.mark.parametrize('already_stopped', [False, True])
def test_pinned_post_respects_and_latches_shared_stop_gate(tmp_path, already_stopped):
    from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopGate
    from pixivvault_nicegui.managed_network_stop_write import write_admission
    _, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    client._transport = lambda *args: _home()
    gate = ManagedNetworkStopGate(tmp_path / 'gate.sqlite')
    shared = SimpleNamespace(gate=gate, _check_owner=lambda: None)
    posts = []
    writer = PixivWriteClient(client, transport=lambda *args: posts.append(1) or WriteHTTPResponse(429, b'{}'))
    writer._post_admission = lambda: write_admission(client, None, shared)
    try:
        if already_stopped: gate.trip(429)
        writer.execute(WriteIntent('synthetic-stop', '777', '91', 'follow'))
        assert len(posts) == (0 if already_stopped else 1)
        assert gate.snapshot().blocked
    finally:
        writer.close(); client.close(); coordinator.close(); store.close(); profile.close()


def test_profile_update_busy_preserves_safe_error_contract(tmp_path):
    from pixivvault_nicegui.native_profile import NativeProfileError
    _, cookie, profile, store, coordinator = setup(tmp_path)
    peer = NativeProfileStore(tmp_path / 'profile')
    peer._connection.execute('PRAGMA busy_timeout=25')
    replacement = cookie.parent / 'synthetic-busy.txt'
    replacement.write_bytes(cookie.read_bytes())
    revision = peer.snapshot().revision
    try:
        with profile.pinned_snapshot():
            with pytest.raises(NativeProfileError):
                peer.update(cookie_file=replacement, expected_revision=revision)
        assert peer.snapshot().cookie_file == cookie
    finally:
        coordinator.close(); store.close(); peer.close(); profile.close()


@pytest.mark.parametrize('cleanup', ['cookie_close', 'profile_rollback'])
@pytest.mark.parametrize('status_source', ['response', 'raised'])
def test_cleanup_failure_never_masks_fatal_stop_status(tmp_path, monkeypatch, cleanup, status_source):
    import pixivvault_nicegui.frozen_source_pin as pin
    from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopGate
    from pixivvault_nicegui.managed_network_stop_write import write_admission
    _, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    client._transport = lambda *args: _home()
    gate = ManagedNetworkStopGate(tmp_path / 'gate.sqlite')
    shared = SimpleNamespace(gate=gate, _check_owner=lambda: None)
    def transport(*args):
        if status_source == 'raised':
            from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected
            raise HTTPStatusRejected(429)
        return WriteHTTPResponse(429, b'{}')
    writer = PixivWriteClient(client, transport=transport)
    writer._post_admission = lambda: write_admission(client, None, shared)
    if cleanup == 'cookie_close':
        descriptor = []
        original_open, original_close = pin._open_held, os.close
        def held(*args):
            value = original_open(*args)
            descriptor.append(value)
            return value
        def close(fd):
            original_close(fd)
            if descriptor and fd == descriptor[-1]:
                descriptor.clear()
                raise OSError('synthetic cleanup failure')
        monkeypatch.setattr(pin, '_open_held', held)
        monkeypatch.setattr(os, 'close', close)
    else:
        import pixivvault_nicegui.native_profile as profile_module
        original_connect = profile_module.sqlite3.connect
        class ConnectionProxy:
            def __init__(self, connection): self.connection = connection
            def execute(self, statement, *args):
                result = self.connection.execute(statement, *args)
                if statement == 'ROLLBACK': raise OSError('synthetic cleanup failure')
                return result
            def close(self): return self.connection.close()
        def connect(path, *args, **kwargs):
            connection = original_connect(path, *args, **kwargs)
            return ConnectionProxy(connection) if str(path).endswith('?mode=rw') else connection
        monkeypatch.setattr(profile_module.sqlite3, 'connect', connect)
    try:
        result = writer.execute(WriteIntent('synthetic-cleanup', '777', '91', 'follow'))
        assert gate.snapshot().blocked
        assert result.status == 'indeterminate' and result.reason == 'transport_unknown'
        if cleanup == 'cookie_close': assert coordinator._closed
    finally:
        writer.close(); client.close(); coordinator.close(); store.close(); profile.close()


def test_profile_snapshot_remains_available_during_post(tmp_path):
    _, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    client._transport = lambda *args: _home()
    started, release, read_done = Event(), Event(), Event()
    def transport(*args):
        started.set()
        assert release.wait(5)
        return WriteHTTPResponse(200, b'{"error":false,"body":{}}')
    writer = PixivWriteClient(client, transport=transport)
    writing = Thread(target=lambda: writer.execute(WriteIntent('snapshot-post', '777', '91', 'follow')))
    reading = Thread(target=lambda: (profile.snapshot(), read_done.set()))
    writing.start()
    try:
        assert started.wait(5)
        reading.start()
        assert read_done.wait(1), 'profile read blocked during network I/O'
    finally:
        release.set(); writing.join(10)
        if reading.ident is not None: reading.join(10)
        assert not writing.is_alive() and not reading.is_alive()
        writer.close(); client.close(); coordinator.close(); store.close(); profile.close()


def test_same_thread_profile_reentry_does_not_release_post_reservation(tmp_path):
    from pixivvault_nicegui.native_profile import NativeProfileError
    _, cookie, profile, store, coordinator = setup(tmp_path)
    peer = NativeProfileStore(tmp_path / 'profile')
    profile._connection.execute('PRAGMA busy_timeout=25')
    peer._connection.execute('PRAGMA busy_timeout=25')
    client, _ = reader(cookie, coordinator, [])
    client._transport = lambda *args: _home()
    replacement = cookie.parent / 'synthetic-reentry.txt'
    replacement.write_bytes(cookie.read_bytes())
    revision = profile.snapshot().revision
    observed = []
    def transport(*args):
        observed.append(profile.snapshot().cookie_file == cookie)
        with pytest.raises(NativeProfileError):
            profile.update(cookie_file=replacement, expected_revision=revision)
        with pytest.raises(NativeProfileError):
            peer.update(cookie_file=replacement, expected_revision=revision)
        return WriteHTTPResponse(200, b'{"error":false,"body":{}}')
    writer = PixivWriteClient(client, transport=transport)
    try:
        result = writer.execute(WriteIntent('synthetic-reentry', '777', '91', 'follow'))
        assert result.status == 'confirmed' and observed == [True]
        assert peer.update(cookie_file=replacement, expected_revision=revision).cookie_file == replacement
    finally:
        writer.close(); client.close(); coordinator.close(); store.close(); peer.close(); profile.close()


@pytest.mark.parametrize('cancel_mode', ['cancel', 'deadline'])
def test_pixiv_actions_cancellation_during_profile_reservation_never_posts(tmp_path, monkeypatch, cancel_mode):
    from pixivvault_nicegui.pixiv_actions import PixivActions, _Observation
    _, cookie, profile, store, coordinator = setup(tmp_path)
    peer = NativeProfileStore(tmp_path / 'profile')
    client, _ = reader(cookie, coordinator, [])
    client._transport = lambda *args: _home()
    posts = []
    writer = PixivWriteClient(client, transport=lambda *args: posts.append(1) or
        WriteHTTPResponse(200, b'{"error":false,"body":{}}'))
    actions = PixivActions(client, writer=writer)
    monkeypatch.setattr(actions, '_observe', lambda *args: _Observation(False))
    entered, cancelled = Event(), Event()
    original = profile.pinned_snapshot
    @contextmanager
    def instrumented():
        entered.set()
        with original() as snapshot: yield snapshot
    monkeypatch.setattr(profile, 'pinned_snapshot', instrumented)
    try:
        with peer.pinned_snapshot():
            future, control = actions._submit('execute', WriteIntent('reservation-wait', '777', '91', 'follow'),
                                              '', cancelled.is_set, 10)
            assert entered.wait(5)
            if cancel_mode == 'cancel': cancelled.set()
            else: control.deadline = 0
        result = future.result(timeout=10)
        assert not posts
        assert result.status in ('cancelled', 'rejected')
    finally:
        assert actions.close(timeout=10)
        client.close(); coordinator.close(); store.close(); peer.close(); profile.close()


def test_reader_close_during_profile_reservation_never_posts(tmp_path, monkeypatch):
    _, cookie, profile, store, coordinator = setup(tmp_path)
    peer = NativeProfileStore(tmp_path / 'profile')
    client, _ = reader(cookie, coordinator, [])
    client._transport = lambda *args: _home()
    posts, results, entered = [], [], Event()
    writer = PixivWriteClient(client, transport=lambda *args: posts.append(1) or
        WriteHTTPResponse(200, b'{"error":false,"body":{}}'))
    original = profile.pinned_snapshot
    @contextmanager
    def instrumented():
        entered.set()
        with original() as snapshot: yield snapshot
    monkeypatch.setattr(profile, 'pinned_snapshot', instrumented)
    thread = Thread(target=lambda: results.append(writer.execute(
        WriteIntent('synthetic-closed', '777', '91', 'follow'))))
    try:
        with peer.pinned_snapshot():
            thread.start()
            assert entered.wait(5)
            client.close()
        thread.join(10)
        assert not thread.is_alive()
        assert not posts
        assert results and results[0].status == 'rejected'
    finally:
        if thread.ident is not None: thread.join(10)
        assert not thread.is_alive()
        writer.close(); client.close(); coordinator.close(); store.close(); peer.close(); profile.close()


def test_reader_close_serializes_with_admitted_transport(tmp_path):
    _, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    client._transport = lambda *args: _home()
    started, release, closed = Event(), Event(), Event()
    results = []
    def transport(*args):
        started.set()
        if not release.wait(5): raise TimeoutError('test synchronization timed out')
        return WriteHTTPResponse(200, b'{"error":false,"body":{}}')
    writer = PixivWriteClient(client, transport=transport)
    writing = Thread(target=lambda: results.append(writer.execute(
        WriteIntent('synthetic-close-admitted', '777', '91', 'follow'))))
    closing = Thread(target=lambda: (client.close(), closed.set()))
    writing.start()
    try:
        assert started.wait(5)
        closing.start()
        assert not closed.wait(.25)
        release.set()
        writing.join(10); closing.join(10)
        assert not writing.is_alive() and not closing.is_alive()
        assert results and results[0].status == 'confirmed' and closed.is_set()
    finally:
        release.set(); writing.join(10)
        if closing.ident is not None: closing.join(10)
        assert not writing.is_alive() and not closing.is_alive()
        writer.close(); client.close(); coordinator.close(); store.close(); profile.close()


def test_reader_close_inside_dispatch_callback_never_posts(tmp_path):
    _, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    client._transport = lambda *args: _home()
    posts = []
    writer = PixivWriteClient(client, transport=lambda *args: posts.append(1) or
        WriteHTTPResponse(200, b'{"error":false,"body":{}}'))
    try:
        result = writer.execute(WriteIntent('synthetic-close-callback', '777', '91', 'follow'),
                                mark_dispatch=client.close)
        assert result.status == 'rejected' and not posts
    finally:
        writer.close(); client.close(); coordinator.close(); store.close(); profile.close()


@pytest.mark.parametrize('target', ['coordinator', 'binding'])
def test_coordinator_close_inside_dispatch_callback_never_posts(tmp_path, target):
    from pixivvault_nicegui.native_startup import NativeProfileBinding
    _, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    client._transport = lambda *args: _home()
    binding = NativeProfileBinding(profile)
    binding._cookie_expiry_coordinator = coordinator
    posts = []
    writer = PixivWriteClient(client, transport=lambda *args: posts.append(1) or
        WriteHTTPResponse(200, b'{"error":false,"body":{}}'))
    callback = coordinator.close if target == 'coordinator' else binding.close
    try:
        result = writer.execute(WriteIntent('synthetic-coordinator-close', '777', '91', 'follow'),
                                mark_dispatch=callback)
        assert result.status == 'rejected' and not posts
    finally:
        writer.close(); client.close(); binding.close(); coordinator.close(); store.close(); profile.close()
