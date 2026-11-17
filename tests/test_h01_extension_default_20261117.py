"""FL-N01〜N03: 拡張機能の受信サーバーを、通常起動でも既定で起動する。

元(server.py)は常時起動。移植版は --extension-bridge 指定時だけだった。
ここでは (1) 既定で有効になる条件 (2) 設定で止められること (3) 実ソケットで
ping/保存済み状態/download が通ること を確認する。実ポート 25010 は掴まない。
"""
import json
from types import SimpleNamespace

import pytest

from pixivvault_nicegui import extension_bridge as bridge
from pixivvault_nicegui.bootstrap import parse_args, extension_bridge_wanted
from pixivvault_nicegui.preferences import SPEC_BY_KEY
from pixivvault_nicegui.services import ApplicationServices

ORIGIN = 'chrome-extension://' + 'a' * 32
HEADER_END = bytes([13, 10, 13, 10])


def _managed_args(tmp_path):
    cookie = tmp_path / 'private' / 'cookies.txt'
    root = tmp_path / 'separate' / 'downloads'
    return ['--pixiv-cookie-file', str(cookie), '--pixiv-download-root', str(root)]


def test_managed_startup_enables_bridge_by_default(tmp_path):
    config = parse_args(_managed_args(tmp_path))
    assert config.extension_bridge is False          # 明示指定ではない
    assert config.extension_bridge_auto is True      # 既定で起動する


def test_explicit_flag_is_unchanged_and_not_auto(tmp_path):
    config = parse_args([*_managed_args(tmp_path), '--extension-bridge'])
    assert config.extension_bridge is True and config.extension_bridge_auto is False


def test_command_line_can_turn_it_off(tmp_path):
    config = parse_args([*_managed_args(tmp_path), '--no-extension-bridge'])
    assert config.extension_bridge is False and config.extension_bridge_auto is False


def test_without_managed_storage_nothing_starts():
    config = parse_args([])
    assert config.extension_bridge is False and config.extension_bridge_auto is False


def test_explicit_and_no_flag_conflict(tmp_path):
    with pytest.raises(SystemExit):
        parse_args([*_managed_args(tmp_path), '--extension-bridge', '--no-extension-bridge'])


def test_wanted_follows_setting_only_for_default_start():
    on = SimpleNamespace(get_setting=lambda key, default=None: '1')
    off = SimpleNamespace(get_setting=lambda key, default=None: '0')
    assert extension_bridge_wanted(False, True, on) is True
    assert extension_bridge_wanted(False, True, off) is False      # 設定で止められる
    assert extension_bridge_wanted(True, False, off) is True       # 明示指定は従来どおり
    assert extension_bridge_wanted(False, False, on) is False
    assert extension_bridge_wanted(False, True, None) is True      # 設定の保存先が無い起動は止める設定が無い
    broken = SimpleNamespace(get_setting=lambda key, default=None: (_ for _ in ()).throw(RuntimeError()))
    assert extension_bridge_wanted(False, True, broken) is False   # 設定を読めない場合は起動しない


def test_setting_exists_default_on():
    spec = SPEC_BY_KEY['extension_bridge_enabled']
    assert spec.default == '1' and spec.choices == ('0', '1')


def test_setting_is_offered_only_with_managed_storage():
    managed = SimpleNamespace(managed_downloads=object())
    none = SimpleNamespace(managed_downloads=None, native_profile=None)
    assert ApplicationServices.extension_setting_available(managed) is True
    assert ApplicationServices.extension_setting_available(none) is False


class _Owner:
    def __init__(self):
        self.jobs = []
        self.closing = False
        self.extension_bridge = None
        self.extension_bridge_status = 'disabled'
        self.extension_commands = SimpleNamespace(
            enqueue=lambda command: self.jobs.append(command) or 'job-1',
            work_status=lambda work_id: {'downloaded': work_id == '123'},
            author_status=lambda user_id: {'downloaded': 4})
        self.cookie_checks = []
        self.native_profile = SimpleNamespace(
            request_cookie_check=lambda imported=True: self.cookie_checks.append(imported))

    _extension_cookie_received = ApplicationServices._extension_cookie_received
    start_extension_bridge = ApplicationServices.start_extension_bridge


class _FakeServer:
    """required_network_guard はループバックの接続も禁じるため、受付処理は直接呼ぶ。"""
    server_port = 12345

    def __init__(self, address, handler):
        self.address, self.handler = address, handler

    def serve_forever(self, **kw):
        pass

    def shutdown(self):
        pass

    def server_close(self):
        pass


