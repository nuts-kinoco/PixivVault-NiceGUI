"""401 recovery through the existing Native settings authentication entry."""
import asyncio
import subprocess
import sys
from threading import Event
from types import SimpleNamespace
import time

import pytest

from pixivvault_nicegui import managed_network_stop_reader
from pixivvault_nicegui.managed_network_stop_gate import (
    ManagedNetworkGateStorageError, ManagedNetworkStopped,
)
from pixivvault_nicegui.managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
from pixivvault_nicegui.managed_network_stop_shared_transport import create_shared_profile_reader
from pixivvault_nicegui.managed_network_stop_shared_scope import _INFLIGHT_BYTES
from pixivvault_nicegui.managed_network_stop_transport import ManagedNetworkStopTransport
from pixivvault_nicegui.native_profile import NativeProfileStore
from pixivvault_nicegui.native_startup import NativeProfileBinding
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected, LivePixivReadClient
from test_stage5_r1_native_first_draft_shared import _generated_shared_owner


HOME = 'https://www.pixiv.net/'
IDENTITY = HOME + 'ajax/user/extra'
IMAGE = 'https://i.pximg.net/img-original/img/2024/01/02/03/04/05/123_p0.png'


def generated_cookie(path, account='55', generation='first'):
    path.write_text(
        '# Netscape HTTP Cookie File\n'
        f'.pixiv.net\tTRUE\t/\tTRUE\t{int(time.time()) + 86400}\tPHPSESSID\t{account}_{generation}\n',
        encoding='utf-8',
    )
    return path


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    store = NativeProfileStore(tmp_path / 'profile')
    local = ManagedProfileNetworkStopScope(store.root)
    shared = _generated_shared_owner(tmp_path)
    credential = tmp_path / 'credential'
    credential.mkdir()
    saved = generated_cookie(credential / 'saved.txt')
    chosen = generated_cookie(credential / 'chosen.txt', generation='second')
    # Automatic authentication never uses the manual recovery lane.
    store.update(cookie_file=saved, expected_revision=0)
    binding = NativeProfileBinding(store)
    binding.auth_stop_required = binding.auth_shared_stop_required = True
    binding.managed_network_stop_scope = local
    binding.shared_network_stop_scope = shared
    sends = []
    responses = {}

    def transport(request, *_):
        url = request.full_url
        assert request.get_method() == 'GET'
        sends.append(url)
        result = responses.get(url)
        if isinstance(result, Exception):
            raise result
        if callable(result):
            return result()
        if url == HOME:
            return b'<meta id="meta-global-data" content="{&quot;userData&quot;:{&quot;id&quot;:&quot;55&quot;}}">'
        if url == IDENTITY:
            return b'{"error":false,"body":{"userId":"55"}}'
        return b''

    monkeypatch.setattr(managed_network_stop_reader, 'create_exact_managed_read_client',
        lambda path, gate, **kwargs: LivePixivReadClient(
            path, transport=ManagedNetworkStopTransport(gate, kwargs.get('transport') or transport)))
    try:
        yield binding, local, shared, saved, chosen, sends, responses, transport
    finally:
        binding.close()


@pytest.mark.parametrize('where', ('local', 'shared', 'both'))
def test_explicit_check_saves_and_clears_401(recovery, where):
    binding, local, shared, _, chosen, sends, _, _ = recovery
    if where != 'shared':
        local.gate.trip(401)
    if where != 'local':
        shared.gate.trip(401)

    assert not asyncio.run(binding.check_authentication(chosen, _imported=True)).ok
    assert not asyncio.run(binding.check_authentication(chosen)).ok
    assert sends == []
    result = asyncio.run(binding.check_selected_authentication(chosen))
    assert result.ok
    assert sends == [HOME, IDENTITY]
    assert binding.auth_status.status.ok
    assert NativeProfileStore.peek(binding.store.root).cookie_file == chosen
    assert not ManagedProfileNetworkStopScope(local.root).gate.snapshot().blocked
    assert not type(shared)(shared.root).gate.snapshot().blocked
    assert not shared.gate.inflight_path.exists()


