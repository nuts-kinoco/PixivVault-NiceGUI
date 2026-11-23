"""Live entry regressions for selection generations and explicit notice recovery."""
from contextlib import ExitStack, closing, contextmanager
import os
from pathlib import Path
from threading import Event
import time

import pytest

from pixivvault_nicegui import managed_network_stop_gate as gates, pixiv_read_client as reads
from pixivvault_nicegui.cookie_expiry_refresh import CookieExpiryCoordinator
from pixivvault_nicegui.extension_cookie_store import ExtensionCookieStore
from pixivvault_nicegui.managed_network_stop_reader import create_exact_managed_read_client
from pixivvault_nicegui.managed_network_stop_shared_transport import create_shared_draft_auth_reader
from pixivvault_nicegui.native_profile import NativeProfileStore
from pixivvault_nicegui.native_startup import NativeProfileBinding
from pixivvault_nicegui.request_notice_gates import GateNoticeState, GateObservationSnapshot, check_request_notice_gates
from test_ag_z04_20261119 import fetch
from test_fix_notice_round6_20261122 import windows_io_error
from test_fix_notice_round7_20261122 import linked_owners
from test_fix_notice_round9_20261122 import guarded_reader
from test_fix_notice_sweep_20261122 import retry_policy
from test_stage5_r1_shared_stop_transport import IMAGE


def _select(store, path):
    store.update(cookie_file=path, expected_revision=store.snapshot().revision)


@pytest.mark.parametrize('manual', (False, True))
def test_late_old_reader_after_response_cookie_adoption(tmp_path, manual):
    local, shared, selected = linked_owners(tmp_path)
    expiry = int(time.time()) + 3600
    selected.write_text('# Netscape HTTP Cookie File\n'
                        f'.pixiv.net\tTRUE\t/\tTRUE\t{expiry}\tPHPSESSID\t777_old\n', encoding='ascii')
    sent = []

    def renew(*_):
        sent.append('renew')
        return reads._ReadResponse(b'{"error":false,"body":{}}', (
            'PHPSESSID=777_new; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200',
        ))

    def other(*_):
        sent.append('other')
        return b'synthetic image'

    def observe():
        if manual:
            return GateObservationSnapshot((GateNoticeState('local', 'changed'),), False)
        return check_request_notice_gates(local)

    policy = retry_policy([0.0], check_gate=observe)
    profile = NativeProfileStore(local.root)
    store = ExtensionCookieStore(tmp_path / 'generations', profile=profile)
    coordinator = CookieExpiryCoordinator(profile, lambda: store)
    try:
        with guarded_reader(local, shared, selected, renew) as current, \
             guarded_reader(local, shared, selected, other) as old, \
             guarded_reader(local, shared, selected, other) as attached:
            for reader in (old, current, attached):
                reader.set_retry_policy(policy)
            current._verified_identity = attached._verified_identity = '777'
            coordinator.register(current)
            coordinator.register(attached)
            initial_generation = current._retry_policy_binding[1]
            fetch(current)
            assert profile.snapshot().cookie_file != selected
            with pytest.raises(gates.ManagedNetworkGateStorageError):
                old.get_image_bytes(IMAGE)
            if manual:
                for _ in range(5):
                    policy.recheck_gate_notices()
                    assert policy.has_gate_notices()
                confirm = getattr(policy, 'confirm_gate_notices', lambda: False)
                assert confirm()
                assert not policy.has_gate_notices()
            else:
                assert current._retry_policy_binding[1] is not initial_generation
                assert attached._retry_policy_binding[1] is current._retry_policy_binding[1]
                assert old._retry_policy_binding[1] is initial_generation
            assert sent == ['renew']
    finally:
        coordinator.close()
        store.close()
        profile.close()


