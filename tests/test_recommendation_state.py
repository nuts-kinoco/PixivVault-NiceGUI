from pathlib import Path
import hashlib,json,sqlite3
import pytest
from pixivvault_nicegui.recommendation_state import (
    FrozenRecommendationSource,RecommendationPreferences,RecommendationController,
    RecommendationError,ORDER,RecommendationSnapshot,RecommendationGroup,RecommendationWork)

def source_db(tmp_path):
    path=tmp_path/'source.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE discovery_swipe_history(work_id TEXT,verdict TEXT,recommend_dismissed INTEGER,judged_at TEXT)')
        db.executemany('INSERT INTO discovery_swipe_history VALUES(?,?,?,?)',[
            ('11','superLike',0,'2026-01-02'),('12','superLike',1,'2026-01-03'),('13','like',0,'2026-01-04')])
        db.execute('CREATE TABLE superlike_info_cache(work_id TEXT,title TEXT,user_id TEXT,user_name TEXT)')
        db.execute("INSERT INTO superlike_info_cache VALUES('11','Saved title','91','Author')")
        db.execute('CREATE TABLE recommend_section_carryover(section TEXT,payload_json TEXT,fetched_at TEXT)')
        db.execute('INSERT INTO recommend_section_carryover VALUES(?,?,?)',('certain',json.dumps([dict(tag='tag',display='Tag',works=[dict(id='11',title='Saved title',user_id='91',user_name='Author'),dict(id='14',title='Other',user_id='92',user_name='Second')])]),'2026-01-02'))
        db.execute('INSERT INTO recommend_section_carryover VALUES(?,?,?)',('boosted','[]','2026-01-02'))
        db.execute('CREATE TABLE discovery_hidden_works(work_id TEXT)')
        db.execute('CREATE TABLE discovery_hidden_authors(author_id TEXT)')
        db.execute('CREATE TABLE settings(key TEXT,value TEXT)')
    return path.resolve()

def ids(state,section):return [w.work_id for g in state['sections'][section] for w in g.works]

def test_legacy_saved_sections_never_change_source(tmp_path):
    path=source_db(tmp_path);before=hashlib.sha256(path.read_bytes()).digest()
    controller=RecommendationController(FrozenRecommendationSource(path),RecommendationPreferences((tmp_path/'prefs').resolve()))
    try:
        s=controller.refresh()
        assert ids(s,'superlike')==['11'] and ids(s,'boosted')==[]
        assert 'boosted' not in s['unavailable'] and 'trend' in s['unavailable']
        s=controller.hide('work','11',True,s['revision'],'genre')
        assert ids(s,'superlike')==[] and ids(s,'certain')==['14']
        s=controller.hide('work','11',False,s['revision'])
        assert ids(s,'superlike')==['11'] and ids(s,'certain')==['11','14']
        s=controller.hide('work','11',False,s['revision'])
        assert ids(s,'superlike')==['11']  # undo from second path never duplicates
        assert hashlib.sha256(path.read_bytes()).digest()==before
        assert not Path(str(path)+'-wal').exists()
    finally:controller.close()

def test_preferences_restart_order_collapse_and_author_hide(tmp_path):
    path=source_db(tmp_path);root=(tmp_path/'prefs').resolve()
    c=RecommendationController(FrozenRecommendationSource(path),RecommendationPreferences(root))
    s=c.refresh();s=c.hide('author','91',True,s['revision'])
    s=c.configure('recommend_section_order',tuple(reversed(ORDER)),s['revision'])
    s=c.configure('recommend_section_collapsed',['certain'],s['revision']);c.close()
    c=RecommendationController(FrozenRecommendationSource(path),RecommendationPreferences(root))
    try:
        s=c.refresh();assert s['order']==tuple(reversed(ORDER));assert s['collapsed']==frozenset({'certain'})
        assert ids(s,'superlike')==[] and ids(s,'certain')==['14']
    finally:c.close()

def test_stale_undo_cannot_remove_new_preference(tmp_path):
    c=RecommendationController(FrozenRecommendationSource(source_db(tmp_path)),RecommendationPreferences((tmp_path/'prefs').resolve()))
    try:
        s=c.refresh();a=c.hide('work','11',True,s['revision']);c.hide('work','11',True,a['revision'],'style')
        with pytest.raises(RecommendationError):c.hide('work','11',False,a['revision'])
        assert ids(c.snapshot(),'superlike')==[]
    finally:c.close()