@pytest.mark.parametrize('status', (401, 403, 429, 503))
def test_recovery_checks_each_get_once_and_keeps_stop_on_failure(recovery, status):
    binding, local, shared, saved, chosen, sends, responses, _ = recovery
    shared.gate.trip(401)
    responses[IDENTITY] = HTTPStatusRejected(status)
    assert not asyncio.run(binding.check_selected_authentication(chosen)).ok
    assert sends == [HOME, IDENTITY]
    assert NativeProfileStore.peek(binding.store.root).cookie_file == saved
    assert local.gate.snapshot().blocked
    assert shared.gate.snapshot().status_code == (status if status in (403, 429) else 401)


@pytest.mark.parametrize('blocker', ('403', '429', 'unknown-with-local-401', 'unknown-with-shared-401', 'unknown-only'))
def test_other_stops_and_unknown_intent_are_untouched(recovery, blocker):
    binding, local, shared, saved, chosen, sends, _, _ = recovery
    if blocker == 'unknown-with-local-401':
        local.gate.trip(401)
    if blocker == 'unknown-with-shared-401':
        shared.gate.trip(401)
    if blocker.startswith('unknown'):
        shared.gate.inflight_path.write_bytes(_INFLIGHT_BYTES)
    else:
        shared.gate.trip(int(blocker))
    assert not asyncio.run(binding.check_selected_authentication(chosen)).ok
    assert sends == []
    assert NativeProfileStore.peek(binding.store.root).cookie_file == saved
    if blocker.startswith('unknown'):
        assert shared.gate.inflight_path.read_bytes() == _INFLIGHT_BYTES
    else:
        assert shared.gate.snapshot().status_code == int(blocker)


def test_completed_401_has_no_unknown_intent_and_can_recover(recovery):
    binding, local, shared, saved, chosen, sends, responses, _ = recovery
    responses[HOME] = HTTPStatusRejected(401)
    assert not asyncio.run(binding.check_authentication(saved)).ok
    assert shared.gate.snapshot().status_code == 401
    assert not shared.gate.inflight_path.exists()
    del responses[HOME]
    assert asyncio.run(binding.check_selected_authentication(chosen)).ok
    assert sends == [HOME, HOME, IDENTITY]


def test_old_reader_with_same_path_cannot_send_after_recovery(recovery):
    binding, local, shared, saved, _, sends, _, transport = recovery
    with create_shared_profile_reader(local, saved, shared.gate, transport=transport,
                                      shared_owner_check=shared._check_owner) as old:
        generated_cookie(saved, generation='replacement')
        shared.gate.trip(401)
        assert asyncio.run(binding.check_selected_authentication(saved)).ok
        with pytest.raises(ManagedNetworkGateStorageError):
            old.require_identity()
    assert sends == [HOME, IDENTITY]
    assert not shared.gate.inflight_path.exists()


def test_settings_button_applies_saved_cookie_to_same_account_readers(recovery):
    from nicegui import Client
    from nicegui.page import page as Page
    from pixivvault_nicegui.cookie_expiry_refresh import CookieExpiryCoordinator

    binding, local, shared, saved, chosen, sends, _, transport = recovery
    app = Client(Page('/generated-401-settings'))
    ready = []
    store = SimpleNamespace(last_synced_account=lambda: '55')
    binding.followup.store_provider = lambda: store
    binding.followup.on_following_ready = lambda uid, selected: ready.append((uid, selected)) or True

    async def confirm():
        with app:
            view = binding.create_view()
            view.build()
            view.draft_cookie = chosen
            assert await view.check_auth()
            await asyncio.gather(*binding._background_tasks)
            assert view.snapshot.cookie_file == chosen
            assert '取得を再開できます' in view.status.text
            view.close()

    try:
        with create_shared_profile_reader(local, saved, shared.gate, transport=transport,
                                          shared_owner_check=shared._check_owner) as reader:
            coordinator = CookieExpiryCoordinator(binding.store, None)
            coordinator.register(reader)
            binding._cookie_expiry_coordinator = coordinator
            shared.gate.trip(401)
            asyncio.run(confirm())
            assert sends == [HOME, IDENTITY]
            assert ready == [('55', store)]
            assert reader.require_identity() == '55'
            assert reader._transport.cookie_path == chosen
            reader._get('identity', IDENTITY, deadline=time.monotonic() + 2)
            assert sends == [HOME, IDENTITY, IDENTITY]
    finally:
        app.delete()


