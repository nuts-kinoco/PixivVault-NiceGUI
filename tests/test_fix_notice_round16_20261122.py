from contextlib import closing
from threading import Event, Thread

import pytest

from pixivvault_nicegui import managed_network_stop_gate as gates, pixiv_read_client as reads
from pixivvault_nicegui import request_notice_gates as observe
from pixivvault_nicegui.managed_network_stop_public_cdn import bind_public_cdn_shared_stop
from pixivvault_nicegui.managed_network_stop_profile_scope import _require_selected_cookie
from pixivvault_nicegui.owned_https import HTTPSOwner
from pixivvault_nicegui.request_policy import request_gate_preflight
from test_fix_notice_round6_20261122 import windows_io_error
from test_fix_notice_round7_20261122 import linked_owners
from test_fix_notice_round13_20261122 import pin_shared, unexpected
from test_fix_notice_round14_20261122 import native_binding
from test_fix_notice_sweep_20261122 import retry_policy
from test_stage5_r1_shared_stop_transport import IMAGE


@pytest.mark.parametrize('saved', (False, True), ids=('not-configured', 'missing-file'))
def test_native_can_clear_an_observed_gate_fault_without_a_saved_reader(tmp_path, monkeypatch, saved):
    local, shared, selected = linked_owners(tmp_path)
    with closing(native_binding(local, shared)) as binding:
        if saved:
            selected.unlink()
        else:
            binding.store.update(cookie_file=None, expected_revision=binding.store.snapshot().revision)
        policy = binding.auth_retry_policy
        # Activate the normal background watch through a first ownership refusal.
        old_gates = binding._notice_admission_gates()
        binding.shared_network_stop_scope = None
        unavailable, _ = binding._verify_auth_result(selected, Event())
        assert not unavailable.ok
        incomplete_gates = binding._notice_admission_gates()
        assert not policy.can_confirm_gate_notices()
        binding.shared_network_stop_scope = shared
        policy.replace_notice_gates(incomplete_gates, old_gates)
        read = observe._Target.read

        def deny_local_read(target, fingerprint):
            if target.authority == 'local':
                raise windows_io_error(5)
            return read(target, fingerprint)

        with monkeypatch.context() as fault:
            fault.setattr(observe._Target, 'read', deny_local_read)
            policy.recheck_gate_notices()
        assert {tuple(row['gate_owners']) for row in policy.progress_snapshot()} == {
            ('local',), ('shared',),
        }
        assert policy.can_confirm_gate_notices()
        monkeypatch.setattr(reads._OwnedReadTransport, '__call__', unexpected)
        assert policy.confirm_gate_notices()
        assert not policy.has_gate_notices()


@pytest.mark.parametrize('local_stop', (None, 401), ids=('storage', 'durable-stop'))
def test_same_policy_keeps_the_other_gate_result_from_this_poll(tmp_path, monkeypatch, local_stop):
    local, shared, selected = linked_owners(tmp_path)
    sample = []
    policy = retry_policy([0.0], check_gate=lambda: poll())

    def poll():
        assert not sample, 'a second poll has no evidence of the injected transient fault'
        snapshot = observe.check_request_notice_gates(local, shared)
        sample.append(snapshot)
        assert policy.confirm_gate_notices()
        return snapshot

    read = observe._Target.read

    def diagnose(target, fingerprint):
        if target.authority == 'local' and local_stop is None:
            raise windows_io_error(5)
        return read(target, fingerprint)

    with closing(reads.PublicImageReadClient()) as public:
        bind_public_cdn_shared_stop(public, shared)
        public.set_retry_policy(policy)
        pin_shared(public, shared, monkeypatch)
        monkeypatch.setattr(observe._Target, 'read', diagnose)
        if local_stop is not None:
            local.gate.trip(local_stop)
        policy.recheck_gate_notices()
        rows = policy.progress_snapshot()
        assert len(rows) == 1 and rows[0]['gate_owners'] == ('local',)
        assert rows[0]['status'] == local_stop
        assert rows[0]['stop_cause'] == ('gate_unverified' if local_stop is None else 'durable_gate')


def test_shared_stop_survives_its_own_confirmation_overlap(tmp_path, monkeypatch):
    _, shared, _ = linked_owners(tmp_path)
    policy = retry_policy([0.0], check_gate=lambda: observe.check_request_notice_gates(shared, _step=interrupt))
    overlapped = []

    def interrupt(phase, authority, side):
        if (phase, authority, side) == ('read', 'shared', 'after') and not overlapped:
            overlapped.append(True)
            with pytest.raises(gates.ManagedNetworkStopped):
                public._confirm_gate_admission()

    with closing(reads.PublicImageReadClient()) as public:
        bind_public_cdn_shared_stop(public, shared)
        public.set_retry_policy(policy)
        shared.gate.trip(403)
        monkeypatch.setattr(reads._OwnedReadTransport, '__call__', unexpected)
        policy.recheck_gate_notices()
        assert overlapped
        rows = policy.progress_snapshot()
        assert [(row['stop_cause'], row['status'], row['gate_owners']) for row in rows] == [
            ('durable_gate', 403, ('shared',)),
        ]


