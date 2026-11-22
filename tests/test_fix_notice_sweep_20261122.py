"""R3 notices, using generated gate owners and no socket transport."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, RLock
from types import SimpleNamespace

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.managed_network_stop_gate import (
    ManagedNetworkGateBusy, ManagedNetworkGateStorageError, ManagedNetworkStopped,
)
from pixivvault_nicegui.managed_network_stop_shared_scope import SharedGetOutcomeUncertain
from pixivvault_nicegui.managed_network_stop_shared_transport import create_shared_profile_reader
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected, ReadDeadlineExceeded
from pixivvault_nicegui.request_policy import (
    GateNoticeState, RequestRetryPolicy, check_request_notice_gates, is_pinned_notice,
)
from pixivvault_nicegui.ui.request_status_view import RequestStatusView
from stage5_generated_shared_owner import generated_shared_scope
from test_stage5_r1_shared_stop_transport import IMAGE, generated_owners


def retry_policy(now, **options):
    return RequestRetryPolicy(
        lambda: SimpleNamespace(retry_limit=3, rate_limit_wait_seconds=3),
        clock=lambda: now[0], **options,
    )


@pytest.fixture
def header_client():
    client = Client(ui.page("/_fix_notice_sweep_20261122"))
    try:
        yield client
    finally:
        client.delete()


def reject(policy, error):
    def operation():
        raise error

    with pytest.raises(type(error)) as caught:
        policy.execute(operation, deadline=1000)
    assert caught.value is error


@pytest.mark.asyncio
async def test_shared_write_failure_remains_after_local_401_ack_and_retry(
        tmp_path, monkeypatch, header_client):
    local, _, _, credential = generated_owners(tmp_path)
    shared = generated_shared_scope(tmp_path)
    local.shared_owner = shared
    sent = []

    def transport(*args):
        sent.append("synthetic")
        raise HTTPStatusRejected(401)

    def fail_commit(*args):
        raise ManagedNetworkGateStorageError()

    now = [0.0]
    policy = retry_policy(now, check_gate=lambda: check_request_notice_gates(local))
    with header_client:
        view = RequestStatusView(policy)
    try:
        with create_shared_profile_reader(
            local, credential, shared.gate, transport=transport,
            shared_owner_check=shared._check_owner,
        ) as reader:
            reader.set_retry_policy(policy)
            monkeypatch.setattr(shared.gate, "_trip_locked", fail_commit)
            with pytest.raises(ManagedNetworkGateStorageError):
                reader.get_image_bytes(IMAGE)
            assert sent == ["synthetic"]
            assert local.gate.snapshot().status_code == 401
            assert shared.gate.inflight_path.is_file()
            await view.recheck_gate()
            view.dismiss()

            with pytest.raises(ManagedNetworkGateStorageError):
                reader.get_image_bytes(IMAGE)
            renewed, = [item for item in policy.progress_snapshot()
                        if item.get("gate_problem") == "storage" and not item.get("gate_owners")]
            await view.recheck_gate()
            records = policy.progress_snapshot()
            assert any(item["notice_id"] == renewed["notice_id"] for item in records)
            assert any(item.get("stop_cause") == "durable_gate" and item["status"] == 401
                       for item in records)
            assert view.label.visible and "通信の状態を確認できない" in view.label.text
            assert view.dismiss_button.visible
            assert shared.gate.inflight_path.is_file()
            assert sent == ["synthetic"]
    finally:
        view.close()


def test_four_429_stops_do_not_hide_uncertainty_or_close_unshown_notices(header_client):
    now = [0.0]
    enter_wait = [3]
    attempts = []

    def operation():
        attempts.append("synthetic")
        raise HTTPStatusRejected(429)

    def sleep(seconds):
        if enter_wait[0]:
            enter_wait[0] -= 1
            with pytest.raises(HTTPStatusRejected):
                policy.execute(operation, deadline=1000)
        now[0] += seconds

    policy = retry_policy(now, sleep=sleep)
    reject(policy, SharedGetOutcomeUncertain())
    with pytest.raises(HTTPStatusRejected):
        policy.execute(operation, deadline=1000)
    assert len(attempts) == 6
    assert [item.get("stop_cause") for item in policy.progress_snapshot()].count("shared_limit") == 3
    with header_client:
        view = RequestStatusView(policy)
    try:
        first_display = view.label.text
        view.dismiss()
        assert "確認できない" in first_display
        assert "確認済み" in view.label.text
        # Two of the four 429 records were obscured before the click.
        assert view.label.text.count("今回のHTTP 429") == 2
    finally:
        view.close()


PINNED_ERRORS = (
    pytest.param(ManagedNetworkStopped, (401,), "ログイン情報を確認", id="durable"),
    pytest.param(ManagedNetworkGateStorageError, (), "通信の状態を確認できない", id="storage"),
    pytest.param(SharedGetOutcomeUncertain, (), "前の通信結果を確認できない", id="uncertain"),
)


@pytest.mark.parametrize("error,args,notice_text", PINNED_ERRORS)
@pytest.mark.parametrize(
    "phase,status,cause",
    [("cancelled", None, None), ("cancelled", None, "gate_busy"), ("requesting", None, None),
     ("waiting", 429, None), ("stopped", 503, None), ("failed", 403, None),
     ("stopped", 429, "own_limit"), ("stopped", 429, "shared_limit")],
)
def test_each_pinned_kind_has_priority_over_each_other_display_kind(
        header_client, error, args, notice_text, phase, status, cause):
    policy = retry_policy([0.0])
    reject(policy, error(*args))
    pin, = policy.progress_snapshot()
    competitors = [
        dict(phase=phase, status=status, stop_cause=cause, notice_id=index,
             attempt=2, limit=3, remaining_seconds=3, wait_seconds=3)
        for index in range(2, 6)
    ]
    rendered = SimpleNamespace(progress_snapshot=lambda: [pin, *competitors])
    with header_client:
        view = RequestStatusView(rendered)
    try:
        assert notice_text in view.label.text
        assert len(view.label.text.splitlines()) == 3
        assert view.more_button.visible and view.more_button.text == "ほか 2 件"
        assert not view.details.visible
        view.dismiss()
        assert "確認済み" in view.label.text
        assert is_pinned_notice(pin) and policy.progress_snapshot() == (pin,)
    finally:
        view.close()


@pytest.mark.parametrize("error,args,_notice_text", PINNED_ERRORS)
def test_each_pin_survives_the_cap_success_and_deadline_then_clears_on_open(
        error, args, _notice_text):
    now = [0.0]
    observed = [GateNoticeState("local", "stopped", 401), GateNoticeState("shared", "uncertain")]
    policy = retry_policy(now, check_gate=lambda: tuple(observed))
    reject(policy, error(*args))
    pin, = policy.progress_snapshot()
    for _ in range(40):
        reject(policy, ReadDeadlineExceeded())
    records = policy.progress_snapshot()
    assert len(records) == 33 and records[0] == pin
    now[0] = 601
    assert policy.execute(lambda: "synthetic success", deadline=650) == "synthetic success"
    assert policy.progress_snapshot() == (pin,)
    policy.recheck_gate_notices()
    assert any(record["notice_id"] == pin["notice_id"] for record in policy.progress_snapshot())
    observed[:] = [GateNoticeState("local", "open"), GateNoticeState("shared", "open")]
    policy.recheck_gate_notices()
    assert not policy.progress_snapshot() and not policy.has_gate_notices()


@pytest.mark.parametrize("shared_issue", ["uncertain", "unverified"])
@pytest.mark.parametrize("recovered", ["local", "shared"])
def test_each_authority_is_rechecked_independently(shared_issue, recovered):
    observed = [GateNoticeState("local", "stopped", 401), GateNoticeState("shared", shared_issue)]
    policy = retry_policy([0.0], check_gate=lambda: tuple(observed))
    reject(policy, ManagedNetworkStopped(401))
    policy.recheck_gate_notices()
    records = {record["stop_cause"]: record for record in policy.progress_snapshot()}
    assert set(records) == {"durable_gate", "gate_unverified"}

    index = 0 if recovered == "local" else 1
    observed[index] = GateNoticeState(recovered, "open")
    policy.recheck_gate_notices()
    remaining, = policy.progress_snapshot()
    untouched = "gate_unverified" if recovered == "local" else "durable_gate"
    assert remaining["notice_id"] == records[untouched]["notice_id"]


def test_hidden_pin_stays_unacknowledged_until_its_details_are_visible(header_client):
    policy = retry_policy([0.0])
    for status in (401, 403, 429):
        reject(policy, ManagedNetworkStopped(status))
    reject(policy, SharedGetOutcomeUncertain())
    with header_client:
        view = RequestStatusView(policy)
    try:
        assert "ログイン情報を確認" not in view.label.text
        assert view.more_button.text == "ほか 1 件（停止 1 件）"
        view.dismiss()
        assert "ログイン情報を確認" in view.label.text and view.dismiss_button.visible
        view.toggle_more()
        assert view.details.visible and "確認済み" in view.details.text
        view.dismiss()
        assert not view.dismiss_button.visible
        assert "通信停止中（HTTP 401）" in view.label.text + view.details.text
        assert len(policy.progress_snapshot()) == 4
    finally:
        view.close()


def test_shared_stop_does_not_prove_existing_uncertainty_is_resolved(tmp_path):
    shared = generated_shared_scope(tmp_path)
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(shared))
    with pytest.raises(RuntimeError):
        with shared.gate.request():
            raise RuntimeError("synthetic interruption")
    reject(policy, SharedGetOutcomeUncertain())
    pin, = policy.progress_snapshot()
    shared.gate.trip(401)
    assert shared.gate.inflight_path.is_file()
    policy.recheck_gate_notices()
    assert pin in policy.progress_snapshot()
    assert any(record["status"] == 401 for record in policy.progress_snapshot())


def test_known_shared_stop_outlives_local_recovery_even_if_the_shared_lock_is_busy():
    states = [GateNoticeState("local", "stopped", 401), GateNoticeState("shared", "open")]
    policy = retry_policy([0.0], check_gate=lambda: tuple(states))
    reject(policy, ManagedNetworkStopped(401))
    policy.recheck_gate_notices()
    original, = policy.progress_snapshot()
    states[:] = [GateNoticeState("local", "open"), GateNoticeState("shared", "busy", 401)]
    policy.recheck_gate_notices()
    remaining, = policy.progress_snapshot()
    assert remaining["notice_id"] == original["notice_id"]
    assert policy.has_gate_notices()


def test_known_storage_fault_does_not_inherit_ack_from_a_different_authority(header_client):
    states = [GateNoticeState("local", "unverified"), GateNoticeState("shared", "open")]
    policy = retry_policy([0.0], check_gate=lambda: tuple(states))
    reject(policy, ManagedNetworkGateBusy())
    policy.recheck_gate_notices()
    old, = policy.progress_snapshot()
    with header_client:
        view = RequestStatusView(policy)
    try:
        view.dismiss()
        assert "確認済み" in view.label.text
        states[:] = [GateNoticeState("local", "open"), GateNoticeState("shared", "unverified")]
        policy.recheck_gate_notices()
        new, = policy.progress_snapshot()
        assert new["notice_id"] != old["notice_id"]
        view.refresh()
        assert view.dismiss_button.visible and "確認済み" not in view.label.text
    finally:
        view.close()


def test_agreeing_overlapping_polls_can_clear_only_the_recovered_shared_side():
    entered = Event()
    release = Event()
    observed = (GateNoticeState("local", "stopped", 401), GateNoticeState("shared", "open"))

    def check_gate():
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
        return observed

    policy = retry_policy([0.0], check_gate=check_gate)
    reject(policy, SharedGetOutcomeUncertain())
    reject(policy, ManagedNetworkStopped(401))
    with ThreadPoolExecutor(max_workers=1) as worker:
        pending = worker.submit(policy.recheck_gate_notices)
        try:
            assert entered.wait(5)
            policy.recheck_gate_notices()
        finally:
            release.set()
        pending.result(timeout=5)
    record, = policy.progress_snapshot()
    assert record["status"] == 401


def test_overlapping_stop_evidence_prevents_expiring_the_only_busy_notice():
    entered = Event()
    release = Event()
    now = [0.0]
    stopped = (GateNoticeState("local", "stopped", 401), GateNoticeState("shared", "open"))

    def check_gate():
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
            return stopped
        if release.is_set():
            return stopped
        return GateNoticeState("local", "busy"), GateNoticeState("shared", "open")

    policy = retry_policy(now, check_gate=check_gate)
    reject(policy, ManagedNetworkGateBusy())
    now[0] = 601
    with ThreadPoolExecutor(max_workers=1) as worker:
        pending = worker.submit(policy.recheck_gate_notices)
        try:
            assert entered.wait(5)
            policy.recheck_gate_notices()
        finally:
            release.set()
        pending.result(timeout=5)
    assert policy.progress_snapshot() and policy.has_gate_notices()
    policy.recheck_gate_notices()
    record, = policy.progress_snapshot()
    assert record["status"] == 401 and is_pinned_notice(record)


def test_other_gate_read_cannot_validate_changed_lock_sentinel(tmp_path, monkeypatch):
    from pixivvault_nicegui import managed_network_stop_history_readonly as reader

    local, _, _, _ = generated_owners(tmp_path)
    shared = generated_shared_scope(tmp_path)
    local.shared_owner = shared
    read_gate = reader._read_gate_immutable

    def corrupt_previous_lock(path):
        if path == shared.gate.path:
            local.gate.lock_path.write_bytes(b"invalid")
        return read_gate(path)

    monkeypatch.setattr(reader, "_read_gate_immutable", corrupt_previous_lock)
    states = {state.authority: state.state for state in check_request_notice_gates(local)}
    assert states == {"local": "unverified", "shared": "open"}


def test_missing_required_shared_owner_does_not_mask_owned_local_stop(tmp_path):
    from pixivvault_nicegui.native_startup import NativeProfileBinding

    local, _, _, _ = generated_owners(tmp_path)
    binding = SimpleNamespace(_reader_lock=RLock(), _closed=False, managed_network_stop_scope=local,
                              shared_network_stop_scope=None, auth_stop_required=True,
                              auth_shared_stop_required=True)
    local.gate.trip(401)
    states = NativeProfileBinding._check_request_notice_gate(binding)
    assert {(state.authority, state.state, state.status) for state in states} == {
        ("local", "stopped", 401), ("shared", "unverified", None),
    }


def test_legacy_rate_stop_has_priority_over_ordinary_cancellations(header_client):
    records = [dict(phase="stopped", status=429, attempt=3, limit=3, notice_id=1)]
    records.extend(dict(phase="cancelled", status=None, attempt=1, limit=3, notice_id=index)
                   for index in range(2, 6))
    with header_client:
        view = RequestStatusView(SimpleNamespace(progress_snapshot=lambda: records))
    try:
        assert "今回のHTTP 429" in view.label.text
    finally:
        view.close()


def test_an_opaque_stop_does_not_prove_that_the_shared_marker_cleared():
    def check_gate():
        raise ManagedNetworkStopped(401)

    policy = retry_policy([0.0], check_gate=check_gate)
    reject(policy, SharedGetOutcomeUncertain())
    original, = policy.progress_snapshot()
    policy.recheck_gate_notices()
    assert original in policy.progress_snapshot()


def test_stale_click_does_not_ack_a_pin_added_by_a_later_render(header_client):
    records = [dict(phase="failed", status=500, attempt=1, limit=3, notice_id=index)
               for index in range(1, 4)]
    with header_client:
        view = RequestStatusView(SimpleNamespace(progress_snapshot=lambda: records))
    try:
        old_revision = view.dismiss_button.props.get("data-notice-revision")
        records.append(dict(phase="stopped", status=None, attempt=1, limit=3, notice_id=4,
                            stop_cause="gate_unverified", gate_problem="uncertain"))
        view.refresh()
        view._dismiss_rendered(SimpleNamespace(args={"revision": old_revision}))
        assert "前の通信結果を確認できない" in view.label.text
        assert "確認済み" not in view.label.text and view.dismiss_button.visible
        current = view.dismiss_button.props["data-notice-revision"]
        view._dismiss_rendered(SimpleNamespace(args={"revision": current}))
        assert "確認済み" in view.label.text
    finally:
        view.close()
