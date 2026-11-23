"""Ordering regressions for refusal capture and manual admission observation."""
from contextlib import closing
import os
from dataclasses import replace
from pathlib import Path
from threading import Event, Thread, current_thread

import pytest

from pixivvault_nicegui import managed_network_stop_gate as gates, pixiv_read_client as reads
from pixivvault_nicegui import managed_network_stop_shared_scope as shared_scopes, request_notice_gates as observe
from pixivvault_nicegui.gate_refusal_state import gate_file, refusal_generations
from pixivvault_nicegui.managed_network_stop_public_cdn import bind_public_cdn_shared_stop
from pixivvault_nicegui.managed_network_stop_startup import attach_selected_histories
from pixivvault_nicegui.managed_network_stop_write import write_admission
from pixivvault_nicegui.native_profile import NativeProfileStore
from pixivvault_nicegui.native_startup import NativeProfileBinding
from test_fix_notice_round6_20261122 import windows_io_error
from test_fix_notice_round7_20261122 import linked_owners
from test_fix_notice_round8_20261122 import database_close_failure
from test_fix_notice_round9_20261122 import guarded_reader
from test_fix_notice_round13_20261122 import pin_shared, unexpected
from test_fix_notice_sweep_20261122 import retry_policy
from test_stage5_r1_shared_stop_transport import IMAGE


@pytest.mark.parametrize('same_policy', (True, False))
@pytest.mark.parametrize('phase', ('classifier', 'native-return'))
def test_raw_lock_denial_fences_before_exception_classification(tmp_path, monkeypatch, same_policy, phase):
    if phase == 'native-return' and os.name != 'nt':
        pytest.skip('Windows lock syscall boundary')
    local, shared, credential = linked_owners(tmp_path)
    policy = retry_policy([0.0])
    at_classification, advance = Event(), Event()
    failures = []
    with guarded_reader(local, shared, credential, unexpected) as reader, \
         guarded_reader(local, shared, credential, unexpected) as other:
        reader.set_retry_policy(policy)
        other.set_retry_policy(retry_policy([0.0]))
        pin_shared(reader, shared, monkeypatch)
        peer = reader if same_policy else other
        confirm = reader._confirm_gate_admission
        set_lock, contended = gates._set_stop_byte_lock, gates._byte_lock_contended

        def get():
            try:
                peer.get_image_bytes(IMAGE)
            except BaseException as error:
                failures.append(error)

        worker = Thread(target=get)

        def deny(stream, *, lock):
            if lock and current_thread() is worker:
                raise windows_io_error(5)
            return set_lock(stream, lock=lock)

        def classify(error):
            if current_thread() is worker:
                at_classification.set()
                assert advance.wait(5)
            return contended(error)

        def after_proof():
            result = confirm()
            worker.start()
            assert at_classification.wait(5)
            return result

        if phase == 'classifier':
            monkeypatch.setattr(gates, '_set_stop_byte_lock', deny)
            monkeypatch.setattr(gates, '_byte_lock_contended', classify)
        else:
            lock_file, last_error = gates._lock_file, gates.ctypes.get_last_error

            def raw_lock(*args):
                if current_thread() is worker:
                    return False
                return lock_file(*args)

            def raw_error():
                if current_thread() is worker:
                    at_classification.set()
                    assert advance.wait(5)
                    return 5
                return last_error()

            monkeypatch.setattr(gates, '_lock_file', raw_lock)
            monkeypatch.setattr(gates.ctypes, 'get_last_error', raw_error)
        monkeypatch.setattr(reader, '_confirm_gate_admission', after_proof)
        try:
            assert not policy.confirm_gate_notices()
            assert policy.has_gate_notices()
        finally:
            advance.set()
            if worker.ident is not None:
                worker.join(5)
        assert len(failures) == 1 and isinstance(failures[0], gates.ManagedNetworkGateStorageError)


@pytest.mark.parametrize('entry', ('owner', 'selected-reader', 'write-selection', 'refresh-selection', 'history'))
def test_refusals_before_the_gate_lock_advance_the_physical_generation(tmp_path, monkeypatch, entry):
    from pixivvault_nicegui.cookie_expiry_refresh import CookieExpiryCoordinator

    local, shared, credential = linked_owners(tmp_path)
    destination = local.gate if 'selected' in entry or 'selection' in entry else shared.gate
    physical = gate_file(destination)
    before = refusal_generations().get(physical, 0)

    def deny_owner(_):
        raise windows_io_error(5)

    lstat = Path.lstat

    def deny_reference(path, *args, **kwargs):
        if path == credential:
            raise windows_io_error(5)
        return lstat(path, *args, **kwargs)

    with guarded_reader(local, shared, credential, unexpected) as reader:
        if entry == 'owner':
            monkeypatch.setattr(shared_scopes, '_readonly_owner_json', deny_owner)
            call = shared._check_owner
        elif entry == 'history':
            root = tmp_path / 'pending-history'
            root.mkdir()
            (root / (local.gate.path.name + '-journal')).touch()
            call = lambda: attach_selected_histories(shared, (('native', root),))
        else:
            monkeypatch.setattr(Path, 'lstat', deny_reference)
            if entry == 'selected-reader':
                call = lambda: local.create_reader(credential, transport=unexpected)
            elif entry == 'write-selection':
                call = lambda: write_admission(reader, local, shared).__enter__()
            else:
                call = lambda: CookieExpiryCoordinator._commit_admission(None, reader).__enter__()
        with pytest.raises(gates.ManagedNetworkGateStorageError):
            call()
    assert refusal_generations().get(physical, 0) > before


