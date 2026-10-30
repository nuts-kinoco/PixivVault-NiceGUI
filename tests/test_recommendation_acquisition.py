"""Generated fixtures only; no Pixiv, credentials or model execution."""
from dataclasses import replace
import hashlib,json,sqlite3
import pytest
from pixivvault_nicegui.recommendation_state import (
    RecommendationController,RecommendationPreferences,RecommendationSnapshot,
    RecommendationGroup,RecommendationWork,RecommendationError,
    FrozenRecommendationSource,DiscoveryRecommendationSource,EmptyRecommendationSource)
from pixivvault_nicegui.recommendation_acquisition import PixivRecommendationAdapter


def record(wid,aid='9'):
    return dict(id=str(wid),title='work '+str(wid),user_id=aid,user_name='author')


class Source:
    def __init__(self):
        self.value=RecommendationSnapshot(
            sections=(('superlike',(RecommendationGroup('superlike','super',
                (RecommendationWork('11','saved','9','author'),)),)),
                ('certain',(RecommendationGroup('old','Old',
                    (RecommendationWork('40','carryover','9','author'),),1,20),))),
            unavailable=('trend','trend_added','boosted'),
            judged=frozenset({'11','12'}),boosted=(('8','boosted'),))
    def load(self):return self.value


class Fetch:
    def __init__(self):self.calls=[];self.fail=set()
    def search(self,tag,page,mode):
        self.calls.append(('search',tag,page,mode))
        if (tag,page) in self.fail:raise RuntimeError('fixture failure')
        return {'works':[record(100+page),record(12),record(101+page),record(101+page)],'total':10}
    def author(self,aid,limit,excluded):
        self.calls.append(('author',aid,limit,frozenset(excluded)))
        if aid in self.fail:raise RuntimeError('fixture failure')
        return [record('201',aid),record('12',aid)]
    def detail(self,wid):
        self.calls.append(('detail',wid))
        if wid in self.fail:raise RuntimeError('fixture failure')
        return record(wid)


def make(tmp_path,fetch=None,trends=None):
    source=Source();fetch=fetch or Fetch()
    c=RecommendationController(source,RecommendationPreferences(tmp_path/'prefs'),
        acquisition=fetch,trend_provider=lambda: trends or [dict(tag='tag',display='Tag')])
    c.refresh()
    return c,source,fetch


def ids(state,kind):
    return [w.work_id for g in state['sections'].get(kind,()) for w in g.works]


def test_open_restart_get_zero_and_explicit_update_persists(tmp_path):
    c,source,fetch=make(tmp_path)
    assert fetch.calls==[]
    s=c.update(c.snapshot()['revision'])
    assert ids(s,'certain')==['101','102']
    assert ids(s,'boosted')==['201']
    assert ('search','tag',1,'all') in fetch.calls
    assert ('detail','11') in fetch.calls
    root=c.preferences.root;c.close();fetch.calls=[]
    c=RecommendationController(source,RecommendationPreferences(root),acquisition=fetch)
    try:
        s=c.refresh()
        assert fetch.calls==[] and ids(s,'certain')==['101','102']
        assert ids(s,'boosted')==['201']
    finally:c.close()


def test_original_top_five_trends_only_and_filtered_duplicates(tmp_path):
    c,_,fetch=make(tmp_path,trends=[dict(tag=str(i),display=str(i)) for i in range(8)])
    try:
        s=c.update(0)
        assert [r[1] for r in fetch.calls if r[0]=='search']==['0','1','2','3','4']
        assert all(len(g.works)==2 for g in s['sections']['certain'])
    finally:c.close()