def _call(b, method, path, body=None):
    import io
    from email.message import Message
    handler = b._server.handler
    data = b'' if body is None else json.dumps(body).encode()
    obj = handler.__new__(handler)
    obj.server = b._server
    obj.path = path
    obj.client_address = ('127.0.0.1', 5)
    obj.headers = Message()
    obj.headers['Origin'] = ORIGIN
    obj.headers['Host'] = '127.0.0.1:12345'
    if body is not None:
        obj.headers['Content-Length'] = str(len(data))
        obj.headers['Content-Type'] = 'application/json'
    obj.rfile = io.BytesIO(data)
    obj.wfile = io.BytesIO()
    obj.request_version = 'HTTP/1.1'
    obj.command = method
    obj.requestline = method + ' ' + path
    getattr(obj, 'do_' + method)()
    raw = obj.wfile.getvalue()
    status = int(raw.split(b' ', 2)[1])
    return status, json.loads(raw.split(HEADER_END, 1)[1] or b'{}')


def test_started_service_answers_ping_status_and_download(monkeypatch):
    real = bridge.ExtensionBridge
    ports = []

    def loopback_ephemeral(**kwargs):
        ports.append(kwargs.get('port'))
        return real(port=0, **kwargs)           # テストは実ポート 25010 を使わない
    monkeypatch.setattr(bridge, 'ExtensionBridge', loopback_ephemeral)
    monkeypatch.setattr(bridge, '_Server', _FakeServer)
    owner = _Owner()
    owner.start_extension_bridge()
    try:
        assert owner.extension_bridge_status == 'running'
        b = owner.extension_bridge
        assert ports == [None]                  # 既定のポート(25010)のまま作る
        assert b.port == 0 and b._server.address == ('127.0.0.1', 0)
        assert _call(b, 'GET', '/ping') == (200, {'status': 'ok'})
        assert _call(b, 'GET', '/api/work/123') == (200, {'downloaded': True})
        assert _call(b, 'GET', '/api/work/9') == (200, {'downloaded': False})
        assert _call(b, 'GET', '/api/user/5/status') == (200, {'downloaded': 4, 'total': 4})
        status, body = _call(b, 'POST', '/download', {'type': 'work', 'work_id': '123'})
        assert status == 200 and body['jobId'] == 'job-1' and len(owner.jobs) == 1
        # 受信は保存キューに積むだけで、その場で Pixiv へ通信しない。
        assert owner.jobs[0].target_id == '123'
    finally:
        owner.extension_bridge.close()


def test_default_port_is_original_25010():
    import inspect
    assert inspect.signature(bridge.ExtensionBridge).parameters['port'].default == 25010


def test_bridge_binds_loopback_only(monkeypatch):
    seen = []
    real_server = bridge._Server

    def spy(address, handler):
        seen.append(address[0])
        return real_server(address, handler)
    monkeypatch.setattr(bridge, '_Server', spy)
    b = bridge.ExtensionBridge(enqueue=lambda c: 'j', work_status=lambda w: {}, author_status=lambda a: {}, port=0)
    b.start()
    b.close()
    assert seen == ['127.0.0.1']


def test_cookie_receipt_only_schedules_the_gated_check():
    owner = _Owner()
    owner._extension_cookie_received()
    assert owner.cookie_checks == [True]       # 取得・更新と同じゲート経由の確認だけ


def test_unconfigured_bridge_status_label_mentions_setting():
    from pixivvault_nicegui.ui.preferences_view import EXTENSION_STATUS_LABELS
    assert '設定で停止' in EXTENSION_STATUS_LABELS['setting_off']


# ---- profile 起動（launch_work.ps1 と同じ引数の形）-----------------------------

def _configured_profile(tmp_path):
    from pixivvault_nicegui.native_profile import NativeProfileStore, provision_storage
    from test_stage5_r1_native_storage_binding import _cookie
    profile = tmp_path / 'profile-native'
    parent = tmp_path / 'save-parent'
    parent.mkdir()
    cookie = _cookie(tmp_path)
    store = NativeProfileStore(profile)
    try:
        store.update(cookie_file=cookie, save_parent=parent, expected_revision=0)
        provision_storage(store.snapshot(), profile)
    finally:
        store.close()
    return profile


def test_profile_launch_enables_bridge_by_default(tmp_path):
    profile = _configured_profile(tmp_path)
    # launch_work.ps1: --host 127.0.0.1 --port N --native --profile-root <dir>（--extension-bridge 無し）
    config = parse_args(['--host', '127.0.0.1', '--port', '22999', '--native', '--profile-root', str(profile)])
    assert config.pixiv_download_root is not None
    assert config.extension_bridge is False and config.extension_bridge_auto is True


