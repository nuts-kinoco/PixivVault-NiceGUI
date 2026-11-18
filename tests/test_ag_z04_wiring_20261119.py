"""Product reader wiring with isolated profile and fake transport."""
from threading import Event, Thread

from pixivvault_nicegui import bootstrap
from pixivvault_nicegui.extension_cookie_store import ExtensionCookieStore
from pixivvault_nicegui.managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
from pixivvault_nicegui.native_profile import NativeProfileStore
from pixivvault_nicegui.native_startup import NativeProfileBinding
from pixivvault_nicegui.pixiv_read_client import _ReadResponse
from stage5_generated_shared_owner import generated_shared_scope
from test_ag_z04_20261119 import setup, reader, fetch


def test_concurrent_readers_serialize_and_second_sends_rotated_cookie(tmp_path):
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    first, _ = reader(cookie, coordinator, [])
    second, sent = reader(cookie, coordinator, [])
    entered, release = Event(), Event()
    results = []
    def wait(request, *args):
        entered.set()
        assert release.wait(10)
        return _ReadResponse(b'{"error":false,"body":{}}',
            ('PHPSESSID=777_new; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200',))
    first._transport = wait
    def run(client):
        try: results.append(fetch(client))
        except Exception as error: results.append(error)
    one, two = Thread(target=run, args=(first,)), Thread(target=run, args=(second,))
    one.start()
    try:
        assert entered.wait(10)
        two.start()
        assert not sent
    finally:
        release.set()
        one.join(10); two.join(10)
    try:
        assert not one.is_alive() and not two.is_alive()
        assert len(results) == 2 and all(item == {} for item in results)
        assert len(sent) == 1 and 'PHPSESSID=777_new' in sent[0]
    finally:
        first.close(); second.close(); coordinator.close(); store.close(); profile.close()


def test_bootstrap_wires_every_owned_reader_to_binding_coordinator(tmp_path, monkeypatch):
    import time
    from pixivvault_nicegui import managed_network_stop_shared_transport as transport_module
    cred = tmp_path / 'cred'; cred.mkdir()
    cookie = cred / 'cookie.txt'
    cookie.write_text('# Netscape HTTP Cookie File\n'
        f'.pixiv.net\tTRUE\t/\tTRUE\t{int(time.time())+3600}\tPHPSESSID\t777_old\n', encoding='ascii')
    profile = NativeProfileStore(tmp_path / 'profile')
    scope = ManagedProfileNetworkStopScope(profile.root)
    shared = generated_shared_scope(tmp_path)
    scope.shared_owner = shared
    profile.update(cookie_file=cookie, expected_revision=profile.snapshot().revision)
    binding = NativeProfileBinding(profile)
    binding.cookie_store_factory = lambda: ExtensionCookieStore(tmp_path / 'generations', profile=profile)
    coordinator = binding.cookie_expiry_coordinator()
    create = transport_module.create_shared_profile_reader
    calls = []
    def fake(request, *args):
        calls.append(request.get_header('Cookie'))
        return _ReadResponse(b'{"error":false,"body":{}}',
            ('PHPSESSID=777_new; Domain=.pixiv.net; Path=/; Secure; Max-Age=7200',) if len(calls) == 1 else ())
    def create_with_fake(*args, **kwargs):
        return create(*args, transport=fake, **kwargs)
    monkeypatch.setattr(transport_module, 'create_shared_profile_reader', create_with_fake)
    live, novel, _, ugoira, _ = bootstrap._build_owned_media_readers(
        pixiv_cookie_file=cookie, pixiv_download_root=tmp_path / 'images',
        novel_download_root=tmp_path / 'novels', novel_fixture_root=None,
        novel_source=None, ugoira_root=tmp_path / 'ugoira', ugoira_fixture_root=None,
        live_client=None, managed_network_stop_scope=scope,
        shared_network_stop_scope=shared, cookie_expiry_coordinator=coordinator)
    readers = (live, novel._reader, ugoira.reader)
    try:
        for client in readers:
            client._verified_identity = '777'
            assert client._expiry_coordinator is coordinator
        fetch(readers[0])
        selected = profile.snapshot().cookie_file
        assert selected != cookie
        for client in readers[1:]:
            assert client._transport.cookie_path == selected
            fetch(client)
        assert len(calls) == 3 and all('PHPSESSID=777_new' in value for value in calls[1:])
    finally:
        for client in readers: client.close()
        binding.close()
