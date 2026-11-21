"""E-C: 借用アカウント自動更新間隔（元 gui.py:1284-1302 / scheduler.py:125-175）。"""
import pytest
from test_oauth_send_boundary import make_vault, payout
from pixivvault_nicegui.external_vault import keeper_plan, VaultError
from pixivvault_nicegui.external_vault_transport import VaultResponse, ACCOUNT, OAUTH
from pixivvault_nicegui.preferences import SPEC_BY_KEY, PreferencesStore

NOW = 1_000_000.0


def data_with(**slot):
    base = {'access_token': 'x', 'token_expires_at_ms': (NOW + 3000) * 1000,
            'token_fetched_at': NOW - 7200, 'last_fetch_at': NOW - 7200}
    base.update(slot)
    return {'shaft_uids': [1], 'slots': {'1': base}}


def test_spec_matches_original_options_and_default():
    spec = SPEC_BY_KEY['nana7mi_shaft_refresh_interval_hours']
    assert spec.default == '24'
    assert spec.choices == ('1', '6', '12', '24', '48')
    assert spec.label == 'Nana7mi借用アカウント自動更新間隔'


def test_preferences_store_roundtrip(tmp_path):
    store = PreferencesStore(tmp_path / 'p.sqlite3')
    snapshot = store.snapshot()
    assert snapshot.values['nana7mi_shaft_refresh_interval_hours'] == '24'
    store.update({'nana7mi_shaft_refresh_interval_hours': '6'}, expected_revision=snapshot.revision)
    assert store.snapshot().values['nana7mi_shaft_refresh_interval_hours'] == '6'


def test_periodic_refresh_is_due_only_after_the_interval():
    data = data_with()
    assert keeper_plan(data, NOW)[1] is False               # 既定: 有効期限だけで動く（従来どおり）
    assert keeper_plan(data, NOW, interval_hours=1)[1] is True    # 2時間前 >= 1時間
    assert keeper_plan(data, NOW, interval_hours=6)[1] is False
    uid, fetch, wait = keeper_plan(data, NOW, interval_hours=6)
    assert 0 < wait <= 900


@pytest.mark.parametrize('interval', (0, -5, 'x', None, 0.5))
def test_interval_floor_is_one_hour_and_garbage_means_default(interval):
    # 30分前に更新済み: 壊れた値でも1時間を下回って叩かない
    data = data_with(last_fetch_at=NOW - 1800, token_fetched_at=NOW - 1800)
    assert keeper_plan(data, NOW, interval_hours=interval)[1] is False


def test_missing_stamp_does_not_trigger_periodic_refresh():
    data = data_with()
    del data['slots']['1']['last_fetch_at'], data['slots']['1']['token_fetched_at']
    assert keeper_plan(data, NOW, interval_hours=1)[1] is False


def test_periodic_refresh_keeps_proactive_floor_and_backoff():
    # 35分(2100秒)の間隔と backoff は周期更新でも越えない
    data = data_with(last_fetch_at=NOW - 2000, token_fetched_at=NOW - 7200)
    assert keeper_plan(data, NOW, interval_hours=1)[1] is False
    data = data_with(backoff_until=NOW + 600)
    assert keeper_plan(data, NOW, interval_hours=1)[1] is False


def test_five_hour_window_never_exceeds_payout_budget_even_at_one_hour():
    """鍵の更新が実際に5時間窓で10回を超えないこと（2スロット・1時間設定・時計進行）。"""
    now = NOW
    data = {'shaft_uids': [1, 2], 'slots': {
        str(u): {'access_token': 'x', 'token_expires_at_ms': (NOW + 3600) * 1000,
                 'token_fetched_at': NOW, 'last_fetch_at': NOW} for u in (1, 2)}}
    fetches = {1: [], 2: []}
    while now < NOW + 5 * 3600 * 4:
        uid, fetch, wait = keeper_plan(data, now, interval_hours=1)
        if fetch:
            fetches[uid].append(now)
            slot = data['slots'][str(uid)]
            slot['last_fetch_at'] = slot['token_fetched_at'] = now
            slot['token_expires_at_ms'] = (now + 3600) * 1000
            now += 1
        else:
            now += max(1, min(wait, 300))
    for uid, times in fetches.items():
        assert all(b - a >= 2100 for a, b in zip(times, times[1:]))
        for start in times:
            assert len([t for t in times if start <= t < start + 5 * 3600]) <= 10


