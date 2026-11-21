"""FL-A08: startup Cookie expiry check, then automatic browser extraction.

Fake browser source, fake reader, no network. The profile, Cookie and the
credential store all live in tmp_path.
"""
import asyncio
import time

import pytest

from pixivvault_nicegui.browser_cookie_source import ExtractedCookies
from pixivvault_nicegui.extension_cookie_store import ExtensionCookieStore
from pixivvault_nicegui.native_auth_result import NativeAuthResult
from pixivvault_nicegui.native_profile import NativeProfileStore
from pixivvault_nicegui.native_startup import NativeProfileBinding
from pixivvault_nicegui.pixiv_read_client import AuthenticationMissing
from test_cookie_auth_status_20261117 import cookie_file

NOTICE = 'ブラウザ (Chrome) からの Cookie 自動抽出・更新に成功しました！'


class FakeReader:
    instances = []
    invalid = set()      # cookie paths the fake pixiv rejects
    uids = {}

    def __init__(self, path):
        self.path = str(path)
        FakeReader.instances.append(self)

    def set_retry_policy(self, policy):
        pass

    def require_identity(self):
        if self.path in FakeReader.invalid:
            raise AuthenticationMissing()
        return FakeReader.uids.get(self.path, '777')

    def close(self):
        pass


class FakeSource:
    def __init__(self, days=25, name='PHPSESSID', value='dummy-browser-session', browser='Chrome',
                 result='rows'):
        self.calls = 0
        self.days, self.name, self.value, self.browser, self.result = days, name, value, browser, result
        self.expiry = int(time.time() + days * 86400)  # fixed: a second extraction must be identical

    def extract(self):
        self.calls += 1
        if self.result is None:
            return None
        if self.result == 'boom':
            raise RuntimeError('browser store exploded')
        return ExtractedCookies(self.browser, [
            {'domain': '.pixiv.net', 'path': '/', 'name': self.name, 'value': self.value,
             'secure': True, 'expirationDate': self.expiry}])


def make(tmp_path, monkeypatch, *, cookie_days=20, configured=True, source=True, store_factory=True,
         expired=False):
    FakeReader.instances, FakeReader.invalid, FakeReader.uids = [], set(), {}
    monkeypatch.setattr('pixivvault_nicegui.pixiv_read_client.LivePixivReadClient', FakeReader)
    store = NativeProfileStore(tmp_path / 'profile')
    old = None
    if configured:
        old = cookie_file(tmp_path, cookie_days)
        store.update(cookie_file=old, expected_revision=store.snapshot().revision)
        if expired:
            FakeReader.invalid.add(str(old))
    binding = NativeProfileBinding(store)
    binding.auth_stop_required = False
    if source:
        binding.browser_cookie_source = FakeSource() if source is True else source
    if store_factory:
        binding.cookie_store_factory = lambda: ExtensionCookieStore(tmp_path / 'extcred', profile=store)
    return binding, store, old


def watch(binding):
    seen = []
    binding.auth_status.subscribe(lambda status, notice: seen.append((status, notice)))
    return seen


@pytest.mark.asyncio
async def test_expired_cookie_at_startup_is_replaced_from_the_browser(tmp_path, monkeypatch):
    binding, store, old = make(tmp_path, monkeypatch, expired=True)
    seen = watch(binding)
    await binding.refresh_auth_status(startup=True)
    assert binding.browser_cookie_source.calls == 1
    saved = store.snapshot().cookie_file
    assert saved != old and 'generations' in saved.parts  # the new generation, adopted by CAS
    status, notice = seen[-1]
    assert status.level == 'valid' and 'ユーザーID: 777' in status.text and '残り約25日' in status.text
    assert notice.startswith(NOTICE)
    assert binding.last_cookie_adoption == 'adopted'
    binding.close()


@pytest.mark.asyncio
async def test_warning_range_triggers_but_healthy_cookie_does_not(tmp_path, monkeypatch):
    binding, store, old = make(tmp_path, monkeypatch, cookie_days=5)
    await binding.refresh_auth_status(startup=True)
    assert binding.browser_cookie_source.calls == 1  # 5 days left: yellow range
    binding.close()
    (tmp_path / 'again').mkdir()
    binding, store, old = make(tmp_path / 'again', monkeypatch, cookie_days=20)
    await binding.refresh_auth_status(startup=True)
    assert binding.browser_cookie_source.calls == 0  # more than a week: leave it alone
    assert store.snapshot().cookie_file == old
    binding.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('code', ['communication_failed', 'stopped', 'busy', 'uncertain',
                                  'rate_limited', 'local_unavailable', 'owner_absent'])
