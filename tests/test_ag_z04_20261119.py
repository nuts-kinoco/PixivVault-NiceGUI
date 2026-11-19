"""Synthetic response-only tests; no real Cookie or network."""
from email.utils import formatdate
import time

import pytest

from pixivvault_nicegui.cookie_expiry_refresh import CookieExpiryCoordinator
from pixivvault_nicegui.extension_cookie_store import ExtensionCookieStore
from pixivvault_nicegui.native_profile import NativeProfileStore
from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient, _ReadResponse, ApiRejected


def setup(tmp_path):
    old_expiry = int(time.time()) + 3600
    (tmp_path / 'cred').mkdir()
    cookie = tmp_path / 'cred' / 'cookie.txt'
    cookie.write_text('# Netscape HTTP Cookie File\n'
        f'.pixiv.net\tTRUE\t/\tTRUE\t{old_expiry}\tPHPSESSID\t777_old\n'
        '#HttpOnly_.pixiv.net\tTRUE\t/\tTRUE\t\tsession\tfake\n', encoding='ascii')
    profile = NativeProfileStore(tmp_path / 'profile')
    profile.update(cookie_file=cookie, expected_revision=profile.snapshot().revision)
    store = ExtensionCookieStore(tmp_path / 'generations', profile=profile)
    coordinator = CookieExpiryCoordinator(profile, lambda: store)
    return old_expiry, cookie, profile, store, coordinator


def reader(cookie, coordinator, headers, *, body=b'{"error":false,"body":{}}'):
    sent = []
    def transport(request, timeout, limit, deadline):
        sent.append(request.get_header('Cookie'))
        return _ReadResponse(body, tuple(headers))
    client = LivePixivReadClient(cookie, transport=transport)
    client._verified_identity = '777'
    coordinator.register(client)
    return client, sent


def fetch(client):
    return client._json('identity', 'https://www.pixiv.net/ajax/user/extra', deadline=time.monotonic()+10)


def test_two_readers_preserve_session_and_use_adopted_generation(tmp_path):
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    header = 'PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Expires=' + formatdate(expiry+3600, usegmt=True)
    first, sent1 = reader(cookie, coordinator, [header])
    second, sent2 = reader(cookie, coordinator, [])
    try:
        fetch(first)
        selected = profile.snapshot().cookie_file
        assert selected != cookie
        saved = selected.read_text(encoding='utf8')
        assert f'\t{expiry+3600}\tPHPSESSID\t777_old' in saved
        assert '#HttpOnly_.pixiv.net\tTRUE\t/\tTRUE\t\tsession\tfake' in saved
        fetch(second)
        assert sent1 and sent2 and 'PHPSESSID=777_old' in sent2[0]
    finally:
        first.close(); second.close(); coordinator.close(); store.close(); profile.close()


@pytest.mark.parametrize('header', [
    'PHPSESSID=changed; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200',
    'PHPSESSID=777_old; Domain=.evil.example; Path=/; Secure; Max-Age=7200',
    'PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Max-Age=1',
])
def test_unrelated_changed_or_shortened_cookie_is_not_saved(tmp_path, header):
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [header])
    try:
        fetch(client)
        assert profile.snapshot().cookie_file == cookie
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_api_error_does_not_adopt_response(tmp_path):
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    header = 'PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200'
    client, _ = reader(cookie, coordinator, [header], body=b'{"error":true,"body":{}}')
    try:
        with pytest.raises(ApiRejected): fetch(client)
        assert profile.snapshot().cookie_file == cookie
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_verified_account_can_rotate_session_but_other_account_cannot(tmp_path):
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    rotate = 'PHPSESSID=777_new; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200'
    client, _ = reader(cookie, coordinator, [rotate])
    try:
        fetch(client)
        current = profile.snapshot().cookie_file
        assert current != cookie
        assert '\tPHPSESSID\t777_new' in current.read_text(encoding='utf8')
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_different_account_session_is_never_adopted(tmp_path):
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, ['PHPSESSID=888_other; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200'])
    try:
        fetch(client)
        assert profile.snapshot().cookie_file == cookie
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_external_cookie_selection_change_prevents_send(tmp_path):
    from pixivvault_nicegui.pixiv_read_client import CookieSourceRejected
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, sent = reader(cookie, coordinator, [])
    replacement = cookie.parent / 'replacement.txt'
    replacement.write_bytes(cookie.read_bytes())
    profile.update(cookie_file=replacement, expected_revision=profile.snapshot().revision)
    try:
        with pytest.raises(CookieSourceRejected): fetch(client)
        assert sent == []
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_rotation_rebinds_second_reader_before_its_next_send(tmp_path):
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    rotate = 'PHPSESSID=777_new; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200'
    first, _ = reader(cookie, coordinator, [rotate])
    second, sent = reader(cookie, coordinator, [])
    try:
        fetch(first)
        fetch(second)
        assert 'PHPSESSID=777_new' in sent[-1]
        assert 'PHPSESSID=777_old' not in sent[-1]
    finally:
        first.close(); second.close(); coordinator.close(); store.close(); profile.close()