def test_vault_service_uses_the_configured_interval(tmp_path):
    reply = VaultResponse(200, {'access_token': 'generated-new', 'refresh_token': 'generated-rotated', 'expires_in': 3600})
    v, wire, _ = make_vault(tmp_path, [payout(), reply])
    now = [100000]
    v.clock = lambda: now[0]
    try:
        epoch, data = v._snapshot()
        for key in ('1', '2'):
            data['slots'][key].update(access_token='generated-old', token_expires_at_ms=(100000 + 3000) * 1000,
                                      token_fetched_at=100000 - 100, last_fetch_at=100000 - 100)
        data['slots']['1'].update(token_fetched_at=100000 - 10000, last_fetch_at=100000 - 10000)
        v._publish(epoch, data)
        # 設定なし: 有効期限が十分あるので何もしない
        v.keeper_tick()
        assert wire.calls == []
        # 24時間設定でも10000秒前では動かない
        v.set_refresh_interval_provider(lambda: '24')
        v.keeper_tick()
        assert wire.calls == []
        # 1時間設定: スロット1が周期更新の対象（同じ keeper の払い出し経路を通る）
        v.set_refresh_interval_provider(lambda: '1')
        v.keeper_tick()
        assert wire.calls[:2] == [ACCOUNT, OAUTH]
        assert v._snapshot()[1]['slots']['1']['access_token'] == 'generated-new'
        assert v._snapshot()[1]['slots']['1']['last_fetch_at'] == 100000
        # 直後は35分の下限で再実行されない
        calls = len(wire.calls)
        v.keeper_tick()
        assert len(wire.calls) == calls
    finally:
        v.close()


def test_provider_failure_disables_periodic_refresh_only(tmp_path):
    v, wire, _ = make_vault(tmp_path, [])
    try:
        def broken():
            raise RuntimeError('prefs')
        v.set_refresh_interval_provider(broken)
        assert v._refresh_interval_hours() is None
    finally:
        v.close()


def test_preferences_view_shows_original_labels_without_relabeling_other_items(tmp_path):
    from nicegui import Client, ui
    from pixivvault_nicegui.ui.preferences_view import PreferencesView
    key = 'nana7mi_shaft_refresh_interval_hours'
    client = Client(ui.page('/_e_interval_prefs'))
    view = None
    try:
        with client:
            view = PreferencesView(PreferencesStore(tmp_path / 'prefs'), enabled_keys=(key, 'api_retry_count'))
            view.open()
        control = view.controls[key]
        assert control.value == '24' and control.enabled
        assert control.options == {'1': '1時間ごと', '6': '6時間ごと', '12': '12時間ごと',
                                   '24': '24時間ごと (1日)', '48': '48時間ごと (2日)'}
        assert view.controls['api_retry_count'].options['1'] == '1'
        folder = view.controls['use_work_folder']
        assert isinstance(folder, ui.switch)
        assert (folder.props['false-value'], folder.props['true-value']) == ('0', '1')
        assert folder.text == '作品ごとのフォルダーを使う'
        control.set_value('6')
        assert view.save()
        assert view.store.snapshot().values[key] == '6'
    finally:
        if view:
            view.close()
        client.delete()


def test_preference_consumer_only_when_vault_is_wired(tmp_path):
    from pixivvault_nicegui.services import ApplicationServices
    key = 'nana7mi_shaft_refresh_interval_hours'
    s = ApplicationServices(2, preferences_root=tmp_path / 'prefs')
    try:
        assert key not in s.preference_consumers()
        s.external_vault = type('V', (), {'set_refresh_interval_provider': lambda self, p: None})()
        assert key in s.preference_consumers()
        s.external_vault = None
    finally:
        s.close()


def test_application_wires_the_setting_into_the_vault(tmp_path):
    from pixivvault_nicegui.services import ApplicationServices
    from pixivvault_nicegui.external_vault_transport import ShaftTransport
    from test_external_vault import Request, profile, FixtureCrypto
    key = 'nana7mi_shaft_refresh_interval_hours'
    transport = ShaftTransport(Request([]), oauth_profiles=[profile()])
    s = ApplicationServices(external_vault_root=tmp_path / 'app-vault', external_vault_transport=transport,
                            external_vault_crypto=FixtureCrypto(), preferences_root=tmp_path / 'prefs')
    try:
        assert key in s.preference_consumers()
        assert s.external_vault._refresh_interval_hours() == '24'
        snapshot = s.preferences.snapshot()
        s.preferences.update({key: '1'}, expected_revision=snapshot.revision)
        assert s.external_vault._refresh_interval_hours() == '1'
    finally:
        s.close()
