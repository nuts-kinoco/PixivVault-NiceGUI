"""PV-B real UI components with injected GET readers, no network or user data."""
import asyncio
from threading import Event
from types import SimpleNamespace
import pytest
from nicegui import Client, ui
from pixivvault_nicegui.models import Artwork
from pixivvault_nicegui.viewer_metadata import read_details, read_author, pixiv_url
from pixivvault_nicegui.ui.gallery import GalleryView
from pixivvault_nicegui.ui.author_browse_view import AuthorBrowseView


class Reader:
    def __init__(self): self.details=[];self.authors=[];self.entered=None;self.release=None;self.fail=False
    def require_identity(self): return '7'
    def get_artwork_metadata_raw(self,wid,aid):
        self.details.append((wid,aid))
        if self.entered:
            self.entered.set();assert self.release.wait(5)
        if self.fail:raise OSError('synthetic read failure')
        return dict(id=wid,userId=aid,description='<p>caption</p><script>bad()</script>',
                    tags={'tags':[{'tag':'blue'}]},bookmarkCount=0,viewCount=12,width=800,height=600,createDate='2026-10-01')
    def get_user_works(self,aid,*,lane):
        self.authors.append((aid,lane))
        if self.fail:raise OSError('synthetic read failure')
        return [dict(id=str(i),user_id=aid,title=f'work {i}',user_name='Author',tags=['blue'],
                     thumbnail_url='https://i.pximg.net/synthetic.png',page_count=2,illust_type=0,x_restrict=0) for i in range(101,152)]


class Images:
    def __init__(self):self.closed=False
    def url(self,wid,page=0,variant='view'):return f'/synthetic/{wid}/{page}/{variant}.png'
    def can_open(self,wid):return True
    def needs_metadata(self,wid):return False
    def close(self):self.closed=True


def services(reader):
    pins=[]
    def acquire(records):
        pin=Images();pins.append(pin);return pin
    return SimpleNamespace(artworks=(Artwork('101','First','Author',author_id='91',page_count=2),
                                    Artwork('102','Second','Other',author_id='92')),
                           images=Images(),live_client=reader,managed_downloads=None,
                           acquire_ranking_images=acquire,pins=pins)


def test_detail_identity_plain_text_and_unknown_values():
    reader=Reader();data=read_details(reader,'101','91')
    assert data['description']=='caption' and data['tags']==('blue',)
    assert data['bookmarkCount']==0 and data['viewCount']==12
    reader.get_artwork_metadata_raw=lambda *args:dict(id='999',userId='91')
    with pytest.raises(ValueError):read_details(reader,'101','91')
    assert pixiv_url('101')=='https://www.pixiv.net/artworks/101'
    assert pixiv_url('javascript:alert(1)') is None


def test_author_wrong_identity_and_duplicate_rows_are_rejected():
    reader=Reader();records=read_author(reader,'91')
    assert len(records)==51 and records[0].work_id=='101' and records[-1].work_id=='151'
    raw=reader.get_user_works('91',lane='interactive')
    reader.get_user_works=lambda *args,**kwargs:raw+[raw[0]]
    with pytest.raises(ValueError):read_author(reader,'91')
    reader.get_user_works=lambda *args,**kwargs:[dict(raw[0],user_id='92')]
    with pytest.raises(ValueError):read_author(reader,'91')


@pytest.mark.asyncio
async def test_real_viewer_lazily_loads_details_and_discards_other_work_response():
    reader=Reader();svc=services(reader);client=Client(ui.page('/_pv_b_details'));tasks=[]
    try:
        with client:view=GalleryView(svc)
        with client:
            view.open('101');assert reader.details==[]
            reader.entered=Event();reader.release=Event()
            before=asyncio.all_tasks();view.toggle_details()
            tasks=list(asyncio.all_tasks()-before)
            assert await asyncio.to_thread(reader.entered.wait,5)
            view.close_viewer();view.open('102')
            reader.release.set();await asyncio.gather(*tasks)
            assert view.viewer.title.text=='Second' and view.description.text!='caption'
            reader.entered=None
            view.close_viewer();view.open('101');view.toggle_details()
            assert view.description.text=='caption'
            assert 'ブクマ 0' in view.viewer.numbers.text and '800 × 600' in view.viewer.numbers.text
            assert view.viewer.pixiv_link.props['href'].endswith('/101')
            assert reader.details==[('101','91')]
    finally:
        if reader.release:reader.release.set()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        client.delete()