def test_page_failure_same_page_retry_and_independent_added_shelf(tmp_path):
    c,_,fetch=make(tmp_path)
    try:
        s=c.update(0);s=c.add_trend('tag','Tag',s['revision'])
        fetch.fail.add(('tag',2))
        with pytest.raises(RuntimeError):c.next_page('certain','tag',s['revision'])
        assert c.snapshot()['sections']['certain'][0].page==1
        assert c.snapshot()['revision']==s['revision']
        fetch.fail.clear()
        s=c.next_page('certain','tag',s['revision'])
        assert s['sections']['certain'][0].page==2
        assert s['sections']['trend_added'][0].page==1
        assert ids(s,'certain')==['101','102','103']
        assert [r[2] for r in fetch.calls if r[0]=='search'][-2:]==[2,2]
        s=c.next_page('trend_added','tag',s['revision'])
        assert s['sections']['trend_added'][0].page==2
    finally:c.close()


def test_failed_empty_update_retains_carryover_and_original_source(tmp_path):
    c,source,fetch=make(tmp_path);original=source.value
    try:
        fetch.fail.update({('tag',1),'8','11'})
        s=c.update(0)
        assert ids(s,'certain')==['40'] and ids(s,'superlike')==['11']
        assert len(s['errors'])==3 and source.value==original
        fetch.search=lambda *args:dict(works=[],total=0)
        s=c.update(s['revision'])
        assert ids(s,'certain')==['40']
    finally:c.close()


def test_late_fetch_cas_does_not_overwrite_hide(tmp_path):
    c,_,fetch=make(tmp_path)
    old=fetch.search
    def raced(*args):
        c.hide('work','101',True,0)
        return old(*args)
    fetch.search=raced
    try:
        with pytest.raises(RecommendationError):c.update(0)
        s=c.snapshot()
        assert s['revision']==1 and '101' in s['hidden_works']
        assert ids(s,'certain')==['40']
    finally:c.close()


def test_hide_then_page_then_undo_preserves_hidden_cache_entry(tmp_path):
    c,_,_=make(tmp_path)
    try:
        s=c.update(0)
        s=c.hide('work','101',True,s['revision'])
        s=c.next_page('certain','tag',s['revision'])
        assert ids(s,'certain')==['102','103']
        s=c.hide('work','101',False,s['revision'])
        assert ids(s,'certain')==['101','102','103']
    finally:c.close()


def test_boost_registration_failure_survives_hidden_author_and_restart(tmp_path):
    c,source,fetch=make(tmp_path)
    try:
        fetch.fail.add('99')
        s=c.boost('99','Ninety nine',True,0)
        assert ('99','Ninety nine') in s['boosted']
        s=c.hide('author','99',True,s['revision'])
        assert ('99','Ninety nine') in s['boosted']
        root=c.preferences.root;c.close()
        c=RecommendationController(source,RecommendationPreferences(root),acquisition=fetch)
        s=c.refresh()
        assert ('99','Ninety nine') in s['boosted'] and '99' in s['hidden_authors']
        fetch.calls=[]
        s=c.boost('99','Ninety nine',False,s['revision'])
        assert fetch.calls==[] and '99' not in dict(s['boosted'])
    finally:c.close()


def test_added_tag_failure_does_not_register_and_offline_remove(tmp_path):
    c,_,fetch=make(tmp_path)
    try:
        fetch.fail.add(('tag',1))
        with pytest.raises(RuntimeError):c.add_trend('tag','Tag',0)
        assert c.snapshot()['added_tags']==() and c.snapshot()['revision']==0
        fetch.fail.clear()
        s=c.add_trend('tag','Tag',0)
        c.acquisition.source=None
        s=c.remove_trend('tag',s['revision'])
        assert not s['added_tags'] and not s['sections']['trend_added']
    finally:c.close()


def test_next_page_no_new_works_exhausts_without_repeating_get(tmp_path):
    c,_,fetch=make(tmp_path)
    try:
        s=c.update(0)
        fetch.search=lambda *args:dict(works=[record(101),record(12)],total=10)
        s=c.next_page('certain','tag',s['revision'])
        assert s['sections']['certain'][0].exhausted
        fetch.search=lambda *args:pytest.fail('exhausted page must not GET')
        assert c.next_page('certain','tag',s['revision'])==s
    finally:c.close()


