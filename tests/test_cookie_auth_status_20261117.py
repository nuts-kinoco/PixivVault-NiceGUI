"""Header login status, startup check, post-save check and ranking first fetch.

Restores the original Flet behaviour (gui.py check_login_status, ranking_tab
on_tab_open). No network: the reader is a fake and cookie files hold dummy data.
"""
import asyncio
import time
from types import SimpleNamespace

import pytest
from nicegui import Client, ui

from pixivvault_nicegui import auth_status as A
from pixivvault_nicegui.native_auth_result import NativeAuthResult
from pixivvault_nicegui.native_profile import NativeProfileStore
from pixivvault_nicegui.native_startup import NativeProfileBinding
from pixivvault_nicegui.ranking_source import RankingSource
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.ranking_view import RankingView
from test_ranking_source import Reader


def cookie_file(tmp_path, days):
    (tmp_path / 'cred').mkdir(exist_ok=True)
    path = tmp_path / 'cred' / 'cookies.txt'
    expires = int(time.time() + days * 86400 - 60) if days else 0
    path.write_text('# Netscape HTTP Cookie File\n'
                    f'.pixiv.net\tTRUE\t/\tTRUE\t{expires}\tPHPSESSID\tdummy\n', encoding='utf-8')
    return path


def verified():
    return NativeAuthResult('verified')


# ---- pure mapping -------------------------------------------------------

@pytest.mark.parametrize('days,level,prefix,color', [
    (30, 'valid', '● [有効] ログイン中 (有効期限 残り約30日) - ユーザーID: 777', A.GREEN),
    (8, 'valid', '● [有効] ', A.GREEN),
    (7, 'warning_yellow', '▲ [注意/残り1週間以内] ', A.YELLOW),
    (4, 'warning_yellow', '▲ [注意/残り1週間以内] ', A.YELLOW),
    (3, 'warning_red', '▲ [警告/残り3日以内] ', A.RED),
    (1, 'warning_red', '▲ [警告/残り3日以内] ', A.RED),
])
def test_verified_status_by_days_left(days, level, prefix, color):
    status = A.status_from_result(verified(), '777', days)
    assert (status.level, status.color, status.banner, status.ok) == (level, color, False, True)
    assert status.text.startswith(prefix)
    assert '777' in status.text


def test_failure_states_follow_original_labels_and_banner():
    comm = A.status_from_result(NativeAuthResult('communication_failed'))
    assert comm.text.startswith('▲ [通信エラー] ') and comm.color == A.YELLOW and not comm.banner
    for code in ('identity_unverified', 'remote_denied', 'cookie_unreadable'):
        status = A.status_from_result(NativeAuthResult(code))
        assert status.text.startswith('× [未認証/期限切れ] ') and status.color == A.RED and status.banner
    missing = A.missing_status()
    assert missing.text.startswith('× [未認証/期限切れ] ') and missing.banner


@pytest.mark.parametrize('code', ['stopped', 'uncertain', 'busy', 'owner_absent',
                                  'local_unavailable', 'rate_limited', 'closed'])
def test_stop_states_show_reason_without_claiming_invalid_cookie(code):
    status = A.status_from_result(NativeAuthResult(code))
    assert status.level == 'stopped' and not status.banner and '未認証' not in status.text
    assert NativeAuthResult(code).message in status.text


def test_initial_label_text_is_original():
    assert A.AuthStatusHolder().status.text == 'ログインチェック中...'


def test_days_left_uses_original_rules(tmp_path):
    assert A.cookie_days_left(cookie_file(tmp_path, 5)) == 5
    assert A.cookie_days_left(cookie_file(tmp_path, 2)) == 2
    assert A.cookie_days_left(cookie_file(tmp_path, 0)) == 30  # no expiry: original default
    assert A.cookie_days_left(tmp_path / 'absent.txt') == 30


# ---- binding: every authentication check feeds the header ---------------

class FakeReader:
    instances = []

    def __init__(self, path):
        self.path = path
        self.closed = False
        FakeReader.instances.append(self)

    def set_retry_policy(self, policy):
        pass

    def require_identity(self):
        return '777'

    def close(self):
        self.closed = True


def make_binding(tmp_path, monkeypatch, *, cookie_days=20, configured=True):
    FakeReader.instances = []
    monkeypatch.setattr('pixivvault_nicegui.pixiv_read_client.LivePixivReadClient', FakeReader)
    store = NativeProfileStore(tmp_path / 'profile')
    if configured:
        cookie = cookie_file(tmp_path, cookie_days)
        store.update(cookie_file=cookie, expected_revision=store.snapshot().revision)
    return NativeProfileBinding(store), store


