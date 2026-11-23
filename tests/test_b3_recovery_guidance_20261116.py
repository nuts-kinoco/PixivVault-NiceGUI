"""B3: real headless views and generated owners, never a live HTTP transport."""
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest
from nicegui import Client, ui

from stage5_generated_shared_owner import generated_shared_scope
from test_stage5_r1_user_shared_scope import generated_sealed_shared_owner
from test_shared_get_recovery import rows
from test_display_recovery_batch import Clock
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopped
from pixivvault_nicegui.managed_network_stop_shared_scope import (
    ManagedUserNetworkStopScope, _INFLIGHT_BYTES, _OWNER_FILE,
)
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected
from pixivvault_nicegui.ui.request_status_view import RequestStatusView, retry_status_text
from pixivvault_nicegui.ui.shared_get_recovery_view import SharedGetRecoveryView
from pixivvault_nicegui.ui.stop_recovery_view import StopRecoveryPanel


def fingerprint(root):
    return {p.relative_to(root).as_posix():
            (p.stat().st_dev, p.stat().st_ino, hashlib.sha256(p.read_bytes()).hexdigest())
            for p in root.rglob('*') if p.is_file()}


def policy_state(policy):
    return copy.deepcopy((policy._rate_stopped, policy._serial, policy._records))


def labels(element):
    return '\n'.join(e.text for e in element.descendants() if isinstance(getattr(e, 'text', None), str))


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [401, 403, 429])
async def test_persisted_stop_notice_never_recovers_or_dispatches(tmp_path, status):
    root, cookie = generated_sealed_shared_owner(tmp_path)
    scope = ManagedUserNetworkStopScope(root)
    scope.gate.trip(status)
    scope.gate.inflight_path.write_bytes(_INFLIGHT_BYTES)
    before = fingerprint(tmp_path)
    state = scope.gate.snapshot()
    calls = []
    reader = scope.create_reader(cookie, transport=lambda *_: calls.append('must not dispatch'))
    policy = Clock().policy()
    reader.set_retry_policy(policy)
    client = Client(ui.page('/_b3_persisted'))
    view = None
    try:
        with client:
            view = SharedGetRecoveryView(scope)
            await view.inspect_summary()
            await view.open()
            assert view.summary.visible
            assert f'過去のHTTP {status}' in view.summary.text
            assert view.notice.text == view.summary.text
            assert view.plan is None and not view.confirm_button.enabled
            assert not view.acknowledge.enabled
            view.acknowledge.set_value(True)  # Even a forged acknowledgement cannot recover.
            await view.recover()
            if status == 429:
                assert '今回の429の設定待機・再試行とは別' in view.notice.text
                assert '再起動しても残ります' in view.notice.text
                assert '停止履歴・所有情報を削除せず' in view.notice.text
                assert '停止履歴の確認と移行手順を開く' in view.notice.text
                assert '正式移行でもSTOPは維持' in view.notice.text
            with pytest.raises(ManagedNetworkStopped):
                reader.get_ranking()
            assert calls == []
            assert scope.gate.snapshot() == state
            assert ManagedUserNetworkStopScope(root).gate.snapshot() == state
            assert fingerprint(tmp_path) == before
            assert not list(root.glob('acknowledged-get-*.v1'))
    finally:
        if view: view.close()
        client.delete()
        reader.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['owner', 'intent'])
async def test_unknown_owner_or_intent_stays_unverified(tmp_path, damage):
    scope = generated_shared_scope(tmp_path)
    scope.gate.inflight_path.write_bytes(_INFLIGHT_BYTES)
    if damage == 'owner':
        (scope.root/_OWNER_FILE).write_text('{}', encoding='utf-8')
    else:
        scope.gate.inflight_path.write_bytes(b'generated unknown intent')
    before = fingerprint(tmp_path)
    client = Client(ui.page('/_b3_unknown'))
    view = None
    try:
        with client:
            view = SharedGetRecoveryView(scope)
            await view.inspect_summary()
            await view.open()
            assert '通信記録または所有者を確認できません' in view.notice.text
            assert '過去のHTTP' not in view.notice.text
            assert view.plan is None and not view.confirm_button.enabled
            view.acknowledge.set_value(True)
            await view.recover()
            assert fingerprint(tmp_path) == before
    finally:
        if view: view.close()
        client.delete()