@pytest.mark.parametrize('manual', (False, True))
def test_draft_auth_after_settings_change_uses_saved_route_evidence(tmp_path, monkeypatch, manual):
    local, shared, selected = linked_owners(tmp_path)
    new = selected.with_name('next-synthetic.txt')
    new.write_bytes(selected.read_bytes())
    binding = NativeProfileBinding(NativeProfileStore(local.root))
    binding.managed_network_stop_scope, binding.shared_network_stop_scope = local, shared
    binding.auth_stop_required = binding.auth_shared_stop_required = True
    sent = []

    def read(*_):
        sent.append(True)
        return b'synthetic image'

    def verify(reader):
        reader.get_image_bytes(IMAGE)
        return '777'

    monkeypatch.setattr(reads._OwnedReadTransport, '__call__', read)
    monkeypatch.setattr(reads.LivePixivReadClient, 'require_identity', verify)
    policy = binding.auth_retry_policy
    try:
        with guarded_reader(local, shared, selected, read) as old:
            old.set_retry_policy(policy)
            before = old._retry_policy_binding[1]
            _select(binding.store, new)
            with pytest.raises(gates.ManagedNetworkGateStorageError):
                old.get_image_bytes(IMAGE)
            if manual:
                policy._check_gate = lambda: GateObservationSnapshot((GateNoticeState('local', 'changed'),), False)
            result, _ = binding._verify_auth_result(new, Event())
            assert result.ok and sent == [True]
            if manual:
                for _ in range(5):
                    policy.recheck_gate_notices()
                    assert policy.has_gate_notices()
                assert getattr(policy, 'confirm_gate_notices', lambda: False)()
                assert not policy.has_gate_notices()
                assert sent == [True]
            else:
                assert policy._selection_generation is not before
    finally:
        binding.close()


@pytest.mark.parametrize('route', ('plain', 'sealed', 'draft'))
def test_gate_exchange_resets_confirmation_streak_for_every_reader_kind(tmp_path, monkeypatch, route):
    root_a, root_b = tmp_path / 'a', tmp_path / 'b'
    root_a.mkdir()
    root_b.mkdir()
    a, sa, ca = linked_owners(root_a)
    b, sb, cb = linked_owners(root_b)
    scope = a if route == 'plain' else sa if route == 'sealed' else a
    physical = scope.gate
    sent = []

    def respond(*_):
        sent.append(True)
        return b'synthetic image'

    def reader(local, shared, credential):
        if route == 'plain':
            return create_exact_managed_read_client(credential, local.gate, transport=respond)
        if route == 'sealed':
            return shared.create_reader(credential, transport=respond)
        return create_shared_draft_auth_reader(local, credential, shared)

    monkeypatch.setattr(reads._OwnedReadTransport, '__call__', respond)
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(scope))
    opener = gates._open_existing_stop_lock

    def refuse(path):
        if path == physical.lock_path:
            raise windows_io_error(5)
        return opener(path)

    with ExitStack() as readers:
        old = readers.enter_context(reader(a, sa, ca))
        old.set_retry_policy(policy)
        with monkeypatch.context() as fault:
            fault.setattr(gates, '_open_existing_stop_lock', refuse)
            with pytest.raises(gates.ManagedNetworkGateStorageError):
                old.get_image_bytes(IMAGE)
        policy.recheck_gate_notices()
        assert policy.has_gate_notices()
        prior_generation = old._retry_policy_binding[1]
        current = readers.enter_context(reader(b, sb, cb))
        current.set_retry_policy(policy)
        assert current._retry_policy_binding[1] is not prior_generation
        scope = b if route != 'sealed' else sb
        policy.recheck_gate_notices()
        assert policy.has_gate_notices()
    assert not sent


@pytest.mark.parametrize('refusal', ('stop', 'intent', 'intent-parent', 'sql', 'rate'))
def test_explicit_confirmation_cannot_clear_current_acquisition_refusal(tmp_path, monkeypatch, refusal):
    local, shared, selected = linked_owners(tmp_path)
    sent = []
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local))
    opener = gates._open_existing_stop_lock

    def refuse(path):
        if path == shared.gate.lock_path:
            raise windows_io_error(5)
        return opener(path)

    with guarded_reader(local, shared, selected, lambda *_: sent.append(True)) as reader:
        reader.set_retry_policy(policy)
        with monkeypatch.context() as fault:
            fault.setattr(gates, '_open_existing_stop_lock', refuse)
            with pytest.raises(gates.ManagedNetworkGateStorageError):
                reader.get_image_bytes(IMAGE)
        if refusal == 'stop':
            shared.gate.trip(401)
        elif refusal == 'intent':
            shared.gate.inflight_path.write_bytes(b'synthetic unfinished GET')
        elif refusal == 'intent-parent':
            def denied_parent(path):
                assert path == shared.gate.inflight_path.parent
                raise windows_io_error(5)
            monkeypatch.setattr(gates, '_probe_inflight_parent', denied_parent)
        elif refusal == 'sql':
            def broken(conn):
                raise gates.sqlite3.OperationalError('synthetic SQL refusal')
            monkeypatch.setattr(gates, '_configure_gate_connection', broken)
        else:
            policy._latch_rate_stop(by_cleanup=False)
        assert hasattr(policy, 'confirm_gate_notices')
        assert not policy.confirm_gate_notices()
        assert policy.has_gate_notices()
        if refusal == 'intent-parent':
            assert not shared.gate.inflight_path.exists()
    assert not sent