@pytest.mark.asyncio
async def test_author_overlay_50_more_revisit_pin_lifetime_and_failure():
    reader=Reader();svc=services(reader);client=Client(ui.page('/_pv_b_author'))
    try:
        with client:overlay=AuthorBrowseView(svc,reader)
        with client:
            await overlay.show('91','Author')
            assert overlay.gallery.state.shown_count==50
            first_ids={wid:card.id for wid,card in overlay.gallery.cards.items()}
            overlay.gallery.more()
            assert overlay.gallery.state.shown_count==51
            assert all(overlay.gallery.cards[wid].id==node for wid,node in first_ids.items())
            overlay.gallery.open('101')
            assert overlay.gallery._viewer_state.selected_artwork.work_id=='101'
            overlay.hide();await overlay.show('91','Author')
            assert reader.authors==[('91','interactive')]
            assert svc.pins == [overlay.pin] and not overlay.pin.closed
            assert overlay.gallery.state.shown_count == 51
            reader.fail=True;await overlay.show('92','Other')
            assert '取得できません' in overlay.status.text and overlay.retry.visible
            assert '92' not in overlay.cache
            reader.fail=False
            await overlay.retry_load()
            assert overlay.gallery._viewer_active and not overlay.retry.visible
            assert reader.authors == [('91','interactive'),('92','interactive'),('92','interactive')]
            overlay.close();assert all(p.closed for p in svc.pins)
    finally:client.delete()


@pytest.mark.asyncio
async def test_cached_author_revisit_keeps_expansion_query_and_existing_card_nodes():
    """Original same-author reopen preserves the existing expanded gallery."""
    reader = Reader()
    get_user_works = reader.get_user_works

    def many_works(author_id, *, lane):
        rows = get_user_works(author_id, lane=lane)
        return rows + [dict(rows[0], id=str(i), title=f'work {i}')
                       for i in range(152, 221)]

    reader.get_user_works = many_works
    svc = services(reader)
    client = Client(ui.page('/_pv_b_author_cached_revisit'))
    try:
        with client:
            overlay = AuthorBrowseView(svc, reader)
            await overlay.show('91', 'Author')
            assert overlay.gallery.state.shown_count == 50
            overlay.gallery.search_input.set_value('blue')
            overlay.gallery.search('blue')
            overlay.gallery.apply_card_size('small')
            overlay.gallery.more()
            assert overlay.gallery.state.shown_count == 100
            assert overlay.gallery.state.total_count == 120
            state = overlay.gallery.state
            grid = overlay.gallery.grid
            scroll_owner = grid.parent_slot.parent
            cards = dict(overlay.gallery.cards)
            pin = overlay.pin
            epoch = overlay.epoch

            overlay.hide()
            await overlay.show('91', 'Author renamed')

            assert overlay.gallery.state.shown_count == 100
            assert overlay.gallery.state is state and not state.closed
            assert state.query == 'blue' and state.card_size == 'small'
            assert overlay.gallery.search_input.value == 'blue'
            assert overlay.gallery.grid is grid
            assert grid.parent_slot.parent is scroll_owner
            assert overlay.gallery.cards == cards
            assert all(overlay.gallery.cards[wid] is card for wid, card in cards.items())
            assert overlay.pin is pin and svc.pins == [pin] and not pin.closed
            assert reader.authors == [('91', 'interactive')]
            assert overlay.epoch > epoch
            assert overlay.visible and overlay.gallery._viewer_active
            assert overlay.gallery.root.visible and not overlay.retry.visible
            assert overlay.title.text == 'Author renamed (91)'
            overlay.close()
            assert pin.closed
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_author_revisit_rebuilds_for_replaced_cached_snapshot():
    from dataclasses import replace

    reader = Reader()
    svc = services(reader)
    client = Client(ui.page('/_pv_b_author_replaced_cache'))
    overlay = None
    try:
        with client:
            overlay = AuthorBrowseView(svc, reader)
            await overlay.show('91', 'Author')
            overlay.gallery.search_input.set_value('blue')
            overlay.gallery.search('blue')
            overlay.gallery.more()
            old_state = overlay.gallery.state
            old_cards = dict(overlay.gallery.cards)
            old_pin = overlay.pin
            assert old_state.shown_count == 51
            overlay.cache['91'] = tuple(
                replace(record, title='new snapshot ' + record.work_id)
                for record in overlay.cache['91'])
            overlay.hide()
            await overlay.show('91', 'Current author')

            assert overlay.gallery.state is not old_state and old_state.closed
            assert overlay.gallery.state.shown_count == 50
            assert overlay.gallery.state.query == ''
            assert overlay.gallery.search_input.value == ''
            assert overlay.pin is not old_pin and old_pin.closed
            assert overlay.pin is svc.pins[-1] and not overlay.pin.closed
            assert all(card is not old_cards.get(wid)
                       for wid, card in overlay.gallery.cards.items())
            assert {work.author_id for work in overlay.gallery.state.artworks} == {'91'}
            assert overlay.gallery._viewer_active and not overlay.retry.visible
            assert all(work.title.startswith('new snapshot ')
                       for work in overlay.gallery.state.artworks)
            assert reader.authors == [('91', 'interactive')]
    finally:
        if overlay is not None:
            overlay.close()
        client.delete()


