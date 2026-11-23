"""R7 request notices through the owned TLS reader and the header status view."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest
from nicegui import Client, ui

from d6_api_tls_fixture import BODY
from pixivvault_nicegui.managed_network_stop_gate import (
    ManagedNetworkGateBusy, ManagedNetworkStopped,
)
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected, ReadDeadlineExceeded
from pixivvault_nicegui.request_policy import RequestRetryPolicy
from pixivvault_nicegui.ui.request_status_view import RequestStatusView
from stage5_generated_shared_owner import generated_shared_scope
from test_d6_api_https import fresh_context, get, wire
from test_d6_api_r3 import dual_reader
from test_stage5_r1_shared_stop_transport import generated_owners


def policy_with_clock(now, **kwargs):
    return RequestRetryPolicy(
        lambda: SimpleNamespace(retry_limit=3, rate_limit_wait_seconds=3),
        clock=lambda: now[0], **kwargs,
    )


@pytest.mark.parametrize("status", [401, 403, 429])
def test_normal_get_records_durable_gate_stop_without_another_dispatch(tmp_path, monkeypatch, wire, status):
    with dual_reader(tmp_path, monkeypatch, policy=True) as (client, local, shared, admissions, waits):
        assert get(client) == BODY
        shared.trip(status)
        with pytest.raises(ManagedNetworkStopped) as error:
            get(client)
        assert error.value.status_code == status
        assert len(wire.requests) == len(admissions) == 1
        assert not waits
        assert local.snapshot().status_code == shared.snapshot().status_code == status
        record, = client._retry_policy.progress_snapshot()
        assert (record["phase"], record["status"], record.get("stop_cause"), record["attempt"]) == (
            "stopped", status, "durable_gate", 1,
        )


def test_cancelled_notice_expires_at_ten_minutes():
    now = [0.0]
    policy = policy_with_clock(now)

    def cancel():
        raise ReadDeadlineExceeded()

    with pytest.raises(ReadDeadlineExceeded):
        policy.execute(cancel, deadline=1)
    now[0] = 599
    assert policy.progress_snapshot()[0]["phase"] == "cancelled"
    now[0] = 600
    assert policy.progress_snapshot() == ()


def test_notice_clears_on_success_not_at_the_start_or_on_404(tmp_path, monkeypatch, wire):
    with dual_reader(tmp_path, monkeypatch, policy=True) as (client, _, _, admissions, waits):
        policy = client._retry_policy

        def cancel():
            raise ReadDeadlineExceeded()

        with pytest.raises(ReadDeadlineExceeded):
            policy.execute(cancel, deadline=policy.clock() + 1)

        saw_cancellation = []

        def missing():
            saw_cancellation.append(any(row["phase"] == "cancelled" for row in policy.progress_snapshot()))
            return 404, "identity", b""

        wire.on_request = missing
        with pytest.raises(HTTPStatusRejected) as error:
            get(client)
        assert error.value.status_code == 404
        assert saw_cancellation == [True]
        record, = policy.progress_snapshot()
        assert record["phase"] == "cancelled" and record["status"] is None

        wire.on_request = lambda: (200, "identity", BODY)
        assert get(client) == BODY
        assert policy.progress_snapshot() == ()
        assert len(wire.requests) == len(admissions) == 2
        assert not waits


@pytest.fixture
def header():
    client = Client(ui.page("/_fix_notice_20261122"))
    try:
        yield client
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_busy_notice_clears_when_shared_gate_opens_and_can_recur(header, tmp_path):
    from pixivvault_nicegui.request_policy import check_request_notice_gates

    scope = generated_shared_scope(tmp_path)
    policy = policy_with_clock([0.0], check_gate=lambda: check_request_notice_gates(scope))
    with header:
        view = RequestStatusView(policy)

    def hold_gate(ready, release):
        with scope.gate.request():
            ready.set()
            assert release.wait(5)

    try:
        with ThreadPoolExecutor(max_workers=1) as worker:
            for _ in range(2):
                ready, release = Event(), Event()
                held = worker.submit(hold_gate, ready, release)
                try:
                    assert ready.wait(3)
                    with pytest.raises(ManagedNetworkGateBusy):
                        policy.execute(scope.require_allowed, deadline=1)
                    view.refresh()
                    assert view.label.visible and "別の通信" in view.label.text
                    await view.recheck_gate()
                    assert view.label.visible
                finally:
                    release.set()
                    held.result(timeout=3)
                await view.recheck_gate()
                assert not view.label.visible
    finally:
        view.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429])
async def test_durable_stop_survives_timeout_and_user_can_dismiss_only_this_notice(
        header, tmp_path, status):
    from pixivvault_nicegui.request_policy import check_request_notice_gates

    now = [0.0]
    scope = generated_shared_scope(tmp_path)
    observed = [scope]
    policy = policy_with_clock(now, check_gate=lambda: check_request_notice_gates(observed[0]))
    with header:
        view = RequestStatusView(policy)
    try:
        scope.gate.trip(status)
        for _ in range(5):
            with pytest.raises(ManagedNetworkStopped):
                policy.execute(scope.require_allowed, deadline=1)
        assert len(policy.progress_snapshot()) == 1
        assert policy.execute(lambda: BODY, deadline=1) == BODY
        now[0] = 1200
        await view.recheck_gate()
        assert view.label.visible and "通信を止めています" in view.label.text
        assert "STOP" not in view.label.text
        assert view.dismiss_button.visible
        view.dismiss()
        await view.recheck_gate()
        assert view.label.visible and "確認済み" in view.label.text
        assert not view.dismiss_button.visible
        assert scope.gate.snapshot().blocked
        with pytest.raises(ManagedNetworkStopped):
            policy.execute(scope.require_allowed, deadline=1201)
        view.refresh()
        assert view.label.visible and view.dismiss_button.visible

        # Observe a clean owner without editing the durable STOP history.
        clean_root = tmp_path / "clean"
        clean_root.mkdir()
        observed[0] = generated_shared_scope(clean_root)
        assert not observed[0].gate.snapshot().blocked
        await view.recheck_gate()
        assert not view.label.visible and policy.progress_snapshot() == ()
        assert scope.gate.snapshot().blocked
    finally:
        view.close()


def test_notice_observation_neither_acquires_the_gate_nor_changes_its_stop_cache(tmp_path, monkeypatch):
    from pixivvault_nicegui.request_policy import check_request_notice_gates

    scope = generated_shared_scope(tmp_path)
    other = generated_shared_scope(tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail("a notice must not acquire the admission lock")

    monkeypatch.setattr(scope.gate, "_exclusive", forbidden)
    assert [state.state for state in check_request_notice_gates(scope)] == ["open"]
    other.gate.trip(429)
    state, = check_request_notice_gates(scope)
    assert (state.state, state.status) == ("stopped", 429)
    assert scope.gate._observed_stop_status is None


@pytest.mark.parametrize("changed", ["local", "shared"])
def test_persistent_stop_during_a_dual_gate_poll_is_not_reported_open(tmp_path, monkeypatch, changed):
    from pixivvault_nicegui import managed_network_stop_history_readonly as reader
    from pixivvault_nicegui.request_policy import check_request_notice_gates

    root = tmp_path / "profile"
    root.mkdir()
    local, _, _, _ = generated_owners(root)
    shared = generated_shared_scope(tmp_path)
    local.shared_owner = shared
    read_gate = reader._read_gate_immutable
    read_shared = []
    changed_gate = local.gate if changed == "local" else shared.gate

    def trip_after_shared_read(path):
        result = read_gate(path)
        if path == shared.gate.path and not read_shared:
            read_shared.append(True)
            changed_gate.trip(429)
        return result

    monkeypatch.setattr(reader, "_read_gate_immutable", trip_after_shared_read)
    observations = {state.authority: state for state in check_request_notice_gates(local)}
    assert observations[changed].state in ("unverified", "stopped")
    assert read_shared == [True]
    observations = {state.authority: state for state in check_request_notice_gates(local)}
    assert (observations[changed].state, observations[changed].status) == ("stopped", 429)


def test_many_active_operations_do_not_drop_stop_classification():
    policy = policy_with_clock([0.0])

    def run(depth):
        if depth > 0:
            policy.execute(lambda: run(depth - 1), deadline=1)
        if depth == 33:
            raise ManagedNetworkStopped(429)

    with pytest.raises(ManagedNetworkStopped):
        policy.execute(lambda: run(33), deadline=1)
    record, = policy.progress_snapshot()
    assert record["phase"] == "stopped" and record["status"] == 429


def test_clean_gate_rows_cannot_prove_a_revoked_native_owner_open(tmp_path):
    from pixivvault_nicegui.managed_network_stop_profile_scope import _SCOPE_MARKER
    from pixivvault_nicegui.request_policy import check_request_notice_gates

    scope, _, _, _ = generated_owners(tmp_path)
    assert [state.state for state in check_request_notice_gates(scope)] == ["open"]
    (scope.root / _SCOPE_MARKER).write_bytes(b"invalid synthetic owner")
    assert [state.state for state in check_request_notice_gates(scope)] == ["unverified"]


@pytest.mark.asyncio
@pytest.mark.parametrize("abandoned", [False, True], ids=["durable", "uncertain"])
async def test_poll_does_not_reopen_a_dismissed_stop_from_an_older_busy_notice(header, tmp_path, abandoned):
    from pixivvault_nicegui.managed_network_stop_shared_scope import SharedGetOutcomeUncertain
    from pixivvault_nicegui.request_policy import check_request_notice_gates

    scope = generated_shared_scope(tmp_path)
    policy = policy_with_clock([0.0], check_gate=lambda: check_request_notice_gates(scope))
    with header:
        view = RequestStatusView(policy)

    def busy():
        raise ManagedNetworkGateBusy()

    try:
        with pytest.raises(ManagedNetworkGateBusy):
            policy.execute(busy, deadline=1)
        if abandoned:
            with pytest.raises(RuntimeError):
                with scope.gate.request():
                    raise RuntimeError("synthetic interruption")
        else:
            scope.gate.trip(401)
        stopped_error = SharedGetOutcomeUncertain if abandoned else ManagedNetworkStopped
        with pytest.raises(stopped_error):
            policy.execute(scope.require_allowed, deadline=1)
        view.refresh()
        assert view.dismiss_button.visible
        view.dismiss()
        await view.recheck_gate()
        assert view.label.visible and "確認済み" in view.label.text
        assert not view.dismiss_button.visible
        with pytest.raises(stopped_error):
            scope.require_allowed()
    finally:
        view.close()


def test_abandoned_inflight_intent_does_not_expire_as_ordinary_contention(tmp_path):
    from pixivvault_nicegui.managed_network_stop_shared_scope import SharedGetOutcomeUncertain
    from pixivvault_nicegui.request_policy import check_request_notice_gates

    scope = generated_shared_scope(tmp_path)
    now = [0.0]
    policy = policy_with_clock(now, check_gate=lambda: check_request_notice_gates(scope))

    def busy():
        raise ManagedNetworkGateBusy()

    with pytest.raises(ManagedNetworkGateBusy):
        policy.execute(busy, deadline=1)
    with pytest.raises(RuntimeError):
        with scope.gate.request():
            raise RuntimeError("synthetic interruption")
    now[0] = 1200
    policy.recheck_gate_notices()
    record, = policy.progress_snapshot()
    assert record["phase"] == "stopped" and record["stop_cause"] == "gate_unverified"
    with pytest.raises(SharedGetOutcomeUncertain):
        scope.require_allowed()