def test_other_account_keeps_existing_explicit_switch_confirmation(recovery):
    binding, local, shared, saved, chosen, sends, responses, _ = recovery
    generated_cookie(chosen, account='56')
    responses[HOME] = lambda: (
        b'<meta id="meta-global-data" content="{&quot;userData&quot;:{&quot;id&quot;:&quot;56&quot;}}">')
    responses[IDENTITY] = lambda: b'{"error":false,"body":{"userId":"56"}}'
    binding.followup.store_provider = lambda: SimpleNamespace(last_synced_account=lambda: '55')
    local.gate.trip(401)
    async def check_and_wait():
        assert (await binding.check_selected_authentication(chosen)).ok
        await asyncio.gather(*binding._background_tasks)

    asyncio.run(check_and_wait())
    assert binding.followup.pending.new_account == '56'
    assert binding.followup.pending.old_account == '55'
    assert binding.store.snapshot().cookie_file == chosen
    assert sends == [HOME, IDENTITY]


def test_cancel_during_confirmation_preserves_stop_and_selection(recovery):
    binding, local, shared, saved, chosen, sends, responses, _ = recovery
    reached, resume = Event(), Event()
    shared.gate.trip(401)

    def pause():
        reached.set()
        assert resume.wait(5)
        return b'{"error":false,"body":{"userId":"55"}}'

    responses[IDENTITY] = pause

    async def cancel():
        task = asyncio.create_task(binding.check_selected_authentication(chosen))
        try:
            assert await asyncio.to_thread(reached.wait, 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            resume.set()

    asyncio.run(cancel())
    assert sends == [HOME, IDENTITY]
    assert local.gate.snapshot().status_code == shared.gate.snapshot().status_code == 401
    assert binding.store.snapshot().cookie_file == saved


def test_cancel_after_shared_commit_starts_returns_real_outcome(recovery, monkeypatch):
    binding, local, shared, _, chosen, sends, _, _ = recovery
    shared.gate.trip(401)
    reached, resume = Event(), Event()
    advance = shared.gate._advance_401_locked

    def pause(status):
        reached.set()
        assert resume.wait(5)
        return advance(status)

    monkeypatch.setattr(shared.gate, '_advance_401_locked', pause)

    async def cancel():
        task = asyncio.create_task(binding.check_selected_authentication(chosen))
        try:
            assert await asyncio.to_thread(reached.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
        finally:
            resume.set()
        assert (await task).ok

    asyncio.run(cancel())
    assert sends == [HOME, IDENTITY]
    assert not local.gate.snapshot().blocked
    assert not shared.gate.snapshot().blocked


def test_old_shared_only_writer_cannot_send_after_recovery(recovery):
    from pixivvault_nicegui.managed_network_stop_write import write_admission

    binding, _, shared, saved, chosen, sends, _, transport = recovery
    with shared.create_reader(saved, transport=transport) as old:
        shared.gate.trip(401)
        assert asyncio.run(binding.check_selected_authentication(chosen)).ok
        with pytest.raises(ManagedNetworkGateStorageError):
            with write_admission(old, None, shared):
                pytest.fail('a prepared write using the old Cookie reached dispatch')
    assert sends == [HOME, IDENTITY]
    assert not shared.gate.inflight_path.exists()


def test_shared_clear_failure_preserves_global_stop_and_allows_retry(recovery, monkeypatch):
    binding, local, shared, _, chosen, sends, _, _ = recovery
    local.gate.trip(401)

    def reject(_):
        raise ManagedNetworkGateStorageError('synthetic recovery write failure')

    with monkeypatch.context() as fault:
        fault.setattr(shared.gate, '_advance_401_locked', reject)
        assert not asyncio.run(binding.check_selected_authentication(chosen)).ok
    assert not local.gate.snapshot().blocked
    assert shared.gate.snapshot().status_code == 401
    assert binding.store.snapshot().cookie_file == chosen
    with pytest.raises(ManagedNetworkStopped):
        type(shared)(shared.root).require_allowed()

    assert asyncio.run(binding.check_selected_authentication(chosen)).ok
    assert sends == [HOME, IDENTITY] * 2
    assert local.gate.snapshot().revision == 4
    assert shared.gate.snapshot().revision == 2


def test_other_process_cannot_enter_shared_get_during_recovery(recovery):
    binding, _, shared, _, chosen, sends, responses, _ = recovery
    # Open the other process's owner before the lock is taken. Use files only
    # for rendezvous and a fail-on-send callable at the guarded boundary.
    script = """
from pathlib import Path
import socket
import sys
from time import monotonic, sleep
from urllib.request import Request
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkGateBusy
from pixivvault_nicegui.managed_network_stop_shared_scope import ManagedUserNetworkStopScope

def no_send(*args):
    raise AssertionError('unexpected send')
socket.socket.connect = no_send
owner = ManagedUserNetworkStopScope(Path(sys.argv[1]))
ready, run = map(Path, sys.argv[2:4])
with owner.create_reader(Path(sys.argv[4]), transport=no_send) as reader:
    ready.touch()
    deadline = monotonic() + 8
    while not run.exists() and monotonic() < deadline:
        sleep(.01)
    assert run.exists()
    try:
        reader._transport(Request('https://www.pixiv.net/'), 1, 100, deadline)
    except ManagedNetworkGateBusy:
        sys.exit(0)
    sys.exit(2)
"""
    ready, run = (shared.root.parent / name for name in ('ready', 'run'))
    args = [sys.executable, '-B', '-c', script, str(shared.root), str(ready), str(run), str(chosen)]
    with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as peer:
        try:
            deadline = time.monotonic() + 8
            while not ready.exists() and peer.poll() is None and time.monotonic() < deadline:
                time.sleep(.01)
            assert ready.exists()

            def contest():
                run.touch()
                assert peer.wait(timeout=5) == 0
                return b'{"error":false,"body":{"userId":"55"}}'

            responses[IDENTITY] = contest
            binding.managed_network_stop_scope.gate.trip(401)
            assert asyncio.run(binding.check_selected_authentication(chosen)).ok
            assert sends == [HOME, IDENTITY]
        finally:
            if peer.poll() is None:
                peer.kill()
                peer.wait(timeout=5)


def test_process_exit_after_local_clear_leaves_shared_blocked(recovery):
    binding, local, shared, _, chosen, sends, _, _ = recovery
    shared.gate.trip(401)
    script = """
from pathlib import Path
import os
import socket
import sys
from threading import Event
from pixivvault_nicegui import managed_network_stop_reader
from pixivvault_nicegui.managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
from pixivvault_nicegui.managed_network_stop_shared_scope import ManagedUserNetworkStopScope
from pixivvault_nicegui.managed_network_stop_transport import ManagedNetworkStopTransport
from pixivvault_nicegui.native_profile import NativeProfileStore
from pixivvault_nicegui.native_startup import NativeProfileBinding
from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient

def no_send(*args):
    raise AssertionError('unexpected send')
socket.socket.connect = no_send

def fake(request, *_):
    if request.full_url == 'https://www.pixiv.net/':
        return b'<meta id="meta-global-data" content="{&quot;userData&quot;:{&quot;id&quot;:&quot;55&quot;}}">'
    assert request.full_url == 'https://www.pixiv.net/ajax/user/extra'
    return b'{"error":false,"body":{"userId":"55"}}'

profile, owner, cookie = map(Path, sys.argv[1:])
binding = NativeProfileBinding(NativeProfileStore(profile))
binding.auth_stop_required = binding.auth_shared_stop_required = True
binding.managed_network_stop_scope = ManagedProfileNetworkStopScope(profile)
binding.shared_network_stop_scope = ManagedUserNetworkStopScope(owner)
gate = binding.shared_network_stop_scope.gate
advance = gate._advance_401_locked

def interrupt(status):
    if status is None:
        os._exit(71)
    return advance(status)

gate._advance_401_locked = interrupt
managed_network_stop_reader.create_exact_managed_read_client = lambda path, gate: LivePixivReadClient(
    path, transport=ManagedNetworkStopTransport(gate, fake))
binding._verify_auth_result(cookie, Event(), committing=Event())
sys.exit(2)
"""
    result = subprocess.run([sys.executable, '-B', '-c', script, str(local.root), str(shared.root), str(chosen)],
                            capture_output=True, timeout=10)
    assert result.returncode == 71
    assert not local.gate.snapshot().blocked
    with pytest.raises(ManagedNetworkStopped):
        type(shared)(shared.root).require_allowed()
    assert binding.store.snapshot().cookie_file == chosen
    assert asyncio.run(binding.check_selected_authentication(chosen)).ok
    assert sends == [HOME, IDENTITY]


def test_bad_identity_does_not_clear_401(recovery):
    binding, local, shared, saved, chosen, sends, responses, _ = recovery
    local.gate.trip(401)
    responses[IDENTITY] = lambda: b'{"error":false,"body":{"userId":"56"}}'
    result = asyncio.run(binding.check_selected_authentication(chosen))
    assert result.code == 'identity_unverified'
    assert sends == [HOME, IDENTITY]
    assert binding.store.snapshot().cookie_file == saved
    assert local.gate.snapshot().status_code == shared.gate.snapshot().status_code == 401


def test_older_profile_stop_cannot_relatch_recovered_shared_owner(recovery, tmp_path, monkeypatch):
    from contextlib import closing
    from pixivvault_nicegui import managed_network_stop_startup as startup

    binding, _, shared, saved, chosen, sends, _, transport = recovery
    with closing(NativeProfileStore(tmp_path / 'other-profile')) as store:
        other = ManagedProfileNetworkStopScope(store.root)
        store.update(cookie_file=saved, expected_revision=0)
        with create_shared_profile_reader(other, saved, shared.gate, transport=transport,
                                          shared_owner_check=shared._check_owner) as stale:
            shared.gate.trip(401)
            with pytest.raises(ManagedNetworkStopped):
                stale.require_identity()
            assert other.gate.snapshot().status_code == 401
            assert asyncio.run(binding.check_selected_authentication(chosen)).ok
            with pytest.raises(ManagedNetworkStopped):
                stale.require_identity()

        reopened = type(shared)(shared.root)
        monkeypatch.setattr(startup.shared, 'open_existing_windows_user_stop', lambda: reopened)
        startup.open_or_initialize_windows_user_stop(profile_scope=ManagedProfileNetworkStopScope(store.root))
        assert not reopened.gate.snapshot().blocked
        assert sends == [HOME, IDENTITY]


def test_failed_401_teardown_preserves_unknown_intent(recovery):
    binding, local, shared, saved, chosen, sends, responses, _ = recovery
    rejection = HTTPStatusRejected(401)
    rejection.gate_cleanup_errors = (ManagedNetworkGateStorageError('synthetic teardown failure'),)
    responses[HOME] = rejection
    assert not asyncio.run(binding.check_authentication(saved)).ok
    assert shared.gate.inflight_path.exists()
    assert not asyncio.run(binding.check_selected_authentication(chosen)).ok
    assert sends == [HOME]
    assert local.gate.snapshot().status_code == 401


def test_stopped_selected_history_is_never_cleared(recovery, tmp_path):
    from test_fix_notice_round7_20261122 import attach_history

    binding, local, shared, _, chosen, sends, _, _ = recovery
    history = attach_history(tmp_path, shared)
    history.gate.trip(401)
    before = history.gate.path.read_bytes()
    local.gate.trip(401)
    assert not asyncio.run(binding.check_selected_authentication(chosen)).ok
    assert sends == []
    assert history.gate.path.read_bytes() == before
    assert history.gate.snapshot().status_code == 401
    assert local.gate.snapshot().status_code == 401


def test_recovery_keeps_the_legacy_gate_reader_strict(recovery):
    from pixivvault_nicegui.managed_network_stop_history_readonly import _Unverified, _read_gate_immutable
    from pixivvault_nicegui.request_notice_gates import check_request_notice_gates

    binding, local, shared, _, chosen, _, _, _ = recovery
    local.gate.trip(401)
    assert asyncio.run(binding.check_selected_authentication(chosen)).ok
    assert [state.state for state in check_request_notice_gates(local, shared).states] == ['open', 'open']
    with pytest.raises(_Unverified, match='unrecognized_gate_schema'):
        _read_gate_immutable(local.gate.path)
    with pytest.raises(_Unverified, match='unrecognized_gate_schema'):
        _read_gate_immutable(shared.gate.path)
    shared.gate.trip(401)
    assert type(shared)(shared.root).gate.snapshot().revision == 3
