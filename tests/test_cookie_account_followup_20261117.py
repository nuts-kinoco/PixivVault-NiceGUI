"""FL-A09 (different-account confirmation) and FL-A10 (24 h automatic following sync).

Fake reader and a real FollowingSyncStore in tmp_path; no network, no real browser.
Every user-visible string checked here is the original gui.py wording.
"""
import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest

import pixivvault_nicegui.bootstrap  # noqa: F401  (import real owners before any test patches the reader class)
import pixivvault_nicegui.managed_network_stop_integration  # noqa: F401
import pixivvault_nicegui.managed_network_stop_reader  # noqa: F401
import pixivvault_nicegui.managed_network_stop_shared_transport  # noqa: F401
from account_test_support import observed_scope
from pixivvault_nicegui.following_sync import FollowingSyncStore
from pixivvault_nicegui.native_profile import NativeProfileStore
from pixivvault_nicegui.native_startup import NativeProfileBinding
from pixivvault_nicegui.pixiv_read_client import AuthenticationMissing
from test_cookie_auth_status_20261117 import cookie_file

SWITCH_TITLE = '別アカウントのCookieを検出しました'


def switch_body(uid):
    return (f'現在保存されているフォロー中データ（お気に入り・ダウンロード状況などを含む）を'
            f'すべて削除し、アカウント（ID: {uid}）の情報で再取得します。'
            'この操作は元に戻せません。よろしいですか？')


class FakeAuthReader:
    """The header-check reader (LivePixivReadClient replacement)."""
    uid = '777'
    invalid = False

    def __init__(self, path):
        pass

    def set_retry_policy(self, policy):
        pass

    def require_identity(self):
        if FakeAuthReader.invalid:
            raise AuthenticationMissing()
        return FakeAuthReader.uid

    def close(self):
        pass


class FakeFollowReader:
    """The following-list reader a gated factory would build for one run."""
    runs = []

    def __init__(self, uid, shown=('11', '12'), hidden=('21',)):
        self.uid, self.shown, self.hidden = uid, shown, hidden
        self.closed = False
        FakeFollowReader.runs.append(self)

    def set_retry_policy(self, policy):
        pass

    def require_identity(self):
        self._verified_identity = self.uid
        return self.uid

    def get_following_users_detailed(self, account_id, rest_type='show'):
        ids = self.shown if rest_type == 'show' else self.hidden
        return [{'user_id': i, 'name': f'name{i}', 'account': f'acct{i}', 'profile_img': ''} for i in ids]

    def close(self):
        self.closed = True


def make(tmp_path, monkeypatch, *, uid='777', reader_factory='default', with_store=True):
    FakeAuthReader.uid, FakeAuthReader.invalid = uid, False
    FakeFollowReader.runs = []
    monkeypatch.setattr('pixivvault_nicegui.pixiv_read_client.LivePixivReadClient', FakeAuthReader)
    profile = NativeProfileStore(tmp_path / 'profile')
    profile.update(cookie_file=cookie_file(tmp_path, 20), expected_revision=profile.snapshot().revision)
    binding = NativeProfileBinding(profile)
    following = FollowingSyncStore(tmp_path / 'follow') if with_store else None
    followup = binding.followup
    followup.busy_retry_delay = 0
    followup.store_provider = (lambda: following) if with_store else None
    followup.reader_factory = ((lambda cookie: FakeFollowReader(uid)) if reader_factory == 'default'
                               else reader_factory)
    events = []
    followup.subscribe(lambda kind, payload: events.append((kind, payload)))
    return binding, following, events


async def go(binding, **kw):
    await binding.refresh_auth_status(**kw)


def texts(events):
    return [payload['text'] for kind, payload in events if kind == 'notice']


