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
    none = SimpleNamespace(managed_downloads=None)
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


def test_unconfigured_profile_launch_starts_nothing(tmp_path):
    from pixivvault_nicegui.native_profile import NativeProfileStore
    profile = tmp_path / 'fresh-profile'
    NativeProfileStore(profile).close()
    config = parse_args(['--native', '--profile-root', str(profile)])
    assert config.pixiv_download_root is None
    assert config.extension_bridge is False and config.extension_bridge_auto is False


def test_uri_relaunch_does_not_force_the_receiver_on():
    import inspect
    from pixivvault_nicegui import bootstrap
    source = inspect.getsource(bootstrap.main)
    assert "'--extension-bridge'" not in source      # URI 起動も設定（受信を止める）に従う
