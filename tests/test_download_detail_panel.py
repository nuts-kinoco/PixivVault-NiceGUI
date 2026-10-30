"""U17 original download detail affordance with actual state and safe layout."""
from types import SimpleNamespace

from nicegui import Client, ui
import pytest

from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.job_views import DownloadView
from pixivvault_nicegui.ui.managed_download_view import ManagedDownloadView


def managed_services():
    class Fake:
        managed_downloads = object()
        jobs = None
        author_media = None

        def download_candidates(self):
            return ()
    service = Fake()
    service.jobs = SimpleNamespace(snapshot=lambda: (
        SimpleNamespace(state='queued',title='First queued item'),
        SimpleNamespace(state='running',title='Second item'),
        SimpleNamespace(state='error',title='Retryable item'),
    ))
    return service


@pytest.mark.parametrize('managed', [False, True])
def test_u17_open_close_detail_has_real_queue_state_and_aria(managed):
    services = managed_services() if managed else ApplicationServices(3)
    client = Client(ui.page('/_u17_detail_'+str(managed).lower()))
    try:
        with client:
            view = ManagedDownloadView(services) if managed else DownloadView(services)
            assert not view.details.opened
            assert view.details.button.props['aria-expanded'] == 'false'
            assert any(e.type == 'dblclick' for e in view.details.bar._event_listeners.values())
            assert view.details.toggle(viewport_height=720)
            assert view.details.opened
            assert view.details.button.props['aria-expanded'] == 'true'
            assert '待機中 1件' in view.details.queue_label.text if managed else '0件' in view.details.queue_label.text
            assert 'エラー 1件' in view.details.queue_label.text if managed else True
            assert view.details.toggle(viewport_height=720)
            assert not view.details.opened
            view.close()
            assert not view.details.toggle(viewport_height=720)
    finally:
        client.delete()
        if not managed:
            services.close()


def test_u17_short_window_fails_visibly_and_recovers_after_resize():
    services = managed_services()
    client=Client(ui.page('/_u17_short'))
    try:
        with client:
            view=ManagedDownloadView(services)
            assert not view.details.toggle(viewport_height=430)
            assert not view.details.opened
            assert '高さ' in view.details.notice.text
            assert view.details.toggle(viewport_height=920)
            assert view.details.opened
            # Closing must remain possible even when the viewport gets short.
            assert view.details.toggle(viewport_height=430)
            assert not view.details.opened
    finally:
        client.delete()


def test_u17_tab_roundtrip_retains_expansion_and_rejects_stale_event():
    services=managed_services()
    client=Client(ui.page('/_u17_roundtrip'))
    try:
        with client:
            view=ManagedDownloadView(services)
            assert view.details.toggle(viewport_height=720)
            old=view.epoch
            view.set_active(False)
            assert view.details.opened
            view.details.handle_event(SimpleNamespace(args={'epoch':str(old),'viewportHeight':720}))
            assert view.details.opened
            view.set_active(True)
            assert view.details.opened
            assert view.details.button.props['aria-expanded'] == 'true'
            assert view.details.toggle(viewport_height=720)
            assert not view.details.opened
    finally:
        client.delete()