def age(following, uid, **delta):
    stamp = (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()
    following._connection.execute('UPDATE following_accounts SET last_synced_at=? WHERE account_id=?',
                                  (stamp, uid))


def seed_account(following, uid, ids=('1', '2'), hours_ago=2):
    class Seed:
        def require_identity(self):
            return uid

        def get_following_users_detailed(self, account_id, rest_type='show'):
            return ([{'user_id': i, 'name': f'old{i}', 'account': f'a{i}', 'profile_img': ''}
                     for i in ids] if rest_type == 'show' else [])
    following.sync(Seed())
    following._connection.execute(
        'UPDATE following_authors SET is_favorite=1,is_zipped=1,last_downloaded=? WHERE account_id=?',
        ('2026-11-01T00:00:00', uid))
    age(following, uid, hours=hours_ago)


# ---- store primitives ------------------------------------------------------------

def test_store_reports_the_most_recently_synced_account_and_clears_one_account(tmp_path):
    following = FollowingSyncStore(tmp_path / 'follow')
    assert following.last_synced_account() is None
    seed_account(following, '100', hours_ago=30)
    seed_account(following, '200', hours_ago=5)
    assert following.last_synced_account() == '200'
    following.clear_account('200', scope=observed_scope('200'))
    assert following.snapshot('200').authors == () and following.snapshot('200').last_synced_at is None
    assert len(following.snapshot('100').authors) == 2          # other accounts are untouched
    assert following.last_synced_account() == '100'
    following.close()


# ---- A10: 24 hour automatic sync ---------------------------------------------------

@pytest.mark.asyncio
async def test_first_confirmed_check_syncs_public_and_private_following(tmp_path, monkeypatch):
    binding, following, events = make(tmp_path, monkeypatch)
    await go(binding)
    snapshot = following.snapshot('777')
    assert sorted((a.user_id, a.restrict) for a in snapshot.following) == [('11', 0), ('12', 0), ('21', 1)]
    assert snapshot.last_synced_at is not None
    assert '起動時自動同期完了 (3人の作者)' in texts(events)
    assert all(run.closed for run in FakeFollowReader.runs)
    binding.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('hours,expected', [(23, 0), (25, 1)])
async def test_sync_only_when_more_than_24_hours_have_passed(tmp_path, monkeypatch, hours, expected):
    binding, following, events = make(tmp_path, monkeypatch)
    seed_account(following, '777', hours_ago=hours)
    await go(binding)
    assert len(FakeFollowReader.runs) == expected
    binding.close()


@pytest.mark.asyncio
async def test_settings_button_check_never_starts_a_sync(tmp_path, monkeypatch):
    binding, following, events = make(tmp_path, monkeypatch)
    await binding.check_authentication(binding.store.snapshot().cookie_file)
    assert FakeFollowReader.runs == []
    binding.close()


@pytest.mark.asyncio
async def test_no_sync_when_the_cookie_is_not_confirmed(tmp_path, monkeypatch):
    binding, following, events = make(tmp_path, monkeypatch)
    FakeAuthReader.invalid = True
    await go(binding)
    assert FakeFollowReader.runs == [] and following.last_synced_account() is None
    binding.close()


@pytest.mark.asyncio
async def test_no_store_or_no_gated_reader_means_no_request(tmp_path, monkeypatch):
    binding, following, events = make(tmp_path, monkeypatch, with_store=False)
    await go(binding)
    assert FakeFollowReader.runs == []
    binding.close()
    (tmp_path / 'b').mkdir()
    binding, following, events = make(tmp_path / 'b', monkeypatch, reader_factory=None)
    binding.followup.reader_factory = None   # default opener: not stop-gated -> refuses
    await go(binding)
    assert following.last_synced_account() is None
    binding.close()


@pytest.mark.asyncio
async def test_a_stopped_gate_keeps_saved_data_and_reports_failure(tmp_path, monkeypatch):
    from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopped
    binding, following, events = make(tmp_path, monkeypatch)
    seed_account(following, '777', hours_ago=30)

    def stopped(cookie):
        raise ManagedNetworkStopped(429)
    binding.followup.reader_factory = stopped
    await go(binding)
    assert len(following.snapshot('777').authors) == 2        # unchanged
    assert any(t.startswith('自動同期失敗') for t in texts(events))
    binding.close()


@pytest.mark.asyncio
async def test_concurrent_checks_run_one_sync(tmp_path, monkeypatch):
    binding, following, events = make(tmp_path, monkeypatch)
    await asyncio.gather(binding.refresh_auth_status(), binding.refresh_auth_status(imported=True))
    assert len(FakeFollowReader.runs) == 1
    binding.close()


# ---- A09: different account ----------------------------------------------------------

@pytest.mark.asyncio
async def test_other_account_asks_first_and_changes_nothing(tmp_path, monkeypatch):
    binding, following, events = make(tmp_path, monkeypatch, uid='888')
    seed_account(following, '777', hours_ago=40)
    await go(binding)
    pending = binding.followup.pending
    assert (pending.new_account, pending.old_account) == ('888', '777')
    assert [kind for kind, _ in events if kind == 'account_switch'] == ['account_switch']
    assert FakeFollowReader.runs == []                                  # no sync before the answer
    assert len(following.snapshot('777').authors) == 2                  # nothing deleted
    assert following.snapshot('888').authors == ()
    binding.close()


@pytest.mark.asyncio
async def test_cancel_keeps_every_local_flag_and_suppresses_the_dialog(tmp_path, monkeypatch):
    binding, following, events = make(tmp_path, monkeypatch, uid='888')
    seed_account(following, '777', hours_ago=40)
    before = following.snapshot('777')
    await go(binding)
    binding.followup.cancel_account_switch()
    assert binding.followup.pending is None
    after = following.snapshot('777')
    assert after == before
    assert all(a.is_favorite and a.is_zipped and a.last_downloaded for a in after.authors)
    assert 'キャンセル' in ' '.join(texts(events)) or '別アカウントのCookieを検出しましたが、フォロー一覧の削除をキャンセルしました。' in texts(events)
    switches = [1 for kind, _ in events if kind == 'account_switch']
    await go(binding, imported=True)                     # Cookie saved again
    assert [1 for kind, _ in events if kind == 'account_switch'] == switches   # not shown again
    assert FakeFollowReader.runs == [] and following.snapshot('888').authors == ()
    binding.close()


@pytest.mark.asyncio
async def test_cancel_is_remembered_in_memory_only(tmp_path, monkeypatch):
    binding, following, events = make(tmp_path, monkeypatch, uid='888')
    seed_account(following, '777', hours_ago=40)
    await go(binding)
    binding.followup.cancel_account_switch()
    FakeAuthReader.uid = '999'                                            # yet another account
    binding.followup.reader_factory = lambda cookie: FakeFollowReader('999')
    await go(binding)
    assert binding.followup.pending is not None and binding.followup.pending.new_account == '999'
    binding.close()
    # a new process (new binding, same data) asks again
    FakeAuthReader.uid = '888'
    profile = NativeProfileStore(tmp_path / 'profile')
    again = NativeProfileBinding(profile)
    again.followup.store_provider = lambda: following
    again.followup.reader_factory = lambda cookie: FakeFollowReader('888')
    await go(again)
    assert again.followup.pending is not None
    again.close()


@pytest.mark.asyncio
async def test_confirm_deletes_only_the_old_account_then_resyncs(tmp_path, monkeypatch):
    binding, following, events = make(tmp_path, monkeypatch, uid='888')
    seed_account(following, '777', hours_ago=40)
    await go(binding)
    await binding.followup.confirm_account_switch()
    assert following.snapshot('777').authors == ()
    assert len(following.snapshot('888').following) == 3
    assert binding.followup.pending is None
    assert ('別アカウント(ID: 888)のCookieを読み込みました。旧フォロー中データを削除・再取得します。'
            in texts(events))
    assert '起動時自動同期完了 (3人の作者)' in texts(events)
    await binding.followup.confirm_account_switch()                       # a second click is a no-op
    assert len(FakeFollowReader.runs) == 1
    binding.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ('observed_identity', 'latest_sync'))