def test_native_reconfirmation_does_not_transfer_local_stop_to_shared(tmp_path, monkeypatch):
    local, shared, selected = linked_owners(tmp_path)
    binding = NativeProfileBinding(NativeProfileStore(local.root))
    binding.managed_network_stop_scope, binding.shared_network_stop_scope = local, shared
    binding.auth_stop_required = binding.auth_shared_stop_required = True
    policy = binding.auth_retry_policy
    opener = gates._open_existing_stop_lock

    def refuse(path):
        if path == local.gate.lock_path:
            raise windows_io_error(5)
        return opener(path)

    try:
        with guarded_reader(local, shared, selected, lambda *_: pytest.fail('unexpected send')) as reader:
            reader.set_retry_policy(policy)
            with monkeypatch.context() as fault:
                fault.setattr(gates, '_open_existing_stop_lock', refuse)
                with pytest.raises(gates.ManagedNetworkGateStorageError):
                    reader.get_image_bytes(IMAGE)
            local.gate.trip(401)
            assert not policy.confirm_gate_notices()
            assert policy.has_gate_notices()
            assert not shared.gate.snapshot().blocked
    finally:
        binding.close()


def test_confirming_an_independent_reader_keeps_the_unrepaired_gate(tmp_path, monkeypatch):
    first_root, second_root = tmp_path / 'first', tmp_path / 'second'
    first_root.mkdir()
    second_root.mkdir()
    a, _, ca = linked_owners(first_root)
    b, _, cb = linked_owners(second_root)
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(a))
    denied = {a.gate.lock_path, b.gate.lock_path}
    opener = gates._open_existing_stop_lock

    def refuse(path):
        if path in denied:
            raise windows_io_error(5)
        return opener(path)

    def unexpected(*_):
        pytest.fail('unexpected send')

    with create_exact_managed_read_client(ca, a.gate, transport=unexpected) as first, \
         create_exact_managed_read_client(cb, b.gate, transport=unexpected) as second:
        first.set_retry_policy(policy)
        second.set_retry_policy(policy)
        monkeypatch.setattr(gates, '_open_existing_stop_lock', refuse)
        for reader in (first, second):
            with pytest.raises(gates.ManagedNetworkGateStorageError):
                reader.get_image_bytes(IMAGE)
        denied.remove(b.gate.lock_path)
        assert policy.confirm_gate_notices()
        assert policy.has_gate_notices()
        assert len(policy.progress_snapshot()) == 1
        denied.clear()
        assert policy.confirm_gate_notices()
        assert not policy.has_gate_notices()


def test_shared_only_confirmation_cannot_clear_a_dual_reader_refusal(tmp_path, monkeypatch):
    local, shared, selected = linked_owners(tmp_path)
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local))
    opener = gates._open_existing_stop_lock

    def refuse(path):
        if path == shared.gate.lock_path:
            raise windows_io_error(5)
        return opener(path)

    def unexpected(*_):
        pytest.fail('unexpected send')

    with guarded_reader(local, shared, selected, unexpected) as dual, \
         shared.create_reader(selected, transport=unexpected) as shared_only:
        dual.set_retry_policy(policy)
        shared_only.set_retry_policy(policy)
        with monkeypatch.context() as fault:
            fault.setattr(gates, '_open_existing_stop_lock', refuse)
            with pytest.raises(gates.ManagedNetworkGateStorageError):
                dual.get_image_bytes(IMAGE)
        local.gate.trip(403)
        assert not policy.confirm_gate_notices()
        assert policy.has_gate_notices()


def test_selection_changed_during_reconfirmation_keeps_notice(tmp_path, monkeypatch):
    local, shared, selected = linked_owners(tmp_path)
    next_path = selected.with_name('next-synthetic.txt')
    next_path.write_bytes(selected.read_bytes())
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local))
    opener = gates._open_existing_stop_lock

    def refuse(path):
        if path == shared.gate.lock_path:
            raise windows_io_error(5)
        return opener(path)

    permit = shared.gate._permission

    @contextmanager
    def change_selection(*, confirming=False):
        with permit(confirming=confirming):
            with closing(NativeProfileStore(local.root)) as store:
                _select(store, next_path)
            yield

    with guarded_reader(local, shared, selected, lambda *_: pytest.fail('unexpected send')) as reader:
        reader.set_retry_policy(policy)
        with monkeypatch.context() as fault:
            fault.setattr(gates, '_open_existing_stop_lock', refuse)
            with pytest.raises(gates.ManagedNetworkGateStorageError):
                reader.get_image_bytes(IMAGE)
        monkeypatch.setattr(shared.gate, '_permission', change_selection)
        assert not policy.confirm_gate_notices()
        assert policy.has_gate_notices()