async def test_no_extraction_when_the_state_is_not_an_expiry(tmp_path, monkeypatch, code):
    binding, store, old = make(tmp_path, monkeypatch)

    monkeypatch.setattr(binding, '_verify_auth_result', lambda path, stopped: (NativeAuthResult(code), None))
    await binding.refresh_auth_status(startup=True)
    assert binding.browser_cookie_source.calls == 0
    assert store.snapshot().cookie_file == old
    binding.close()


@pytest.mark.asyncio
async def test_missing_cookie_file_setting_is_not_extracted_like_the_original(tmp_path, monkeypatch):
    binding, store, _ = make(tmp_path, monkeypatch, configured=False)
    await binding.refresh_auth_status(startup=True)
    assert binding.browser_cookie_source.calls == 0
    assert binding.auth_status.status.banner
    binding.close()


@pytest.mark.asyncio
async def test_only_the_startup_check_extracts_and_only_once(tmp_path, monkeypatch):
    binding, store, old = make(tmp_path, monkeypatch, expired=True)
    await binding.refresh_auth_status(imported=True)            # saved / received Cookie
    await binding.check_authentication(old)                      # settings button
    assert binding.browser_cookie_source.calls == 0
    FakeReader.invalid.add(str(store.snapshot().cookie_file))
    await binding.refresh_auth_status(startup=True)
    await binding.refresh_auth_status(startup=True)
    assert binding.browser_cookie_source.calls == 1
    binding.close()


@pytest.mark.asyncio
async def test_bare_binding_has_no_browser_source_and_never_extracts(tmp_path, monkeypatch):
    FakeReader.instances, FakeReader.invalid = [], set()
    monkeypatch.setattr('pixivvault_nicegui.pixiv_read_client.LivePixivReadClient', FakeReader)
    store = NativeProfileStore(tmp_path / 'profile')
    old = cookie_file(tmp_path, 20)
    store.update(cookie_file=old, expected_revision=store.snapshot().revision)
    FakeReader.invalid.add(str(old))
    binding = NativeProfileBinding(store)
    assert binding.browser_cookie_source is None and binding.cookie_store_factory is None
    await binding.refresh_auth_status(startup=True)
    assert binding.auth_status.status.banner  # still expired
    assert not (tmp_path / 'extcred').exists()
    binding.close()


@pytest.mark.asyncio
async def test_nothing_found_in_any_browser_changes_nothing(tmp_path, monkeypatch):
    binding, store, old = make(tmp_path, monkeypatch, expired=True, source=FakeSource(result=None))
    await binding.refresh_auth_status(startup=True)
    assert store.snapshot().cookie_file == old and binding.auth_status.status.banner
    assert binding.last_cookie_adoption == 'not_found'
    assert not (tmp_path / 'extcred').exists()
    binding.close()


@pytest.mark.asyncio
async def test_extractor_failure_is_contained(tmp_path, monkeypatch):
    binding, store, old = make(tmp_path, monkeypatch, expired=True, source=FakeSource(result='boom'))
    await binding.refresh_auth_status(startup=True)
    assert store.snapshot().cookie_file == old and binding.last_cookie_adoption == 'error'
    binding.close()


@pytest.mark.asyncio
async def test_browser_cookie_that_pixiv_rejects_is_not_adopted(tmp_path, monkeypatch):
    binding, store, old = make(tmp_path, monkeypatch, expired=True)
    real = binding._verify_auth_result
    monkeypatch.setattr(binding, '_verify_auth_result',
                        lambda path, stopped: ((NativeAuthResult('identity_unverified'), None)
                                               if str(path).endswith('cookies.txt') and 'generations' in str(path)
                                               else real(path, stopped)))
    await binding.refresh_auth_status(startup=True)
    assert store.snapshot().cookie_file == old        # profile untouched
    assert binding.last_cookie_adoption == 'rejected'
    assert binding.auth_status.status.banner          # still shown as not logged in
    # the pending slot was released: a later attempt can prepare again
    monkeypatch.setattr(binding, '_verify_auth_result', real)
    assert await binding.adopt_browser_cookies() == 'adopted'
    binding.close()


@pytest.mark.asyncio
async def test_identical_browser_cookie_reports_unchanged(tmp_path, monkeypatch):
    binding, store, old = make(tmp_path, monkeypatch, expired=True)
    assert await binding.adopt_browser_cookies() == 'adopted'
    first = store.snapshot()
    assert await binding.adopt_browser_cookies() == 'unchanged'
    assert store.snapshot() == first
    binding.close()


@pytest.mark.asyncio
async def test_manual_adoption_entry_point_does_not_need_an_expired_state(tmp_path, monkeypatch):
    # The original _run_auto_browser_cookie_picker (no call site there) maps to this method.
    binding, store, old = make(tmp_path, monkeypatch, cookie_days=20)
    assert await binding.adopt_browser_cookies() == 'adopted'
    assert store.snapshot().cookie_file != old
    binding.close()


