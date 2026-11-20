import asyncio
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

import pytest

from pixivvault_nicegui.ui.learning_view import LearningView
from z_learning_support_20261118 import open_discovery_view, settled


@pytest.mark.parametrize('count', [0, 2])
async def test_learning_picture_has_a_click_only_pixiv_link(tmp_path, monkeypatch, count):
    view, _, _, cleanup = await open_discovery_view(tmp_path, monkeypatch, count=count)
    try:
        link, = [child for child in view.root.descendants()
                 if child.props.get('data-testid') == 'learning-open-pixiv']
        assert link.visible is bool(count)
        assert link.props['icon'] == 'open_in_new'
        assert link.props['target'] == '_blank'
        assert link.props['rel'] == 'noopener noreferrer'
        assert link.parent_slot is view.picture.parent_slot
        if count:
            assert link.props['href'] == 'https://www.pixiv.net/artworks/1000'
            assert not view.state.ready
            view.state.index += 1
            with view.client:
                view.refresh()
            assert link.props['href'] == 'https://www.pixiv.net/artworks/1001'
            view.state.index += 1
            with view.client:
                view.refresh()
        assert not link.visible
        assert not link.props.get('href')
    finally:
        await cleanup()


async def test_pixiv_link_is_disabled_during_candidate_reload(tmp_path, monkeypatch):
    view, _, _, cleanup = await open_discovery_view(tmp_path, monkeypatch)
    try:
        assert view.open_pixiv_button.enabled
        with view.client:
            view.reload_storage()
            assert not view.open_pixiv_button.enabled
        await settled(view)
        assert view.open_pixiv_button.enabled
    finally:
        await cleanup()


@pytest.mark.parametrize('choice, bonus', [('like', 1), ('superLike', 1), ('skip', 0), ('hate', 0)])
@pytest.mark.parametrize('entry', ['link', 'picture'])
async def test_candidate_opens_record_history_and_one_positive_bonus(tmp_path, monkeypatch, choice, bonus, entry):
    view, _, service, cleanup = await open_discovery_view(tmp_path, monkeypatch)
    try:
        work_id = view.state.current_artwork.work_id
        payload = dict(work_id=work_id, generation=view.state.generation, operation_id='judge')
        event = SimpleNamespace(args=payload)
        button = view.open_pixiv_button if entry == 'link' else view.picture
        click, = [listener for listener in button._event_listeners.values() if listener.type == 'click']
        with view.client:
            await click.handler(event)
            await click.handler(event)
            await click.handler(SimpleNamespace(args={**payload, 'generation': -1}))

        rows = await service.view_history()
        assert [(row['work_id'], row['open_count']) for row in rows] == [(work_id, 2)]
        assert await service.judged_count() == 0
        with view.client:
            view._on_loaded(SimpleNamespace(args=view._image_url))
            assert view.judge(choice, payload)
        await settled(view)

        def saved():
            db = service._database()
            return (
                db.conn.execute('SELECT opened FROM discovery_swipe_history WHERE work_id=?', (work_id,)).fetchone()[0],
                [row[0] for row in db.conn.execute('SELECT open_count FROM discovery_preference')],
                [row[0] for row in db.conn.execute('SELECT open_count FROM discovery_author_preference')],
            )

        assert await service._wait(service._submit(saved)) == (1, [bonus, bonus], [bonus])
        assert (await service.view_history())[0]['open_count'] == 2
    finally:
        await cleanup()


async def test_saved_history_alone_does_not_give_a_current_session_bonus(tmp_path, monkeypatch):
    view, _, service, cleanup = await open_discovery_view(tmp_path, monkeypatch)
    try:
        work_id = view.state.current_artwork.work_id
        await service.record_view(work_id)
        with view.client:
            view._on_loaded(SimpleNamespace(args=view._image_url))
            assert view.judge('like', dict(work_id=work_id, generation=view.state.generation, operation_id='judge'))
        await settled(view)
        assert (await service.judgment_export_rows())[0]['opened'] == 0
        assert (await service.view_history())[0]['open_count'] == 1
    finally:
        await cleanup()


