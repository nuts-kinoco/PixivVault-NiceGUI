"""Behavior of fixed notices and zero-send admission confirmation."""
from contextlib import closing, contextmanager
import errno
from pathlib import Path
from threading import Event, Thread

import pytest

from pixivvault_nicegui import managed_network_stop_gate as gates, pixiv_read_client as reads
from pixivvault_nicegui.managed_network_stop_public_cdn import bind_public_cdn_shared_stop
from pixivvault_nicegui.managed_network_stop_reader import create_exact_managed_read_client
from pixivvault_nicegui.native_profile import NativeProfileStore
from pixivvault_nicegui.native_startup import NativeProfileBinding
from pixivvault_nicegui.request_notice_gates import GateNoticeState, GateObservationSnapshot, check_request_notice_gates
from test_fix_notice_round6_20261122 import windows_io_error
from test_fix_notice_round7_20261122 import linked_owners
from test_fix_notice_round9_20261122 import guarded_reader
from test_fix_notice_sweep_20261122 import retry_policy
from test_stage5_r1_shared_stop_transport import IMAGE


def unexpected(*_):
    pytest.fail('unexpected send')


def pin_shared(reader, shared, monkeypatch):
    opener = gates._open_existing_stop_lock

    def refuse(path):
        if path == shared.gate.lock_path:
            raise windows_io_error(5)
        return opener(path)

    with monkeypatch.context() as fault:
        fault.setattr(gates, '_open_existing_stop_lock', refuse)
        with pytest.raises(gates.ManagedNetworkGateStorageError):
            reader.get_image_bytes(IMAGE)


@pytest.mark.parametrize('phase', ('create', 'fsync', 'remove'))
def test_reconfirm_uses_real_shared_intent_lifecycle(tmp_path, monkeypatch, phase):
    local, shared, selected = linked_owners(tmp_path)
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local))
    marker = shared.gate.inflight_path
    with guarded_reader(local, shared, selected, unexpected) as reader:
        reader.set_retry_policy(policy)
        pin_shared(reader, shared, monkeypatch)
        opened, removed = Path.open, Path.unlink

        def open_marker(path, mode='r', *args, **kwargs):
            if path == marker and mode == 'xb':
                raise OSError(errno.ENOSPC, 'synthetic full destination')
            return opened(path, mode, *args, **kwargs)

        def unlink_marker(path, *args, **kwargs):
            if path == marker:
                raise windows_io_error(5)
            return removed(path, *args, **kwargs)

        def fsync(_):
            raise OSError(errno.ENOSPC, 'synthetic full destination')

        with monkeypatch.context() as fault:
            if phase == 'create':
                fault.setattr(Path, 'open', open_marker)
            elif phase == 'remove':
                fault.setattr(Path, 'unlink', unlink_marker)
            else:
                fault.setattr(gates.os, 'fsync', fsync)
            assert not policy.confirm_gate_notices()
            assert policy.has_gate_notices()
        if marker.exists():
            marker.unlink()
        assert policy.confirm_gate_notices()
        assert not policy.has_gate_notices()


@pytest.mark.parametrize('refusal', ('lock', 'saved-reference'))
def test_reconfirmation_fences_a_real_refusal_before_publication(tmp_path, monkeypatch, refusal):
    local, shared, selected = linked_owners(tmp_path)
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local))
    entered, release = Event(), Event()
    errors = []
    with guarded_reader(local, shared, selected, unexpected) as reader, \
         guarded_reader(local, shared, selected, unexpected) as parallel:
        reader.set_retry_policy(policy)
        parallel.set_retry_policy(policy)
        pin_shared(reader, shared, monkeypatch)
        opener = gates._open_existing_stop_lock
        original = parallel._get_once
        confirm = parallel._confirm_gate_admission

        def refuse(path):
            if path == shared.gate.lock_path:
                raise windows_io_error(5)
            return opener(path)

        def delay_publish(*args, **kwargs):
            try:
                return original(*args, **kwargs)
            except gates.ManagedNetworkGateStorageError:
                entered.set()
                assert release.wait(5)
                raise

        def get():
            try:
                parallel.get_image_bytes(IMAGE)
            except BaseException as error:
                errors.append(error)

        from pixivvault_nicegui.native_profile import PROFILE_DB
        journal = local.root / (PROFILE_DB + '-journal')
        worker = Thread(target=get)

        def after_permission():
            proof = confirm()
            if worker.ident is not None:
                return proof
            if refusal == 'lock':
                monkeypatch.setattr(gates, '_open_existing_stop_lock', refuse)
            else:
                journal.write_bytes(b'synthetic active rollback journal')
            worker.start()
            assert entered.wait(5)
            if refusal == 'saved-reference':
                policy.recheck_gate_notices()
            return proof

        monkeypatch.setattr(parallel, '_get_once', delay_publish)
        monkeypatch.setattr(reader, '_confirm_gate_admission', after_permission)
        monkeypatch.setattr(parallel, '_confirm_gate_admission', after_permission)
        initial_ids = {row['notice_id'] for row in policy.progress_snapshot()}
        try:
            assert not policy.confirm_gate_notices()
            assert initial_ids <= {row['notice_id'] for row in policy.progress_snapshot()}
        finally:
            release.set()
            if worker.ident is not None:
                worker.join(5)
        assert len(errors) == 1 and isinstance(errors[0], gates.ManagedNetworkGateStorageError)