def test_changed_source_during_response_fails_closed(tmp_path):
    from pixivvault_nicegui.pixiv_read_client import CookieSourceRejected
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    calls = []
    def transport(request, *args):
        calls.append(1)
        cookie.write_bytes(cookie.read_bytes() + b'# change\n')
        return _ReadResponse(b'{"error":false,"body":{}}',
            ('PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200',))
    client._transport = transport
    try:
        with pytest.raises(CookieSourceRejected): fetch(client)
        assert profile.snapshot().cookie_file == cookie
        with pytest.raises(CookieSourceRejected): fetch(client)
        assert len(calls) == 1
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_unconfirmed_commit_does_not_resend_or_retry(tmp_path, monkeypatch):
    from pixivvault_nicegui.extension_cookie_store import CookieSyncCommitUnconfirmed
    from pixivvault_nicegui.pixiv_read_client import CookieSourceRejected
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, sent = reader(cookie, coordinator, ['PHPSESSID=777_new; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200'])
    commit = store.commit
    def uncertain(candidate):
        commit(candidate)
        raise CookieSyncCommitUnconfirmed()
    monkeypatch.setattr(store, 'commit', uncertain)
    try:
        with pytest.raises(CookieSourceRejected): fetch(client)
        committed = profile.snapshot().cookie_file
        assert committed != cookie
        with pytest.raises(CookieSourceRejected): fetch(client)
        assert profile.snapshot().cookie_file == committed and len(sent) == 1
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_cancelled_get_does_not_adopt_response(tmp_path):
    from pixivvault_nicegui.pixiv_read_client import ClientClosed
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    def cancel(request, *args):
        client.close()
        return _ReadResponse(b'{"error":false,"body":{}}',
            ('PHPSESSID=777_new; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200',))
    client._transport = cancel
    try:
        with pytest.raises(ClientClosed): fetch(client)
        assert profile.snapshot().cookie_file == cookie
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_cdn_response_cookie_does_not_save(tmp_path):
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, ['PHPSESSID=777_new; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200'])
    try:
        assert client._get('image', 'https://i.pximg.net/generated.png', deadline=time.monotonic()+10) == b'{"error":false,"body":{}}'
        assert profile.snapshot().cookie_file == cookie
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_response_repr_and_failures_do_not_disclose_cookie(tmp_path, caplog):
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    secret = '777_synthetic_private'
    response = _ReadResponse(b'private synthetic body', (f'PHPSESSID={secret}; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200',))
    client, _ = reader(cookie, coordinator, [])
    client._transport = lambda *args: response
    try:
        assert secret not in repr(response) and 'private synthetic body' not in repr(response)
        from pixivvault_nicegui.pixiv_read_client import InvalidPayload
        with pytest.raises(InvalidPayload) as caught: fetch(client)
        assert secret not in str(caught.value) and secret not in caplog.text
        assert profile.snapshot().cookie_file == cookie
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_real_profile_bound_readers_rebind_and_keep_shared_stop(tmp_path):
    from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopped
    from pixivvault_nicegui.managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
    from pixivvault_nicegui.managed_network_stop_shared_transport import create_shared_profile_reader
    from stage5_generated_shared_owner import generated_shared_scope
    (tmp_path / 'cred').mkdir()
    cookie = tmp_path / 'cred' / 'cookie.txt'
    expiry = int(time.time()) + 3600
    cookie.write_text('# Netscape HTTP Cookie File\n'
        f'.pixiv.net\tTRUE\t/\tTRUE\t{expiry}\tPHPSESSID\t777_old\n', encoding='ascii')
    profile = NativeProfileStore(tmp_path / 'profile')
    scope = ManagedProfileNetworkStopScope(profile.root)
    shared = generated_shared_scope(tmp_path)
    scope.shared_owner = shared
    profile.update(cookie_file=cookie, expected_revision=profile.snapshot().revision)
    store = ExtensionCookieStore(tmp_path / 'generations', profile=profile)
    coordinator = CookieExpiryCoordinator(profile, lambda: store)
    sent = []
    def first_transport(request, *args):
        sent.append(request.get_header('Cookie'))
        return _ReadResponse(b'{"error":false,"body":{}}',
            ('PHPSESSID=777_new; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200',))
    def second_transport(request, *args):
        sent.append(request.get_header('Cookie'))
        return b'{"error":false,"body":{}}'
    first = create_shared_profile_reader(scope, cookie, shared.gate, transport=first_transport,
                                         shared_owner_check=shared._check_owner)
    second = create_shared_profile_reader(scope, cookie, shared.gate, transport=second_transport,
                                          shared_owner_check=shared._check_owner)
    first._verified_identity = second._verified_identity = '777'
    coordinator.register(first); coordinator.register(second)
    try:
        fetch(first)
        adopted = profile.snapshot().cookie_file
        assert adopted != cookie
        assert first._transport.cookie_path == second._transport.cookie_path == adopted
        fetch(second)
        assert 'PHPSESSID=777_new' in sent[-1]
        shared.gate.trip(429)
        with pytest.raises(ManagedNetworkStopped): fetch(second)
        assert len(sent) == 2
    finally:
        first.close(); second.close(); coordinator.close(); store.close(); profile.close()