def test_a_healthy_sibling_cannot_confirm_poisoned_pool_cleanup(tmp_path, monkeypatch):
    local, shared, selected = linked_owners(tmp_path)
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local))

    with guarded_reader(local, shared, selected) as poisoned, \
         guarded_reader(local, shared, selected, lambda *_: pytest.fail('unexpected send')) as healthy:
        poisoned.set_retry_policy(policy)
        with monkeypatch.context() as fault:
            fault.setattr(reads.LivePixivReadClient, 'shutdown_unverified',
                          property(lambda reader: reader is poisoned))
            with pytest.raises(reads.ClientClosed):
                poisoned.get_image_bytes(IMAGE)
            healthy.set_retry_policy(policy)
            assert not policy.confirm_gate_notices()
            assert policy.has_gate_notices()


@pytest.mark.asyncio
async def test_banner_reconfirmation_calls_real_permission_without_get(tmp_path, monkeypatch):
    from nicegui import Client, ui
    from pixivvault_nicegui.ui.request_status_view import RequestStatusView

    local, shared, selected = linked_owners(tmp_path)
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local))
    denied = True
    saw_permissions = []
    opener = gates._open_existing_stop_lock
    require = shared.gate._require_open_locked

    def refuse(path):
        if denied and path == shared.gate.lock_path:
            raise windows_io_error(5)
        return opener(path)

    def verify():
        saw_permissions.append(True)
        return require()

    def unexpected(*_):
        pytest.fail('unexpected send')

    with guarded_reader(local, shared, selected, unexpected) as reader:
        reader.set_retry_policy(policy)
        monkeypatch.setattr(gates, '_open_existing_stop_lock', refuse)
        with pytest.raises(gates.ManagedNetworkGateStorageError):
            reader.get_image_bytes(IMAGE)
        page = Client(ui.page('/_fix_notice_round12'))
        with page:
            view = RequestStatusView(policy)
        try:
            assert view.confirm_button.visible
            await view.confirm_gate()
            assert view.confirm_status.visible and policy.has_gate_notices()
            denied = False
            monkeypatch.setattr(shared.gate, '_require_open_locked', verify)
            await view.confirm_gate()
            assert saw_permissions == [True]
            assert not policy.has_gate_notices() and not view.confirm_button.visible
        finally:
            view.close()
            page.delete()


def test_successful_get_supplies_evidence_for_automatic_clear(tmp_path, monkeypatch):
    local, shared, selected = linked_owners(tmp_path)
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local))
    sent = []
    opener = gates._open_existing_stop_lock

    def refuse(path):
        if path == shared.gate.lock_path:
            raise windows_io_error(5)
        return opener(path)

    def respond(*_):
        sent.append(True)
        return b'synthetic image'

    with guarded_reader(local, shared, selected, respond) as reader:
        reader.set_retry_policy(policy)
        with monkeypatch.context() as fault:
            fault.setattr(gates, '_open_existing_stop_lock', refuse)
            with pytest.raises(gates.ManagedNetworkGateStorageError):
                reader.get_image_bytes(IMAGE)
        for _ in range(4):
            policy.recheck_gate_notices()
            assert policy.has_gate_notices()
        assert reader.get_image_bytes(IMAGE) == b'synthetic image'
        policy.recheck_gate_notices()
        assert policy.has_gate_notices()
        policy.recheck_gate_notices()
        assert not policy.has_gate_notices()
    assert sent == [True]


