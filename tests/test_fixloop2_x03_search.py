"""X03: synthetic eleven-group view; no user data or network."""
from dataclasses import replace
import asyncio
import pytest
from nicegui import Client, ui
from test_duplicate_view import DuplicateView, StubSource, loaded
from test_duplicate_inspection import page, group, scan_of

@pytest.mark.asyncio
async def test_eleven_groups_filter_by_author_and_empty_query():
    source=StubSource()
    source.near=scan_of([
        replace(group([page(str(2000+2*i)),page(str(2001+2*i))],f'group-{i}'),
                author_id=str(9000+i),author_name=f'Author {i}')
        for i in range(11)])
    client=Client(ui.page('/generated-fixloop2-x03'))
    view=None
    try:
        with client:
            view=DuplicateView(source)
            view.set_active(True)
        await loaded(view)
        await view.scan()
        view.set_mode('near')
        assert len(view.thumbnails)==22
        # A toolbar control must never be counted as a candidate checkbox.
        assert not view.low_button.props['data-testid'].startswith('duplicate-select-')
        cards = [element for element in view.rows.descendants()
                 if element.props.get('data-testid') == 'duplicate-near-group']
        assert len(cards) == 11
        # The search field consumes author IDs, never checkbox work IDs.
        assert cards[4].props['data-author-id'] == '9004'
        checkboxes = [element for element in view.rows.descendants()
                      if str(element.props.get('data-testid', '')).startswith('duplicate-select-')]
        ids = {element.props['data-testid'].split('-')[2] for element in checkboxes}
        assert ids == {str(9000+i) for i in range(11)}
        assert 'duplicate-select-9004-2008-1' in {e.props['data-testid'] for e in checkboxes}
        view.search.set_value('9004')
        await asyncio.sleep(0)
        assert len(view.thumbnails)==2
        assert list(view.selections)==['group-4']
        view.search.set_value('no-matching-author')
        await asyncio.sleep(0)
        assert len(view.thumbnails)==0
        assert '一致する候補はありません' in [
            label.text for label in view.rows.descendants() if isinstance(label,ui.label)]
        view.search.set_value('')
        await asyncio.sleep(0)
        assert len(view.thumbnails)==22
    finally:
        if view:view.close()
        client.delete()