@pytest.mark.asyncio
@pytest.mark.parametrize('count,wait', [(1, 3), (3, 5), (5, 10), (10, 3), (3, 30)])
async def test_current_429_views_preserve_wait_and_launch_stop(tmp_path, count, wait):
    root, cookie = generated_sealed_shared_owner(tmp_path)
    scope = ManagedUserNetworkStopScope(root)
    clock = Clock()
    policy = clock.policy(count, wait)
    calls = []
    reject = [True]
    def wire(*_):
        calls.append(429 if reject[0] else 200)
        if reject[0]: raise HTTPStatusRejected(429)
        return json.dumps({'contents': rows()}).encode()
    reader = scope.create_reader(cookie, transport=wire)
    reader._verified_identity = '55'  # Generated fixture identity only.
    reader.set_retry_policy(policy)
    before = fingerprint(tmp_path)
    state = scope.gate.snapshot()
    waiting = []
    client = Client(ui.page('/_b3_current'))
    recovery = status = None
    try:
        with client:
            recovery = SharedGetRecoveryView(scope)
            status = RequestStatusView(policy)
            def observe_wait():
                saved = policy_state(policy)
                status.refresh()
                assert policy_state(policy) == saved
                record, = policy.progress_snapshot()
                waiting.append((record['attempt'], record['remaining_seconds']))
                assert status.label.visible
                assert '今回の429' in status.label.text and '再試行待機中' in status.label.text
                assert f'試行 {len(calls)}/{count}' in status.label.text
                assert f'待機設定 {wait}秒' in status.label.text
                assert f'残り約{record["remaining_seconds"]}秒' in status.label.text
                assert 0 < record['remaining_seconds'] <= wait
                assert '初回を含む' in status.label.text
                assert '再起動後に残る停止として記録しません' in status.label.text
                assert not scope.gate.snapshot().blocked
            clock.on_wait = observe_wait
            with pytest.raises(HTTPStatusRejected): reader.get_ranking()
            assert calls == [429]*count
            assert {attempt for attempt, _ in waiting} == set(range(1, count))
            for attempt in range(1, count):
                remaining = [seconds for seen, seconds in waiting if seen == attempt]
                assert remaining[0] == wait and remaining[-1] <= 1
                assert remaining == sorted(remaining, reverse=True)
            assert sum(clock.waits) == pytest.approx((count-1)*wait)
            saved = policy_state(policy)
            for _ in range(2):
                status.refresh()
                await recovery.inspect_summary()
                await recovery.open()
                recovery.acknowledge.set_value(True)
                await recovery.recover()
            assert policy_state(policy) == saved
            assert status.label.visible and 'この起動中は停止' in status.label.text
            assert '同じ通信の仕組みを使う取得' in status.label.text
            assert '手動でやり直して' in status.label.text
            assert '過去に記録した通信停止は残ります' in status.label.text
            assert '自動再送は行いません' in status.label.text
            assert not recovery.summary.visible and recovery.plan is None
            assert scope.gate.snapshot() == state and fingerprint(tmp_path) == before
            with pytest.raises(HTTPStatusRejected): reader.get_ranking()
            assert calls == [429]*count  # Display/open/ack did not release or resend.
            assert policy_state(policy) == saved
            reject[0] = False
            restarted = clock.policy(count, wait)
            reader.set_retry_policy(restarted)  # Model the existing restart lifetime explicitly.
            assert len(reader.get_ranking()) == 50 and calls == [429]*count+[200]
            assert not restarted.progress_snapshot()
            assert policy_state(policy) == saved
            assert fingerprint(tmp_path) == before
    finally:
        if status: status.close()
        if recovery: recovery.close()
        client.delete()
        reader.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('code', [401, 403, 429])
async def test_offline_panel_reads_actual_history_without_changing_it(tmp_path, code):
    from pixivvault_nicegui.native_profile import NativeProfileStore
    from pixivvault_nicegui.native_startup import NativeProfileBinding
    from pixivvault_nicegui.managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
    store = NativeProfileStore(tmp_path/'profile')
    scope = ManagedProfileNetworkStopScope(store.root)
    scope.gate.trip(code)
    binding = NativeProfileBinding(store)
    before = fingerprint(tmp_path)
    client = Client(ui.page('/_b3_offline'))
    panel = None
    try:
        with client:
            panel = StopRecoveryPanel(binding)
            panel.open()
            await panel.check()
            assert f'停止履歴あり（HTTP {code}）' in panel.result.text
            text = labels(panel.dialog)
            assert '今回の429の設定待機・再試行は別' in text
            assert 'STOP解除ではありません' in text
            assert 'STOPは移行・再起動後も維持' in text
            assert '他の取得' not in panel.result.text
            assert '通信停止・認証無効を維持' in panel.result.text
            if code == 429:
                assert '過去の永続STOP履歴' in panel.result.text
                assert '正式移行・再起動でも解除されません' in panel.result.text
            assert panel.migration_view is None  # Opening/checking never starts migration.
            assert scope.gate.snapshot().status_code == code
            assert fingerprint(tmp_path) == before
    finally:
        if panel: panel.close()
        client.delete()
        binding.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('state,expected', [('observed_unblocked', '読取時点ではSTOPなし'),
                                            ('history_unverified', '履歴を確認できません')])
async def test_offline_observation_never_claims_retry_or_permission(state, expected):
    observations = ({'owner': 'native', 'state': state, 'status_code': None, 'authorizes_get': False},)
    saved = copy.deepcopy(observations)
    client = Client(ui.page('/_b3_observation'))
    panel = None
    try:
        with client:
            panel = StopRecoveryPanel(SimpleNamespace(inspect_stop_history=lambda: observations))
            await panel.check()
            assert expected in panel.result.text
            assert '通信停止・認証無効を維持' in panel.result.text
            assert '永続STOP履歴です' not in panel.result.text
            assert observations == saved
    finally:
        if panel: panel.close()
        client.delete()


def test_shared_policy_stop_does_not_claim_this_operation_exhausted_its_limit():
    records = (dict(phase='stopped', attempt=2, limit=5, status=429),)
    saved = copy.deepcopy(records)
    text = retry_status_text(records)
    assert '試行 2/5で上限に達した' not in text
    assert '別の取得処理が429の試行上限に達した' in text
    assert records == saved
