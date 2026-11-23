"""Regressions for process-wide refusal tracking and explicit admission."""
from contextlib import closing, contextmanager
from threading import Event, Thread, current_thread

import pytest

from pixivvault_nicegui import managed_network_stop_gate as gates, pixiv_read_client as reads
from pixivvault_nicegui.managed_network_stop_public_cdn import bind_public_cdn_shared_stop
from pixivvault_nicegui.native_profile import NativeProfileStore
from pixivvault_nicegui.native_startup import NativeProfileBinding
from pixivvault_nicegui.request_notice_gates import check_request_notice_gates
from test_fix_notice_round6_20261122 import windows_io_error
from test_fix_notice_round7_20261122 import linked_owners
from test_fix_notice_round9_20261122 import guarded_reader
from test_fix_notice_round13_20261122 import pin_shared, unexpected
from test_fix_notice_sweep_20261122 import retry_policy
from test_stage5_r1_shared_stop_transport import IMAGE


def native_binding(local, shared):
    binding = NativeProfileBinding(NativeProfileStore(local.root))
    binding.managed_network_stop_scope, binding.shared_network_stop_scope = local, shared
    binding.auth_stop_required = binding.auth_shared_stop_required = True
    return binding


@pytest.mark.parametrize('entry', ('cdn', 'cdn-alias', 'followup'))
def test_native_confirmation_fences_peer_refusal_across_entry_points(tmp_path, monkeypatch, entry):
    local, shared, credential = linked_owners(tmp_path)
    with closing(native_binding(local, shared)) as binding, \
         guarded_reader(local, shared, credential, unexpected) as reader, \
         closing(reads.PublicImageReadClient()) as public:
        policy = binding.auth_retry_policy
        reader.set_retry_policy(policy)
        pin_shared(reader, shared, monkeypatch)
        cdn_owner = type(shared)(shared.root) if entry == 'cdn-alias' else shared
        bind_public_cdn_shared_stop(public, cdn_owner)
        public.set_retry_policy(retry_policy([0.0]))
        monkeypatch.setattr(reads._OwnedReadTransport, '__call__', unexpected)
        confirm = binding._confirm_request_notice_reader
        opener = gates._open_existing_stop_lock
        publish = policy.report_preflight_refusal if entry == 'followup' else None
        deferred = []
        if publish is not None:
            monkeypatch.setattr(policy, 'report_preflight_refusal', lambda *args: deferred.append(args))

        def refuse(path):
            if path == shared.gate.lock_path:
                raise windows_io_error(5)
            return opener(path)

        def after_permission(*args, **kwargs):
            proof = confirm(*args, **kwargs)
            with monkeypatch.context() as fault, pytest.raises(gates.ManagedNetworkGateStorageError):
                fault.setattr(gates, '_open_existing_stop_lock', refuse)
                if entry.startswith('cdn'):
                    public.get_image_bytes(IMAGE)
                else:
                    binding.followup._open_reader(credential)
            return proof

        monkeypatch.setattr(binding, '_confirm_request_notice_reader', after_permission)
        notice_ids = {row['notice_id'] for row in policy.progress_snapshot()}
        assert not policy.confirm_gate_notices()
        assert notice_ids <= {row['notice_id'] for row in policy.progress_snapshot()}
        if publish is not None:
            assert len(deferred) == 1
            publish(*deferred[0])


def test_other_policy_observes_native_confirmation_without_pinning_its_lock(tmp_path, monkeypatch):
    local, shared, credential = linked_owners(tmp_path)
    with closing(native_binding(local, shared)) as binding, \
         guarded_reader(local, shared, credential, unexpected) as reader, \
         closing(reads.PublicImageReadClient()) as public:
        policy = binding.auth_retry_policy
        reader.set_retry_policy(policy)
        pin_shared(reader, shared, monkeypatch)
        observer = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(shared))
        bind_public_cdn_shared_stop(public, shared)
        public.set_retry_policy(observer)
        request = shared.gate.request

        @contextmanager
        def poll():
            with request():
                assert check_request_notice_gates(shared).states[0].state == 'busy'
                observer.recheck_gate_notices()
                yield

        monkeypatch.setattr(shared.gate, 'request', poll)
        assert policy.confirm_gate_notices()
        assert not observer.has_gate_notices()