@pytest.mark.asyncio
async def test_explicit_author_retry_owns_cache_after_older_read_finishes():
    reader = Reader()
    get_user_works = reader.get_user_works
    entered, release = Event(), Event()

    def delayed_first(author_id, *, lane):
        rows = get_user_works(author_id, lane=lane)
        first = len(reader.authors) == 1
        if first:
            entered.set()
            assert release.wait(5)
        title = 'older response ' if first else 'fresh response '
        return [dict(row, title=title + row['id']) for row in rows]

    reader.get_user_works = delayed_first
    svc = services(reader)
    client = Client(ui.page('/_pv_b_author_retry_cache_owner'))
    overlay = None
    first = None
    try:
        with client:
            overlay = AuthorBrowseView(svc, reader)
            first = asyncio.create_task(overlay.show('91', 'Author'))
            assert await asyncio.to_thread(entered.wait, 5)
            await overlay.retry_load()
            current_state = overlay.gallery.state
            current_pin = overlay.pin
            current_cards = dict(overlay.gallery.cards)
            assert all(work.title.startswith('fresh response ')
                       for work in current_state.artworks)

            release.set()
            await first
            assert all(record.title.startswith('fresh response ')
                       for record in overlay.cache['91'])
            assert overlay.gallery.state is current_state
            assert overlay.pin is current_pin and not current_pin.closed

            overlay.hide()
            await overlay.show('91', 'Author')
            assert overlay.gallery.state is current_state
            assert overlay.gallery.cards == current_cards
            assert overlay.pin is current_pin and svc.pins == [current_pin]
            assert reader.authors == [('91', 'interactive'), ('91', 'interactive')]
    finally:
        release.set()
        if first is not None:
            await asyncio.gather(first, return_exceptions=True)
        if overlay is not None:
            overlay.close()
        client.delete()



def test_missing_author_preview_stays_in_source_order_without_invented_url():
    reader = Reader()
    raw = reader.get_user_works('91', lane='interactive')
    raw[1]['thumbnail_url'] = ''
    reader.get_user_works = lambda *args, **kwargs: raw
    records = read_author(reader, '91')
    assert [r.work_id for r in records[:3]] == ['101', '102', '103']
    assert len(records) == 51 and records[1].thumbnail == ''
    raw[1]['thumbnail_url'] = 'http://127.0.0.1/private'
    with pytest.raises(ValueError):
        read_author(reader, '91')


@pytest.mark.asyncio
async def test_details_failure_is_not_cached_and_explicit_reopen_retries():
    reader = Reader()
    reader.fail = True
    client = Client(ui.page('/_pv_b_detail_retry'))
    tasks = []
    try:
        with client:
            view = GalleryView(services(reader))
            view.open('101')
            before = asyncio.all_tasks()
            view.toggle_details()
            tasks = list(asyncio.all_tasks() - before)
            await asyncio.gather(*tasks)
            assert '取得できません' in view.viewer.detail_status.text
            assert view._detail_cache == {} and view._detail_pending == set()
            reader.fail = False
            view.toggle_details()
            before = asyncio.all_tasks()
            view.toggle_details()
            tasks = list(asyncio.all_tasks() - before)
            await asyncio.gather(*tasks)
            assert view.description.text == 'caption' and view.viewer.detail_status.text == ''
            assert reader.details == [('101', '91'), ('101', '91')]
            view.close()
    finally:
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        client.delete()


