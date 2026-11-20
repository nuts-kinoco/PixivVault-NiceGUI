from types import SimpleNamespace

import pytest

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
