import pytest

from z_learning_support_20261118 import open_discovery_view


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
