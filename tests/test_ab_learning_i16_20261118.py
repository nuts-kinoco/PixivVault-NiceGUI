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