def test_lock_refusal_fences_deletion_while_status_lock_is_held(tmp_path, monkeypatch):
    local, shared, credential = linked_owners(tmp_path)
    policy = retry_policy([0.0])
    at_byte_lock, deny, entered_error, recorded, finish = (Event() for _ in range(5))
    failures = []
    with guarded_reader(local, shared, credential, unexpected) as admitting, \
         guarded_reader(local, shared, credential, unexpected) as peer:
        admitting.set_retry_policy(policy)
        peer.set_retry_policy(policy)
        pin_shared(admitting, shared, monkeypatch)
        original_byte_lock = gates._set_stop_byte_lock
        init_error = gates.ManagedNetworkGateStorageError.__init__
        peer_get = peer._get_once
        plan = policy._confirmation_plan
        confirm = admitting._confirm_gate_admission
        calls = 0

        def peer_use():
            try:
                peer.get_image_bytes(IMAGE)
            except BaseException as error:
                failures.append(error)

        worker = Thread(target=peer_use)

        def byte_lock(stream, *, lock=True):
            if lock and current_thread() is worker:
                at_byte_lock.set()
                assert deny.wait(5)
                raise windows_io_error(5)
            return original_byte_lock(stream, lock=lock)

        def capture_error(error, *args, **kwargs):
            if current_thread() is worker:
                entered_error.set()
                init_error(error, *args, **kwargs)
                recorded.set()
            else:
                init_error(error, *args, **kwargs)

        def delay_publication(*args, **kwargs):
            try:
                return peer_get(*args, **kwargs)
            except gates.ManagedNetworkGateStorageError:
                assert finish.wait(5)
                raise

        proofs = []

        def after_confirmation():
            if not proofs:
                proofs.append(confirm())
            proof = proofs[0]
            if worker.ident is None:
                worker.start()
                assert at_byte_lock.wait(5)
            return proof

        def commit_plan():
            nonlocal calls
            calls += 1
            if calls == 2:
                deny.set()
                assert entered_error.wait(5)
                # A refusal recorder independent of the policy lock completes
                # here. The old recorder cannot proceed until commit returns.
                recorded.wait(.25)
            return plan()

        # The second candidate does not need another OS lock in this test;
        # both callbacks reference the one real confirmation.
        monkeypatch.setattr(peer, '_confirm_gate_admission', after_confirmation)
        monkeypatch.setattr(admitting, '_confirm_gate_admission', after_confirmation)
        monkeypatch.setattr(peer, '_get_once', delay_publication)
        monkeypatch.setattr(gates, '_set_stop_byte_lock', byte_lock)
        monkeypatch.setattr(gates.ManagedNetworkGateStorageError, '__init__', capture_error)
        monkeypatch.setattr(policy, '_confirmation_plan', commit_plan)
        notice_ids = {row['notice_id'] for row in policy.progress_snapshot()}
        try:
            assert not policy.confirm_gate_notices()
            assert notice_ids <= {row['notice_id'] for row in policy.progress_snapshot()}
        finally:
            deny.set()
            finish.set()
            if worker.ident is not None:
                worker.join(5)
        assert len(failures) == 1 and isinstance(failures[0], gates.ManagedNetworkGateStorageError)


def test_first_native_lock_failure_pins_and_starts_observation(tmp_path, monkeypatch):
    local, shared, credential = linked_owners(tmp_path)
    with closing(native_binding(local, shared)) as binding:
        policy = binding.auth_retry_policy
        opener = gates._open_existing_stop_lock
        observe = policy._check_gate
        ticks = []

        def refuse(path):
            if path == local.gate.lock_path:
                raise windows_io_error(5)
            return opener(path)

        def observation():
            ticks.append(True)
            return observe()

        monkeypatch.setattr(policy, '_check_gate', observation)
        monkeypatch.setattr(gates, '_open_existing_stop_lock', refuse)
        assert not policy.should_watch_gate()
        result, _ = binding._verify_auth_result(credential, Event())
        assert result.code == 'local_unavailable'
        assert policy.has_gate_notices() and policy.should_watch_gate()
        policy.recheck_gate_notices()
        assert ticks == [True]
        assert any(row['gate_owners'] == ('local',) for row in policy.progress_snapshot())


