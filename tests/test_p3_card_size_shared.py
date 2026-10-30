"""L13 shared card size: original column caps, persistence and view propagation."""
from types import SimpleNamespace
import sqlite3
import pytest
from nicegui import Client, ui
from pixivvault_nicegui.preferences import PreferencesStore
from pixivvault_nicegui.ui.grid_layout import MAX_COLUMNS, columns_for_width
from pixivvault_nicegui.ui.shell import DemoShell
from pixivvault_nicegui.ui.recommend_view import RecommendView

@pytest.mark.parametrize('size,columns',[('large',6),('medium',8),('small',12)])
def test_original_caps_and_restart(tmp_path,size,columns):
    assert MAX_COLUMNS[size]==columns
    assert columns_for_width(10000,size)==columns
    assert columns_for_width(180,size)==1
    path=tmp_path/'preferences.sqlite3';store=PreferencesStore(path)
    try:
        assert store.snapshot().values['grid_card_size_mode']=='large'
        store.update({'grid_card_size_mode':size},expected_revision=store.snapshot().revision)
    finally:store.close()
    reopened=PreferencesStore(path)
    try:assert reopened.snapshot().values['grid_card_size_mode']==size
    finally:reopened.close()

def test_invalid_stored_size_falls_back_without_modifying_record(tmp_path):
    path=tmp_path/'preferences.sqlite3';store=PreferencesStore(path);store.close()
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO preferences_values(key,value) VALUES('grid_card_size_mode','invalid')")
    store=PreferencesStore(path)
    try:assert store.snapshot().values['grid_card_size_mode']=='large'
    finally:store.close()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM preferences_values WHERE key='grid_card_size_mode'").fetchone()==('invalid',)

@pytest.mark.parametrize('entry',['ranking','library','recommend','personal'])
def test_each_entry_persists_and_updates_all_views(tmp_path,entry):
    store=PreferencesStore(tmp_path/'preferences.sqlite3')
    class View:
        def __init__(self):self.value='large'
        def apply_card_size(self,value):self.value=value
    shell=DemoShell.__new__(DemoShell);shell.services=SimpleNamespace(preferences=store);shell.closed=False
    shell.gallery=View();shell.library=View();shell.recommend_view=View();shell.card_size='large'
    shell.backup_view=View()  # normal DemoShell initializes this optional view
    shell.personal_view=View()
    try:
        # All three existing UI handlers are wired to the same shell callback.
        callback={'ranking':shell.change_card_size,'library':shell.change_card_size,'recommend':shell.change_card_size,'personal':shell.change_card_size}[entry]
        callback('small')
        assert shell.card_size=='small'
        assert [v.value for v in (shell.gallery,shell.library,shell.recommend_view)]==['small']*3
        assert shell.backup_view.value=='small'
        assert shell.personal_view.value=='small'
        assert store.snapshot().values['grid_card_size_mode']=='small'
    finally:store.close()

@pytest.mark.asyncio
async def test_recommend_control_changes_real_render_without_resetting_page():
    from test_p3_ui_state import rendered_data
    client=Client(ui.page('/_l13_recommend'))
    try:
        with client:view=RecommendView(SimpleNamespace(recommendations=object()),card_size='medium')
        view.active=True;view.data=rendered_data();view.visible[('certain','tag')]=20
        view.render()
        grids=[e for e in view.body.descendants() if 'pv-grid' in e.classes]
        assert len(grids)==1 and grids[0].style.get('--pv-max-cols')=='8'
        assert any(str(e.props.get('data-testid','')).startswith('recommend-work-certain-')
                   for e in grids[0].descendants())
        view.change_card_size('small')
        assert view.card_size=='small' and view.visible[('certain','tag')]==20
        assert view.size_buttons['small'].props['aria-pressed']=='true'
        grids=[e for e in view.body.descendants() if 'pv-grid' in e.classes]
        assert len(grids)==1 and grids[0].style.get('--pv-max-cols')=='12'
        assert any(str(e.props.get('data-testid','')).startswith('recommend-work-certain-')
                   for e in grids[0].descendants())
    finally:client.delete()