@pytest.mark.asyncio
async def test_check_authentication_publishes_valid_header_with_uid_and_days(tmp_path, monkeypatch):
    binding, store = make_binding(tmp_path, monkeypatch)
    seen = []
    binding.auth_status.subscribe(lambda status, notice: seen.append((status, notice)))
    result = await binding.check_authentication(store.snapshot().cookie_file)
    assert result == NativeAuthResult('verified')  # return contract unchanged
    status = binding.auth_status.status
    assert status.level == 'valid' and 'ユーザーID: 777' in status.text and '残り約20日' in status.text
    assert seen[-1][1] is None  # explicit button never raises the update notice
    binding.close()


@pytest.mark.asyncio
async def test_refresh_after_save_notifies_updated(tmp_path, monkeypatch):
    binding, _ = make_binding(tmp_path, monkeypatch)
    seen = []
    binding.auth_status.subscribe(lambda status, notice: seen.append((status, notice)))
    await binding.refresh_auth_status(imported=True)
    assert seen[-1][0].level == 'valid' and seen[-1][1] == 'Cookie を確認・更新しました。'
    binding.close()


@pytest.mark.asyncio
async def test_no_cookie_means_no_request_and_banner_status(tmp_path, monkeypatch):
    binding, _ = make_binding(tmp_path, monkeypatch, configured=False)
    await binding.refresh_auth_status()
    assert FakeReader.instances == []
    assert binding.auth_status.status.banner and binding.auth_status.status.text.startswith('× [未認証/期限切れ]')
    binding.close()


@pytest.mark.asyncio
async def test_stopped_check_shows_reason_and_sends_nothing(tmp_path, monkeypatch):
    binding, _ = make_binding(tmp_path, monkeypatch)
    monkeypatch.setattr(binding, '_verify_auth_result', lambda path, stopped: (NativeAuthResult('stopped'), None))
    await binding.refresh_auth_status()
    assert FakeReader.instances == []
    assert binding.auth_status.status.level == 'stopped'
    assert NativeAuthResult('stopped').message in binding.auth_status.status.text
    binding.close()


@pytest.mark.asyncio
async def test_startup_check_runs_once_and_only_for_stop_gated_profiles(tmp_path, monkeypatch):
    binding, _ = make_binding(tmp_path, monkeypatch)
    assert binding.start_startup_auth_check() is None  # legacy fixture binding: never auto-connect
    assert FakeReader.instances == []
    binding.auth_stop_required = True
    calls = []
    original = binding._check_authentication_with_identity

    async def counted(path, **kw):
        calls.append(path)
        return await original(path, **kw)
    monkeypatch.setattr(binding, '_check_authentication_with_identity', counted)
    monkeypatch.setattr(binding, '_verify_auth_result', lambda path, stopped: (NativeAuthResult('stopped'), None))
    task = binding.start_startup_auth_check()
    assert task is not None and binding.start_startup_auth_check() is task
    await task
    assert len(calls) == 1 and binding.auth_status.status.level == 'stopped'
    assert binding.start_startup_auth_check() is task and len(calls) == 1
    binding.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('closing', (False, True))
async def test_startup_auth_waits_for_discovery_warmup(tmp_path, monkeypatch, closing):
    binding, _ = make_binding(tmp_path, monkeypatch)
    binding.auth_stop_required = True
    discovery = asyncio.get_running_loop().create_future()
    services = SimpleNamespace(_startup_discovery_task=discovery, closing=False)
    binding._shared_event_services = services
    checks = []

    async def refresh(*, startup=False):
        checks.append((startup, discovery.done()))

    monkeypatch.setattr(binding, 'refresh_auth_status', refresh)
    try:
        auth = binding.start_startup_auth_check()
        await asyncio.sleep(0)
        assert not checks
        services.closing = closing
        discovery.set_result(None)
        await auth
        assert checks == ([] if closing else [(True, True)])
    finally:
        if not discovery.done():
            discovery.cancel()
        binding.close()


@pytest.mark.asyncio
async def test_concurrent_requests_are_coalesced_but_final_state_is_fresh(tmp_path, monkeypatch):
    binding, _ = make_binding(tmp_path, monkeypatch)
    await asyncio.gather(*(binding.refresh_auth_status(imported=True) for _ in range(3)))
    assert 1 <= len(FakeReader.instances) <= 2
    assert binding.auth_status.status.level == 'valid'
    binding.close()


