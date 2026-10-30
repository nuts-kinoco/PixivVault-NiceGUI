"""Original W01 entry points over a frozen generated vector DB; no API or user data."""
from __future__ import annotations

import asyncio
from hashlib import sha256
from threading import Event, get_ident
from types import SimpleNamespace

from nicegui import Client, core, ui, background_tasks
from nicegui.events import GenericEventArguments, handle_event
import pytest
import pytest_asyncio

from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.similar_saved import SavedSimilaritySource
from pixivvault_nicegui.ui.library_view import LibraryView
from test_similar_saved import database, row


@pytest.mark.parametrize('case,expected', [('fallback', '12'), ('representative', '10'), ('missing', '10')])
def test_author_query_uses_first_page_embedding_before_existing_all_page_search(tmp_path, case, expected):
    rows = [row('11', 2, [1, 0]), row('90', 2, [.8, .6]),
            row('91', 1, [.49, .8]), row('90', 2, [.8, .6], kind='style')]
    if case != 'missing':
        rows += [row('12', 1, [1, 0], kind='style'), row('12', 2, [1, 0], kind='style')]
    if case == 'representative':
        rows += [row('10', 1, [1, 0], kind='style')]
    # Neither p2-only nor a different model may displace the real p1 origin.
    rows += [row('10', 1, [1, 0], model='different-model')]
    path = database(tmp_path, rows)
    before = (sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
    source = SavedSimilaritySource(path)
    try:
        chosen, result = source.search_author(('11', '12', '10'), '10')
        assert chosen == expected
        if case == 'missing':
            assert result['status'] == 'no_query_embedding' and not result['results']
        else:
            assert result['kind'] == 'style'
            assert (('90', 2), pytest.approx(.8)) in result['results']
            assert all(key[0] != chosen for key, _ in result['results'])
        assert (sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns) == before
        assert all(not path.with_name(path.name + suffix).exists()
                   for suffix in ('-wal', '-shm', '-journal'))
    finally:
        source.close()


@pytest_asyncio.fixture
async def product(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    from nicegui.app.app import State
    monkeypatch.setattr(core.app, '_state', State.STARTED)
    services = ApplicationServices(120)
    snapshot = services.library_snapshot()
    author = snapshot.authors[0]
    bookmarked = {art.work_id for art in services.bookmark_remote_snapshot()}
    query = next(work for work in author.works
                 if work.work_id != author.representative.work_id and work.work_id in bookmarked)
    hit = next(work for work in snapshot.works
               if work.author_id != author.author_id and work.page_count >= 2)
    rows = [row(query.work_id, 1, [1, 0]),
            row(query.work_id, 2, [1, 0]),  # same-work pages must stay excluded
            row(author.representative.work_id, 2, [0, 1]),
            row(hit.work_id, 2, [.8, .6]),
            row(hit.work_id, 2, [.8, .6], kind='style')]
    path = database(tmp_path, rows)
    signature = (sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
    source = SavedSimilaritySource(path)
    calls = []
    actual_search = source.search
    def search(wid, page=1):
        calls.append(('work', wid, page, get_ident()))
        return actual_search(wid, page)
    source.search = search
    actual_author = getattr(source, 'search_author', None)
    if actual_author is not None:
        def search_author(ids, representative):
            calls.append(('author', tuple(ids), representative, get_ident()))
            return actual_author(ids, representative)
        source.search_author = search_author
    services.similarity_source = source
    client = Client(ui.page('/_w01_context_entries'))
    with client:
        view = LibraryView(services)
    item = SimpleNamespace(view=view, client=client, services=services, source=source,
                           calls=calls, author=author, query=query, hit=hit)
    try:
        yield item
    finally:
        client.delete()
        services.close()
        assert (sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns) == signature
        assert all(not path.with_name(path.name + suffix).exists()
                   for suffix in ('-wal', '-shm', '-journal'))


def entry(card, testid):
    menus = [node for node in card.descendants()
             if node.props.get('context-menu') is True]
    assert len(menus) == 1, 'the original right-click menu is missing'
    assert menus[0].parent_slot.parent is card, 'right click must target the whole card, including its image'
    buttons = [node for node in menus[0].descendants()
               if node.props.get('data-testid') == testid]
    assert len(buttons) == 1, 'the original Similar action is missing'
    return buttons[0]


def registered_click(button):
    listeners = [item for item in button._event_listeners.values() if item.type == 'click']
    assert len(listeners) == 1
    return listeners[0].handler


async def click(button, client, *, handler=None):
    if handler is None:
        handler = registered_click(button)
    before = set(background_tasks.running_tasks)
    with client:
        handle_event(handler,
                     GenericEventArguments(sender=button, client=client, args=None))
    tasks = set(background_tasks.running_tasks) - before
    if tasks:
        await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_author_right_click_chooses_embedded_work_without_changing_card_or_left_click(product):
    p = product
    card = p.view.author_cards[p.author.author_id]
    representative = card.props['data-representative-id']
    button = entry(card, 'library-author-similar-open')
    await click(button, p.client)
    assert p.view.similarity.dialog.value
    assert p.query.artwork.title in p.view.similarity.title.text
    assert representative == p.author.representative.work_id
    assert card is p.view.author_cards[p.author.author_id]
    assert card.props['data-representative-id'] == representative
    assert p.view.state.selected_author_id is None
    assert len(p.calls) == 1 and p.calls[0][0] == 'author'
    assert p.calls[0][-1] != get_ident(), 'SQLite selection/search must leave the UI thread'
    assert any(p.hit.work_id in node.text and '2ページ' in node.text
               for node in p.view.similarity.results.descendants() if isinstance(node, ui.button))
    p.view.similarity.dialog.close()
    await click(p.view.author_openers[p.author.author_id], p.client)
    assert p.view.state.selected_author_id == p.author.author_id


@pytest.mark.asyncio
async def test_bookmark_right_click_keeps_selection_and_opens_result_exact_saved_page(product):
    p = product
    with p.client:
        p.view.show_subview('bookmarks')
        p.view.bookmarks.select(p.query.work_id, True, p.view.bookmarks._session)
    selected = set(p.view.bookmark_state.selected_ids)
    card = p.view.bookmarks.cards[p.query.work_id]
    await click(entry(card, 'bookmark-similar-open'), p.client)
    assert p.view.subview == 'bookmarks' and p.view.similarity.dialog.value
    assert p.view.bookmark_state.selected_ids == selected
    assert p.calls == [('work', p.query.work_id, 1, p.calls[0][-1])]
    result = next(node for node in p.view.similarity.results.descendants()
                  if isinstance(node, ui.button) and node.text.startswith(p.hit.work_id + ' · 2ページ'))
    await click(result, p.client)
    assert p.view.subview == 'downloaded'
    assert p.view._viewer_state.selected_artwork.work_id == p.hit.work_id
    assert p.view._viewer_state.selected_page == 1
    assert p.view.bookmark_state.selected_ids == selected


@pytest.mark.asyncio
async def test_unsaved_bookmark_without_embedding_opens_reason_without_fetch_or_queue(product, monkeypatch):
    p = product
    with p.client:
        p.view.show_subview('bookmarks')
        unsaved = next(item for item in p.view.bookmark_state.items if not item.downloaded)
        p.view.bookmarks.search(unsaved.work_id)
    def forbidden(*_args, **_kwargs):
        raise AssertionError('similarity must not resolve or fetch remote content')
    # Actual product boundaries: metadata preparation, explicit work lookup,
    # save admission and a remote bookmark refresh. Do not invent a resolver.
    monkeypatch.setattr(type(p.view._image_provider), 'prepare_artwork', forbidden)
    monkeypatch.setattr(p.services, 'lookup_managed_work_id', forbidden)
    monkeypatch.setattr(p.services, 'save_explicit_work', forbidden)
    monkeypatch.setattr(p.services, 'refresh_bookmark_cache', forbidden)
    jobs = p.services.jobs
    before = jobs.snapshot() if jobs is not None else None
    await click(entry(p.view.bookmarks.cards[unsaved.work_id], 'bookmark-similar-open'), p.client)
    assert p.view.similarity.dialog.value
    assert 'このページの保存済み解析データがありません' in p.view.similarity.notice.text
    assert not p.view.similarity.results.default_slot.children
    assert p.calls == [('work', unsaved.work_id, 1, p.calls[0][-1])]
    assert p.services.jobs is jobs
    if jobs is not None:
        assert jobs.snapshot() == before
    assert not p.view._viewer_state.viewer_open


@pytest.mark.asyncio
@pytest.mark.parametrize('surface', ['author', 'bookmark'])
async def test_queued_old_menu_action_cannot_cross_reload_or_bookmark_revisit(product, surface):
    p = product
    with p.client:
        if surface == 'author':
            button = entry(p.view.author_cards[p.author.author_id], 'library-author-similar-open')
            handler = registered_click(button)
            p.view.reload()
        else:
            p.view.show_subview('bookmarks')
            button = entry(p.view.bookmarks.cards[p.query.work_id], 'bookmark-similar-open')
            handler = registered_click(button)
            p.view.show_subview('downloaded')
            p.view.show_subview('bookmarks')
    before = set(p.view.bookmark_state.selected_ids)
    # The packet was queued before deletion cleared the old element's table.
    await click(button, p.client, handler=handler)
    assert not p.calls
    assert not p.view.similarity.dialog.value
    assert p.view.bookmark_state.selected_ids == before


@pytest.mark.asyncio
async def test_pending_author_query_cannot_reappear_after_switch_to_bookmarks(product, monkeypatch):
    p = product
    button = entry(p.view.author_cards[p.author.author_id], 'library-author-similar-open')
    entered, release = Event(), Event()
    real = p.source.search_author
    def held(*args):
        entered.set()
        assert release.wait(5)
        return real(*args)
    monkeypatch.setattr(p.source, 'search_author', held)
    task = asyncio.create_task(click(button, p.client))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        assert p.view.similarity.dialog.value and '探しています' in p.view.similarity.notice.text
        with p.client:
            p.view.show_subview('bookmarks')
        release.set()
        await task
        assert not p.view.similarity.dialog.value
        assert not p.view.similarity.results.default_slot.children
        assert p.view.subview == 'bookmarks'
    finally:
        release.set()
        await task
