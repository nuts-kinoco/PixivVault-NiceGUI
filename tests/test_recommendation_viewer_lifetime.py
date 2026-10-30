"""Real reader + common viewer lifetime, including animated recommendations.

Every HTTP request is handled by SyntheticRecommendationHTTP. Original imagery,
Cookie sources and the external network are not used.
"""
from __future__ import annotations

import asyncio
import base64
from io import BytesIO
import json
import re
from threading import Event
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image
from nicegui import ui
import pytest

from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient
from pixivvault_nicegui.recommendation_state import (
    RecommendationController, RecommendationGroup, RecommendationSnapshot,
    RecommendationWork)
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.shell import DemoShell
from test_pixiv_actions import SyntheticHTTP
from test_recommendation_visual_shell import product_client


class SyntheticRecommendationHTTP(SyntheticHTTP):
    def __init__(self):
        super().__init__()
        self.block_detail = {}
        self.archive = BytesIO()
        with ZipFile(self.archive, 'w', compression=ZIP_DEFLATED) as archive:
            for index, color in enumerate(('red', 'green', 'blue')):
                raw = BytesIO()
                Image.new('RGB', (96, 64), color).save(raw, 'PNG')
                archive.writestr(f'{index:06d}.png', raw.getvalue())

    @staticmethod
    def image_url(wid, page=0, *, original=False):
        folder = 'img-original' if original else 'img-master'
        suffix = '' if original else '_master1200'
        extension = 'png' if original else 'jpg'
        return (f'https://i.pximg.net/{folder}/img/2026/01/01/00/00/00/'
                f'{wid}_p{page}{suffix}.{extension}')

    def get(self, request, timeout, limit, deadline):
        assert request.get_method() == 'GET'
        route = urlsplit(request.full_url)
        assert route.hostname in ('www.pixiv.net', 'i.pximg.net')
        match = re.fullmatch(r'/ajax/illust/([1-9][0-9]*)(?:/(pages|ugoira_meta))?', route.path)
        if route.hostname == 'www.pixiv.net' and match:
            wid, operation = match.groups()
            self.reads.append((route.path, route.query))
            if operation is None:
                blocked = self.block_detail.get(wid)
                if blocked is not None:
                    started, release = blocked
                    started.set()
                    assert release.wait(4), 'synthetic delayed GET was not released'
                body = dict(id=wid, userId='91', userName='Synthetic author',
                    title='Synthetic ' + wid, description='Synthetic description',
                    tags=['synthetic'], urls=dict(small=self.image_url(wid)),
                    pageCount=1 if wid == '13' else 3, illustType=2 if wid == '13' else 1,
                    xRestrict=0, aiType=0, width=160, height=120,
                    createDate='2026-01-01T00:00:00Z',
                    updateDate='2026-01-01T00:00:00Z',
                    bookmarkCount=0, viewCount=0, bookmarkData=self.bookmark)
            elif operation == 'pages':
                body = [dict(urls=dict(regular=self.image_url(wid, page),
                                       original=self.image_url(wid, page, original=True)))
                        for page in range(3)]
            else:
                assert wid == '13'
                body = dict(originalSrc='https://i.pximg.net/img-zip-ugoira/fixture/13_ugoira600x600.zip',
                    frames=[dict(file=f'{n:06d}.png', delay=delay)
                            for n, delay in enumerate((40, 90, 170))])
            return json.dumps(dict(error=False, body=body)).encode()
        if route.hostname == 'i.pximg.net':
            self.reads.append((route.path, route.query))
            if route.path == '/img-zip-ugoira/fixture/13_ugoira600x600.zip':
                return self.archive.getvalue()
            image = re.search(r'/([1-9][0-9]*)_p([0-9]+)(?:_master1200)?\.(?:png|jpg)$', route.path)
            assert image is not None, 'unexpected synthetic image route'
            wid, page = map(int, image.groups())
            raw = BytesIO()
            size = (160, 120) if route.path.startswith('/img-original/') else (80, 60)
            Image.new('RGB', size, (wid % 255, page * 40, 90)).save(raw, 'PNG')
            return raw.getvalue()
        return super().get(request, timeout, limit, deadline)