async def test_account_switch_rejects_stale_confirmation(tmp_path, monkeypatch, change):
    factory = (lambda _: FakeFollowReader('999')) if change == 'observed_identity' else 'default'
    binding, following, events = make(tmp_path, monkeypatch, uid='888', reader_factory=factory)
    try:
        seed_account(following, '777', hours_ago=40)
        await go(binding)
        assert binding.followup.pending.old_account == '777'
        if change == 'latest_sync':
            seed_account(following, '999')
        before = tuple(following._connection.iterdump())
        await binding.followup.confirm_account_switch()
        assert tuple(following._connection.iterdump()) == before
        assert any('削除していません' in line for line in texts(events))
    finally:
        binding.close()
        following.close()


@pytest.mark.asyncio
async def test_no_previous_account_and_same_account_do_not_ask(tmp_path, monkeypatch):
    binding, following, events = make(tmp_path, monkeypatch)
    await go(binding)                                   # first ever: just sync
    assert binding.followup.pending is None
    age(following, '777', hours=30)
    await go(binding)                                   # same account, due again
    assert binding.followup.pending is None and len(FakeFollowReader.runs) == 2
    assert not [1 for kind, _ in events if kind == 'account_switch']
    binding.close()


# ---- shell dialog ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_shell_shows_the_original_confirmation_and_routes_the_buttons(tmp_path, monkeypatch):
    from nicegui import Client, ui
    from pixivvault_nicegui.services import ApplicationServices
    from pixivvault_nicegui.ui.shell import DemoShell
    binding, following, events = make(tmp_path, monkeypatch, uid='888')
    seed_account(following, '777', hours_ago=40)
    await go(binding)           # pending before any window exists
    services = ApplicationServices(1)
    services.native_profile = binding
    client = Client(ui.page('/_account_switch_'))
    try:
        with client:
            shell = DemoShell(services)
            dialog = shell.account_switch_dialog
            assert dialog is not None and dialog.value
            labels = [e.text for e in dialog.descendants() if hasattr(e, 'text') and isinstance(e.text, str)]
            assert SWITCH_TITLE in labels and switch_body('888') in labels
            assert 'キャンセル' in labels and '削除して再取得する' in labels
            binding.followup.cancel_account_switch()
            assert not shell.account_switch_dialog or not shell.account_switch_dialog.value
            assert len(following.snapshot('777').authors) == 2
            shell.close()
        assert len(binding.followup._listeners) == 1  # only the test's own listener remains
    finally:
        client.delete()
        services.close()
        binding.close()


