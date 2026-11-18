from pixivvault_nicegui.learning_review import requeue_outcome, review_trace


def test_review_requeue_outcomes_and_original_trace():
    assert requeue_outcome(('1', '2'), '1', '1') == 'current'
    assert requeue_outcome(('1', '2'), '1', '2') == 'requeued'
    assert requeue_outcome(('1', '2'), '1', '3') == 'absent'
    assert review_trace('作品', 'requeued') == '『作品』の判定を取り消しました — 未判定に戻り、めくるへ戻しました'
    assert review_trace('作品', 'current') == review_trace('作品', 'requeued')
    assert review_trace('作品', 'absent') == '『作品』の判定を取り消しました — 未判定に戻りました（めくるへ出るのは次に候補が届いたとき）'

async def test_discovery_repeated_review_undo_keeps_seen_candidates(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from z_learning_support_20261118 import open_discovery_view, settled
    view, services, service, cleanup = await open_discovery_view(tmp_path, monkeypatch, route='/_i16_repeated')
    try:
        judged = []
        for n in range(2):
            view._on_loaded(SimpleNamespace(args=view._image_url))
            wid = view.state.current_artwork.work_id
            assert view.judge('like', dict(work_id=wid, generation=view.state.generation, operation_id=f'j{n}'))
            await settled(view)
            judged.append(wid)
        with view.client:
            await view.open_review()
            for wid in reversed(judged):
                row = next(r for r in view._review_rows if r['work_id'] == wid)
                await view._review_change(row)
                await settled(view)
                assert view._review_outcomes[wid] == 'requeued'
        assert [a.work_id for a in view.state.artworks[view.state.index:]][1:3] == judged
    finally:
        await cleanup()


async def test_committed_review_undo_requeues_after_dialog_closes(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from z_learning_support_20261118 import open_discovery_view, settled
    view, services, service, cleanup = await open_discovery_view(tmp_path, monkeypatch, route='/_i16_closed')
    try:
        view._on_loaded(SimpleNamespace(args=view._image_url))
        wid = view.state.current_artwork.work_id
        assert view.judge('like', dict(work_id=wid, generation=view.state.generation, operation_id='j'))
        await settled(view)
        with view.client:
            await view.open_review()
        row = next(r for r in view._review_rows if r['work_id'] == wid)
        submitted = None
        old_submit = service.submit_review_undo
        old_wait = service._wait
        committed, release = asyncio.Event(), asyncio.Event()
        def submit(*args):
            nonlocal submitted
            submitted = old_submit(*args)
            return submitted
        async def wait(future):
            value = await old_wait(future)
            if future is submitted:
                committed.set()
                await release.wait()
            return value
        monkeypatch.setattr(service, 'submit_review_undo', submit)
        monkeypatch.setattr(service, '_wait', wait)
        with view.client:
            task = asyncio.create_task(view._review_change(row))
            await committed.wait()
            view._close_review()
            release.set()
            await task
            await settled(view)
        assert wid in [a.work_id for a in view.state.artworks[view.state.index:]]
    finally:
        await cleanup()


async def test_review_trace_obeys_search_and_paging(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from z_learning_support_20261118 import open_discovery_view, settled
    view, services, service, cleanup = await open_discovery_view(tmp_path, monkeypatch, route='/_i16_filter')
    try:
        view._on_loaded(SimpleNamespace(args=view._image_url))
        wid = view.state.current_artwork.work_id
        assert view.judge('like', dict(work_id=wid, generation=view.state.generation, operation_id='j'))
        await settled(view)
        with view.client:
            await view.open_review()
            await view._review_change(view._review_rows[0])
            await settled(view)
            view.review_text.set_value('not-found')
            view._render_review()
            texts = [getattr(x, 'text', '') for x in view.review_content.descendants()]
            assert not any('candidate 0' in (s or '') for s in texts)
    finally:
        await cleanup()


async def test_review_commit_blocks_new_judgment_until_queue_updated(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from z_learning_support_20261118 import open_discovery_view, settled
    view, services, service, cleanup = await open_discovery_view(tmp_path, monkeypatch, route='/_i16_guard')
    try:
        view._on_loaded(SimpleNamespace(args=view._image_url))
        wid = view.state.current_artwork.work_id
        assert view.judge('like', dict(work_id=wid, generation=view.state.generation, operation_id='j'))
        await settled(view)
        with view.client:
            await view.open_review()
        row = view._review_rows[0]
        submitted = None
        old_submit, old_wait = service.submit_review_undo, service._wait
        committed, release = asyncio.Event(), asyncio.Event()
        def submit(*args):
            nonlocal submitted
            submitted = old_submit(*args)
            return submitted
        async def wait(future):
            value = await old_wait(future)
            if future is submitted:
                committed.set()
                await release.wait()
            return value
        monkeypatch.setattr(service, 'submit_review_undo', submit)
        monkeypatch.setattr(service, '_wait', wait)
        with view.client:
            task = asyncio.create_task(view._review_change(row))
            await committed.wait()
            view._close_review()
            view._on_loaded(SimpleNamespace(args=view._image_url))
            current = view.state.current_artwork.work_id
            assert not view.judge('like', dict(work_id=current, generation=view.state.generation, operation_id='j2'))
            release.set()
            await task
            await settled(view)
        assert wid in [a.work_id for a in view.state.artworks[view.state.index:]]
    finally:
        await cleanup()


async def test_absent_work_stays_out_after_other_review_undo(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from z_learning_support_20261118 import open_discovery_view, settled
    view, services, service, cleanup = await open_discovery_view(tmp_path, monkeypatch, route='/_i16_absent')
    try:
        async def judge(op):
            view._on_loaded(SimpleNamespace(args=view._image_url))
            wid = view.state.current_artwork.work_id
            assert view.judge('like', dict(work_id=wid, generation=view.state.generation, operation_id=op))
            await settled(view)
            return wid
        first = await judge('j1')
        with view.client:
            view.reload_storage()
            await settled(view)
            second = await judge('j2')
            await view.open_review()
            for wid in (first, second):
                row = next(r for r in view._review_rows if r['work_id'] == wid)
                await view._review_change(row)
                await settled(view)
            assert view._review_outcomes[first] == 'absent'
            assert view._review_outcomes[second] == 'requeued'
            assert first not in [a.work_id for a in view.state.artworks[view.state.index:]]
    finally:
        await cleanup()


async def test_review_mode_switch_offers_return_to_swipe(tmp_path, monkeypatch):
    from z_learning_support_20261118 import open_discovery_view
    view, services, service, cleanup = await open_discovery_view(tmp_path, monkeypatch, route='/_i16_modes')
    try:
        with view.client:
            await view.open_review()
            assert view._review_open
            assert view.swipe_button.visible
            view.return_to_swipe()
            assert not view._review_open
            assert view.trend_main.visible
    finally:
        await cleanup()


async def test_review_filters_stay_with_their_columns(tmp_path, monkeypatch):
    from z_learning_support_20261118 import open_discovery_view
    view, services, service, cleanup = await open_discovery_view(tmp_path, monkeypatch, route='/_i16_columns')
    try:
        with view.client:
            await view.open_review()
            assert view.review_unsaved in view.review_columns[True].descendants()
            assert view.review_superlike in view.review_columns[True].descendants()
            assert view.review_hate in view.review_columns[False].descendants()
            view._render_review()
            assert view.review_unsaved in view.review_columns[True].descendants()
    finally:
        await cleanup()


async def test_review_card_uses_original_mark_and_positive_thumbnail_size(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from z_learning_support_20261118 import open_discovery_view, settled
    view, services, service, cleanup = await open_discovery_view(tmp_path, monkeypatch, route='/_i16_card')
    try:
        view._on_loaded(SimpleNamespace(args=view._image_url))
        item = view.state.current_artwork
        assert view.judge('like', dict(work_id=item.work_id, generation=view.state.generation, operation_id='j'))
        await settled(view)
        with view.client:
            await view.open_review()
            elements = list(view.review_columns[True].descendants())
            assert any(getattr(e, 'text', None) == '→' for e in elements)
            assert any(getattr(e, '_style', {}).get('width') == '80px' for e in elements)
    finally:
        await cleanup()


async def test_review_mode_rejects_hidden_swipe_shortcuts(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from z_learning_support_20261118 import open_discovery_view
    view, services, service, cleanup = await open_discovery_view(tmp_path, monkeypatch, route='/_i16_keys')
    try:
        view._on_loaded(SimpleNamespace(args=view._image_url))
        item = view.state.current_artwork
        with view.client:
            await view.open_review()
            assert not view.judge('like', dict(work_id=item.work_id, generation=view.state.generation, operation_id='hidden'))
            assert view.state.current_artwork is item
    finally:
        await cleanup()


async def test_reselecting_review_refreshes_and_clears_undo_trace(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from z_learning_support_20261118 import open_discovery_view, settled
    view, services, service, cleanup = await open_discovery_view(tmp_path, monkeypatch, route='/_i16_refresh')
    try:
        view._on_loaded(SimpleNamespace(args=view._image_url))
        item = view.state.current_artwork
        assert view.judge('like', dict(work_id=item.work_id, generation=view.state.generation, operation_id='j'))
        await settled(view)
        with view.client:
            await view.open_review()
            await view._review_change(view._review_rows[0])
            await settled(view)
            assert item.work_id in view._review_undone
            await view.open_review()
            assert not view._review_undone
    finally:
        await cleanup()


async def test_column_filter_only_resets_its_own_page(tmp_path, monkeypatch):
    from z_learning_support_20261118 import open_discovery_view
    view, services, service, cleanup = await open_discovery_view(tmp_path, monkeypatch, route='/_i16_filter_pages')
    try:
        view._review_pages = {True:2, False:3}
        view._review_column_filter_changed(True)
        assert view._review_pages == {True:0, False:3}
    finally:
        await cleanup()


async def test_reselect_review_rejects_stale_restore_event(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from z_learning_support_20261118 import open_discovery_view, settled
    view, services, service, cleanup = await open_discovery_view(tmp_path, monkeypatch, route='/_i16_stale_restore')
    try:
        view._on_loaded(SimpleNamespace(args=view._image_url))
        item = view.state.current_artwork
        assert view.judge('like', dict(work_id=item.work_id, generation=view.state.generation, operation_id='j'))
        await settled(view)
        with view.client:
            await view.open_review()
            row = view._review_rows[0]
            await view._review_change(row)
            await settled(view)
            operation = view._review_undone[item.work_id][1]
            await view.open_review()
            await view._review_change(row, operation)
            await settled(view)
            assert not (await service.review_snapshot(view._review_session_id))['rows']
    finally:
        await cleanup()


async def test_review_columns_scroll_independently(tmp_path, monkeypatch):
    from z_learning_support_20261118 import open_discovery_view
    view, services, service, cleanup = await open_discovery_view(tmp_path, monkeypatch, route='/_i16_scroll')
    try:
        for column in view.review_columns.values():
            assert column._style.get('overflow-y') == 'auto'
            assert column._style.get('max-height')
    finally:
        await cleanup()


async def test_column_filter_preserves_other_column_elements(tmp_path, monkeypatch):
    from z_learning_support_20261118 import open_discovery_view
    view, services, service, cleanup = await open_discovery_view(tmp_path, monkeypatch, route='/_i16_filter_dom')
    try:
        with view.client:
            await view.open_review()
            before = tuple(view.review_bodies[False].default_slot.children)
            view._review_column_filter_changed(True)
            assert tuple(view.review_bodies[False].default_slot.children) == before
    finally:
        await cleanup()