@pytest.mark.asyncio
async def test_unavailable_without_source_or_store(tmp_path, monkeypatch):
    binding, *_ = make(tmp_path, monkeypatch, source=False)
    assert await binding.adopt_browser_cookies() == 'unavailable'
    binding.close()
    (tmp_path / 'b').mkdir()
    binding, *_ = make(tmp_path / 'b', monkeypatch, store_factory=False)
    assert await binding.adopt_browser_cookies() == 'unavailable'
    binding.close()


@pytest.mark.asyncio
async def test_credential_store_is_created_lazily_and_closed_with_the_binding(tmp_path, monkeypatch):
    binding, store, old = make(tmp_path, monkeypatch, cookie_days=20)
    await binding.refresh_auth_status(startup=True)
    assert not (tmp_path / 'extcred').exists()      # healthy cookie: nothing created
    await binding.adopt_browser_cookies()
    assert (tmp_path / 'extcred' / 'generations').is_dir()
    binding.close()
    # the owner lock is released: a new owner can open the same root
    store2 = NativeProfileStore(tmp_path / 'profile')
    ExtensionCookieStore(tmp_path / 'extcred', profile=store2).close()
    store2.close()


# ---- wiring: ranking, native launch, next-start validation ------------------------

@pytest.mark.asyncio
async def test_ranking_auto_fetch_is_skipped_when_the_cookie_was_replaced_this_session(tmp_path, monkeypatch):
    from nicegui import Client, ui
    from pixivvault_nicegui.ui.ranking_view import RankingView
    from test_cookie_auth_status_20261117 import ranking_setup, rankings, settle
    binding, store, old = make(tmp_path, monkeypatch, cookie_days=20)
    assert not binding.cookie_replaced_since_launch()
    await binding.adopt_browser_cookies()
    assert binding.cookie_replaced_since_launch()  # readers still hold the launch Cookie
    services, reader = ranking_setup()
    services.native_profile = binding
    client = Client(ui.page('/_ranking_stale_'))
    try:
        with client:
            view = RankingView(services)
            view.set_active(True)
            await asyncio.sleep(.1)
            assert rankings(reader) == 0 and view.page is None
            view.close()
    finally:
        client.delete()
        services.close()
        binding.close()


def test_native_launch_wiring_builds_real_source_lazily_and_never_in_a_test_owner(tmp_path, monkeypatch):
    from pixivvault_nicegui import bootstrap, test_owner_runtime
    from pixivvault_nicegui.browser_cookie_source import BrowserCookieSource
    store = NativeProfileStore(tmp_path / 'profile')
    binding = NativeProfileBinding(store)
    bootstrap._wire_browser_cookie_source(binding, store)
    assert type(binding.browser_cookie_source) is BrowserCookieSource
    assert not (tmp_path / 'profile-credentials').exists()  # nothing created at launch
    created = binding.cookie_store_factory()
    assert (tmp_path / 'profile-credentials' / 'generations').is_dir()
    created.close()
    binding.close()

    store = NativeProfileStore(tmp_path / 'profile2')
    other = NativeProfileBinding(store)
    monkeypatch.setattr(test_owner_runtime, 'owner_root', lambda: tmp_path)
    bootstrap._wire_browser_cookie_source(other, store)
    assert other.browser_cookie_source is None and other.cookie_store_factory is None
    other.close()


@pytest.mark.asyncio
async def test_adopted_generation_passes_the_next_start_profile_validation(tmp_path, monkeypatch):
    from pixivvault_nicegui.native_profile import native_kwargs
    from pixivvault_nicegui.native_startup import validate_profile_boundaries
    binding, store, old = make(tmp_path, monkeypatch, expired=True)
    assert await binding.adopt_browser_cookies() == 'adopted'
    snapshot = store.snapshot()
    values = native_kwargs(snapshot, store.root)
    assert values['pixiv_cookie_file'] == snapshot.cookie_file
    validate_profile_boundaries(store.root, snapshot, values)
    binding.close()


@pytest.mark.asyncio
async def test_empty_cookie_file_is_missing_not_expired_and_imported_startup_never_extracts(tmp_path, monkeypatch):
    binding, store, old = make(tmp_path, monkeypatch, expired=True)
    monkeypatch.setattr(binding, '_verify_auth_result', lambda path, stopped: (NativeAuthResult('cookie_unreadable'), None))
    await binding.refresh_auth_status(imported=True, startup=True)   # coalesced re-check
    assert binding.browser_cookie_source.calls == 0
    old.write_bytes(b'')                                             # original "missing"
    await binding.refresh_auth_status(startup=True)
    assert binding.browser_cookie_source.calls == 0
    old.write_text('garbage', encoding='utf-8')                      # present but unusable: expired
    await binding.refresh_auth_status(startup=True)
    assert binding.browser_cookie_source.calls == 1
    binding.close()