def test_unconfigured_profile_launch_has_no_storage_but_receives_cookies(tmp_path):
    from pixivvault_nicegui.native_profile import NativeProfileStore
    profile = tmp_path / 'fresh-profile'
    NativeProfileStore(profile).close()
    config = parse_args(['--native', '--profile-root', str(profile)])
    assert config.pixiv_download_root is None
    assert config.extension_bridge is False and config.extension_bridge_auto is True


def test_uri_relaunch_does_not_force_the_receiver_on():
    import inspect
    from pixivvault_nicegui import bootstrap
    source = inspect.getsource(bootstrap.main)
    assert "'--extension-bridge'" not in source      # URI 起動も設定（受信を止める）に従う


# ---- checker 指摘 1・2・5 -------------------------------------------------------

def test_port_answered_by_another_app_is_not_taken(monkeypatch):
    # 元アプリは 0.0.0.0:25010 に bind する。127.0.0.1 の bind は成功してしまい、指示が黙って流れるため、
    # 起動前に接続を試し、応答があれば起動しない。
    monkeypatch.setattr(bridge, '_port_answers', lambda port: True)
    b = bridge.ExtensionBridge(enqueue=lambda c: 'j', work_status=lambda w: {}, author_status=lambda a: {})
    with pytest.raises(bridge.PortInUse):
        b.start()
    assert b._server is None


def test_probe_is_skipped_for_ephemeral_port(monkeypatch):
    monkeypatch.setattr(bridge, '_port_answers', lambda port: pytest.fail('probe must not run'))
    monkeypatch.setattr(bridge, '_Server', _FakeServer)
    b = bridge.ExtensionBridge(enqueue=lambda c: 'j', work_status=lambda w: {}, author_status=lambda a: {}, port=0)
    b.start()
    b.close()


def test_service_reports_port_in_use(monkeypatch):
    monkeypatch.setattr(bridge, '_port_answers', lambda port: True)
    owner = _Owner()
    owner.start_extension_bridge()
    assert owner.extension_bridge_status == 'in_use'
    from pixivvault_nicegui.ui.preferences_view import EXTENSION_STATUS_LABELS
    assert '他のアプリがポートを使用中' in EXTENSION_STATUS_LABELS['in_use']


def _services_for_prepare(root_exists, fail):
    class Root:
        def __init__(self): self.exists_value = root_exists
        def __truediv__(self, name): return self
        def exists(self): return self.exists_value

    def enable():
        if fail:
            raise ValueError('ledger')
    return SimpleNamespace(managed_downloads=SimpleNamespace(root=Root()), enable_extension_commands=enable,
                           extension_bridge_status='setting_off')


def test_existing_ledger_failure_still_stops_startup():
    from pixivvault_nicegui.bootstrap import prepare_extension_receiver
    services = _services_for_prepare(True, True)
    with pytest.raises(ValueError):
        prepare_extension_receiver(services, False, True, False)      # 設定OFFでも既存台帳が壊れていれば止める
    assert services.extension_bridge_status == 'setting_off'


def test_new_ledger_failure_does_not_block_default_start_and_is_not_called_port_problem():
    from pixivvault_nicegui.bootstrap import prepare_extension_receiver
    services = _services_for_prepare(False, True)
    assert prepare_extension_receiver(services, False, True, True) is False
    assert services.extension_bridge_status == 'commands_unavailable'
    from pixivvault_nicegui.ui.preferences_view import EXTENSION_STATUS_LABELS
    assert 'ポート' not in EXTENSION_STATUS_LABELS['commands_unavailable']


def test_explicit_flag_failure_still_raises():
    from pixivvault_nicegui.bootstrap import prepare_extension_receiver
    with pytest.raises(ValueError):
        prepare_extension_receiver(_services_for_prepare(False, True), True, False, True)


# ---- FL-N03: POST /api/cookie/sync の結線 ---------------------------------------

SECRET = '55_secretvalue_must_not_leak'


def _cookie_rows(value=SECRET):
    import time
    return [{'name': 'PHPSESSID', 'value': value, 'domain': '.pixiv.net', 'path': '/',
             'secure': True, 'expirationDate': int(time.time()) + 86400}]


class _SyncOwner(_Owner):
    _extension_cookie_sync = ApplicationServices._extension_cookie_sync

    def __init__(self, binding):
        super().__init__()
        self.native_profile = binding
        self.native_profile.request_cookie_check = lambda imported=True: self.cookie_checks.append(imported)


def _binding(tmp_path, with_factory=True):
    from pixivvault_nicegui.native_profile import NativeProfileStore
    from pixivvault_nicegui.native_startup import NativeProfileBinding
    from pixivvault_nicegui.extension_cookie_store import ExtensionCookieStore
    store = NativeProfileStore(tmp_path / 'profile-native')
    binding = NativeProfileBinding(store)
    if with_factory:
        credentials = tmp_path / 'profile-native-credentials'
        binding.cookie_store_factory = lambda: ExtensionCookieStore(credentials, profile=store)
    return binding, store


