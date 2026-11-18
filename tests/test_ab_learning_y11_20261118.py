import asyncio
from types import SimpleNamespace
from z_learning_support_20261118 import open_discovery_view, settled


async def test_review_loads_missing_metadata_and_retries_failure(tmp_path, monkeypatch):
    view, services, service, cleanup = await open_discovery_view(tmp_path, monkeypatch, route='/_y11_fetch')
    calls = []
    def fetch(wid):
        calls.append(wid)
        if len(calls) == 1:
            raise OSError('fake failure')
        return {'id':wid, 'title':'fetched title', 'user_id':'500', 'user_name':'author',
                'tags':['tag'], 'thumbnail_url':'https://i.pximg.net/c/250x250/img/0_p0.jpg'}
    monkeypatch.setattr(service.reader, 'get_discovery_work', fetch, raising=False)
    try:
        view._on_loaded(SimpleNamespace(args=view._image_url))
        wid = view.state.current_artwork.work_id
        assert view.judge('like', dict(work_id=wid, generation=view.state.generation, operation_id='y11'))
        await settled(view)
        view._discovery_image_ids = frozenset()
        original_snapshot = service._review_snapshot
        def snapshot_without_title(session_id):
            packet = original_snapshot(session_id)
            for row in packet['rows']:
                row['title'] = row['work_id']
            return packet
        monkeypatch.setattr(service, '_review_snapshot', snapshot_without_title)
        with view.client:
            await view.open_review()
            async with asyncio.timeout(5):
                while wid not in view._review_detail_failed:
                    await asyncio.sleep(.01)
            assert calls == [wid]
            await view._review_fetch_detail(wid, retry=True)
            assert view._review_details[wid][0].title == 'fetched title'
            assert calls == [wid, wid]
    finally:
        await cleanup()


async def test_review_keeps_title_if_thumbnail_missing(tmp_path, monkeypatch):
    view, services, service, cleanup = await open_discovery_view(tmp_path, monkeypatch, route='/_y11_partial')
    def fetch(wid):
        return {'id':wid, 'title':'title only', 'user_id':'500', 'user_name':'author',
                'tags':['tag'], 'thumbnail_url':''}
    monkeypatch.setattr(service.reader, 'get_discovery_work', fetch, raising=False)
    try:
        view._on_loaded(SimpleNamespace(args=view._image_url))
        wid = view.state.current_artwork.work_id
        assert view.judge('like', dict(work_id=wid, generation=view.state.generation, operation_id='partial'))
        await settled(view)
        view._discovery_image_ids = frozenset()
        original = service._review_snapshot
        def snapshot(session):
            packet = original(session)
            for row in packet['rows']:
                row['title'] = row['work_id']
                row['thumbnail_url'] = None
            return packet
        monkeypatch.setattr(service, '_review_snapshot', snapshot)
        with view.client:
            await view.open_review()
            async with asyncio.timeout(5):
                while wid not in view._review_detail_failed:
                    await asyncio.sleep(.01)
            assert view._review_detail_titles[wid] == 'title only'
            labels = [getattr(x, 'text', '') for x in view.review_content.descendants()]
            assert 'title only' in labels
            assert any('取得失敗' in (label or '') for label in labels)
    finally:
        await cleanup()