@pytest.mark.parametrize('condition', ['image-failed', 'saving', 'reload-failed'])
async def test_unavailable_candidate_rejects_both_open_events(tmp_path, monkeypatch, condition):
    view, _, service, cleanup = await open_discovery_view(tmp_path, monkeypatch)
    pending = None
    try:
        work_id = view.state.current_artwork.work_id
        payload = dict(work_id=work_id, generation=view.state.generation, operation_id='save')
        with view.client:
            if condition == 'image-failed':
                view._on_failed(SimpleNamespace(args=view._image_url))
            elif condition == 'saving':
                pending = Future()
                monkeypatch.setattr(service, 'submit_record', lambda *_: pending)
                view._on_loaded(SimpleNamespace(args=view._image_url))
                assert view.judge('like', payload)
            else:
                async def unavailable():
                    raise OSError('fixture reload failed')

                monkeypatch.setattr(service, 'open_session', unavailable)
                view.reload_storage()
                async with asyncio.timeout(2):
                    while view._storage_busy:
                        await asyncio.sleep(.01)
                assert not view._storage_ready
            event = SimpleNamespace(args=payload)
            await view._open_learning_picture(event)
            await view._open_learning_pixiv(event)
        assert work_id not in view._opened_work_ids
        assert await service.view_history() == []
        if condition != 'saving':
            assert not view.open_pixiv_button.enabled
    finally:
        if pending is not None:
            pending.set_exception(OSError('fixture cleanup'))
            await settled(view)
        await cleanup()


@pytest.mark.parametrize('generation', [True, 1.0, '1'])
async def test_open_requires_integer_generation(tmp_path, monkeypatch, generation):
    view, _, service, cleanup = await open_discovery_view(tmp_path, monkeypatch)
    try:
        view.state.generation = 1
        wid = view.state.current_artwork.work_id
        with view.client:
            await view._open_learning_pixiv(SimpleNamespace(args=dict(work_id=wid, generation=generation)))
        assert wid not in view._opened_work_ids
        assert await service.view_history() == []
    finally:
        await cleanup()


@pytest.mark.parametrize('state', ['no-storage', 'already-closed', 'closed-during-write'])
async def test_record_open_does_not_report_unavailable_or_closed_ui(tmp_path, monkeypatch, state):
    view, _, service, cleanup = await open_discovery_view(tmp_path, monkeypatch)
    try:
        history = view.view_history
        calls = []

        async def fail(wid):
            calls.append(wid)
            history.close()
            raise OSError('fixture record failed')

        monkeypatch.setattr(service, 'record_view', fail)
        if state == 'no-storage':
            history.storage = None
        elif state == 'already-closed':
            history.close()
        status = view.status.text
        with view.client:
            await history.record_open('1000')
        assert view.status.text == status
        assert calls == (['1000'] if state == 'closed-during-write' else [])
    finally:
        await cleanup()


async def test_late_expired_toast_undo_releases_browser_guard_without_doing_undo(tmp_path, monkeypatch):
    view, _, service, cleanup = await open_discovery_view(tmp_path, monkeypatch)
    try:
        with view.client:
            view._on_loaded(SimpleNamespace(args=view._image_url))
            assert view.judge('like', dict(work_id=view.state.current_artwork.work_id,
                                          generation=view.state.generation, operation_id='save'))
        await settled(view)
        with view.client:
            click, = [listener for listener in view.toast.undo_button._event_listeners.values()
                      if listener.type == 'click']
            view.toast.hide()
            click.handler(SimpleNamespace(args=dict(work_id=view.state.current_artwork.work_id,
                                                     generation=view.state.generation, operation_id='late')))
        assert view.root.props.get('data-finished-operation') == 'late'
        assert view.state.index == 1 and await service.judged_count() == 1
    finally:
        await cleanup()


async def test_open_picture_does_not_publish_after_failed_reload(tmp_path, monkeypatch):
    view, _, service, cleanup = await open_discovery_view(tmp_path, monkeypatch)
    recorded, release = asyncio.Event(), asyncio.Event()
    opened = []
    pending = None
    try:
        original_record = view.view_history.record_open

        async def record(wid):
            await original_record(wid)
            recorded.set()
            await release.wait()

        async def broken_session():
            raise OSError('fixture reload failed')

        monkeypatch.setattr(view.view_history, 'record_open', record)
        monkeypatch.setattr(view.view_history.viewer, 'show', lambda *args, **kw: opened.append(args))
        monkeypatch.setattr(service, 'open_session', broken_session)
        event = SimpleNamespace(args=dict(work_id=view.state.current_artwork.work_id,
                                          generation=view.state.generation))
        with view.client:
            pending = asyncio.create_task(view._open_learning_picture(event))
            async with asyncio.timeout(3):
                await recorded.wait()
                view.reload_storage()
                while view._storage_busy:
                    await asyncio.sleep(.01)
                release.set()
                await pending
        assert not opened
        assert (await service.view_history())[0]['open_count'] == 1
    finally:
        release.set()
        if pending is not None:
            await pending
        await cleanup()


