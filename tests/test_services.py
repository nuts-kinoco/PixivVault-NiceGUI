import pytest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from fastapi import FastAPI

from pixivvault_nicegui.services import ApplicationServices, get_services


def test_app_scoped_services_and_lifecycle():
    first_app, second_app = FastAPI(), FastAPI()
    first = get_services(first_app)
    assert first is get_services(first_app)
    second = get_services(second_app)
    assert first is not second
    first.images.url(first.artworks[0].work_id)
    first.close()
    first.close()
    with pytest.raises(RuntimeError, match="closed"):
        get_services(first_app)
    assert get_services(second_app) is second
    second.images.url(second.artworks[0].work_id)
    second.close()


def test_services_close_does_not_depend_on_view_lifetime():
    services = ApplicationServices(count=2)
    assert len(services.artworks) == 2
    assert services.images.get(services.artworks[0].work_id).data.startswith(b"\x89PNG")
    services.close()
    with pytest.raises(RuntimeError):
        services.images.get(services.artworks[0].work_id)


def test_concurrent_close_calls_underlying_catalog_once(monkeypatch):
    services = ApplicationServices(count=1)
    entered, release = Event(), Event()
    calls = []
    original_close = services.images.close

    def slow_close():
        calls.append(True)
        entered.set()
        assert release.wait(timeout=5)
        original_close()

    monkeypatch.setattr(services.images, "close", slow_close)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(services.close)
        assert entered.wait(timeout=5)
        second = pool.submit(services.close)
        release.set()
        first.result(timeout=5)
        second.result(timeout=5)
    assert len(calls) == 1
    assert services.closed