@pytest.mark.parametrize('phase', ('pool-result', 'convert'))
def test_cdn_refusal_fences_confirmation_before_exception_publication(tmp_path, monkeypatch, phase):
    _, shared, _ = linked_owners(tmp_path)
    policy = retry_policy([0.0])
    caught, release = Event(), Event()
    errors = []

    with closing(reads.PublicImageReadClient()) as confirming, closing(reads.PublicImageReadClient()) as public:
        bind_public_cdn_shared_stop(confirming, shared)
        bind_public_cdn_shared_stop(public, shared)
        confirming.set_retry_policy(policy)
        public.set_retry_policy(retry_policy([0.0]))
        pin_shared(confirming, shared, monkeypatch)
        public._owned_transport._poisoned = True
        prove = confirming._confirm_gate_admission
        convert = gates._gate_cleanup_failure
        verify = reads.PublicImageReadClient.shutdown_unverified.fget

        def pause():
            caught.set()
            assert release.wait(8)

        def inspect(reader):
            status = verify(reader)
            if reader is public and status and phase == 'pool-result':
                pause()
            return status

        def convert_after_detection(*args, **kwargs):
            if phase == 'convert':
                pause()
            return convert(*args, **kwargs)

        def get():
            try:
                public.get_image_bytes(IMAGE)
            except BaseException as error:
                errors.append(error)

        worker = Thread(target=get)
        monkeypatch.setattr(reads.PublicImageReadClient, 'shutdown_unverified', property(inspect))
        monkeypatch.setattr(gates, '_gate_cleanup_failure', convert_after_detection)
        monkeypatch.setattr(reads._OwnedReadTransport, '__call__', unexpected)

        def proof_with_parallel_denial():
            result = prove()
            worker.start()
            assert caught.wait(8)
            return result

        monkeypatch.setattr(confirming, '_confirm_gate_admission', proof_with_parallel_denial)
        try:
            assert not policy.confirm_gate_notices()
            assert policy.has_gate_notices()
        finally:
            release.set()
            if worker.ident is not None:
                worker.join(8)
        assert len(errors) == 1 and isinstance(errors[0], reads.ClientClosed)
        assert public._retry_policy.has_gate_notices()


def test_removed_native_selection_is_not_a_confirmation_candidate(tmp_path):
    local, shared, selected = linked_owners(tmp_path)
    with closing(native_binding(local, shared)) as binding:
        policy = binding.auth_retry_policy
        owners = binding._notice_admission_gates()
        binding.store.update(cookie_file=None, expected_revision=binding.store.snapshot().revision)
        with pytest.raises(gates.ManagedNetworkGateStorageError), request_gate_preflight(
                policy, owners, checks_selection=True):
            _require_selected_cookie(local.root, selected, local.profile_identity, gate=local.gate)
        assert policy.has_gate_notices()
        assert not policy.can_confirm_gate_notices()
        assert not policy.confirm_gate_notices()
        assert policy.has_gate_notices()


def test_cdn_idle_cleanup_refusal_fences_before_the_next_get(tmp_path, monkeypatch):
    _, shared, _ = linked_owners(tmp_path)
    policy = retry_policy([0.0])
    caught, release = Event(), Event()

    with closing(reads.PublicImageReadClient()) as confirming, closing(reads.PublicImageReadClient()) as public:
        bind_public_cdn_shared_stop(confirming, shared)
        bind_public_cdn_shared_stop(public, shared)
        confirming.set_retry_policy(policy)
        pin_shared(confirming, shared, monkeypatch)
        idle = HTTPSOwner(1, 0, 'synthetic.invalid', 443)
        public._owned_transport._idle = idle
        prove = confirming._confirm_gate_admission
        convert = gates._gate_cleanup_failure

        def reject_cleanup():
            raise windows_io_error(5)

        def convert_after_detection(*args, **kwargs):
            caught.set()
            assert release.wait(8)
            return convert(*args, **kwargs)

        worker = Thread(target=public._owned_transport._expire_idle)
        monkeypatch.setattr(idle, 'cleanup', reject_cleanup)
        monkeypatch.setattr(gates, '_gate_cleanup_failure', convert_after_detection)

        def proof_with_concurrent_cleanup():
            result = prove()
            worker.start()
            assert caught.wait(8)
            return result

        monkeypatch.setattr(confirming, '_confirm_gate_admission', proof_with_concurrent_cleanup)
        try:
            assert not policy.confirm_gate_notices()
            assert policy.has_gate_notices()
        finally:
            release.set()
            if worker.ident is not None:
                worker.join(8)
        assert public.shutdown_unverified