@pytest.mark.parametrize('during_get', (False, True))
def test_success_and_background_observation_keep_fixed_notice(tmp_path, monkeypatch, during_get):
    local, shared, selected = linked_owners(tmp_path)
    clock = [0.0]
    policy = retry_policy(clock, check_gate=lambda: check_request_notice_gates(local))

    def respond(*_):
        if during_get:
            policy.recheck_gate_notices()
        return b'synthetic image'

    with guarded_reader(local, shared, selected, respond) as reader:
        reader.set_retry_policy(policy)
        pin_shared(reader, shared, monkeypatch)
        assert reader.get_image_bytes(IMAGE) == b'synthetic image'
        clock[0] = 601.0
        for _ in range(3):
            policy.recheck_gate_notices()
            assert policy.has_gate_notices()
        assert policy.confirm_gate_notices()
        assert not policy.has_gate_notices()


def test_observed_busy_is_fixed_until_explicit_confirmation(tmp_path, monkeypatch):
    local, shared, selected = linked_owners(tmp_path)
    clock = [0.0]
    snapshot = None
    policy = retry_policy(clock, check_gate=lambda: snapshot or check_request_notice_gates(local))
    with guarded_reader(local, shared, selected, unexpected) as reader:
        reader.set_retry_policy(policy)
        pin_shared(reader, shared, monkeypatch)
        assert policy.confirm_gate_notices()
        snapshot = GateObservationSnapshot((GateNoticeState('shared', 'busy'),))
        policy.recheck_gate_notices()
        ids = {row['notice_id'] for row in policy.progress_snapshot() if row['stop_cause'] == 'gate_busy'}
        assert ids
        snapshot = None
        clock[0] = 601.0
        policy.recheck_gate_notices()
        assert ids <= {row['notice_id'] for row in policy.progress_snapshot()}
        assert policy.confirm_gate_notices()
        assert not policy.has_gate_notices()


def test_closed_sibling_does_not_hide_live_reconfirmation(tmp_path, monkeypatch):
    local, shared, selected = linked_owners(tmp_path)
    policy = retry_policy([0.0])
    with guarded_reader(local, shared, selected, unexpected) as live, \
         guarded_reader(local, shared, selected, unexpected) as closed:
        live.set_retry_policy(policy)
        closed.set_retry_policy(policy)
        pin_shared(live, shared, monkeypatch)
        closed.close()
        assert policy.confirm_gate_notices()
        assert not policy.has_gate_notices()


def test_official_gate_exchange_keeps_pin_until_reconfirmation(tmp_path, monkeypatch):
    first, second = tmp_path / 'first', tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    local, shared, selected = linked_owners(first)
    new_local, new_shared, new_selected = linked_owners(second)
    policy = retry_policy([0.0])
    with guarded_reader(local, shared, selected, unexpected) as retired, \
         guarded_reader(new_local, new_shared, new_selected, unexpected) as replacement:
        retired.set_retry_policy(policy)
        pin_shared(retired, shared, monkeypatch)
        retired.close()
        replacement.set_retry_policy(policy, replaces=retired)
        assert policy.progress_snapshot()
        assert policy.confirm_gate_notices()
        assert not policy.progress_snapshot()


def test_local_cannot_confirm_unconfigured_public_cdn(tmp_path):
    local, _, selected = linked_owners(tmp_path)
    policy = retry_policy([0.0])
    with create_exact_managed_read_client(selected, local.gate, transport=unexpected) as reader, \
         closing(reads.PublicImageReadClient()) as public:
        reader.set_retry_policy(policy)
        bind_public_cdn_shared_stop(public, None)
        public.set_retry_policy(policy)
        with pytest.raises(gates.ManagedNetworkGateStorageError):
            public.get_image_bytes(IMAGE)
        assert not policy.can_confirm_gate_notices()
        assert not policy.confirm_gate_notices()
        assert policy.has_gate_notices()


