"""B3-C2-UI-001: assert actor meaning on the real status label and policy."""
import copy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from nicegui import Client, ui

from stage5_generated_shared_owner import generated_shared_scope
from pixivvault_nicegui.managed_network_stop_shared_scope import _INFLIGHT_BYTES
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected
from pixivvault_nicegui.request_policy import RequestRetryPolicy
from pixivvault_nicegui.ui.request_status_view import RequestStatusView, retry_status_text


def fingerprint(root):
    return {p.relative_to(root).as_posix():
            (p.stat().st_ino, hashlib.sha256(p.read_bytes()).hexdigest())
            for p in root.rglob('*') if p.is_file()}


def state(policy):
    return copy.deepcopy(dict(rate_stopped=policy._rate_stopped, serial=policy._serial, records=policy._records))


def capture(name, value):
    destination = os.environ.get('PV_B3_C2_EVIDENCE')
    if destination:
        folder = Path(destination)
        folder.mkdir(parents=True, exist_ok=True)
        (folder/(name+'.json')).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def run_case(tmp_path, *, target_sends=None, limit=5):
    """Interleave a second actual policy execution while the target is waiting."""
    live = tmp_path/'live'; live.mkdir()
    historical = tmp_path/'historical'; historical.mkdir()
    scope = generated_shared_scope(live)
    old = generated_shared_scope(historical)
    old.gate.trip(429)
    old.gate.inflight_path.write_bytes(_INFLIGHT_BYTES)
    fixture_before = fingerprint(tmp_path)
    now = [0.]
    sends = []
    waits = []
    actor = ['target']
    injected = [False]
    snapshots = []
    policy = None
    def operation():
        scope._check_owner()
        with scope.gate.request():
            sends.append(actor[0])
            raise HTTPStatusRejected(429)
    def observe():
        saved = state(policy)
        view.refresh()
        assert state(policy) == saved
        assert fingerprint(tmp_path) == fixture_before
        snapshots.append(policy.progress_snapshot())
    def sleep(seconds):
        waits.append([actor[0], seconds])
        observe()
        if (target_sends is not None and actor[0] == 'target' and not injected[0]
                and sends.count('target') == target_sends):
            injected[0] = True
            actor[0] = 'other'
            try:
                with pytest.raises(HTTPStatusRejected): policy.execute(operation, deadline=1000)
            finally:
                actor[0] = 'target'
        now[0] += seconds
    policy = RequestRetryPolicy(lambda: SimpleNamespace(retry_limit=limit, rate_limit_wait_seconds=3),
                                clock=lambda: now[0], sleep=sleep, jitter=lambda *_: 0)
    client = Client(ui.page('/_b3_c2_actor'))
    view = None
    try:
        with client:
            view = RequestStatusView(policy)
            with pytest.raises(HTTPStatusRejected): policy.execute(operation, deadline=1000)
            saved = state(policy)
            sent = list(sends)
            for _ in range(3): observe()
            assert view.label.visible and state(policy) == saved
            with pytest.raises(HTTPStatusRejected): policy.execute(operation, deadline=1000)
            assert sends == sent and state(policy) == saved
            assert fingerprint(tmp_path) == fixture_before
            result = dict(sends=sends, waits=waits, clock=now[0], state=saved,
                          text=view.label.text, snapshots=snapshots,
                          fixture_unchanged=True, display_state_unchanged=True, post_stop_dispatches=0)
            capture('own-'+str(limit) if target_sends is None else 'shared-after-'+str(target_sends), result)
            return result
    finally:
        if view: view.close()
        client.delete()


@pytest.mark.parametrize('sent', [1, 4])
def test_other_operation_cause_is_explicit_even_at_last_attempt_slot(tmp_path, sent):
    result = run_case(tmp_path, target_sends=sent)
    assert result['sends'].count('target') == sent and result['sends'].count('other') == 5
    target, other = result['text'].splitlines()
    assert f'この操作の試行 {sent+1}/5' in target
    assert 'この回は送信前に停止' in target
    assert '別の取得処理が' in target and '429の試行上限に達したため' in target
    assert 'この操作自身は試行上限に達していません' in target
    assert 'この操作自身が429の試行上限に達した' not in target
    assert 'この操作の試行 5/5' in other and 'この操作自身が429の試行上限に達した' in other
    records = list(result['state']['records'].values())
    assert records[0]['stop_cause'] == 'shared_limit'
    assert records[1]['stop_cause'] == 'own_limit'
    for line in (target, other):
        assert 'この起動中は停止' in line and '手動でやり直して' in line
        assert '再起動後に残る停止として記録しません' in line and '自動再送は行いません' in line


@pytest.mark.parametrize('limit', [1, 5])
def test_own_limit_is_explicit_on_the_real_status_label(tmp_path, limit):
    result = run_case(tmp_path, limit=limit)
    assert result['sends'] == ['target']*limit
    assert len(result['waits']) == limit-1
    assert result['text'].count('この操作自身が429の試行上限に達した') == 1
    assert f'この操作の試行 {limit}/{limit}' in result['text']
    assert '別の取得処理が' not in result['text']
    assert 'この操作自身は試行上限に達していません' not in result['text']
    assert result['state']['records'][1]['stop_cause'] == 'own_limit'


def test_replay_native_record_below_limit_explains_other_actor_in_product_label():
    # Exact phase/attempt/limit/status fields from the sealed CoS 2/5 + 5/5 snapshot.
    records = tuple(dict(phase='stopped', attempt=a, limit=5, status=429) for a in (2, 5))
    saved = copy.deepcopy(records)
    client = Client(ui.page('/_b3_c2_replay'))
    view = None
    try:
        with client:
            view = RequestStatusView(SimpleNamespace(progress_snapshot=lambda: records))
            view.refresh()
            target, _ = view.label.text.splitlines()
            capture('native-record-replay', dict(records=records, text=view.label.text))
            assert 'この操作の試行 2/5' in target
            assert '別の取得処理が' in target and 'この操作自身は試行上限に達していません' in target
            assert records == saved
    finally:
        if view: view.close()
        client.delete()


def test_unannotated_limit_record_does_not_invent_a_stopping_actor():
    records = (dict(phase='stopped', attempt=5, limit=5, status=429),)
    saved = copy.deepcopy(records)
    text = retry_status_text(records)
    assert 'この操作自身か別の取得処理かは特定できません' in text
    assert 'この操作自身が429の試行上限に達した' not in text
    assert records == saved