@pytest.mark.parametrize('order', ('observer-first', 'confirmation-first', 'finish-inside-probe', 'nested'))
def test_confirmation_activity_has_no_observation_notice_in_either_order(tmp_path, monkeypatch, order):
    local, shared, credential = linked_owners(tmp_path)
    policy = retry_policy([0.0])
    sampled, created, done = Event(), Event(), Event()
    with guarded_reader(local, shared, credential, unexpected) as reader, \
         closing(reads.PublicImageReadClient()) as public:
        reader.set_retry_policy(policy)
        pin_shared(reader, shared, monkeypatch)
        observer = retry_policy([0.0], check_gate=lambda: observe.check_request_notice_gates(shared))
        bind_public_cdn_shared_stop(public, shared)
        public.set_retry_policy(observer)
        database_fingerprint = observe._database_fingerprint
        begin = shared.gate._begin_inflight
        results = []
        errors = []

        def poll():
            try:
                observer.recheck_gate_notices()
            except BaseException as error:
                errors.append(error)
            finally:
                done.set()

        worker = Thread(target=poll)
        entered = False

        def after_open(path, *args, **kwargs):
            nonlocal entered
            result = database_fingerprint(path, *args, **kwargs)
            if current_thread() is worker and not entered:
                entered = True
                sampled.set()
                assert created.wait(5)
            return result

        def begin_with_poll():
            identity = begin()
            if order == 'observer-first':
                created.set()
                assert done.wait(5)
            elif order == 'confirmation-first':
                poll()
            elif order == 'nested':
                poll()
            return identity

        if order in ('observer-first', 'finish-inside-probe'):
            monkeypatch.setattr(observe, '_database_fingerprint', after_open)
        monkeypatch.setattr(shared.gate, '_begin_inflight', begin_with_poll)
        try:
            if order in ('observer-first', 'finish-inside-probe'):
                worker.start()
                assert sampled.wait(5)
            if order == 'nested':
                # The Native provider can enclose this transport's admission.
                with closing(NativeProfileStore(local.root)) as store:
                    binding = NativeProfileBinding(store)
                    binding.managed_network_stop_scope, binding.shared_network_stop_scope = local, shared
                    binding.auth_stop_required = binding.auth_shared_stop_required = True
                    with closing(binding):
                        reader.set_retry_policy(binding.auth_retry_policy)
                        pin_shared(reader, shared, monkeypatch)
                        results.append(binding.auth_retry_policy.confirm_gate_notices())
            else:
                results.append(policy.confirm_gate_notices())
        finally:
            created.set()
            if worker.ident is not None:
                worker.join(5)
        assert not errors and results == [True]
        assert not shared.gate.inflight_path.exists()
        assert not observer.has_gate_notices()
        observer.recheck_gate_notices()
        assert not observer.has_gate_notices()


def test_confirmation_overlap_retains_other_targets_inconsistency(tmp_path, monkeypatch):
    local, shared, credential = linked_owners(tmp_path)
    policy = retry_policy([0.0])
    observer = retry_policy([0.0], check_gate=lambda: observe.check_request_notice_gates(local))
    targets = observe._targets
    with shared.create_reader(credential, transport=unexpected) as reader, \
         closing(reads.PublicImageReadClient()) as public:
        reader.set_retry_policy(policy)
        pin_shared(reader, shared, monkeypatch)
        bind_public_cdn_shared_stop(public, shared)
        public.set_retry_policy(observer)

        def changing_targets(scope, authority):
            for target in targets(scope, authority):
                if authority == 'local' and target.read_owner is not None:
                    calls = iter((b'old-reference', b'new-reference'))
                    yield replace(target, read_owner=lambda: next(calls))
                else:
                    yield target

        begin = shared.gate._begin_inflight

        def begin_with_poll():
            identity = begin()
            observer.recheck_gate_notices()
            return identity

        monkeypatch.setattr(observe, '_targets', changing_targets)
        monkeypatch.setattr(shared.gate, '_begin_inflight', begin_with_poll)
        assert policy.confirm_gate_notices()
        assert any(row['stop_cause'] == 'gate_busy' and row['gate_owners'] == ('local',)
                   for row in observer.progress_snapshot())
        assert not any('shared' in row['gate_owners'] for row in observer.progress_snapshot())