def _start(monkeypatch, owner):
    real = bridge.ExtensionBridge
    monkeypatch.setattr(bridge, 'ExtensionBridge', lambda **kw: real(port=0, **kw))
    monkeypatch.setattr(bridge, '_Server', _FakeServer)
    owner.start_extension_bridge()
    return owner.extension_bridge


def test_cookie_sync_is_not_offered_without_a_profile_store(monkeypatch, tmp_path):
    binding, store = _binding(tmp_path, with_factory=False)
    owner = _SyncOwner(binding)
    b = _start(monkeypatch, owner)
    try:
        assert _call(b, 'POST', '/api/cookie/sync', {'cookies': _cookie_rows()})[0] == 404
    finally:
        b.close(); binding.close()


def test_cookie_sync_saves_to_profile_and_triggers_gated_check(monkeypatch, tmp_path):
    binding, store = _binding(tmp_path)
    owner = _SyncOwner(binding)
    b = _start(monkeypatch, owner)
    try:
        status, body = _call(b, 'POST', '/api/cookie/sync', {'cookies': _cookie_rows()})
        assert status == 200 and body['saved'] is True and body['state'] == 'pending'
        assert SECRET not in json.dumps(body)
        saved = store.snapshot().cookie_file
        assert saved is not None and 'profile-native-credentials' in str(saved)
        assert SECRET in saved.read_text(encoding='utf-8')      # 保存先には保存される
        assert owner.cookie_checks == [True]                    # 保存後に認証チェック（ゲート経由）を依頼
        first = saved
        status, body = _call(b, 'POST', '/api/cookie/sync', {'cookies': _cookie_rows()})
        assert status == 200 and body['state'] == 'unchanged'
        assert store.snapshot().cookie_file == first
    finally:
        b.close(); binding.close()


def test_rejected_cookie_never_echoes_values(monkeypatch, tmp_path):
    binding, store = _binding(tmp_path)
    owner = _SyncOwner(binding)
    b = _start(monkeypatch, owner)
    try:
        rows = _cookie_rows()
        rows[0]['domain'] = ''
        status, body = _call(b, 'POST', '/api/cookie/sync', {'cookies': rows})
        assert status in (400, 409, 503) and SECRET not in json.dumps(body)
        assert store.snapshot().cookie_file is None and owner.cookie_checks == []
    finally:
        b.close(); binding.close()


# ---- 最初の Cookie 連携（Cookie 未設定の profile 起動でも受信する）----------------------

def test_unconfigured_profile_still_receives_for_cookie_sync(tmp_path):
    from pixivvault_nicegui.native_profile import NativeProfileStore
    profile = tmp_path / 'fresh-profile'
    NativeProfileStore(profile).close()
    config = parse_args(['--native', '--profile-root', str(profile)])
    assert config.pixiv_download_root is None
    assert config.extension_bridge_auto is True       # 保存先が無くても、Cookie を受けるために起動する


def test_bridge_without_managed_queue_answers_ping_and_cookie_but_refuses_commands(monkeypatch, tmp_path):
    binding, store = _binding(tmp_path)
    owner = _SyncOwner(binding)
    owner.extension_commands = None
    b = _start(monkeypatch, owner)
    try:
        assert owner.extension_bridge_status == 'running'
        assert _call(b, 'GET', '/ping')[0] == 200
        assert _call(b, 'POST', '/download', {'type': 'work', 'work_id': '1'})[0] == 503
        assert _call(b, 'GET', '/api/work/1')[0] == 503
        assert _call(b, 'POST', '/api/cookie/sync', {'cookies': _cookie_rows()})[0] == 200
    finally:
        b.close(); binding.close()


def test_without_queue_or_profile_store_nothing_starts(monkeypatch):
    owner = _Owner()
    owner.extension_commands = None
    owner.start_extension_bridge()
    assert owner.extension_bridge is None


def test_setting_is_offered_for_profile_launch_without_storage():
    profile = SimpleNamespace(cookie_store_factory=lambda: None)
    assert ApplicationServices.extension_setting_available(SimpleNamespace(managed_downloads=None, native_profile=profile))


def test_cookie_banner_claim_matches_receiver_state():
    from pixivvault_nicegui.ui.shell import cookie_banner_second_line
    assert cookie_banner_second_line('running') == 'ブラウザでPixivを開くだけで自動的に連携されます。'
    for state in ('in_use', 'setting_off', 'unavailable', 'disabled', 'commands_unavailable'):
        text = cookie_banner_second_line(state)
        assert '自動的に連携されます' not in text and 'cookies.txt' in text