@pytest.mark.asyncio
async def test_real_author_handler_and_late_response_cannot_reopen_departed_view():
    reader = Reader()
    entered, release = Event(), Event()
    original = reader.get_user_works
    def blocked(aid, *, lane):
        entered.set()
        assert release.wait(5)
        return original(aid, lane=lane)
    reader.get_user_works = blocked
    svc = services(reader)
    client = Client(ui.page('/_pv_b_author_departure'))
    task = None
    try:
        with client:
            view = GalleryView(svc)
            view.open('101')
            handlers = [x.handler for x in view.viewer.author_button._event_listeners.values() if x.type == 'click']
            assert len(handlers) == 1
            task = asyncio.create_task(handlers[0](None))
            assert await asyncio.to_thread(entered.wait, 5)
            overlay = view._author_browser
            assert overlay.visible
            view.set_viewer_active(False)
            release.set()
            await task
            assert not overlay.visible and 'hidden' in overlay.root.classes
            assert svc.pins == [] and not overlay.gallery._viewer_active
            view.set_viewer_active(True)
            view.open('101')
            await handlers[0](None)
            assert overlay.visible and len(overlay.gallery.cards) == 50
            assert reader.authors == [('91', 'interactive')]
            view.close()
            assert all(pin.closed for pin in svc.pins)
    finally:
        release.set()
        if task:
            await asyncio.gather(task, return_exceptions=True)
        client.delete()


@pytest.mark.asyncio
async def test_author_missing_preview_render_and_unadopted_pin_cleanup():
    reader = Reader()
    rows = reader.get_user_works('91', lane='interactive')
    rows[1]['thumbnail_url'] = ''
    reader.get_user_works = lambda *args, **kwargs: rows
    svc = services(reader)
    client = Client(ui.page('/_pv_b_author_render'))
    try:
        with client:
            overlay = AuthorBrowseView(svc, reader)
            render = overlay.gallery.render_gallery
            def failed_render():
                raise RuntimeError('synthetic render failure')
            overlay.gallery.render_gallery = failed_render
            await overlay.show('91', 'Author')
            assert len(svc.pins) == 1 and svc.pins[0].closed and overlay.pin is None
            assert overlay.retry.visible and not overlay.gallery.root.visible
            overlay.gallery.render_gallery = render
            await overlay.retry_load()
            assert '102' in overlay.gallery.cards and '102' not in overlay.gallery.images
            assert len(overlay.gallery.cards) == 50 and overlay.gallery._viewer_active
            assert '画像を取得できません' in [getattr(e, 'text', '') for e in overlay.gallery.cards['102'].descendants()]
            overlay.gallery.open('102')
            assert not overlay.gallery.state.viewer_open
            overlay.close()
            assert all(pin.closed for pin in svc.pins)
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_author_unknown_page_response_cannot_replace_later_selection():
    reader = Reader()
    svc = services(reader)
    client = Client(ui.page('/_pv_b_author_unknown'))
    entered, release = Event(), Event()
    tasks = []
    try:
        with client:
            overlay = AuthorBrowseView(svc, reader)
            await overlay.show('91', 'Author')
            pin = overlay.pin
            pin.needs_metadata = lambda wid: wid == '101'
            def prepare(wid):
                entered.set()
                assert release.wait(5)
                return Artwork(wid, 'Resolved', 'Author', author_id='91', page_count=3)
            pin.prepare_artwork = prepare
            before = asyncio.all_tasks()
            overlay.gallery.open('101')
            tasks = list(asyncio.all_tasks() - before)
            assert await asyncio.to_thread(entered.wait, 5)
            overlay.gallery.open('102')
            release.set()
            await asyncio.gather(*tasks)
            assert overlay.gallery.state.selected_id == '102'
            assert overlay.gallery.viewer.title.text == 'work 102'
            overlay.hide()
            overlay.gallery.open('101')
            assert not overlay.gallery.state.viewer_open
            overlay.close()
    finally:
        release.set()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        client.delete()

async def _dispatch_registered_author_retry(overlay, client, payload):
    """Use the actual button listener and NiceGUI's ordinary event dispatch."""
    from nicegui import background_tasks
    from nicegui.events import GenericEventArguments, handle_event
    listeners = [item for item in overlay.retry._event_listeners.values()
                 if item.type == 'click']
    assert len(listeners) == 1
    before = set(background_tasks.running_tasks)
    handle_event(listeners[0].handler,
                 GenericEventArguments(sender=overlay.retry, client=client, args=payload))
    scheduled = set(background_tasks.running_tasks) - before
    assert scheduled, 'the registered retry handler was not dispatched'
    await asyncio.gather(*scheduled)
    return listeners[0]


