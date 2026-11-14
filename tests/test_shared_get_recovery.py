"""Generated shared owner only: no real Cookie, stop owner or network."""
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from nicegui import Client, ui

from stage5_generated_shared_owner import generated_shared_scope
from test_stage5_r1_user_shared_scope import generated_sealed_shared_owner
from pixivvault_nicegui.managed_network_stop_gate import (
    ManagedNetworkGateStorageError, ManagedNetworkGateBusy, ManagedNetworkStopped,
    _file_identity,
)
from pixivvault_nicegui.managed_network_stop_shared_scope import (
    ManagedUserNetworkStopScope, _INFLIGHT_BYTES, _OWNER_FILE,
)


def rows():
    return [dict(illust_id=991110000+n,user_id=91,user_name='Generated author',
                 title=f'Generated rank {n}',tags=[],url='https://i.pximg.net/generated.png',
                 rank=n,yes_rank=0,illust_type=0,illust_page_count=1)
            for n in range(1,51)]


def owned_reader(tmp_path):
    root, cookie = generated_sealed_shared_owner(tmp_path)
    scope = ManagedUserNetworkStopScope(root)
    calls = []
    def wire(request, *_):
        assert request.get_method() == 'GET' and '/ranking.php?' in request.full_url
        calls.append('generated-ranking-200')
        return json.dumps({'contents':rows()}).encode()
    reader = scope.create_reader(cookie, transport=wire)
    reader._verified_identity = '55'  # Generated auth fixture, not a real login.
    scope.gate.inflight_path.write_bytes(_INFLIGHT_BYTES)
    return scope, reader, calls, cookie


def test_uncertain_marker_has_typed_reason_and_emits_no_http(tmp_path):
    scope, reader, calls, _ = owned_reader(tmp_path)
    try:
        with pytest.raises(ManagedNetworkGateStorageError) as error:
            reader.get_ranking()
        assert getattr(error.value, 'code', None) == 'shared-get-outcome-uncertain'
        assert calls == [] and scope.gate.inflight_path.exists()
    finally:
        reader.close()


def test_explicit_recovery_keeps_history_and_cookie_and_allows_same_reader(tmp_path):
    scope, reader, calls, cookie = owned_reader(tmp_path)
    before = (scope.gate.path.read_bytes(), cookie.read_bytes(),
              _file_identity(scope.gate.path), _file_identity(scope.gate.lock_path))
    try:
        plan = scope.preview_uncertain_get_recovery()
        with pytest.raises(ValueError):
            scope.recover_uncertain_get(plan, confirmed=False)
        assert scope.gate.inflight_path.exists() and calls == []
        scope.recover_uncertain_get(plan, confirmed=True)
        assert calls == [] and not scope.gate.inflight_path.exists()
        assert before == (scope.gate.path.read_bytes(), cookie.read_bytes(),
                          _file_identity(scope.gate.path), _file_identity(scope.gate.lock_path))
        assert len(list(scope.root.glob('acknowledged-get-*.v1'))) == 1
        assert len(reader.get_ranking()) == 50 and calls == ['generated-ranking-200']
        assert not scope.gate.inflight_path.exists()
    finally:
        reader.close()


@pytest.mark.parametrize('status',[401,403,429])
def test_recovery_never_clears_a_latched_http_stop(tmp_path,status):
    scope = generated_shared_scope(tmp_path)
    scope.gate.inflight_path.write_bytes(_INFLIGHT_BYTES)
    plan = scope.preview_uncertain_get_recovery()
    state = scope.gate.trip(status)
    with pytest.raises(ManagedNetworkStopped):
        scope.recover_uncertain_get(plan, confirmed=True)
    with pytest.raises(ManagedNetworkStopped):
        scope.preview_uncertain_get_recovery()
    assert scope.gate.inflight_path.exists() and scope.gate.snapshot() == state


@pytest.mark.parametrize('change',['in_place','replace'])
def test_changed_marker_invalidates_the_confirmation(tmp_path,change):
    scope = generated_shared_scope(tmp_path)
    marker = scope.gate.inflight_path
    marker.write_bytes(_INFLIGHT_BYTES)
    plan = scope.preview_uncertain_get_recovery()
    if change == 'replace':
        new = scope.root/'generated-replacement'
        new.write_bytes(_INFLIGHT_BYTES)
        os.replace(new, marker)
    else:
        info = marker.stat()
        os.utime(marker,ns=(info.st_atime_ns,info.st_mtime_ns+1000000000))
    with pytest.raises(ManagedNetworkGateStorageError):
        scope.recover_uncertain_get(plan, confirmed=True)
    assert marker.exists() and not list(scope.root.glob('acknowledged-get-*.v1'))


def test_corrupt_marker_and_changed_owner_are_not_recovered(tmp_path):
    scope = generated_shared_scope(tmp_path)
    scope.gate.inflight_path.write_bytes(b'generated unknown state')
    with pytest.raises(ManagedNetworkGateStorageError):
        scope.preview_uncertain_get_recovery()
    scope.gate.inflight_path.write_bytes(_INFLIGHT_BYTES)
    plan = scope.preview_uncertain_get_recovery()
    marker = scope.root/_OWNER_FILE
    data = json.loads(marker.read_text('utf8'))
    data['gate_identity'] = [0,0]
    marker.write_text(json.dumps(data),'utf8')
    with pytest.raises(ManagedNetworkGateStorageError):
        scope.recover_uncertain_get(plan, confirmed=True)
    assert scope.gate.inflight_path.exists()


def test_active_get_is_not_mistaken_for_an_abandoned_marker(tmp_path):
    scope = generated_shared_scope(tmp_path)
    started, release = Event(), Event()
    def active_get():
        with scope.gate.request():
            started.set()
            assert release.wait(5)
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(active_get)
        try:
            assert started.wait(5)
            with pytest.raises(ManagedNetworkGateBusy):
                scope.preview_uncertain_get_recovery()
            assert scope.gate.inflight_path.exists()
        finally:
            release.set()
            task.result(timeout=5)
    assert not scope.gate.inflight_path.exists()


@pytest.mark.asyncio
async def test_real_ranking_notice_and_confirmed_ui_recovery(tmp_path):
    from pixivvault_nicegui.ui.shared_get_recovery_view import SharedGetRecoveryView
    from pixivvault_nicegui.ui.ranking_view import RankingView
    from pixivvault_nicegui.ranking_source import RankingSource
    from test_ranking_ui import setup
    scope, reader, calls, _ = owned_reader(tmp_path)
    services, _, _ = setup()
    services.ranking_source = RankingSource(reader)
    client = Client(ui.page('/_shared_get_ui'))
    try:
        with client:
            view = RankingView(services)
            view.set_active(True)
            recovery = SharedGetRecoveryView(scope)
            await recovery.inspect_summary()
            assert recovery.summary.visible and '前の取得の結果が不確か' in recovery.summary.text
            await view.load()
            assert '前の取得の結果が不確か' in view.notice.text and calls == []
            assert '取得停止の確認・回復' in view.notice.text
            await recovery.open()
            assert recovery.plan is not None and not recovery.confirm_button.enabled
            await recovery.recover()
            assert calls == [] and scope.gate.inflight_path.exists()
            recovery.acknowledge.set_value(True)
            await recovery.recover()
            assert calls == [] and not scope.gate.inflight_path.exists()
            assert not recovery.summary.visible
            await view.load()
            assert len(view.page.items)==50 and len(view.gallery.cards)==50
            assert calls==['generated-ranking-200']
            recovery.close()
    finally:
        client.delete()
        reader.close()
        services.close()
