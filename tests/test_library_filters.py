"""P3 original local filter semantics and readonly metadata propagation."""
from dataclasses import replace
import sqlite3
from types import SimpleNamespace
import pytest
from nicegui import Client,ui
from pixivvault_nicegui.models import Artwork
from pixivvault_nicegui.library_filters import LibraryFilters
from pixivvault_nicegui.library_models import LibraryWork,LibrarySnapshot
from pixivvault_nicegui.library_state import LibraryState
from pixivvault_nicegui.bookmark_state import BookmarkState
from pixivvault_nicegui.library_repository import ReadonlyLibraryRepository
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.library_view import LibraryView


def art(wid='1',**kwargs):
    return Artwork(wid,'Title','Author',tags=('red',),author_id='10',**kwargs)


def test_date_is_inclusive_date_only_and_missing_is_not_invented():
    filters=LibraryFilters(date_from='2026-01-01',date_to='2026-01-31')
    assert filters.matches_artwork(art(create_date='2026-01-31T23:59:59+09:00'))
    assert filters.matches_artwork(art(create_date='2026-01-01T00:00:00Z'))
    assert not filters.matches_artwork(art(create_date='2026-02-01T00:00:00Z'))
    assert not filters.matches_artwork(art())
    # Legacy end-only bound allows missing dates; DTO remains explicitly unknown.
    assert LibraryFilters(date_to='2026-01-31').matches_artwork(art())


@pytest.mark.parametrize('field,value,excluded',[('bookmark_count',None,True),('bookmark_count',99,True),('bookmark_count',100,False)])
def test_bookmark_minimum_unknown_and_boundary(field,value,excluded):
    assert LibraryFilters(bookmark_min=100).matches_artwork(art(**{field:value})) is not excluded


def test_original_restriction_ai_and_history_unknown_fallbacks():
    filters=LibraryFilters(include_r18=False,exclude_ai=True,unread_only=True)
    assert filters.matches_artwork(art())
    assert not filters.matches_artwork(art(x_restrict=1))
    assert not filters.matches_artwork(art(x_restrict=2))
    assert not filters.matches_artwork(art(ai_type=2))
    assert filters.matches_artwork(art(ai_type=1))
    assert not filters.matches_artwork(art(viewed=True))
    assert filters.matches_artwork(art(viewed=False))


def test_author_tags_and_unread_representative_use_original_grouping():
    read_old=LibraryWork(art('1',viewed=True), '10', last_backup='2025-01-01')
    unread_new=LibraryWork(art('2',viewed=False), '10', last_backup='2026-01-01')
    state=LibraryState(LibrarySnapshot((read_old,unread_new)))
    assert state.set_filters(LibraryFilters(authors=frozenset({'10'}),include=frozenset({'red'}),unread_only=True))
    assert state.matched_author_count==1
    assert state.open_author('10') and state.total_page_count==2
    read_new=replace(unread_new,artwork=replace(unread_new.artwork,viewed=True))
    state=LibraryState(LibrarySnapshot((replace(read_old,artwork=replace(read_old.artwork,viewed=False)),read_new)))
    state.set_filters(LibraryFilters(unread_only=True))
    assert state.matched_author_count==0


def test_unfetched_is_bookmark_only_and_preserves_hidden_selection():
    a,b=art('1'),art('2')
    state=BookmarkState(local=(a,),cached_remote=(a,b))
    state.select('1')
    state.set_filters(LibraryFilters(unfetched_only=True,authors=frozenset({'10'})))
    assert [c.work_id for c in state.matched_items]==['2']
    assert state.selected_ids=={'1'}
    local=LibraryState(LibrarySnapshot((LibraryWork(a,'10'),)))
    local.set_filters(state.filters)
    assert local.matched_author_count==1


def test_capacity_keeps_original_actual_tuple_order_and_direction_memory():
    works=tuple(LibraryWork(Artwork(str(i),str(i),'author'),str(i),capacity_bytes=size) for i,size in enumerate((None,30,10,10)))
    state=LibraryState(LibrarySnapshot(works))
    state.select_sort('capacity')
    assert [a.author_id for a in state.visible_authors]==['1','2','3','0']
    state.select_sort('capacity')
    assert [a.author_id for a in state.visible_authors]==['0','2','3','1']
    state.select_sort('view_count');state.select_sort('capacity')
    assert not state.descending