@pytest.mark.asyncio
async def test_threadsafe_request_from_bridge_thread_runs_check(tmp_path, monkeypatch):
    binding, _ = make_binding(tmp_path, monkeypatch)
    seen = []
    binding.auth_status.subscribe(lambda status, notice: seen.append(notice))
    binding.bind_loop(asyncio.get_running_loop())
    await asyncio.to_thread(binding.request_cookie_check)  # as the extension bridge thread does
    for _ in range(100):
        if seen:
            break
        await asyncio.sleep(.02)
    assert seen and seen[-1] == 'Cookie を確認・更新しました。'
    binding.close()


# ---- shell header -----------------------------------------------------------

@pytest.mark.asyncio
async def test_shell_header_label_and_banner_follow_holder(tmp_path, monkeypatch):
    from pixivvault_nicegui.ui.shell import DemoShell
    binding, _ = make_binding(tmp_path, monkeypatch)
    services = ApplicationServices(1)
    services.native_profile = binding
    client = Client(ui.page('/_auth_header_'))
    try:
        with client:
            shell = DemoShell(services)
            assert shell.auth_label.text == 'ログインチェック中...'
            assert not shell.cookie_banner.visible
            binding.auth_status.publish(A.status_from_result(verified(), '777', 20))
            assert shell.auth_label.text.startswith('● [有効] ') and not shell.cookie_banner.visible
            binding.auth_status.publish(A.missing_status())
            assert shell.cookie_banner.visible and shell.auth_label.text.startswith('× [未認証/期限切れ]')
            shell.close_cookie_banner()
            assert not shell.cookie_banner.visible
            shell.close()
        assert binding.auth_status._listeners == []
    finally:
        client.delete()
        services.close()
        binding.close()


@pytest.mark.asyncio
async def test_shell_without_binding_has_no_auth_label():
    from pixivvault_nicegui.ui.shell import DemoShell
    services = ApplicationServices(1)
    client = Client(ui.page('/_auth_header_none_'))
    try:
        with client:
            shell = DemoShell(services)
            assert shell.auth_label is None
            shell.close()
    finally:
        client.delete()
        services.close()


# ---- ranking: first open fetches once -------------------------------------------

def ranking_setup(live=True):
    services = ApplicationServices(1)
    reader = Reader()
    services.ranking_source = RankingSource(reader)
    if live:
        class Live:
            def close(self):
                pass
        services.live_client = Live()

    class Pin:
        closed = False

        def url(self, *a, **k):
            return '/x.png'

        def can_open(self, wid):
            return True

        def needs_metadata(self, wid):
            return False

        def close(self):
            self.closed = True
    services.acquire_ranking_images = lambda records: Pin()
    return services, reader


def rankings(reader):
    return sum(1 for call in reader.calls if call[0] == 'ranking')


async def settle(view):
    for _ in range(200):
        task = view._auto_task
        if task is not None and task.done():
            return
        await asyncio.sleep(.01)


@pytest.mark.asyncio
async def test_first_activation_fetches_once_only(tmp_path):
    services, reader = ranking_setup()
    client = Client(ui.page('/_ranking_auto_'))
    try:
        with client:
            view = RankingView(services)
            view.set_active(True)
            await settle(view)
            assert rankings(reader) == 1 and view.page is not None
            view.set_active(False)
            view.set_active(True)
            await settle(view)
            await asyncio.sleep(.05)
            assert rankings(reader) == 1
            view.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_no_auto_fetch_without_live_reader():
    services, reader = ranking_setup(live=False)
    client = Client(ui.page('/_ranking_noauto_'))
    try:
        with client:
            view = RankingView(services)
            view.set_active(True)
            await asyncio.sleep(.05)
            assert reader.calls == [] and view.page is None
            view.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_auto_fetch_goes_through_the_same_load_gate(tmp_path, monkeypatch):
    """A stopped gate surfaces its reason in the notice, exactly like the button."""
    services, reader = ranking_setup()

    class Stopped(Exception):
        code = 'shared-get-outcome-uncertain'
    def boom(*a, **k):
        raise Stopped()
    services.ranking_source.fetch = boom
    client = Client(ui.page('/_ranking_gate_'))
    try:
        with client:
            view = RankingView(services)
            view.set_active(True)
            await settle(view)
            assert view.page is None and view.notice.text != '取得中…'
            view.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_auto_fetch_waits_for_startup_auth_check(tmp_path, monkeypatch):
    services, reader = ranking_setup()
    binding, _ = make_binding(tmp_path, monkeypatch)
    services.native_profile = binding
    gate = asyncio.get_running_loop().create_future()
    binding.startup_auth_task = asyncio.ensure_future(gate)
    client = Client(ui.page('/_ranking_wait_'))
    try:
        with client:
            view = RankingView(services)
            view.set_active(True)
            await asyncio.sleep(.1)
            assert rankings(reader) == 0  # auth check first, then the ranking GET
            gate.set_result(None)
            await settle(view)
            assert rankings(reader) == 1
            view.close()
    finally:
        client.delete()
        services.close()
        binding.close()


