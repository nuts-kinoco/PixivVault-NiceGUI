"""Regressions for coalesced context reads and pin-scoped ranking details."""
import asyncio
from threading import Event
from types import SimpleNamespace

from nicegui import Client, core, ui
import pytest

from pixivvault_nicegui.ui import ranking_card_menus
from pixivvault_nicegui.ui.ranking_view import RankingView
from pixivvault_nicegui.ui.recommend_view import RecommendView
from test_personal_ranking import raw
from test_ranking_source import row
from test_ranking_ui import setup
from test_x_ctx_ranking_20261118 import make as make_recommendations
from test_y_ranking_personal_20261118 import make, open_view


@pytest.fixture
def client():
    client = Client(ui.page('/_d6_cardgrid_r4'))
    try:
        yield client
    finally:
        client.delete()


@pytest.mark.parametrize('change', ['hide', 'local'])
async def test_personal_context_replays_changes_arriving_during_read(client, monkeypatch, change):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    hidden = set()
    release = Event()
    entered = Event()
    reads = []

    def read():
        snapshot = dict(hidden_work_ids=set(hidden), hidden_author_ids=set(),
                        downloaded_ids=set(), hated_ids=set())
        if not release.is_set():
            reads.append(snapshot['hidden_work_ids'])
            entered.set()
            if not release.wait(5):
                raise RuntimeError('fixture read timed out')
        elif entered.is_set():
            reads.append(snapshot['hidden_work_ids'])
        return snapshot

    def hide(services, work_id, enabled):
        assert enabled
        hidden.add(work_id)
        return frozenset(hidden)

    services, vault = make()
    vault.saved = {'tags': ['fixture']}
    services.local_tag_view = SimpleNamespace(revision=lambda: 1)
    services.personal_ranking_context_base = lambda: {}
    try:
        with client:
            view = await open_view(services, vault)
            await view.load()
            services.personal_ranking_context = read
            monkeypatch.setattr(ranking_card_menus, 'set_hidden', hide)
            view._start_context_refresh()
            first_task = view._context_task
            assert await asyncio.to_thread(entered.wait, 4)
            if change == 'hide':
                assert await view.hide_work('123')
                view._after_hidden_change()
            else:
                hidden.add('123')
                view.local_tags_changed()
                view.local_tags_changed()
            release.set()
            await asyncio.wait_for(first_task, 5)
            assert set(view.gallery.cards) == {'124'}
            assert reads == [set(), {'123'}]
    finally:
        release.set()
        services.close()


async def test_recommendation_viewer_change_is_applied_after_inflight_refresh(client, tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services, _ = make_recommendations(tmp_path)
    release = Event()
    entered = Event()
    rendered = []
    done = asyncio.Event()
    read = services.recommendations.refresh

    def delayed_read():
        snapshot = read()
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
        return snapshot

    def render(*, preserve_viewer):
        rendered.append(preserve_viewer)
        if len(rendered) == 2:
            done.set()

    try:
        with client:
            view = RecommendView(services)
            view.active = True
            monkeypatch.setattr(view, 'render', render)
            monkeypatch.setattr(services.recommendations, 'refresh', delayed_read)
            task = asyncio.create_task(view.refresh())
            assert await asyncio.to_thread(entered.wait, 4)
            view._viewer_hidden('123')
            view._viewer_hidden('124')
            release.set()
            await asyncio.wait_for(task, 5)
            await asyncio.wait_for(done.wait(), 2)
            assert rendered == [False, True]
    finally:
        release.set()
        services.close()


@pytest.mark.parametrize('personal', [False, True])
async def test_resolved_ranked_artwork_survives_unchanged_background_projection(client, monkeypatch, personal):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    if personal:
        services, vault = make()
        vault.saved = {'tags': ['fixture']}
        entry = raw()
        entry.pop('page_count')
        vault.search = lambda *args, **kw: {'illusts': [entry], 'next_url': None}
    else:
        services, reader, _ = setup()
    requests = []

    def pages(work_id):
        requests.append(work_id)
        return tuple(f'https://i.pximg.net/fixture-{page}.png' for page in range(5))

    try:
        with client:
            if personal:
                view = await open_view(services, vault)
            else:
                view = RankingView(services)
                view.set_active(True)
            del services.acquire_ranking_images
            services._vault_images = SimpleNamespace(get_artwork_pages=pages)
            await view.load()
            pin = view.pin
            assert pin.needs_metadata('123')
            await view.open_unknown('123', pin, view.epoch, view._open_generation)
            gallery = view.gallery
            state = gallery.state
            assert state.viewer_open and state.selected_artwork.page_count == 5
            assert state.move_page(1)
            epoch = gallery.grid.render_epoch
            if personal:
                view._start_context_refresh()
                await view._context_task
            else:
                view.render()
            assert gallery.state is state and state.selected_page == 1
            assert state.selected_artwork.page_count == 5 and gallery.grid.render_epoch == epoch
            await view.load()
            assert view.pin is pin and gallery.state is state and requests == ['123']

            if personal:
                entry['page_count'] = 2
            else:
                reader.rows = [row(page_count=2)]
            await view.load()
            assert pin.closed and view.pin is not pin
            assert gallery.state.artworks[0].page_count == 2
            assert not gallery.state.viewer_open and requests == ['123']
            view.close()
    finally:
        services.close()