@pytest.mark.asyncio
async def test_old_retry_click_keeps_other_author_cache_pending_pin_and_expansion(monkeypatch):
    from nicegui import core
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    reader = Reader()
    original = reader.get_user_works

    def many_works(author_id, *, lane):
        rows = original(author_id, lane=lane)
        return rows + [dict(rows[0], id=str(i), title=f'work {i}')
                       for i in range(152, 221)]

    reader.get_user_works = many_works
    svc = services(reader)
    client = Client(ui.page('/_pv_b_stale_retry_click'))
    overlay = None
    try:
        with client:
            overlay = AuthorBrowseView(svc, reader)
            reader.fail = True
            await overlay.show('91', 'Failed author')
            assert overlay.retry.visible
            # This packet belongs to the click on author 91's failure view.
            old_click = dict(author='91', epoch=str(overlay.epoch))
            reader.fail = False
            await overlay.show('92', 'Current author')
            overlay.gallery.search_input.set_value('blue')
            overlay.gallery.search('blue')
            overlay.gallery.more()
            assert overlay.gallery.state.shown_count == 100
            assert overlay.gallery.state.total_count == 120
            state = overlay.gallery.state
            pin = overlay.pin
            cache = dict(overlay.cache)
            pending = dict(overlay.pending)
            cards = dict(overlay.gallery.cards)
            grid = overlay.gallery.grid
            scroll_owner = grid.parent_slot.parent
            epoch = overlay.epoch
            calls = list(reader.authors)

            await _dispatch_registered_author_retry(overlay, client, old_click)

            assert reader.authors == calls, 'old author click started a new GET'
            assert overlay.epoch == epoch
            assert overlay.author_id == '92'
            assert overlay.cache.keys() == cache.keys()
            assert all(overlay.cache[key] is value for key, value in cache.items())
            assert overlay.pending == pending
            assert overlay.gallery.state is state and not state.closed
            assert state.shown_count == 100 and state.query == 'blue'
            assert overlay.gallery.search_input.value == 'blue'
            assert overlay.gallery.cards == cards
            assert all(overlay.gallery.cards[key] is value for key, value in cards.items())
            assert overlay.gallery.grid is grid and grid.parent_slot.parent is scroll_owner
            assert overlay.pin is pin and not pin.closed
            assert svc.pins == [pin]
            assert overlay.visible and overlay.gallery._viewer_active
            assert not overlay.retry.visible
    finally:
        if overlay is not None:
            overlay.close()
        client.delete()


@pytest.mark.asyncio
async def test_retry_click_requires_current_visible_owner_and_valid_retry_runs_once(monkeypatch):
    from nicegui import core
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    reader = Reader()
    svc = services(reader)
    client = Client(ui.page('/_pv_b_retry_click_admission'))
    overlay = None
    try:
        with client:
            overlay = AuthorBrowseView(svc, reader)
            reader.fail = True
            await overlay.show('91', 'Author')
            assert overlay.retry.visible
            calls = list(reader.authors)
            valid = dict(author='91', epoch=str(overlay.epoch))
            for packet in (dict(valid, author='92'),
                           dict(valid, epoch=str(overlay.epoch - 1)),
                           None, {}):
                await _dispatch_registered_author_retry(overlay, client, packet)
                assert reader.authors == calls
                assert overlay.visible and overlay.retry.visible
                assert not overlay.cache and not overlay.pending and overlay.pin is None

            overlay.hide()
            hidden = dict(author='91', epoch=str(overlay.epoch))
            await _dispatch_registered_author_retry(overlay, client, hidden)
            assert reader.authors == calls
            assert not overlay.visible

            await overlay.show('91', 'Author')
            assert overlay.retry.visible
            calls = list(reader.authors)
            valid = dict(author='91', epoch=str(overlay.epoch))
            svc.closing = True
            await _dispatch_registered_author_retry(overlay, client, valid)
            assert reader.authors == calls
            svc.closing = False

            reader.fail = False
            listener = await _dispatch_registered_author_retry(overlay, client, valid)
            assert reader.authors == calls + [('91', 'interactive')]
            assert overlay.gallery._viewer_active and not overlay.retry.visible
            assert len(overlay.cache['91']) == 51
            assert len(svc.pins) == 1 and not overlay.pin.closed
            assert 'dataset.authorEpoch' in listener.js_handler
            assert 'dataset.authorId' in listener.js_handler
            assert overlay.root.props['data-author-id'] == '91'
            assert overlay.root.props['data-author-epoch'] == str(overlay.epoch)

            # The admitted retry changes its epoch, so replaying the same click
            # cannot discard the newly published snapshot or create another GET.
            calls = list(reader.authors)
            state, pin = overlay.gallery.state, overlay.pin
            await _dispatch_registered_author_retry(overlay, client, valid)
            assert reader.authors == calls
            assert overlay.gallery.state is state and overlay.pin is pin
            overlay.close()
            after_close = dict(author='91', epoch=str(overlay.epoch))
            await _dispatch_registered_author_retry(overlay, client, after_close)
            assert reader.authors == calls and pin.closed and overlay.pin is None
            assert overlay.closed and not overlay.visible
    finally:
        if overlay is not None:
            overlay.close()
        client.delete()