# ---- review fixes: pacing, retry, running confirm, wiring ----------------------------

@pytest.mark.asyncio
async def test_a_busy_gate_attempt_is_retried_and_the_list_changes_once(tmp_path, monkeypatch):
    binding, following, events = make(tmp_path, monkeypatch)
    state = {'n': 0}

    def flaky(cookie):
        state['n'] += 1
        reader = FakeFollowReader('777')
        if state['n'] == 1:
            def boom(*a, **k):
                raise RuntimeError('another request holds the shared fence')
            reader.get_following_users_detailed = boom
        return reader
    binding.followup.reader_factory = flaky
    await go(binding)
    assert state['n'] == 2 and len(following.snapshot('777').following) == 3
    assert texts(events).count('起動時自動同期完了 (3人の作者)') == 1
    assert not any(t.startswith('自動同期失敗') for t in texts(events))
    binding.close()


def test_the_per_run_reader_gets_the_application_request_pacing(tmp_path, monkeypatch):
    binding, following, events = make(tmp_path, monkeypatch)
    given = []
    reader = FakeFollowReader('777')
    reader.set_request_admission = given.append
    binding.followup.admission_provider = lambda: 'shared-admission'
    binding.followup._pace(reader)
    assert given == ['shared-admission']
    binding.followup.admission_provider = None      # no pacing source: nothing is configured
    binding.followup._pace(reader)
    assert given == ['shared-admission']
    binding.close()


@pytest.mark.asyncio
async def test_confirming_while_a_sync_runs_keeps_the_dialog_and_says_why(tmp_path, monkeypatch):
    binding, following, events = make(tmp_path, monkeypatch, uid='888')
    seed_account(following, '777', hours_ago=40)
    await go(binding)
    binding.followup._running = True
    await binding.followup.confirm_account_switch()
    assert binding.followup.pending is not None
    assert '同期中です。完了してからもう一度お試しください。' in texts(events)
    assert len(following.snapshot('777').authors) == 2
    binding.followup._running = False
    binding.close()