async def test_late_history_write_error_preserves_reload_recovery_message(tmp_path, monkeypatch):
    view, _, service, cleanup = await open_discovery_view(tmp_path, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    pending = None
    try:
        async def broken_record(wid):
            entered.set()
            await release.wait()
            raise OSError('fixture history failed')

        async def broken_session():
            raise OSError('fixture reload failed')

        monkeypatch.setattr(service, 'record_view', broken_record)
        monkeypatch.setattr(service, 'open_session', broken_session)
        event = SimpleNamespace(args=dict(work_id=view.state.current_artwork.work_id,
                                          generation=view.state.generation))
        with view.client:
            pending = asyncio.create_task(view._open_learning_pixiv(event))
            async with asyncio.timeout(3):
                await entered.wait()
                view.reload_storage()
                while view._storage_busy:
                    await asyncio.sleep(.01)
                release.set()
                await pending
        assert '保存状態を読み込めませんでした' in view.status.text
    finally:
        release.set()
        if pending is not None:
            await pending
        await cleanup()


def test_browser_blocks_link_between_save_gesture_and_next_server_update(tmp_path):
    sync_playwright = pytest.importorskip('playwright.sync_api').sync_playwright
    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).is_file():
            pytest.skip('Playwright Chromium is unavailable')
        context = playwright.chromium.launch_persistent_context(
            str(tmp_path / 'chromium'), headless=True,
            args=['--proxy-server=http://127.0.0.1:1', '--disable-background-networking'],
        )
        try:
            def route(request):
                if request.request.url == 'http://127.0.0.1/fixture':
                    request.fulfill(content_type='text/html', body='<html></html>')
                else:
                    request.abort()

            context.route('**/*', route)
            page = context.pages[0]
            page.goto('http://127.0.0.1/fixture')
            page.set_content('''<main class="pv-learning" data-active="true" data-generation="1"
                data-work-id="1000"><button data-generation="1" data-work-id="1000"></button>
                <button id="reload"></button><div id="picture" data-generation="2"></div>
                <a href="#open" data-generation="1" data-work-id="1000" data-work-open="true"></a></main>''')
            blocked = page.evaluate('''({save, open, key, imageError}) => {
                const root = document.querySelector('main');
                const button = root.querySelector('button');
                const link = root.querySelector('a');
                const records = [];
                const bind = code => new Function('emit', 'return (' + code + ')')(p => records.push(p));
                button.addEventListener('click', bind(save));
                root.querySelector('#reload').addEventListener('click', bind(save));
                root.addEventListener('keydown', bind(key));
                link.addEventListener('click', bind(open));
                link.addEventListener('auxclick', bind(open));
                const click = () => link.dispatchEvent(new MouseEvent('click', {cancelable:true}));
                button.click();
                const afterButton = !click();
                const auxBlocked = !link.dispatchEvent(new MouseEvent('auxclick', {button:1, cancelable:true}));
                root.dataset.finishedOperation = records[0].operation_id;
                const afterRejection = click();
                root.dispatchEvent(new KeyboardEvent('keydown', {key:'ArrowRight', cancelable:true}));
                const afterKey = !click();
                link.dataset.generation = '2';
                const afterAdvance = click();
                root.dataset.generation = '2';
                root.querySelector('#reload').click();
                const afterReload = !click();
                root.dataset.finishedOperation = records.at(-1).operation_id;
                bind(imageError)('fixture image');
                const beforeErrorUpdate = !click();
                link.dataset.generation = '3';
                link.dataset.workOpen = 'false';
                const afterImageError = !click();
                return {afterButton, auxBlocked, afterRejection, afterKey, afterAdvance, afterReload,
                        beforeErrorUpdate, afterImageError,
                        events: records.length};
            }''', {'save': LearningView._click_js('block-open'), 'open': LearningView._click_js('open'),
                    'key': LearningView._key_js(), 'imageError': LearningView._image_error_js('picture')})
        finally:
            context.close()
    assert blocked == dict(afterButton=True, auxBlocked=True, afterRejection=True,
                           afterKey=True, afterAdvance=True, afterReload=True, beforeErrorUpdate=True,
                           afterImageError=True, events=6)
