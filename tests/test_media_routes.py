import asyncio

import httpx
from fastapi import FastAPI

from pixivvault_nicegui.media import DemoImageCatalog
from pixivvault_nicegui.media_routes import register_media_routes
from pixivvault_nicegui.models import Artwork


def test_get_head_etag_and_404():
    async def run():
        app = FastAPI()
        catalog = DemoImageCatalog([Artwork("demo", "test", "tester", page_count=2)])
        register_media_routes(app, catalog)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            url = catalog.url("demo", 1)
            response = await client.get(url)
            assert response.status_code == 200
            assert response.content.startswith(b"\x89PNG")
            assert response.headers["content-type"] == "image/png"
            assert response.headers["x-content-type-options"] == "nosniff"
            assert "max-age" in response.headers["cache-control"]
            etag = response.headers["etag"]
            head = await client.head(url)
            assert head.status_code == 200 and head.content == b""
            assert head.headers["etag"] == etag
            assert int(head.headers["content-length"]) == len(response.content)
            matched = await client.get(url, headers={"If-None-Match": etag})
            assert matched.status_code == 304 and matched.content == b""
            assert matched.headers["etag"] == etag
            assert (await client.get(url, headers={"If-None-Match": "W/" + etag})).status_code == 304
            for path in ("/media/nope/0/view.png", "/media/demo/2/view.png", "/media/demo/0/evil.png", "/media/demo/00/view.png", "/media/demo/-1/view.png", "/media/demo/x/view.png", "/media/demo/0/view.png/extra", "/media/demo/" + "9" * 50000 + "/view.png"):
                assert (await client.get(path)).status_code in (404, 405)
            catalog.close()
            assert (await client.get(url)).status_code == 503
    asyncio.run(run())


def test_unexpected_generator_failure_is_server_error(monkeypatch):
    async def run():
        app = FastAPI()
        catalog = DemoImageCatalog([Artwork("demo", "test", "tester")])
        register_media_routes(app, catalog)
        def broken(*args, **kwargs):
            raise OSError("generator unavailable")
        monkeypatch.setattr(catalog, "get", broken)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as client:
            assert (await client.get("/media/demo/0/view.png")).status_code == 500
    asyncio.run(run())