@pytest.mark.parametrize('fault_reader', (1, 2))
def test_any_confirmation_unlock_failure_is_pinned_even_with_a_healthy_sibling(tmp_path, monkeypatch, fault_reader):
    local, shared, credential = linked_owners(tmp_path)
    policy = retry_policy([0.0])
    with guarded_reader(local, shared, credential, unexpected) as first, \
         guarded_reader(local, shared, credential, unexpected) as second:
        first.set_retry_policy(policy)
        second.set_retry_policy(policy)
        pin_shared(first, shared, monkeypatch)
        original = gates._set_stop_byte_lock
        unlocks = []

        # The Windows HANDLE-based opener does not expose a path as .name.
        # Count the first unlock per shared request in the actual gate stack.
        request = shared.gate.request
        active = False

        @contextmanager
        def shared_request():
            nonlocal active
            try:
                active = True
                with request():
                    yield
            finally:
                active = False

        def release(stream, *, lock):
            original(stream, lock=lock)
            if active and not lock:
                unlocks.append(True)
                if len(unlocks) == fault_reader:
                    raise windows_io_error(5)

        monkeypatch.setattr(shared.gate, 'request', shared_request)
        monkeypatch.setattr(gates, '_set_stop_byte_lock', release)
        assert not policy.confirm_gate_notices()
        assert unlocks == [True, True]
        assert policy.has_gate_notices()
        assert not policy.can_confirm_gate_notices()


def test_later_confirmation_does_not_hide_an_earlier_ordinary_lock_sample(tmp_path, monkeypatch):
    local, shared, credential = linked_owners(tmp_path)
    with closing(native_binding(local, shared)) as binding, \
         guarded_reader(local, shared, credential, unexpected) as reader, \
         closing(reads.PublicImageReadClient()) as public:
        policy = binding.auth_retry_policy
        reader.set_retry_policy(policy)
        pin_shared(reader, shared, monkeypatch)

        def observe():
            with shared.gate.request():
                sample = check_request_notice_gates(shared)
                assert sample.states[0].state == 'busy'
            assert policy.confirm_gate_notices()
            return sample

        observer = retry_policy([0.0], check_gate=observe)
        bind_public_cdn_shared_stop(public, shared)
        public.set_retry_policy(observer)
        observer.recheck_gate_notices()
        assert any(row['stop_cause'] == 'gate_busy' for row in observer.progress_snapshot())


def test_transferred_stop_advances_the_destination_gate_before_stale_confirmation(tmp_path, monkeypatch):
    local, shared, credential = linked_owners(tmp_path)
    cdn_policy, dual_policy = retry_policy([0.0]), retry_policy([0.0])
    with shared.create_reader(credential, transport=unexpected) as public, \
         guarded_reader(local, shared, credential, unexpected) as dual:
        public.set_retry_policy(cdn_policy)
        dual.set_retry_policy(dual_policy)
        pin_shared(public, shared, monkeypatch)
        local.gate.trip(401)
        assert not shared.gate.snapshot().blocked
        check = public._confirm_gate_admission

        def transfer_after_check():
            proof = check()
            with pytest.raises(gates.ManagedNetworkStopped) as stopped:
                dual.get_image_bytes(IMAGE)
            assert stopped.value.status_code == 401
            assert shared.gate.snapshot().blocked
            return proof

        monkeypatch.setattr(public, '_confirm_gate_admission', transfer_after_check)
        assert not cdn_policy.confirm_gate_notices()
        assert cdn_policy.has_gate_notices()