@pytest.mark.parametrize('saved', (False, True))
def test_native_draft_reconfirmation_uses_the_draft_route(tmp_path, monkeypatch, saved):
    local, shared, selected = linked_owners(tmp_path)
    draft = selected.with_name('draft-synthetic.txt')
    draft.write_bytes(selected.read_bytes())
    binding = NativeProfileBinding(NativeProfileStore(local.root))
    binding.managed_network_stop_scope, binding.shared_network_stop_scope = local, shared
    binding.auth_stop_required = binding.auth_shared_stop_required = True
    if saved:
        selected.unlink()
    else:
        binding.store.update(cookie_file=None, expected_revision=binding.store.snapshot().revision)
    opener = Path.open

    def reject_intent(path, mode='r', *args, **kwargs):
        if path == shared.gate.inflight_path and mode == 'xb':
            raise OSError(errno.ENOSPC, 'synthetic intent refusal')
        return opener(path, mode, *args, **kwargs)

    monkeypatch.setattr(reads.LivePixivReadClient, 'require_identity', lambda reader: reader.get_image_bytes(IMAGE))
    monkeypatch.setattr(reads._OwnedReadTransport, '__call__', unexpected)
    try:
        with monkeypatch.context() as fault:
            fault.setattr(Path, 'open', reject_intent)
            result, _ = binding._verify_auth_result(draft, Event())
        assert not result.ok
        assert binding.auth_retry_policy.has_gate_notices()
        assert binding.auth_retry_policy.confirm_gate_notices()
        assert not binding.auth_retry_policy.has_gate_notices()
    finally:
        binding.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('cause', ('rate', 'closed-pool'))
async def test_unrecheckable_notices_do_not_offer_confirmation(tmp_path, monkeypatch, cause):
    from nicegui import Client, ui
    from pixivvault_nicegui.ui.request_status_view import RequestStatusView

    local, shared, selected = linked_owners(tmp_path)
    policy = retry_policy([0.0])
    with guarded_reader(local, shared, selected, unexpected) as reader:
        reader.set_retry_policy(policy)
        pin_shared(reader, shared, monkeypatch)
        if cause == 'rate':
            policy._latch_rate_stop(by_cleanup=False)
        else:
            assert policy.confirm_gate_notices()
            monkeypatch.setattr(reads.LivePixivReadClient, 'shutdown_unverified', property(lambda _: True))
            with pytest.raises(reads.ClientClosed):
                reader.get_image_bytes(IMAGE)
        page = Client(ui.page('/_fix_notice_round13'))
        with page:
            view = RequestStatusView(policy)
        try:
            assert not view.confirm_button.visible
            assert view.unrecheckable_status.visible
            assert '再確認できません' in view.unrecheckable_status.text
        finally:
            view.close()
            page.delete()
        assert not policy.can_confirm_gate_notices()
        assert not policy.confirm_gate_notices()
        assert policy.has_gate_notices()


def test_reconfirmation_cannot_clear_a_401_known_before_gate_unwinds(tmp_path, monkeypatch):
    local, shared, selected = linked_owners(tmp_path)
    policy = retry_policy([0.0])
    entered, release = Event(), Event()
    failures = []

    def refuse_response(*_):
        raise reads.HTTPStatusRejected(401)

    with guarded_reader(local, shared, selected, refuse_response) as reader:
        reader.set_retry_policy(policy)
        pin_shared(reader, shared, monkeypatch)
        trip = shared.gate._trip_locked
        confirm = reader._confirm_gate_admission

        def delayed_trip(status):
            assert status == 401
            entered.set()
            assert release.wait(5)
            return trip(status)

        def get():
            try:
                reader.get_image_bytes(IMAGE)
            except BaseException as error:
                failures.append(error)

        worker = Thread(target=get)

        def race_after_permission():
            proof = confirm()
            worker.start()
            assert entered.wait(5)
            return proof

        monkeypatch.setattr(shared.gate, '_trip_locked', delayed_trip)
        monkeypatch.setattr(reader, '_confirm_gate_admission', race_after_permission)
        try:
            assert not policy.confirm_gate_notices()
            assert policy.has_gate_notices()
        finally:
            release.set()
            if worker.ident is not None:
                worker.join(5)
        assert len(failures) == 1 and isinstance(failures[0], gates.ManagedNetworkStopped)
        assert failures[0].status_code == 401