def test_adapter_uses_explicit_original_reader_methods_and_early_exit():
    class Reader:
        def search_recommendation_illusts(self,tag,**kw):
            assert kw==dict(page=3,mode='all');return dict(works=[record(1)],total=40)
        def get_discovery_work(self,wid):return record(wid)
        def get_user_works(self,aid,early_exit_checker,lane):
            assert lane=='interactive'
            result=[]
            for wid in ('1','2','3','4'):
                row=record(wid,aid)
                if early_exit_checker(row):break
                result.append(row)
            return result
    adapter=PixivRecommendationAdapter(Reader())
    assert adapter.search('tag',3,'all')['total']==40
    assert adapter.detail('7')['id']=='7'
    assert [r['id'] for r in adapter.author('9',2,{'1'})]==['2','3']


def test_source_reads_sidecar_superlikes_newest_first_without_inventing_results():
    class Discovery:
        def recommendation_snapshot(self):
            return dict(works=[dict(work_id='3',title='three',author_id='9',author_name='A')],
                judged_ids=frozenset({'3','4','5'}),superlike_ids=frozenset({'3','4'}),
                superlike_order=('4','3'))
    source=DiscoveryRecommendationSource(Discovery())
    value=source.load()
    assert [w.work_id for w in value.sections[0][1][0].works]==['4','3']
    assert value.sections[0][1][0].works[0].title==''  # no invented detail
    assert value.judged==frozenset({'3','4','5'})
    empty=EmptyRecommendationSource().load()
    assert not empty.sections and 'certain' in empty.unavailable


@pytest.mark.asyncio
async def test_ui_updates_trend_add_page_boost_and_closed_fence(tmp_path):
    from types import SimpleNamespace
    from nicegui import Client,ui
    from pixivvault_nicegui.ui.recommend_view import RecommendView
    c,_,fetch=make(tmp_path)
    client=Client(ui.page('/_recommend_residual_unit'))
    try:
        with client:
            view=RecommendView(SimpleNamespace(recommendations=c))
            view.active=True;await view.refresh()
            assert not fetch.calls
            await view.update()
            await view.toggle_trend('tag','Tag')
            assert view.data['added_tags']==({'tag':'tag','display':'Tag'},)
            await view.next_page('trend_added','tag')
            assert view.data['sections']['trend_added'][0].page==2
            await view.boost('55','Fifty five',True)
            assert ('55','Fifty five') in view.data['boosted']
            await view.toggle_trend('tag','Tag')
            assert not view.data['added_tags']
            calls=len(fetch.calls);view.close()
            await view.update()
            assert len(fetch.calls)==calls
    finally:
        if client.id in Client.instances:client.delete()
        c.close()