def test_readonly_optional_metadata_and_zero_count_history_membership(tmp_path):
    path=tmp_path/'library.db'
    with sqlite3.connect(path) as connection:
        connection.executescript("""
          CREATE TABLE works(work_id TEXT,title TEXT,page_count INTEGER,tags TEXT,user_id TEXT,is_deleted INTEGER,
            create_date TEXT,bookmark_count INTEGER,x_restrict INTEGER,ai_type INTEGER,is_bookmarked INTEGER);
          CREATE TABLE following_users(user_id TEXT,name TEXT);
          CREATE TABLE discovery_view_history(work_id TEXT,open_count INTEGER);
          INSERT INTO following_users VALUES('10','Author');
          INSERT INTO works VALUES('1','one',1,'["red"]','10',0,'2026-01-31T23:59:59',150,1,2,1);
          INSERT INTO works VALUES('2','two',1,'[]','10',0,NULL,NULL,NULL,NULL,1);
          INSERT INTO discovery_view_history VALUES('1',0);
        """)
    before=path.read_bytes()
    with ReadonlyLibraryRepository(path) as repo:
        one,two=(w.artwork for w in repo.library_snapshot().works)
        assert (one.author_id,one.create_date,one.bookmark_count,one.x_restrict,one.ai_type,one.viewed)==('10','2026-01-31T23:59:59',150,1,2,True)
        assert (two.create_date,two.bookmark_count,two.x_restrict,two.ai_type,two.viewed)==(None,None,None,None,False)
        assert repo.local_bookmark_snapshot()==(one,two)
    assert path.read_bytes()==before


@pytest.mark.asyncio
async def test_filter_ui_preserves_conditions_when_tags_change_and_rejects_stale_epoch():
    services=ApplicationServices(120);client=Client(ui.page('/_p3_filter_ui'))
    try:
        with client:view=LibraryView(services)
        panel=view.filter_panel;panel.toggle()
        panel.change_setting('date_from','2026-01-01')
        panel.change_setting('bookmark_min',100)
        panel.change_setting('include_r18',False)
        panel.add(value='red')
        assert view.filters.date_from=='2026-01-01' and view.filters.bookmark_min==100 and not view.filters.include_r18
        panel.toggle_mode();panel.flip('red',panel._session)
        assert view.filters.exclude==frozenset({'red'}) and view.filters.date_from=='2026-01-01'
        handler=next(e.handler for e in panel.exclude_ai._event_listeners.values() if e.js_handler and 'filterEpoch' in e.js_handler)
        stale=SimpleNamespace(args={'epoch':panel._epoch,'value':True})
        view.set_active(False);view.set_active(True);handler(stale)
        assert not view.filters.exclude_ai
        view.reload(preserve=True)
        assert view.filters.date_from=='2026-01-01' and view.state.filters==view.filters
        panel.clear();assert view.filters==LibraryFilters()
        assert 'capacity' in view.sort_buttons
    finally:client.delete();services.close()


@pytest.mark.asyncio
async def test_saved_navigation_checks_snapshot_page_bounds_and_view_lifetime(monkeypatch):
    from pixivvault_nicegui.ui.viewer_controller import ViewerController
    services=ApplicationServices(120);client=Client(ui.page('/_p3_saved_navigation'))
    opened=[]
    monkeypatch.setattr(ViewerController,'open',lambda self,wid,page:opened.append((wid,page)))
    try:
        with client:view=LibraryView(services)
        view.show_subview('bookmarks')
        assert view.open_saved_work('demo-0060',3)
        assert opened==[('demo-0060',2)] and view.subview=='downloaded'
        assert not view.open_saved_work('demo-0060',4)
        assert not view.open_saved_work('missing',1)
        assert not view.open_saved_work('demo-0060',True)
        view.set_active(False)
        assert not view.open_saved_work('demo-0060',1)
        assert opened==[('demo-0060',2)]
    finally:client.delete();services.close()


def test_author_directory_keeps_follow_order_and_authors_without_works(tmp_path):
    path=tmp_path/'authors.db'
    with sqlite3.connect(path) as c:
        c.executescript("""
            CREATE TABLE works(work_id TEXT,title TEXT,page_count INTEGER,tags TEXT,user_id TEXT,is_deleted INTEGER);
            CREATE TABLE following_users(user_id TEXT,name TEXT,follow_order INTEGER);
            INSERT INTO following_users VALUES('20','Same saved',2);
            INSERT INTO following_users VALUES('10','Same empty',1);
            INSERT INTO works VALUES('1','work',1,'[]','20',0);
        """)
    before=path.read_bytes()
    with ReadonlyLibraryRepository(path) as repo:snapshot=repo.library_snapshot()
    assert list(snapshot.author_names)==['10','20']
    assert snapshot.author_names['10']=='Same empty'
    state=LibraryState(snapshot);state.set_filters(LibraryFilters(authors=frozenset({'10'})))
    assert state.matched_author_count==0
    assert path.read_bytes()==before