def test_stop_after_response_before_adoption_blocks_cookie_save(tmp_path, monkeypatch):
    from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopped
    from pixivvault_nicegui.managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
    from pixivvault_nicegui.managed_network_stop_shared_transport import create_shared_profile_reader
    from stage5_generated_shared_owner import generated_shared_scope
    (tmp_path / 'cred').mkdir()
    cookie = tmp_path / 'cred' / 'cookie.txt'
    expiry = int(time.time()) + 3600
    cookie.write_text('# Netscape HTTP Cookie File\n'
        f'.pixiv.net\tTRUE\t/\tTRUE\t{expiry}\tPHPSESSID\t777_old\n', encoding='ascii')
    profile = NativeProfileStore(tmp_path / 'profile')
    scope = ManagedProfileNetworkStopScope(profile.root)
    shared = generated_shared_scope(tmp_path)
    scope.shared_owner = shared
    profile.update(cookie_file=cookie, expected_revision=profile.snapshot().revision)
    store = ExtensionCookieStore(tmp_path / 'generations', profile=profile)
    coordinator = CookieExpiryCoordinator(profile, lambda: store)
    sent = []
    def transport(request, *args):
        sent.append(1)
        return _ReadResponse(b'{"error":false,"body":{}}',
            ('PHPSESSID=777_new; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200',))
    client = create_shared_profile_reader(scope, cookie, shared.gate, transport=transport,
                                          shared_owner_check=shared._check_owner)
    client._verified_identity = '777'
    coordinator.register(client)
    observe = coordinator.observe
    def stop_first(*args):
        shared.gate.trip(429)
        return observe(*args)
    monkeypatch.setattr(coordinator, 'observe', stop_first)
    try:
        with pytest.raises(ManagedNetworkStopped): fetch(client)
        assert profile.snapshot().cookie_file == cookie
        with pytest.raises(ManagedNetworkStopped): fetch(client)
        assert len(sent) == 1
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_unrelated_site_cookie_does_not_break_existing_read(tmp_path):
    expiry, cookie, profile, store, old = setup(tmp_path)
    old.close()
    with cookie.open('a', encoding='ascii') as stream:
        stream.write('.example.com\tTRUE\t/\tTRUE\t2000000000\tother\tfake\n')
    coordinator = CookieExpiryCoordinator(profile, lambda: store)
    client, sent = reader(cookie, coordinator, ['PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200'])
    try:
        assert fetch(client) == {}
        assert len(sent) == 1
        saved = profile.snapshot().cookie_file
        assert saved != cookie
        assert '.example.com' in saved.read_text(encoding='ascii')
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_busy_browser_candidate_does_not_block_reader_and_can_retry(tmp_path):
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    candidate = store.prepare([{'domain': '.pixiv.net', 'path': '/', 'name': 'PHPSESSID',
        'value': '777_old', 'secure': True, 'expirationDate': expiry+60}],
        expected_revision=profile.snapshot().revision)
    client, sent = reader(cookie, coordinator, ['PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200'])
    try:
        assert fetch(client) == {}
        assert profile.snapshot().cookie_file == cookie
        assert store.discard(candidate)
        assert fetch(client) == {}
        assert profile.snapshot().cookie_file != cookie and len(sent) == 2
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_binding_recognizes_only_its_own_renewal(tmp_path):
    from pixivvault_nicegui.native_startup import NativeProfileBinding
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    binding = NativeProfileBinding(profile)
    binding._cookie_expiry_coordinator = coordinator
    client, _ = reader(cookie, coordinator, ['PHPSESSID=777_new; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200'])
    try:
        assert not binding.cookie_replaced_since_launch()
        fetch(client)
        assert not binding.cookie_replaced_since_launch()
        foreign = cookie.parent / 'external.txt'
        foreign.write_bytes(cookie.read_bytes())
        profile.update(cookie_file=foreign, expected_revision=profile.snapshot().revision)
        assert binding.cookie_replaced_since_launch()
    finally:
        client.close(); store.close(); binding.close()