@pytest.mark.parametrize('failure', ('lock', 'close', 'remove'))
def test_all_live_candidates_must_pass_even_when_a_later_sibling_recovers(tmp_path, monkeypatch, failure):
    local, shared, credential = linked_owners(tmp_path)
    policy = retry_policy([0.0])
    with guarded_reader(local, shared, credential, unexpected) as first, \
         guarded_reader(local, shared, credential, unexpected) as second:
        first.set_retry_policy(policy)
        second.set_retry_policy(policy)
        pin_shared(first, shared, monkeypatch)
        first_check, second_check = first._confirm_gate_admission, second._confirm_gate_admission
        opener, unlink = gates._open_existing_stop_lock, Path.unlink
        completed = []

        def deny_file(path):
            if path == shared.gate.lock_path:
                raise windows_io_error(5)
            return opener(path)

        def fail_after_remove(path, *args, **kwargs):
            unlink(path, *args, **kwargs)
            if path == shared.gate.inflight_path:
                raise windows_io_error(5)

        def fail_first():
            with monkeypatch.context() as fault:
                if failure == 'lock':
                    fault.setattr(gates, '_open_existing_stop_lock', deny_file)
                elif failure == 'close':
                    database_close_failure(fault, shared.gate.path, 'writable')
                else:
                    fault.setattr(Path, 'unlink', fail_after_remove)
                return first_check()

        def pass_second():
            proof = second_check()
            completed.append(True)
            return proof

        monkeypatch.setattr(first, '_confirm_gate_admission', fail_first)
        monkeypatch.setattr(second, '_confirm_gate_admission', pass_second)
        assert not policy.confirm_gate_notices()
        assert completed == [True] and policy.has_gate_notices()


def test_unconfigured_native_owner_observation_deduplicates_the_first_denial(tmp_path):
    local, _, credential = linked_owners(tmp_path)
    with closing(NativeProfileBinding(NativeProfileStore(local.root))) as binding:
        binding.auth_stop_required = binding.auth_shared_stop_required = True
        result, _ = binding._verify_auth_result(credential, Event())
        assert result.code == 'local_unavailable'
        assert len(binding.auth_retry_policy.progress_snapshot()) == 1
        binding.auth_retry_policy.recheck_gate_notices()
        rows = binding.auth_retry_policy.progress_snapshot()
        assert sorted(row['gate_owners'] for row in rows) == [('local',), ('shared',)]



def test_discarded_observation_does_not_record_a_shared_owner_denial(tmp_path, monkeypatch):
    local, shared, credential = linked_owners(tmp_path)
    policy = retry_policy([0.0], check_gate=lambda: observe.check_request_notice_gates(shared))
    with guarded_reader(local, shared, credential, unexpected) as reader:
        reader.set_retry_policy(policy)
        pin_shared(reader, shared, monkeypatch)
        begin, read_owner = shared.gate._begin_inflight, shared_scopes._readonly_owner_json
        refused = []

        def deny_once(path):
            if not refused:
                refused.append(True)
                raise windows_io_error(5)
            return read_owner(path)

        def poll():
            identity = begin()
            with monkeypatch.context() as fault:
                fault.setattr(shared_scopes, '_readonly_owner_json', deny_once)
                policy.recheck_gate_notices()
            return identity

        monkeypatch.setattr(shared.gate, '_begin_inflight', poll)
        assert policy.confirm_gate_notices()
        assert refused and not policy.has_gate_notices()


def test_confirmation_is_registered_before_the_first_lock_returns(tmp_path, monkeypatch):
    _, shared, credential = linked_owners(tmp_path)
    policy = retry_policy([0.0])
    observer = retry_policy([0.0], check_gate=lambda: observe.check_request_notice_gates(shared))
    with shared.create_reader(credential, transport=unexpected) as reader, \
         closing(reads.PublicImageReadClient()) as public:
        reader.set_retry_policy(policy)
        pin_shared(reader, shared, monkeypatch)
        bind_public_cdn_shared_stop(public, shared)
        public.set_retry_policy(observer)
        set_lock = gates._set_stop_byte_lock
        checked = []

        def poll_before_return(stream, *, lock):
            set_lock(stream, lock=lock)
            if lock:
                assert not shared.gate.inflight_path.exists()
                observer.recheck_gate_notices()
                checked.append(True)

        monkeypatch.setattr(gates, '_set_stop_byte_lock', poll_before_return)
        assert policy.confirm_gate_notices()
        assert checked == [True]
        assert not observer.has_gate_notices()
