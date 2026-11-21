"""Exercise actual viewer, FastAPI routes and their ownership boundaries."""

import ast
import asyncio
from base64 import b64decode
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from nicegui import Client, ui

from pixivvault_nicegui.media_routes import register_media_routes
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.gallery import GalleryView


GIF = b64decode('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7')


@pytest.fixture
def animated_gallery():
    services = ApplicationServices(3)
    app = FastAPI()
    register_media_routes(app, services.images)
    request = Request({'type': 'http', 'path': '/', 'headers': [], 'app': app})
    client = Client(ui.page('/_d6_gif'), request=request)
    with client:
        view = GalleryView(services)
    art = services.artworks[0]
    display = SimpleNamespace(record=SimpleNamespace(remote_id=art.work_id, author_id=art.author_id), data=GIF)
    view._image_provider = SimpleNamespace(is_animation=lambda _: True, display_animation=lambda _: display)
    try:
        yield services, app, client, view, art
    finally:
        if not client.is_deleted:
            client.delete()
        services.close()


async def display_gif(view, art):
    view.open(art.work_id)
    await view._animation_task
    return view.viewer_image.props['src']


@pytest.mark.asyncio
async def test_gif_get_head_and_key_format(animated_gallery):
    _, app, _, view, art = animated_gallery
    url = await display_gif(view, art)
    key = url.removeprefix('/media/viewer-gif/').removesuffix('.gif')
    assert UUID(hex=key, version=4).hex == key
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://testserver') as http:
        response = await http.get(url)
        assert response.status_code == 200 and response.content == GIF
        assert response.headers['content-type'] == 'image/gif'
        assert response.headers['cache-control'] == 'private, no-store'
        assert response.headers['x-content-type-options'] == 'nosniff'
        for bad in ('invalid', key.upper(), key[:-1], key[:12] + '1' + key[13:], key[:16] + '0' + key[17:]):
            assert (await http.get(f'/media/viewer-gif/{bad}.gif')).status_code == 404
        response = await http.head(url)
        assert response.status_code == 200 and response.content == b''
        assert response.headers['content-length'] == str(len(GIF))
        assert response.headers['content-type'] == 'image/gif'


@pytest.mark.asyncio
async def test_gif_is_unavailable_to_another_app_with_the_same_catalog(animated_gallery):
    services, _, _, view, art = animated_gallery
    url = await display_gif(view, art)
    other_app = FastAPI()
    register_media_routes(other_app, services.images)
    async with AsyncClient(transport=ASGITransport(app=other_app), base_url='http://testserver') as http:
        assert (await http.get(url)).status_code == 404


@pytest.mark.asyncio
async def test_gif_does_not_wait_for_catalog_publication(animated_gallery):
    services, app, _, view, art = animated_gallery
    url = await display_gif(view, art)
    closing, release, expired = Event(), Event(), Event()

    class HeldProvider:
        def close(self):
            closing.set()
            if not release.wait(3):
                expired.set()

    # Publishing the next snapshot prunes the old provider under the real
    # registry lock. The already-rendered GIF requires no access to that work.
    services.images.publish(HeldProvider(), None)
    publish = asyncio.create_task(asyncio.to_thread(services.images.publish, None, None))
    try:
        assert await asyncio.to_thread(closing.wait, 5)
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://testserver') as http:
            assert (await http.get(url)).content == GIF
        assert not expired.is_set(), 'GET waited for the publishing thread to release the registry lock'
    finally:
        release.set()
        await publish


@pytest.mark.parametrize('release', ['failure', 'retry', 'close', 'query-change',
                                   'page-delete', 'client-delete', 'service-close'])
@pytest.mark.asyncio
async def test_gif_route_expires_on_owner_lifecycle(animated_gallery, release):
    services, app, client, view, art = animated_gallery
    url = await display_gif(view, art)
    image = view.viewer_image
    source = image._viewer_gif  # Keep a reference to verify explicit revocation.
    assert source is not None
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://testserver') as http:
        assert (await http.get(url)).status_code == 200
        if release == 'failure':
            view._image_failed(SimpleNamespace(args=view._image_url))
            assert view._displayed_gif is None
            assert image._viewer_gif is None and image.props['src'] == ''
        elif release == 'retry':
            prior = view._image_url
            view.request_image()
            await view._animation_task
            view._image_failed(SimpleNamespace(args=prior))
            assert image.props['src'] != url
            assert (await http.get(image.props['src'])).status_code == 200
        elif release == 'close':
            view.close_viewer()
            assert view._displayed_gif is None
        elif release == 'query-change':
            assert not view.viewer.has_opened  # Browser decode has not returned yet.
            view.search('does-not-match')
            assert view._displayed_gif is None
        elif release == 'page-delete':
            view.viewer.page.delete()
        elif release == 'client-delete':
            client.delete()
            assert view._displayed_gif is None
        else:
            png_url = services.images.url(art.work_id, 0, 'view')
            services.close()
            assert (await http.get(png_url)).status_code == 503
        status = 503 if release == 'service-close' else 404
        assert (await http.get(url)).status_code == status
        assert (await http.head(url)).status_code == status


@pytest.mark.asyncio
async def test_late_gif_render_cannot_publish_into_a_deleted_page(animated_gallery):
    _, app, _, view, art = animated_gallery
    started, finish = Event(), Event()
    render = view._image_provider.display_animation

    def delayed_render(work_id):
        started.set()
        assert finish.wait(5)
        return render(work_id)

    view._image_provider.display_animation = delayed_render
    view.open(art.work_id)
    try:
        assert await asyncio.to_thread(started.wait, 5)
        view.viewer.page.delete()
    finally:
        finish.set()
        await view._animation_task
    assert view._displayed_gif is None
    assert view.viewer_image._viewer_gif is None
    assert not app.state.viewer_gifs


def test_gif_rejects_non_bytes_without_replacing_the_current_source(animated_gallery):
    _, _, _, view, _ = animated_gallery
    view.viewer_image.set_gif(GIF, event_source='old')
    with pytest.raises(TypeError):
        view.viewer_image.set_gif(bytearray(GIF), event_source='new')
    assert view.viewer_image.props['event_source'] == 'old'


def test_viewer_http_resource_routes_are_covered():
    source = Path(__file__).resolve().parents[1] / 'src' / 'pixivvault_nicegui' / 'media_routes.py'
    tree = ast.parse(source.read_bytes(), filename=str(source))
    routes = [node.args[0].value for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr == 'add_api_route']
    assert sorted(routes) == ['/media/viewer-gif/{key}.gif', '/media/{work_id}/{page_no}/{variant}.png']
