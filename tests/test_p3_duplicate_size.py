"""Original X04 thumbnail sizes preserve selection and existing review guards."""
from dataclasses import replace
import pytest
from nicegui import Client, ui
from pixivvault_nicegui.ui.duplicate_view import DuplicateView
from test_duplicate_inspection import page, group, scan_of

@pytest.mark.asyncio
async def test_size_changes_keep_selection_revision_and_all_original_guards():
    client=Client(ui.page('/_p3_duplicate_size'))
    try:
        items=[replace(page(i),thumb_b64='AA==') for i in range(1,7)]
        items[1]=replace(items[1],thumb_b64=None)
        small_group=group(items[:2],'two');large_group=group(items,'six')
        with client:
            view=DuplicateView(object());view.set_active(True)
            view.scan_result=scan_of([small_group,large_group]);view.render()
        assert view.thumb_size=='medium'
        assert view.size_buttons['medium'].props['aria-pressed']=='true'
        assert len(view.thumbnails)==7  # 2 + 5; sixth item is not shown
        assert all(x.style.get('width')=='144px' and x.style.get('height')=='144px' for x in view.thumbnails)
        view.toggle('two',items[0].key,True,view.selection_revision)
        assert view.selections['two']=={items[0].key}
        revision=view.selection_revision;ids=[x.id for x in view.thumbnails]
        for size,pixels in [('small',88),('large',200),('medium',144)]:
            view.change_thumb_size(size)
            assert view.selection_revision==revision and view.selections['two']=={items[0].key}
            assert [x.id for x in view.thumbnails]==ids
            assert all(x.style.get('width')==f'{pixels}px' and x.style.get('height')==f'{pixels}px' for x in view.thumbnails)
            assert view.size_buttons[size].props['aria-pressed']=='true'
        view.toggle('two',items[1].key,True,view.selection_revision)
        assert view.selections['two']=={items[0].key}  # one image must remain
        assert '1枚以上' in view.notice.text
        view.toggle('six',items[5].key,True,view.selection_revision)
        assert not view.selections['six']  # hidden sixth image cannot be selected
        view.filter('author');assert view.selections['two']=={items[0].key}
        assert all(x.style.get('width')=='144px' for x in view.thumbnails)
        view.filter('missing');assert not any(view.selections.values())
        view.filter('author');assert not any(view.selections.values())
        view.close()
    finally:client.delete()

@pytest.mark.asyncio
async def test_size_chosen_before_scan_applies_to_later_render_and_closed_view_ignores_changes():
    client=Client(ui.page('/_p3_duplicate_size_before'))
    try:
        with client:view=DuplicateView(object());view.set_active(True)
        view.change_thumb_size('small')
        view.scan_result=scan_of([group([replace(page('1'),thumb_b64='AA=='),page('2')])]);view.render()
        assert all(x.style.get('width')=='88px' for x in view.thumbnails)
        view.close();view.change_thumb_size('large');assert view.thumb_size=='small'
        with pytest.raises(ValueError):view.change_thumb_size('invalid')
    finally:client.delete()