@pytest.fixture
def remote_application(tmp_path):
    http = SyntheticRecommendationHTTP()
    cookie = tmp_path / 'synthetic-recommendation-cookie.txt'
    cookie.write_text('# Netscape HTTP Cookie File\n.pixiv.net\tTRUE\t/\tTRUE\t'
        + str(int(time.time()) + 86400) + '\tPHPSESSID\t55_synthetic\n')
    before = cookie.read_bytes()
    reader = LivePixivReadClient(cookie, transport=http.get)
    services = ApplicationServices(live_client=reader)
    try:
        yield SimpleNamespace(http=http, reader=reader, services=services)
    finally:
        for _, release in http.block_detail.values():
            release.set()
        services.close()
        assert cookie.read_bytes() == before
        assert http.posts == []


def item(wid, *, unknown=False):
    return RecommendationWork(wid, 'Synthetic ' + wid, '91', 'Synthetic author',
        SyntheticRecommendationHTTP.image_url(wid),
        None if unknown else (1 if wid == '13' else 3),
        None if unknown else (2 if wid == '13' else 1),
        ('synthetic',), '2026-01-01T00:00:00Z', 0)


async def mount(env, client, works):
    snapshot = RecommendationSnapshot(
        sections=(('superlike', (RecommendationGroup('superlike', 'Synthetic shelf', works),)),),
        unavailable=('certain', 'boosted', 'trend', 'trend_added'))
    env.services.recommendations = RecommendationController(
        SimpleNamespace(load=lambda: snapshot))
    with client, ui.column():
        shell = DemoShell(env.services)
        shell.show('recommend')
    await shell.recommend_view.refresh()
    return shell, shell.recommend_view


@pytest.mark.asyncio
@pytest.mark.parametrize('leave', ('another-work', 'tab', 'client-view-close'))
async def test_delayed_unknown_work_cannot_replace_another_selection_or_reopen_closed_view(
        remote_application, product_client, leave):
    env = remote_application
    shell, view = await mount(env, product_client, (item('11', unknown=True), item('12')))
    started, release = Event(), Event()
    env.http.block_detail['11'] = (started, release)
    task = asyncio.create_task(view.open_work('11', generation=view._render_generation))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        # A second click on the same pending card must not duplicate its GET.
        await view.open_work('11', generation=view._render_generation)
        if leave == 'another-work':
            await view.open_work('12', generation=view._render_generation)
            assert view._recommend_viewer._viewer_state.selected_artwork.work_id == '12'
        elif leave == 'tab':
            shell.show('ranking')
        else:
            shell.close()
        release.set()
        await asyncio.wait_for(task, 3)
        selected = view._recommend_viewer._viewer_state.selected_artwork
        assert (selected.work_id if selected else None) == ('12' if leave == 'another-work' else None)
        assert sum(path == '/ajax/illust/11' for path, _ in env.http.reads) == 1
        assert not env.reader._closed and env.http.posts == []
    finally:
        release.set()
        await asyncio.wait_for(task, 3)
        shell.close()
    assert len(env.services.images._versions) == 1


@pytest.mark.asyncio
async def test_recommended_ugoira_uses_common_manifest_archive_gif_viewer_not_cover(
        remote_application, product_client):
    env = remote_application
    shell, view = await mount(env, product_client, (item('13'),))
    try:
        assert env.http.reads == []
        await view.open_work('13', generation=view._render_generation)
        viewer = view._recommend_viewer
        await asyncio.wait_for(viewer._animation_task, 3)
        displayed = viewer._displayed_gif
        assert displayed is not None and displayed.record.remote_id == '13'
        assert displayed.record.illustration_type == 2
        with Image.open(BytesIO(displayed.data)) as animation:
            assert animation.format == 'GIF' and animation.n_frames == 3
            durations = []
            for frame in range(animation.n_frames):
                animation.seek(frame)
                durations.append(animation.info['duration'])
            assert durations == [40, 90, 170]
        source = viewer.viewer_image.props['src']
        assert source.startswith('data:image/gif;base64,')
        assert base64.b64decode(source.partition(',')[2]) == displayed.data
        assert any(path == '/ajax/illust/13/ugoira_meta' for path, _ in env.http.reads)
        assert any(path.endswith('/13_ugoira600x600.zip') for path, _ in env.http.reads)
        assert not any(path.endswith('/pages') or path == '/ajax/illust/13'
                       or path.startswith('/img-master/') for path, _ in env.http.reads)
        assert viewer._quality == 'view' and not viewer._quality_available
        assert not viewer.toggle_quality() and env.http.posts == []
    finally:
        shell.close()
    assert len(env.services.images._versions) == 1