# ---- extension bridge hook ------------------------------------------------------

class _Server:
    def __init__(self, address, handler):
        self.handler = handler
        self.server_port = 12345

    def serve_forever(self, **kw):
        pass

    def shutdown(self):
        pass

    def server_close(self):
        pass


def _post_cookie(monkeypatch, cookie_sync, hook):
    import io
    from email.message import Message
    from pixivvault_nicegui import extension_bridge as bridge
    monkeypatch.setattr(bridge, '_Server', _Server)
    b = bridge.ExtensionBridge(enqueue=lambda c: 'job', work_status=lambda w: {}, author_status=lambda a: {},
                               port=0, cookie_sync=cookie_sync, on_cookie_received=hook)
    b.start()
    handler = b._server.handler
    body = b'{"cookies":[{"name":"PHPSESSID"}]}'
    obj = handler.__new__(handler)
    obj.server = b._server
    obj.path = '/api/cookie/sync'
    obj.client_address = ('127.0.0.1', 5)
    obj.headers = Message()
    obj.headers['Origin'] = 'chrome-extension://' + 'a' * 32
    obj.headers['Host'] = '127.0.0.1:12345'
    obj.headers['Content-Length'] = str(len(body))
    obj.headers['Content-Type'] = 'application/json'
    obj.rfile = io.BytesIO(body)
    obj.wfile = io.BytesIO()
    obj.request_version = 'HTTP/1.1'
    obj.command = 'POST'
    obj.requestline = 'POST /api/cookie/sync'
    obj.do_POST()
    counts = b.response_snapshot()[0]
    b.close()
    return counts


class _Receipt:
    state = 'applied'
    saved = True


def test_bridge_calls_hook_once_after_accepted_cookie(monkeypatch):
    hooks = []
    counts = _post_cookie(monkeypatch, lambda rows: _Receipt(), lambda: hooks.append(1))
    assert counts[200] == 1 and hooks == [1]


def test_bridge_hook_not_called_when_cookie_rejected(monkeypatch):
    hooks = []

    def reject(rows):
        raise RuntimeError()
    counts = _post_cookie(monkeypatch, reject, lambda: hooks.append(1))
    assert counts[503] == 1 and hooks == []


def test_bridge_hook_failure_never_changes_the_response(monkeypatch):
    def bad():
        raise RuntimeError('boom')
    counts = _post_cookie(monkeypatch, lambda rows: _Receipt(), bad)
    assert counts[200] == 1 and counts[503] == 0


@pytest.mark.asyncio
async def test_busy_gate_is_retried_so_the_header_does_not_stay_stopped(tmp_path, monkeypatch):
    """Startup check met a briefly busy shared gate on the real app (2026-11-17) and
    the header stayed on "別の通信または設定処理が実行中です". A busy gate is transient."""
    binding, store = make_binding(tmp_path, monkeypatch)
    monkeypatch.setattr('pixivvault_nicegui.native_startup.BUSY_RETRY_DELAYS', (0.0, 0.0, 0.0))
    calls = []
    real = binding._check_authentication_with_identity

    async def flaky(cookie, **kw):
        calls.append(1)
        if len(calls) <= 2:
            result = NativeAuthResult('busy')
            binding.auth_status.publish(A.status_from_result(result))
            return result, None
        return await real(cookie, **kw)

    monkeypatch.setattr(binding, '_check_authentication_with_identity', flaky)
    await binding.refresh_auth_status(startup=True)
    assert len(calls) == 3
    assert binding.auth_status.status.level == 'valid'
    binding.close()


@pytest.mark.asyncio
async def test_busy_gate_gives_up_after_the_bounded_retries(tmp_path, monkeypatch):
    binding, store = make_binding(tmp_path, monkeypatch)
    monkeypatch.setattr('pixivvault_nicegui.native_startup.BUSY_RETRY_DELAYS', (0.0, 0.0))
    calls = []

    async def always_busy(cookie, **kw):
        calls.append(1)
        result = NativeAuthResult('busy')
        binding.auth_status.publish(A.status_from_result(result))
        return result, None

    monkeypatch.setattr(binding, '_check_authentication_with_identity', always_busy)
    await binding.refresh_auth_status(startup=True)
    assert len(calls) == 3  # first try + two retries, then it stops
    assert binding.auth_status.status.text.startswith('▲ [通信停止中]')
    binding.close()
