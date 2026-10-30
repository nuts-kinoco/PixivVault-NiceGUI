"""Application wiring with isolated preferences and synthetic asynchronous readers."""
import asyncio
from threading import Event
from types import SimpleNamespace

import pytest

from pixivvault_nicegui.bootstrap import parse_args
from pixivvault_nicegui.preferences import PreferencesStore
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.request_policy import request_scope
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected


def test_wake_uri_is_not_a_download_or_implicit_storage_configuration():
    config = parse_args(['--', 'pixivvault://start'])
    assert config.startup_uri == 'pixivvault://start'
    assert config.pixiv_download_root is None
    assert not config.extension_bridge


@pytest.mark.parametrize('uri', ['pixivvault://start?work=12', 'pixivvault://download/12', '--native'])
def test_launch_payload_cannot_inject_cli_options(uri):
    with pytest.raises(SystemExit):
        parse_args(['--', uri])


def test_extension_cannot_start_without_managed_owner():
    with pytest.raises(SystemExit):
        parse_args(['--extension-bridge'])


def test_live_settings_bind_all_media_readers_and_preserve_strict_scope(tmp_path):
    class Reader:
        def set_request_admission(self, value): self.admission = value
        def set_retry_policy(self, value): self.retry = value
    readers = [Reader(), Reader(), Reader()]
    store = PreferencesStore(tmp_path / 'settings.sqlite3')
    services = ApplicationServices.__new__(ApplicationServices)
    services.preferences = store
    services.live_client = readers[0]
    services.novel_fixture_source = SimpleNamespace(_reader=readers[1])
    services.ugoira_source = SimpleNamespace(reader=readers[2])
    try:
        services._configure_request_preferences()
        assert readers[0].retry is readers[1].retry is readers[2].retry
        assert readers[0].admission is readers[1].admission is readers[2].admission
        store.update({'api_retry_count':'5', 'api_retry_wait':'10.0'}, expected_revision=0)
        assert readers[0].retry._settings() == (5, 10.0)
        calls = []
        def reject():
            calls.append(429)
            raise HTTPStatusRejected(429)
        import time
        with request_scope(allow_retry=False), pytest.raises(HTTPStatusRejected):
            readers[0].retry.execute(reject, deadline=time.monotonic()+30)
        assert calls == [429]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_application_startup_is_nonblocking_once_and_drains_before_resource_close():
    started, release = asyncio.Event(), asyncio.Event()
    calls = []
    class Discovery:
        async def initialize_once(self, refresh, *, is_current):
            calls.append('start')
            started.set()
            await release.wait()
            calls.append(is_current())
    services = ApplicationServices.__new__(ApplicationServices)
    services.closing = False
    services.discovery = Discovery()
    services._startup_discovery_stop = Event()
    services._startup_discovery_task = None
    services.start_discovery_initial_load()
    services.start_discovery_initial_load()
    await started.wait()
    drain = asyncio.create_task(services.drain_startup_discovery())
    await asyncio.sleep(0)
    assert not drain.done()
    assert services.closing
    release.set()
    await drain
    assert calls == ['start', False]


@pytest.mark.asyncio
async def test_shutdown_stops_following_context_before_next_request():
    calls = []
    services = ApplicationServices.__new__(ApplicationServices)
    services.closing = False
    services.discovery = object()
    services._discovery_following_source = None
    services._startup_discovery_stop = Event()
    services._following_snapshot = ('previous',)
    class Reader:
        def require_identity(self):
            calls.append('identity')
            services._startup_discovery_stop.set()
            return '91'
        def get_following_users(self, uid, rest):
            calls.append(rest)
            return []
    services.live_client = Reader()
    with pytest.raises(RuntimeError, match='stopped'):
        await services.refresh_discovery_context()
    assert calls == ['identity']
    assert services._following_snapshot == ('previous',)