def test_original_db_immutable_during_new_acquisition_and_boost(tmp_path):
    from test_recommendation_state import source_db
    path=source_db(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE discovery_boosted_authors(author_id TEXT,display_name TEXT,boosted_at TEXT)')
        db.execute("INSERT INTO discovery_boosted_authors VALUES('8','Eight','2026-01-01')")
        db.execute("INSERT INTO settings VALUES('recommend_trend_added_tags',?)",
            (json.dumps([dict(tag='oldtag',display='Old tag')]),))
        db.execute("INSERT INTO settings VALUES('discovery_search_mode','safe')")
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    fetch=Fetch()
    c=RecommendationController(FrozenRecommendationSource(path),
        RecommendationPreferences(tmp_path/'prefs'),acquisition=fetch,
        trend_provider=lambda:[dict(tag='tag',display='Tag')])
    try:
        s=c.refresh();assert fetch.calls==[]
        assert s['added_tags']==({'tag':'oldtag','display':'Old tag'},)
        s=c.update(s['revision'])
        assert ('search','oldtag',1,'safe') in fetch.calls
        s=c.boost('8','Eight',False,s['revision'])
        s=c.boost('99','Other',True,s['revision'])
        assert hashlib.sha256(path.read_bytes()).hexdigest()==before
        assert not path.with_name(path.name+'-wal').exists()
        assert not path.with_name(path.name+'-journal').exists()
    finally:c.close()


def test_combined_source_current_judgment_overrides_old_superlike():
    from pixivvault_nicegui.recommendation_state import CombinedRecommendationSource
    legacy=Source();current=Source()
    current.value=RecommendationSnapshot(
        sections=(('superlike',(RecommendationGroup('superlike','new',
            (RecommendationWork('15','new'),)),)),),
        judged=frozenset({'11','15'}))
    value=CombinedRecommendationSource(legacy,current).load()
    assert [w.work_id for g in dict(value.sections)['superlike'] for w in g.works]==['15']
    assert dict(value.sections)['certain'][0].key=='old'
    assert value.boosted==(('8','boosted'),)


def test_more_author_uses_original_expansion_steps_and_stale_cas(tmp_path):
    c,_,fetch=make(tmp_path)
    try:
        s=c.update(0)
        s=c.more_author('8',24,s['revision'])
        assert fetch.calls[-1][0:3]==('author','8',24)
        with pytest.raises(RecommendationError):c.more_author('8',25,s['revision'])
        with pytest.raises(RecommendationError):c.more_author('8',36,0)
        c.acquisition.visible_limit=16
        s=c.more_author('8',32,s['revision'])
        assert fetch.calls[-1][0:3]==('author','8',32)
    finally:c.close()


def test_legacy_trend_exact_original_counts_readonly(tmp_path):
    from test_recommendation_state import source_db
    from pixivvault_nicegui.discovery_ranking import trend_rows
    path=source_db(tmp_path)
    columns=('superlike_count','like_count','skip_count','hate_count','shown_count',
             'open_count','bookmark_count','seed_saved','seed_bookmarked')
    values=(3,5,1,1,20,0,0,2,0)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE discovery_preference(tag TEXT,display_tag TEXT,'+
            ','.join(k+' INTEGER' for k in columns)+')')
        db.execute('INSERT INTO discovery_preference VALUES('+','.join('?' for _ in range(11))+')',
            ('specific','Specific')+values)
        db.execute('CREATE TABLE works(id TEXT)')
        db.executemany('INSERT INTO works VALUES(?)',[(str(i),) for i in range(100)])
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    source=FrozenRecommendationSource(path)
    expected=trend_rows({'specific':dict(zip(columns,values),display_tag='Specific')},total_works=100)
    assert source.trend_rows()==tuple(expected)
    assert source.trend_rows()[0]['score']==8
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before


def test_detail_cache_never_overrides_changed_superlike_membership(tmp_path):
    c,source,_=make(tmp_path)
    try:
        s=c.update(0)
        assert ids(s,'superlike')==['11']
        source.value=replace(source.value,sections=(
            ('superlike',(RecommendationGroup('superlike','super',(RecommendationWork('15','new'),)),)),),
            judged=frozenset({'11','15'}))
        s=c.refresh()
        assert ids(s,'superlike')==['15']
    finally:c.close()



@pytest.mark.asyncio
async def test_view_save_retains_operation_on_failure_and_reports_acceptance(tmp_path):
    from nicegui import Client,ui
    from pixivvault_nicegui.ui.recommend_view import RecommendView
    from types import SimpleNamespace
    c,_,_=make(tmp_path);calls=[]
    async def save(wid,operation,*,is_current):
        assert is_current();calls.append((wid,operation))
        if len(calls)==1:raise RuntimeError('uncertain fixture result')
        return SimpleNamespace(job_id='accepted')
    client=Client(ui.page('/recommend-save-fixture'))
    try:
        with client,ui.card():view=RecommendView(SimpleNamespace(recommendations=c),on_enqueue=save)
        view.active=True
        await view.refresh()
        await view.save_work('11')
        assert '確認できません' in view.notice.text
        await view.save_work('11')
        assert calls[0]==calls[1] and 'キュー' in view.notice.text
        await view.save_work('999')
        view.close();await view.save_work('11')
        assert len(calls)==2
    finally:
        c.close();client.delete()