def test_cookiejar_debug_cannot_leak_response_values(tmp_path, monkeypatch, caplog):
    import http.cookiejar
    import logging
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, ['PHPSESSID=777_private_dummy; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200'])
    monkeypatch.setattr(http.cookiejar, 'debug', True)
    caplog.set_level(logging.DEBUG, logger='http.cookiejar')
    try:
        fetch(client)
        assert '777_private_dummy' not in caplog.text
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


@pytest.mark.parametrize('case', ['expired', 'other_path'])
def test_cookie_not_sent_with_request_cannot_be_revived(tmp_path, case):
    expiry, cookie, profile, store, old = setup(tmp_path)
    old.close()
    content = cookie.read_text(encoding='ascii')
    if case == 'expired':
        content = content.replace(str(expiry), str(int(time.time())-60))
        response_path = '/'
    else:
        content = content.replace('.pixiv.net\tTRUE\t/\tTRUE\t' + str(expiry),
                                  '.pixiv.net\tTRUE\t/not-sent\tTRUE\t' + str(expiry))
        response_path = '/not-sent'
    cookie.write_text(content, encoding='ascii')
    coordinator = CookieExpiryCoordinator(profile, lambda: store)
    header = 'PHPSESSID=777_old; Domain=.pixiv.net; Path=' + response_path + '; Secure; Max-Age=7200'
    client, sent = reader(cookie, coordinator, [header])
    try:
        fetch(client)
        assert 'PHPSESSID=' not in (sent[-1] or '')
        assert profile.snapshot().cookie_file == cookie
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_ranking_success_renews_cookie_and_bad_payload_does_not(tmp_path):
    from pixivvault_nicegui.pixiv_read_client import InvalidPayload
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    header = 'PHPSESSID=777_new; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200'
    client, _ = reader(cookie, coordinator, [header], body=b'{"contents":null}')
    try:
        with pytest.raises(InvalidPayload): client.get_ranking()
        assert profile.snapshot().cookie_file == cookie
        client._transport = lambda *args: _ReadResponse(b'{"contents":[]}', (header,))
        assert client.get_ranking() == []
        assert profile.snapshot().cookie_file != cookie
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def _home():
    import json
    state = json.dumps({'api': {'token': 'a' * 32}})
    data = {'props': {'pageProps': {'isLoggedIn': True, 'gaUserData': {'id': '777'},
        'serverSerializedPreloadedState': state}}}
    return ('<script id="__NEXT_DATA__" type="application/json">' + json.dumps(data) + '</script>').encode()


