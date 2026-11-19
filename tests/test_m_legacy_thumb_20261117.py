"""Legacy (read-only old library) media URLs must use the served /media route."""
import re
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from required_network_guard import no_network
from pixivvault_nicegui.media_routes import register_media_routes
from pixivvault_nicegui.legacy_quarantine import LegacyQuarantine
from pixivvault_nicegui.library_media_registry import LibraryImageRegistry  # noqa: F401
from test_required_legacy_duplicates import setup

_ROUTE = re.compile(r'^/media/(\d+)/(\d+)/(thumb|view|original)\.png$')


class _Versioned:
    """Stand-in for the registry: token-aware get, delegating to the legacy catalog."""
    def __init__(self, catalog):
        self.catalog = catalog

    def get_versioned(self, token, work_id, page_no, variant):
        assert token == "a" * 32
        return self.catalog.get(work_id, page_no, variant)


@pytest.fixture
def projected(tmp_path):
    source, legacy, owner, snapshot, original, archive = setup(tmp_path, readonly=True)
    try:
        _, catalog = legacy.project(snapshot)
        yield catalog
    finally:
        source.close()


@pytest.mark.parametrize("variant", ["thumb", "view", "original"])
def test_legacy_url_is_media_route_without_query(projected, variant):
    url = projected.url("1", 0, variant)
    assert _ROUTE.fullmatch(url) and url.endswith(f"/{variant}.png")
    assert "?" not in url


def test_legacy_url_with_catalog_token_has_single_query(projected):
    from pixivvault_nicegui.media import DemoImageCatalog
    reg = LibraryImageRegistry(DemoImageCatalog(()))
    try:
        token = reg.publish(projected, None)
        url = reg.url_for(token, "1", 0, "thumb")
        assert url == f"/media/1/0/thumb.png?catalog={token}"
        assert url.count("?") == 1
    finally:
        reg.close()


@pytest.mark.parametrize("variant", ["thumb", "view", "original"])
def test_legacy_media_served_200(projected, variant):
    app = FastAPI()
    register_media_routes(app, _Versioned(projected))
    client = TestClient(app)
    for wid in ("1", "2", "3"):
        path = projected.url(wid, 0, variant) + "?catalog=" + "a" * 32
        r = client.get(path)
        assert r.status_code == 200, (path, r.status_code)
        assert r.headers["content-type"] == "image/png" or variant == "original"
        assert r.content


def test_legacy_invalid_variant_and_missing_page_stay_404(projected):
    from pixivvault_nicegui.local_media_adapter import MediaMissing
    with pytest.raises(MediaMissing):
        projected.url("1", 0, "bogus")
    with pytest.raises(MediaMissing):
        projected.url("1", 5, "thumb")
    app = FastAPI()
    register_media_routes(app, _Versioned(projected))
    client = TestClient(app)
    assert client.get("/media/1/5/thumb.png?catalog=" + "a" * 32).status_code == 404
    assert client.get("/media/999/0/thumb.png?catalog=" + "a" * 32).status_code == 404


def test_excluded_page_stays_404(tmp_path):
    from test_required_legacy_duplicates import select
    from uuid import uuid4
    source, legacy, owner, snapshot, original, archive = setup(tmp_path)
    try:
        plan = select(source, {"2"})
        source.execute(plan, operation_id=uuid4().hex)
        _, catalog = legacy.project(snapshot)
        app = FastAPI()
        register_media_routes(app, _Versioned(catalog))
        client = TestClient(app)
        assert client.get("/media/2/0/thumb.png?catalog=" + "a" * 32).status_code == 404
        assert client.get("/media/1/0/thumb.png?catalog=" + "a" * 32).status_code in (200, 404)
    finally:
        source.close()