def test_native_current_owner_vetoes_local_draft_reader_confirmation(tmp_path, monkeypatch):
    from pixivvault_nicegui.native_profile import PROFILE_MARKER

    local, _, selected = linked_owners(tmp_path)
    binding = NativeProfileBinding(NativeProfileStore(local.root))
    binding.managed_network_stop_scope = local
    binding.auth_stop_required = True
    policy = binding.auth_retry_policy
    opener = gates._open_existing_stop_lock

    def refuse(path):
        if path == local.gate.lock_path:
            raise windows_io_error(5)
        return opener(path)

    try:
        with create_exact_managed_read_client(selected, local.gate, transport=unexpected) as reader:
            reader.set_retry_policy(policy)
            with monkeypatch.context() as fault:
                fault.setattr(gates, '_open_existing_stop_lock', refuse)
                with pytest.raises(gates.ManagedNetworkGateStorageError):
                    reader.get_image_bytes(IMAGE)
            marker = local.root / PROFILE_MARKER
            contents = marker.read_bytes()
            marker.unlink()
            assert not policy.confirm_gate_notices()
            assert policy.has_gate_notices()
            marker.write_bytes(contents)
            assert policy.confirm_gate_notices()
            assert not policy.has_gate_notices()
    finally:
        binding.close()


def test_confirmation_does_not_pin_its_own_shared_gate_activity(tmp_path, monkeypatch):
    local, shared, selected = linked_owners(tmp_path)
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local))
    with guarded_reader(local, shared, selected, unexpected) as reader:
        reader.set_retry_policy(policy)
        pin_shared(reader, shared, monkeypatch)
        request = shared.gate.request

        @contextmanager
        def poll_during_request():
            with request():
                assert shared.gate.inflight_path.is_file()
                assert any(state.state == 'busy' for state in check_request_notice_gates(shared).states)
                policy.recheck_gate_notices()
                yield

        monkeypatch.setattr(shared.gate, 'request', poll_during_request)
        assert policy.confirm_gate_notices()
        assert not policy.has_gate_notices()
        assert not shared.gate.inflight_path.exists()


def test_real_native_preflight_refusal_fences_confirmation(tmp_path, monkeypatch):
    local, shared, credential = linked_owners(tmp_path)
    binding = NativeProfileBinding(NativeProfileStore(local.root))
    binding.managed_network_stop_scope, binding.shared_network_stop_scope = local, shared
    binding.auth_stop_required = binding.auth_shared_stop_required = True
    policy = binding.auth_retry_policy
    confirm = binding._confirm_request_notice_reader

    def check_then_deny_native(*args, **kwargs):
        proof = confirm(*args, **kwargs)
        marker = shared.gate.inflight_path
        marker.write_bytes(b'synthetic unfinished GET')
        result, _ = binding._verify_auth_result(credential, Event())
        assert result.code == 'uncertain'
        return proof

    try:
        with guarded_reader(local, shared, credential, unexpected) as reader:
            reader.set_retry_policy(policy)
            pin_shared(reader, shared, monkeypatch)
            monkeypatch.setattr(binding, '_confirm_request_notice_reader', check_then_deny_native)
            assert not policy.confirm_gate_notices()
            assert policy.has_gate_notices()
    finally:
        binding.close()


def test_observed_refusal_belongs_only_to_its_actual_physical_owner(tmp_path, monkeypatch):
    roots = tmp_path / 'first', tmp_path / 'second'
    for root in roots:
        root.mkdir()
    first, _, first_credential = linked_owners(roots[0])
    second, _, second_credential = linked_owners(roots[1])
    first.shared_owner = second.shared_owner = None
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(first, second))
    opener = gates._open_existing_stop_lock

    def refuse(path):
        if path == first.gate.lock_path:
            raise windows_io_error(5)
        return opener(path)

    with create_exact_managed_read_client(first_credential, first.gate, transport=unexpected) as a, \
         create_exact_managed_read_client(second_credential, second.gate, transport=unexpected) as b:
        a.set_retry_policy(policy)
        b.set_retry_policy(policy)
        with monkeypatch.context() as fault:
            fault.setattr(gates, '_open_existing_stop_lock', refuse)
            policy.recheck_gate_notices()
            assert policy.has_gate_notices()
            assert not policy.confirm_gate_notices()
        assert policy.confirm_gate_notices()
        assert not policy.has_gate_notices()