def test_readonly_and_changed_source_fail_closed(tmp_path):
    path=source_db(tmp_path);c=RecommendationController(FrozenRecommendationSource(path));s=c.refresh()
    assert not s['writable']
    with pytest.raises(RecommendationError):c.hide('work','11',True,None)
    with sqlite3.connect(path) as db:db.execute("UPDATE superlike_info_cache SET title='changed'")
    with pytest.raises(RecommendationError):c.refresh()
    assert c.snapshot()==s

def test_source_hidden_preference_overlay_is_not_legacy_write(tmp_path):
    path=source_db(tmp_path)
    with sqlite3.connect(path) as db:db.execute("INSERT INTO discovery_hidden_works VALUES('11')")
    c=RecommendationController(FrozenRecommendationSource(path),RecommendationPreferences((tmp_path/'prefs').resolve()))
    try:
        s=c.refresh();assert ids(s,'superlike')==[]
        s=c.hide('work','11',False,s['revision']);assert ids(s,'superlike')==['11']
        with sqlite3.connect(path) as db:assert db.execute('SELECT count(*) FROM discovery_hidden_works').fetchone()[0]==1
    finally:c.close()

def test_unknown_preference_root_never_modified(tmp_path):
    root=tmp_path/'foreign';root.mkdir();(root/'foreign.txt').write_text('keep')
    with pytest.raises(Exception):RecommendationPreferences(root.resolve())
    assert list(root.iterdir())==[root/'foreign.txt']

def test_missing_cache_distinct_from_successfully_empty_cache(tmp_path):
    path=source_db(tmp_path)
    with sqlite3.connect(path) as db:db.execute("DELETE FROM recommend_section_carryover WHERE section='certain'")
    c=RecommendationController(FrozenRecommendationSource(path));s=c.refresh()
    assert 'certain' in s['unavailable'] and 'boosted' not in s['unavailable']

def test_injected_snapshot_source_has_no_model_or_network_dependency():
    class Source:
        def load(self):return RecommendationSnapshot((('superlike',(RecommendationGroup('s','saved',(RecommendationWork('1','one'),)),)),))
    c=RecommendationController(Source())
    assert ids(c.refresh(),'superlike')==['1']


def test_corrupt_carryover_never_replaces_loaded_snapshot(tmp_path):
    path=source_db(tmp_path)
    with sqlite3.connect(path) as db:db.execute("UPDATE recommend_section_carryover SET payload_json='{' WHERE section='certain'")
    c=RecommendationController(FrozenRecommendationSource(path))
    with pytest.raises(ValueError):c.refresh()
    with pytest.raises(RecommendationError):c.snapshot()

def test_second_preference_owner_is_rejected(tmp_path):
    root=(tmp_path/'prefs').resolve();first=RecommendationPreferences(root)
    try:
        with pytest.raises(Exception):RecommendationPreferences(root)
        assert first.snapshot()[0]==0
    finally:first.close()

@pytest.mark.asyncio
async def test_view_browsing_hide_undo_and_close_fence(tmp_path):
    from types import SimpleNamespace
    from nicegui import Client,ui
    from pixivvault_nicegui.ui.recommend_view import RecommendView
    c=RecommendationController(FrozenRecommendationSource(source_db(tmp_path)),RecommendationPreferences((tmp_path/'prefs').resolve()))
    client=Client(ui.page('/_recommend_unit'))
    try:
        with client:
            view=RecommendView(SimpleNamespace(recommendations=c))
            view.active=True
            await view.refresh()
            assert ids(view.data,'superlike')==['11']
            await view.hide('work','11')
            assert ids(view.data,'superlike')==[] and view.undo is not None
            await view.undo_hide()
            assert ids(view.data,'superlike')==['11'] and view.undo is None
            await view.collapse('superlike')
            assert 'superlike' in view.data['collapsed']
            revision=view.data['revision'];view.close()
            await view.hide('work','11')
            assert c.snapshot()['revision']==revision
    finally:
        if client.id in Client.instances:client.delete()
        c.close()