def test_observed_new_intent_is_shown_in_one_cycle_and_requires_real_confirmation(tmp_path, monkeypatch):
    local, shared, selected = linked_owners(tmp_path)
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local))
    opener = gates._open_existing_stop_lock

    def refuse(path):
        if path == shared.gate.lock_path:
            raise windows_io_error(5)
        return opener(path)

    with guarded_reader(local, shared, selected, lambda *_: pytest.fail('unexpected send')) as reader:
        reader.set_retry_policy(policy)
        with monkeypatch.context() as fault:
            fault.setattr(gates, '_open_existing_stop_lock', refuse)
            with pytest.raises(gates.ManagedNetworkGateStorageError):
                reader.get_image_bytes(IMAGE)
        assert policy.confirm_gate_notices()
        marker = shared.gate.inflight_path
        marker.write_bytes(b'synthetic unfinished GET')
        policy.recheck_gate_notices()
        assert any(row.get('gate_problem') == 'uncertain' for row in policy.progress_snapshot())
        assert not policy.confirm_gate_notices()
        marker.unlink()
        for _ in range(3):
            policy.recheck_gate_notices()
            assert policy.has_gate_notices()
        assert policy.confirm_gate_notices()
        assert not policy.has_gate_notices()


def test_fresh_observed_intent_fences_reconfirmation_of_the_same_notice(tmp_path, monkeypatch):
    local, shared, selected = linked_owners(tmp_path)
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local))
    marker = shared.gate.inflight_path

    with guarded_reader(local, shared, selected, lambda *_: pytest.fail('unexpected send')) as reader:
        reader.set_retry_policy(policy)
        first_confirm = reader._confirm_gate_admission
        marker.write_bytes(b'synthetic prior unfinished GET')
        with pytest.raises(gates.ManagedNetworkGateStorageError):
            reader.get_image_bytes(IMAGE)
        marker.unlink()

        def racing_confirmation():
            proof = first_confirm()
            marker.write_bytes(b'synthetic new unfinished GET')
            policy.recheck_gate_notices()
            return proof

        monkeypatch.setattr(reader, '_confirm_gate_admission', racing_confirmation)
        assert not policy.confirm_gate_notices()
        assert policy.has_gate_notices()


@pytest.mark.parametrize('fault', ('intent-parent', 'physical-rw', 'lock-access', 'db-handle', 'owner',
                                 'sqlite-exclusive'))
def test_observed_permission_denial_invalidates_an_earlier_reconfirmation(tmp_path, monkeypatch, fault):
    if fault == 'sqlite-exclusive' and os.name != 'nt':
        pytest.skip('Windows SQLite lock probe')
    local, shared, selected = linked_owners(tmp_path)
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local))
    opener = gates._open_existing_stop_lock
    deny_initial_lock, deny_later = True, False
    connect = gates.sqlite3.connect
    probe = gates._probe_inflight_parent
    uri = gates._gate_database_uri(shared.gate.path)
    exclusive = None

    def refuse_lock(path):
        if (path == shared.gate.lock_path and (deny_initial_lock or deny_later and fault == 'lock-access')
                or path == shared.gate.path and deny_later and fault == 'db-handle'):
            raise windows_io_error(5)
        return opener(path)

    check_owner = shared._check_owner

    def reject_owner():
        if deny_later and fault == 'owner':
            raise gates.ManagedNetworkGateStorageError('synthetic owner refusal')
        return check_owner()

    def reject_parent(path):
        if deny_later and fault == 'intent-parent' and path == shared.gate.inflight_path.parent:
            raise windows_io_error(5)
        return probe(path)

    def reject_rw(path, *args, **kwargs):
        if deny_later and fault in ('physical-rw', 'db-handle') and path == uri:
            raise gates.sqlite3.OperationalError('synthetic R/W permission failure')
        return connect(path, *args, **kwargs)

    monkeypatch.setattr(shared, '_check_owner', reject_owner)
    with guarded_reader(local, shared, selected, lambda *_: pytest.fail('unexpected send')) as reader:
        reader.set_retry_policy(policy)
        first_confirm = reader._confirm_gate_admission
        monkeypatch.setattr(gates, '_open_existing_stop_lock', refuse_lock)
        monkeypatch.setattr(gates, '_probe_inflight_parent', reject_parent)
        monkeypatch.setattr(gates.sqlite3, 'connect', reject_rw)
        with pytest.raises(gates.ManagedNetworkGateStorageError):
            reader.get_image_bytes(IMAGE)
        deny_initial_lock = False

        def racing_confirmation():
            nonlocal deny_later, exclusive
            proof = first_confirm()
            deny_later = True
            if fault == 'sqlite-exclusive':
                exclusive = connect(shared.gate.path)
                exclusive.execute('BEGIN EXCLUSIVE')
            policy.recheck_gate_notices()
            return proof

        monkeypatch.setattr(reader, '_confirm_gate_admission', racing_confirmation)
        try:
            assert not policy.confirm_gate_notices()
            assert policy.has_gate_notices()
        finally:
            if exclusive is not None:
                exclusive.close()