def test_reconfirmation_busy_filter_is_limited_to_the_gate_it_owns(tmp_path, monkeypatch):
    from test_fix_notice_round5_20261122 import hold_windows_lock
    from contextlib import ExitStack

    roots = tmp_path / 'first', tmp_path / 'second'
    for root in roots:
        root.mkdir()
    first, _, first_credential = linked_owners(roots[0])
    second, _, second_credential = linked_owners(roots[1])
    first.shared_owner = second.shared_owner = None
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(first, second))
    opener = gates._open_existing_stop_lock

    def refuse(path):
        if path in (first.gate.lock_path, second.gate.lock_path):
            raise windows_io_error(5)
        return opener(path)

    with ExitStack() as held:
        a = held.enter_context(create_exact_managed_read_client(first_credential, first.gate, transport=unexpected))
        b = held.enter_context(create_exact_managed_read_client(second_credential, second.gate, transport=unexpected))
        a.set_retry_policy(policy)
        b.set_retry_policy(policy)
        with monkeypatch.context() as fault:
            fault.setattr(gates, '_open_existing_stop_lock', refuse)
            for reader in (a, b):
                with pytest.raises(gates.ManagedNetworkGateStorageError):
                    reader.get_image_bytes(IMAGE)

        confirm_first = a._confirm_gate_admission
        request = second.gate.request

        def confirm_then_contend():
            result = confirm_first()
            held.enter_context(hold_windows_lock(first.gate.lock_path, exclusive_open=False))
            return result

        @contextmanager
        def check_during_second():
            with request():
                snapshot = check_request_notice_gates(first, second)
                assert [state.state for state in snapshot.states] == ['busy', 'busy']
                policy.recheck_gate_notices()
                yield

        monkeypatch.setattr(a, '_confirm_gate_admission', confirm_then_contend)
        monkeypatch.setattr(second.gate, 'request', check_during_second)
        assert not policy.confirm_gate_notices()
        assert any(row['stop_cause'] == 'gate_busy' for row in policy.progress_snapshot())


def test_later_manual_stop_invalidates_an_earlier_proof_of_its_shared_gate(tmp_path, monkeypatch):
    local, shared, selected = linked_owners(tmp_path)
    policy = retry_policy([0.0])
    with shared.create_reader(selected, transport=unexpected) as cdn, \
         guarded_reader(local, shared, selected, unexpected) as dual:
        cdn.set_retry_policy(policy)
        dual.set_retry_policy(policy)
        pin_shared(cdn, shared, monkeypatch)
        pin_shared(dual, shared, monkeypatch)
        check = dual._confirm_gate_admission

        def stop_before_check():
            shared.gate.trip(401)
            return check()

        monkeypatch.setattr(dual, '_confirm_gate_admission', stop_before_check)
        ids = {row['notice_id'] for row in policy.progress_snapshot()}
        assert len(ids) == 2
        assert not policy.confirm_gate_notices()
        assert ids <= {row['notice_id'] for row in policy.progress_snapshot()}



def test_reconfirmation_does_not_hide_a_shared_lock_it_never_acquired(tmp_path, monkeypatch):
    from contextlib import ExitStack
    from test_fix_notice_round5_20261122 import hold_windows_lock

    local, shared, selected = linked_owners(tmp_path)
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local))
    opener = gates._open_existing_stop_lock
    deny_local = False

    def refuse_local(path):
        nonlocal deny_local
        if path == local.gate.lock_path and deny_local:
            deny_local = False
            snapshot = check_request_notice_gates(local)
            assert any(state.authority == 'shared' and state.state == 'busy' for state in snapshot.states)
            policy.recheck_gate_notices()
            raise windows_io_error(5)
        return opener(path)

    with ExitStack() as held:
        cdn = held.enter_context(shared.create_reader(selected, transport=unexpected))
        dual = held.enter_context(guarded_reader(local, shared, selected, unexpected))
        cdn.set_retry_policy(policy)
        dual.set_retry_policy(policy)
        pin_shared(cdn, shared, monkeypatch)
        pin_shared(dual, shared, monkeypatch)
        confirm_first = cdn._confirm_gate_admission

        def after_shared_confirmation():
            nonlocal deny_local
            proof = confirm_first()
            held.enter_context(hold_windows_lock(shared.gate.lock_path, exclusive_open=False))
            deny_local = True
            return proof

        monkeypatch.setattr(cdn, '_confirm_gate_admission', after_shared_confirmation)
        monkeypatch.setattr(gates, '_open_existing_stop_lock', refuse_local)
        assert not policy.confirm_gate_notices()
        assert any(row['stop_cause'] == 'gate_busy' and row['gate_owners'] == ('shared',)
                   for row in policy.progress_snapshot())