def test_checkout_failure_before_policy_entry_advances_shared_refusal(tmp_path, monkeypatch):
    local, shared, credential = linked_owners(tmp_path)
    policy = retry_policy([0.0])
    with guarded_reader(local, shared, credential, unexpected) as reader, \
         closing(reads.PublicImageReadClient()) as public:
        reader.set_retry_policy(policy)
        pin_shared(reader, shared, monkeypatch)
        bind_public_cdn_shared_stop(public, shared)
        public.set_retry_policy(retry_policy([0.0]))
        original = reader._confirm_gate_admission

        def checkout_after_confirmation():
            proof = original()
            with monkeypatch.context() as fault, pytest.raises(reads.ClientClosed):
                fault.setattr(reads.PublicImageReadClient, 'shutdown_unverified', property(lambda _: True))
                public.get_image_bytes(IMAGE)
            return proof

        monkeypatch.setattr(reader, '_confirm_gate_admission', checkout_after_confirmation)
        assert not policy.confirm_gate_notices()
        assert policy.has_gate_notices()


def test_observer_does_not_attribute_another_holder_after_native_unlock(tmp_path, monkeypatch):
    from test_fix_notice_round5_20261122 import hold_windows_lock

    local, shared, credential = linked_owners(tmp_path)
    with closing(native_binding(local, shared)) as binding, \
         guarded_reader(local, shared, credential, unexpected) as reader, \
         closing(reads.PublicImageReadClient()) as public:
        policy = binding.auth_retry_policy
        reader.set_retry_policy(policy)
        pin_shared(reader, shared, monkeypatch)
        observer = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(shared))
        bind_public_cdn_shared_stop(public, shared)
        public.set_retry_policy(observer)
        request = shared.gate.request
        active, fired = False, False

        @contextmanager
        def shared_request():
            nonlocal active
            active = True
            try:
                with request():
                    yield
            finally:
                active = False

        set_lock = gates._set_stop_byte_lock

        def release(stream, *, lock):
            nonlocal fired
            set_lock(stream, lock=lock)
            if active and not lock:
                fired = True
                with hold_windows_lock(shared.gate.lock_path, exclusive_open=False):
                    observer.recheck_gate_notices()

        monkeypatch.setattr(shared.gate, 'request', shared_request)
        monkeypatch.setattr(gates, '_set_stop_byte_lock', release)
        assert policy.confirm_gate_notices()
        assert fired and observer.has_gate_notices()


@pytest.mark.parametrize('missing', ('local', 'shared'))
def test_official_native_registration_recovers_an_initial_missing_owner(tmp_path, missing):
    local, shared, credential = linked_owners(tmp_path)
    with closing(native_binding(local, shared)) as binding:
        if missing == 'local':
            binding.managed_network_stop_scope = None
        else:
            binding.shared_network_stop_scope = None
        policy = binding.auth_retry_policy
        old = binding._notice_admission_gates()
        result, _ = binding._verify_auth_result(credential, Event())
        assert result.code == ('local_unavailable' if missing == 'local' else 'owner_absent')
        assert policy.has_gate_notices()
        assert not policy.can_confirm_gate_notices()

        binding.managed_network_stop_scope, binding.shared_network_stop_scope = local, shared
        policy.replace_notice_gates(old, binding._notice_admission_gates())
        assert policy.has_gate_notices()
        assert policy.confirm_gate_notices()
        assert not policy.has_gate_notices()


def test_refusal_generations_preserve_concurrent_updates_for_the_same_physical_files(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from pixivvault_nicegui.gate_refusal_state import gate_file, gate_request_route, refusal_generations

    local, shared, _ = linked_owners(tmp_path)
    peer = type(shared)(shared.root)
    files = gate_file(shared.gate)
    assert peer.gate is not shared.gate and gate_file(peer.gate) == files
    other = gate_file(local.gate)
    initial = refusal_generations()
    loops = 600

    def refuse_many(gate):
        with gate_request_route((None, gate)):
            for _ in range(loops):
                # Exercise failure creation without taking an OS or policy
                # lock, as in the real pre-gate ownership refusal paths.
                gates.ManagedNetworkGateStorageError(authority='shared')

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(refuse_many, (shared.gate, peer.gate) * 4))
    final = refusal_generations()
    assert final[files] - initial.get(files, 0) == 8 * loops
    assert final.get(other, 0) == initial.get(other, 0)