def test_native_launch_wiring_gives_the_followup_its_store_and_pacing(tmp_path):
    import inspect
    from types import SimpleNamespace
    from pixivvault_nicegui import bootstrap
    profile = NativeProfileStore(tmp_path / 'profile')
    binding = NativeProfileBinding(profile)
    services = SimpleNamespace(following_sync='STORE', _request_admission='PACE')
    bootstrap._wire_followup(binding, services)
    assert binding.followup.store_provider() == 'STORE' and binding.followup.admission_provider() == 'PACE'
    assert '_wire_followup(native_profile, services)' in inspect.getsource(bootstrap.register_pages)
    binding.close()


# ---- the real stop-gated reader path (no GET is made) ------------------------------------

def _gated_binding(tmp_path):
    from pixivvault_nicegui.extension_cookie_store import ExtensionCookieStore
    from pixivvault_nicegui.managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
    from pixivvault_nicegui.managed_network_stop_shared_scope import ManagedUserNetworkStopScope
    from test_stage5_r1_shared_product_integration import synthetic_owner
    shared_root, cookie = synthetic_owner(tmp_path)
    shared = ManagedUserNetworkStopScope(shared_root)
    profile_root = tmp_path / 'gated-profile'
    NativeProfileStore(profile_root).close()
    scope = ManagedProfileNetworkStopScope(profile_root)
    store = NativeProfileStore(profile_root)
    store.update(cookie_file=cookie, expected_revision=store.snapshot().revision)
    binding = NativeProfileBinding(store)
    binding.auth_stop_required = binding.auth_shared_stop_required = True
    binding.managed_network_stop_scope, binding.shared_network_stop_scope = scope, shared
    return binding, store, shared, cookie, ExtensionCookieStore


def test_real_gate_gives_a_following_capable_reader_and_no_get_is_made(tmp_path):
    binding, store, shared, cookie, _ = _gated_binding(tmp_path)
    reader = binding.followup._open_reader(cookie)
    try:
        assert callable(reader.get_following_users_detailed) and callable(reader.require_identity)
    finally:
        reader.close()
    binding.close()


@pytest.mark.parametrize('status', (401, 403, 429))
def test_latched_stop_yields_no_reader_and_keeps_saved_data(tmp_path, status):
    from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopped
    binding, store, shared, cookie, _ = _gated_binding(tmp_path)
    following = FollowingSyncStore(tmp_path / 'follow')
    seed_account(following, '777', hours_ago=30)
    shared.gate.trip(status)
    with pytest.raises(ManagedNetworkStopped):
        binding.followup._open_reader(cookie)
    binding.followup.store_provider = lambda: following
    binding.followup.busy_retry_delay = 0
    events = []
    binding.followup.subscribe(lambda kind, payload: events.append((kind, payload)))

    async def run():
        binding.followup._running = True
        await binding.followup._sync('777', following)
    asyncio.run(run())
    assert len(following.snapshot('777').authors) == 2
    assert any(t.startswith('自動同期失敗') for t in texts(events))
    binding.close()


def test_real_reader_follows_an_adopted_cookie_but_not_a_stale_path(tmp_path):
    from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkGateStorageError
    binding, store, shared, cookie, Store = _gated_binding(tmp_path)
    credentials = Store(tmp_path / 'gated-credentials', profile=store)
    candidate = credentials.prepare([{'domain': '.pixiv.net', 'path': '/', 'name': 'PHPSESSID',
                                      'value': 'dummy-new', 'secure': True,
                                      'expirationDate': int(time.time()) + 86400 * 20}],
                                    expected_revision=store.snapshot().revision)
    new_cookie = credentials.commit(candidate).cookie_file
    reader = binding.followup._open_reader(new_cookie)      # the current profile Cookie works
    reader.close()
    with pytest.raises(ManagedNetworkGateStorageError):     # the launch Cookie no longer matches
        binding.followup._open_reader(cookie)
    credentials.close()
    binding.close()