def test_post_does_not_send_stale_csrf_after_other_reader_rotates(tmp_path):
    from pixivvault_nicegui.pixiv_write_client import PixivWriteClient, WriteIntent, WriteHTTPResponse
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    first, _ = reader(cookie, coordinator, [])
    second, _ = reader(cookie, coordinator, ['PHPSESSID=777_new; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200'])
    first._transport = lambda *args: _home()
    posts = []
    def post(request, *args):
        posts.append(1)
        return WriteHTTPResponse(200, b'{"error":false,"body":{}}')
    writer = PixivWriteClient(first, transport=post)
    def rotate():
        fetch(second)
    try:
        result = writer.execute(WriteIntent('synthetic-op', '777', '91', 'follow'), before_dispatch=rotate)
        assert result.status == 'rejected' and result.reason == 'preflight_not_sent'
        assert posts == []
    finally:
        writer.close(); first.close(); second.close(); coordinator.close(); store.close(); profile.close()


def test_post_debug_logging_never_contains_fake_cookie(tmp_path, monkeypatch, caplog):
    import http.cookiejar
    import logging
    from pixivvault_nicegui.pixiv_write_client import PixivWriteClient, WriteIntent, WriteHTTPResponse
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    client._transport = lambda *args: _home()
    writer = PixivWriteClient(client, transport=lambda *args: WriteHTTPResponse(200, b'{"error":false,"body":{}}'))
    monkeypatch.setattr(http.cookiejar, 'debug', True)
    caplog.set_level(logging.DEBUG, logger='http.cookiejar')
    try:
        assert writer.execute(WriteIntent('synthetic-post', '777', '91', 'follow')).status == 'confirmed'
        assert '777_old' not in caplog.text
    finally:
        writer.close(); client.close(); coordinator.close(); store.close(); profile.close()


def test_precommit_gate_busy_skips_only_this_renewal(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkGateBusy
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, sent = reader(cookie, coordinator, ['PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200'])
    original = coordinator._commit_admission
    calls = []
    @contextmanager
    def busy_once(reader):
        calls.append(1)
        if len(calls) == 1:
            raise ManagedNetworkGateBusy('synthetic busy')
        with original(reader):
            yield
    monkeypatch.setattr(coordinator, '_commit_admission', busy_once)
    try:
        assert fetch(client) == {}
        assert profile.snapshot().cookie_file == cookie
        assert fetch(client) == {}
        assert profile.snapshot().cookie_file != cookie and len(sent) == 2
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_initial_identity_response_renews_only_after_identity_validation(tmp_path):
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    header = 'PHPSESSID=777_new; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200'
    def transport(request, *args):
        if request.full_url.endswith('/'):
            return _home()
        return _ReadResponse(b'{"error":false,"body":{"userId":"777"}}', (header,))
    client = LivePixivReadClient(cookie, transport=transport)
    coordinator.register(client)
    try:
        assert client.require_identity() == '777'
        assert profile.snapshot().cookie_file != cookie
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_initial_identity_mismatch_never_adopts_response_cookie(tmp_path):
    from pixivvault_nicegui.pixiv_read_client import AuthenticationMissing
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    def transport(request, *args):
        if request.full_url.endswith('/'):
            return _home()
        return _ReadResponse(b'{"error":false,"body":{"userId":"888"}}',
            ('PHPSESSID=777_new; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200',))
    client = LivePixivReadClient(cookie, transport=transport)
    coordinator.register(client)
    try:
        with pytest.raises(AuthenticationMissing): client.require_identity()
        assert profile.snapshot().cookie_file == cookie
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_real_response_reader_preserves_repeated_headers_without_repr_leak():
    from email.message import Message
    from io import BytesIO
    from pixivvault_nicegui.pixiv_read_client import _read_response
    from urllib.request import Request
    headers = Message()
    headers.add_header('Set-Cookie', 'first=fake_private_one; Path=/')
    headers.add_header('Set-Cookie', 'second=fake_private_two; Path=/')
    class Response:
        def __init__(self): self.headers = headers; self.body = BytesIO(b'{}')
        def getcode(self): return 200
        def read1(self, size): return self.body.read(size)
        def __enter__(self): return self
        def __exit__(self, *args): self.body.close()
    class Opener:
        def open(self, request, timeout): return Response()
    result = _read_response(Opener(), Request('https://www.pixiv.net/'), 1, 1024,
                            time.monotonic()+5, capture_cookies=True)
    assert len(result.set_cookie) == 2 and result.body == b'{}'
    assert 'fake_private' not in repr(result)


def test_unparseable_foreign_cookie_preserves_reader_and_notifies(tmp_path, caplog):
    expiry, cookie, profile, store, old = setup(tmp_path)
    old.close()
    with cookie.open('a', encoding='ascii') as stream:
        stream.write('.bad_domain.example\tTRUE\t/\tTRUE\t2000000000\tother\tfake\n')
    coordinator = CookieExpiryCoordinator(profile, lambda: store)
    client, sent = reader(cookie, coordinator, ['PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200'])
    try:
        assert fetch(client) == {} and len(sent) == 1
        assert profile.snapshot().cookie_file == cookie
        assert 'Cookie expiry renewal unavailable' in caplog.text
        assert '777_old' not in caplog.text and 'fake' not in caplog.text
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_post_rechecks_external_profile_change_at_dispatch(tmp_path):
    from pixivvault_nicegui.pixiv_write_client import PixivWriteClient, WriteIntent, WriteHTTPResponse
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    client._transport = lambda *args: _home()
    posts = []
    writer = PixivWriteClient(client, transport=lambda *args: posts.append(1) or WriteHTTPResponse(200, b'{"error":false,"body":{}}'))
    replacement = cookie.parent / 'external.txt'
    replacement.write_bytes(cookie.read_bytes())
    def switch():
        profile.update(cookie_file=replacement, expected_revision=profile.snapshot().revision)
    try:
        result = writer.execute(WriteIntent('synthetic-external', '777', '91', 'follow'), mark_dispatch=switch)
        assert result.status == 'rejected' and result.reason == 'preflight_not_sent'
        assert not posts
    finally:
        writer.close(); client.close(); coordinator.close(); store.close(); profile.close()


def test_cookiejar_debug_does_not_log_old_or_renewed_get_cookie(tmp_path, monkeypatch, caplog):
    import http.cookiejar
    import logging
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    first, _ = reader(cookie, coordinator, ['PHPSESSID=777_new; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200'])
    second, sent = reader(cookie, coordinator, [])
    monkeypatch.setattr(http.cookiejar, 'debug', True)
    caplog.set_level(logging.DEBUG, logger='http.cookiejar')
    try:
        fetch(first)
        fetch(second)
        assert 'PHPSESSID=777_new' in sent[-1]
        assert '777_old' not in caplog.text and '777_new' not in caplog.text
    finally:
        first.close(); second.close(); coordinator.close(); store.close(); profile.close()


def test_writer_and_identity_lock_order_never_deadlock(tmp_path, monkeypatch):
    from threading import Event, Thread, current_thread
    from pixivvault_nicegui.pixiv_write_client import PixivWriteClient, WriteIntent, WriteHTTPResponse
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    client._transport = lambda *args: _home()
    writer = PixivWriteClient(client, transport=lambda *args: WriteHTTPResponse(200, b'{"error":false,"body":{}}'))
    held, attempted = Event(), Event()
    observations, results = [], []
    original = client.require_identity
    def instrumented():
        if current_thread().name == 'synthetic-writer':
            attempted.set()
        return original()
    monkeypatch.setattr(client, 'require_identity', instrumented)
    def identity_thread():
        with client._identity_lock:
            held.set()
            if attempted.wait(5):
                acquired = coordinator.lock.acquire(timeout=1)
                observations.append(acquired)
                if acquired: coordinator.lock.release()
    def writer_thread():
        results.append(writer.execute(WriteIntent('synthetic-lock', '777', '91', 'follow')))
    first = Thread(target=identity_thread)
    second = Thread(target=writer_thread, name='synthetic-writer')
    first.start()
    try:
        assert held.wait(5)
        second.start()
        first.join(10); second.join(10)
        assert not first.is_alive() and not second.is_alive()
        assert observations == [True]
        assert len(results) == 1 and results[0].status == 'confirmed'
    finally:
        first.join(10); second.join(10)
        writer.close(); client.close(); coordinator.close(); store.close(); profile.close()


def test_out_of_range_expiry_is_ignored_without_disabling_reader(tmp_path):
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, sent = reader(cookie, coordinator, ['PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Max-Age=999999999999'])
    try:
        assert fetch(client) == {}
        assert fetch(client) == {}
        assert profile.snapshot().cookie_file == cookie and len(sent) == 2
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_sent_session_cookie_receives_first_persistent_expiry(tmp_path):
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    header = 'session=fake; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200'
    client, sent = reader(cookie, coordinator, [header])
    try:
        fetch(client)
        assert 'session=fake' in sent[-1]
        selected = profile.snapshot().cookie_file
        assert selected != cookie
        from pixivvault_nicegui.extension_cookie_store import _parse_existing
        rows = _parse_existing(selected.read_bytes())
        assert rows[('.pixiv.net', '/', 'session')][2] > int(time.time())
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_unsent_session_cookie_is_not_made_persistent(tmp_path):
    expiry, cookie, profile, store, old = setup(tmp_path)
    old.close()
    content = cookie.read_text(encoding='ascii').replace(
        '#HttpOnly_.pixiv.net\tTRUE\t/\tTRUE\t\tsession',
        '#HttpOnly_.pixiv.net\tTRUE\t/hidden\tTRUE\t\tsession')
    cookie.write_text(content, encoding='ascii')
    coordinator = CookieExpiryCoordinator(profile, lambda: store)
    client, sent = reader(cookie, coordinator, ['session=fake; Domain=.pixiv.net; Path=/hidden; Secure; Max-Age=7200'])
    try:
        fetch(client)
        assert 'session=fake' not in (sent[-1] or '')
        assert profile.snapshot().cookie_file == cookie
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_oversized_response_headers_fail_without_cookie_leak():
    from email.message import Message
    from io import BytesIO
    from pixivvault_nicegui.pixiv_read_client import _read_response, ResponseLimitExceeded
    from urllib.request import Request
    headers = Message()
    headers.add_header('Set-Cookie', 'fake_private=' + 'x' * 65536)
    class Response:
        def __init__(self): self.headers = headers; self.body = BytesIO(b'{}')
        def getcode(self): return 200
        def read1(self, size): return self.body.read(size)
        def __enter__(self): return self
        def __exit__(self, *args): self.body.close()
    class Opener:
        def open(self, request, timeout): return Response()
    with pytest.raises(ResponseLimitExceeded) as caught:
        _read_response(Opener(), Request('https://www.pixiv.net/'), 1, 1024,
                       time.monotonic()+5, capture_cookies=True)
    assert 'fake_private' not in str(caught.value)


def test_store_rejects_foreign_cookie_extension_even_if_called_directly(tmp_path):
    from pixivvault_nicegui.extension_cookie_store import CookieSyncError
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    coordinator.close()
    with cookie.open('a', encoding='ascii') as stream:
        stream.write(f'.example.com\tTRUE\t/\tTRUE\t{expiry}\tforeign\tfake\n')
    snapshot, proof, rows = store.expiry_source()
    key = ('.example.com', '/', 'foreign')
    try:
        with pytest.raises(CookieSyncError):
            store.prepare_expiry_extensions({key: ('fake', True, expiry+3600)},
                expected_snapshot=snapshot, expected_proof=proof, verified_identity='777')
        assert profile.snapshot().cookie_file == cookie
    finally:
        store.close(); profile.close()


def test_external_profile_change_during_get_rejects_stale_response(tmp_path):
    from pixivvault_nicegui.pixiv_read_client import CookieSourceRejected
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    replacement = cookie.parent / 'external.txt'
    replacement.write_bytes(cookie.read_bytes())
    calls = []
    def change(request, *args):
        calls.append(1)
        profile.update(cookie_file=replacement, expected_revision=profile.snapshot().revision)
        return _ReadResponse(b'{"error":false,"body":{}}',
            ('PHPSESSID=777_new; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200',))
    client._transport = change
    try:
        with pytest.raises(CookieSourceRejected): fetch(client)
        assert profile.snapshot().cookie_file == replacement
        with pytest.raises(CookieSourceRejected): fetch(client)
        assert len(calls) == 1
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_verified_response_can_rotate_an_existing_non_session_cookie(tmp_path):
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    header = 'session=rotated; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200'
    client, sent = reader(cookie, coordinator, [header])
    try:
        fetch(client)
        assert 'session=fake' in sent[0]
        selected = profile.snapshot().cookie_file
        assert selected != cookie
        assert '\tsession\trotated' in selected.read_text(encoding='utf8')
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_rotated_cookie_with_delimiter_is_not_saved(tmp_path):
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    header = 'session="bad;value"; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200'
    client, _ = reader(cookie, coordinator, [header])
    try:
        fetch(client)
        assert profile.snapshot().cookie_file == cookie
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_non_session_rotation_requires_sent_bound_php_session(tmp_path):
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    coordinator.close()
    raw = cookie.read_bytes().replace(b'TRUE\t/\tTRUE\t', b'TRUE\t/private\tTRUE\t', 1)
    cookie.write_bytes(raw)
    coordinator = CookieExpiryCoordinator(profile, lambda: store)
    header = 'session=rotated; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200'
    client, sent = reader(cookie, coordinator, [header])
    try:
        fetch(client)
        assert 'PHPSESSID' not in sent[0]
        assert profile.snapshot().cookie_file == cookie
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_foreign_php_cookie_cannot_authorize_pixiv_cookie_rotation(tmp_path):
    from pixivvault_nicegui.extension_cookie_store import CookieSyncError
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    coordinator.close()
    raw = cookie.read_bytes().replace(b'.pixiv.net\tTRUE\t/\tTRUE\t' + str(expiry).encode() + b'\tPHPSESSID',
                                      b'.other.example\tTRUE\t/\tTRUE\t' + str(expiry).encode() + b'\tPHPSESSID')
    cookie.write_bytes(raw)
    try:
        snapshot, proof, _ = store.expiry_source()
        with pytest.raises(CookieSyncError):
            store.prepare_expiry_extensions({('.pixiv.net', '/', 'session'): ('rotated', True, int(time.time()) + 7200)},
                                            expected_snapshot=snapshot, expected_proof=proof, verified_identity='777')
        assert profile.snapshot().cookie_file == cookie
    finally:
        store.close(); profile.close()


@pytest.mark.parametrize('length', [16385, 16370])
def test_oversized_rotated_cookie_keeps_existing_reader(tmp_path, length):
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    headers = ['session=' + ('x' * length) + '; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200']
    client, _ = reader(cookie, coordinator, headers)
    try:
        fetch(client)
        assert profile.snapshot().cookie_file == cookie
        headers.clear()
        fetch(client)
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_new_httponly_attribute_is_preserved(tmp_path):
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    header = 'PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; HttpOnly; Max-Age=7200'
    client, _ = reader(cookie, coordinator, [header])
    try:
        fetch(client)
        selected = profile.snapshot().cookie_file
        assert selected != cookie
        assert any(line.startswith('#HttpOnly_.pixiv.net\t') and '\tPHPSESSID\t' in line for line in selected.read_text(encoding='utf8').splitlines())
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_unusable_candidate_header_keeps_current_reader(tmp_path, monkeypatch):
    from pixivvault_nicegui import cookie_expiry_refresh as refresh
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    headers = ['PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200']
    client, _ = reader(cookie, coordinator, headers)
    original_read = refresh._read_cookies
    class BadJar:
        def __iter__(self):
            return iter(())
        def add_cookie_header(self, request):
            raise ValueError('synthetic invalid candidate')
    def read_candidate(path):
        if path != cookie:
            return BadJar(), '777'
        return original_read(path)
    try:
        with monkeypatch.context() as faults:
            faults.setattr(refresh, '_read_cookies', read_candidate)
            fetch(client)
        assert profile.snapshot().cookie_file == cookie
        headers.clear()
        fetch(client)
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_oversized_serialized_generation_keeps_current_reader(tmp_path):
    from pixivvault_nicegui.pixiv_read_client import COOKIE_LIMIT
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    coordinator.close()
    raw = cookie.read_bytes()
    index = 0
    while len(raw) < COOKIE_LIMIT - 20000:
        raw += (f'.foreign{index}.example\tTRUE\t/\tTRUE\t{expiry}\tname\t'.encode() + b'x' * 16000 + b'\n')
        index += 1
    prefix = f'.foreign{index}.example\tTRUE\t/\tTRUE\t{expiry}\tname\t'.encode()
    padding = COOKIE_LIMIT - 600 - len(raw) - len(prefix) - 1
    assert 0 < padding < 16384
    raw += prefix + b'x' * padding + b'\n'
    cookie.write_bytes(raw)
    coordinator = CookieExpiryCoordinator(profile, lambda: store)
    headers = ['session=' + ('z' * 1000) + '; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200']
    client, _ = reader(cookie, coordinator, headers)
    try:
        fetch(client)
        assert profile.snapshot().cookie_file == cookie
        headers.clear()
        fetch(client)
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()
