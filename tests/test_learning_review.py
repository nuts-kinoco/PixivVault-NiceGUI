import asyncio
from datetime import datetime, timedelta
import pytest
from pixivvault_nicegui.fixtures import generate_demo_artworks
from pixivvault_nicegui.learning import Judgment
from pixivvault_nicegui.learning_review import ReviewFilter, PAGE_SIZE
from pixivvault_nicegui.learning_repository import LearningConflict
from pixivvault_nicegui.learning_service import LearningService

NOW=datetime(2026,10,26,12)
def row(verdict='like',**changes):
    return dict(dict(work_id='1',verdict=verdict,title='Blue sky',tags='["cloud"]',judged_at=NOW.isoformat(),work_meta=None,is_deleted=0),**changes)

@pytest.mark.parametrize('verdict,positive,expected',[('like',True,True),('superLike',True,True),('skip',False,True),('hate',False,True),('skip',True,False),('like',False,False)])
def test_review_categories(verdict,positive,expected):
    assert ReviewFilter().matches(row(verdict),positive,now=NOW,session_started=NOW)==expected

def test_original_review_filters_do_not_fake_saved_or_time():
    def match(f,r,positive=True):return f.matches(r,positive,now=NOW,session_started=NOW-timedelta(hours=1))
    assert match(ReviewFilter(text='CLOUD'),row())
    assert not match(ReviewFilter(text='absent'),row())
    assert not match(ReviewFilter(),row(work_meta='{}'))
    assert match(ReviewFilter(),row(work_meta='{}',is_deleted=1))
    assert not match(ReviewFilter(positive_superlike_only=True),row())
    assert match(ReviewFilter(positive_superlike_only=True),row('superLike'))
    assert not match(ReviewFilter(negative_hate_only=True),row('skip'),False)
    for period,age in [('today',1),('7d',8),('30d',31),('session',1)]:
        assert not match(ReviewFilter(period=period),row(judged_at=(NOW-timedelta(days=age)).isoformat()))
    assert not match(ReviewFilter(period='7d'),row(judged_at='broken'))
    assert PAGE_SIZE==10

@pytest.fixture
async def service(tmp_path):
    s=LearningService(tmp_path/'learning',generate_demo_artworks(3))
    try:yield s
    finally:await asyncio.to_thread(s.close)

async def history(s,session):return (await s.review_snapshot(session))['rows']

async def test_review_undo_redo_is_atomic_idempotent_and_updates_preferences(service):
    main=await service.open_session();review=await service.open_session()
    await service.record(main.session_id,Judgment('demo-0001','like','judge'))
    before=await service.review_snapshot(review.session_id)
    r=before['rows'][0]
    assert r['title'] and r['revision']>=1
    await service._wait(service.submit_review_undo(review.session_id,r['work_id'],r['revision'],'undo'))
    await service._wait(service.submit_review_undo(review.session_id,r['work_id'],r['revision'],'undo'))
    undone=await service.review_snapshot(review.session_id)
    assert undone['rows']==[] and not undone['tags'] and not undone['authors']
    await service._wait(service.submit_review_restore(review.session_id,'undo','redo'))
    await service._wait(service.submit_review_restore(review.session_id,'undo','redo'))
    after=await service.review_snapshot(review.session_id)
    assert after['rows'][0]['verdict']=='like' and after['rows'][0]['revision']>r['revision']
    assert after['tags']==before['tags'] and after['authors']==before['authors']
    with pytest.raises(LearningConflict):await service.revert(main.session_id,Judgment('demo-0001','like','judge'),'old-stack-undo')

async def test_stale_review_and_aba_cannot_remove_new_judgment(service):
    first=await service.open_session();second=await service.open_session()
    await service.record(first.session_id,Judgment('demo-0001','like','one'))
    old=(await history(service,first.session_id))[0]
    await service.record(second.session_id,Judgment('demo-0001','like','two'))
    with pytest.raises(LearningConflict):
        await service._wait(service.submit_review_undo(first.session_id,'demo-0001',old['revision'],'stale'))
    current=(await history(service,first.session_id))[0]
    await service._wait(service.submit_review_undo(first.session_id,'demo-0001',current['revision'],'undo'))
    await service.record(second.session_id,Judgment('demo-0001','hate','three'))
    with pytest.raises(LearningConflict):await service._wait(service.submit_review_restore(first.session_id,'undo','redo'))
    assert (await history(service,first.session_id))[0]['verdict']=='hate'

async def test_review_receipts_are_session_owned_and_acceptance_precedes_release(service):
    main=await service.open_session();review=await service.open_session()
    await service.record(main.session_id,Judgment('demo-0001','like','judge'))
    r=(await history(service,review.session_id))[0]
    pending=service.submit_review_undo(review.session_id,r['work_id'],r['revision'],'undo')
    service.release_session(review.session_id)
    await service._wait(pending)
    assert await history(service,main.session_id)==[]
    fresh=await service.open_session()
    with pytest.raises(KeyError):await service._wait(service.submit_review_restore(fresh.session_id,'undo','redo'))

async def test_review_failure_rolls_back_history_and_counts(service,monkeypatch):
    session=await service.open_session()
    await service.record(session.session_id,Judgment('demo-0001','superLike','judge'))
    before=await service.review_snapshot(session.session_id);r=before['rows'][0]
    original=service._db._recompute_consecutive_skips
    def fail(*args):raise OSError('synthetic write failure')
    monkeypatch.setattr(service._db,'_recompute_consecutive_skips',fail)
    with pytest.raises(OSError):await service._wait(service.submit_review_undo(session.session_id,r['work_id'],r['revision'],'retryable'))
    assert await service.review_snapshot(session.session_id)==before
    monkeypatch.setattr(service._db,'_recompute_consecutive_skips',original)
    await service._wait(service.submit_review_undo(session.session_id,r['work_id'],r['revision'],'retryable'))
    assert await history(service,session.session_id)==[]


async def test_old_v2_fixture_marker_identity_is_unchanged(tmp_path):
    import hashlib,json
    from pixivvault_nicegui.learning_service import MARKER_NAME
    arts=generate_demo_artworks(2)
    original=[dict(work_id=a.work_id,title=a.title,author=a.author,description=a.description,
                   aspect_ratio=a.aspect_ratio,page_count=a.page_count,tags=a.tags) for a in arts]
    fingerprint=hashlib.sha256(json.dumps(original,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    s=LearningService(tmp_path/'old',arts)
    try:
        await s.open_session()
        marker=json.loads((tmp_path/'old'/MARKER_NAME).read_bytes())
        assert marker['format']=='pixivvault-nicegui-synthetic-learning-v2'
        assert marker['fixture_sha256']==fingerprint
    finally:await asyncio.to_thread(s.close)
    before=(tmp_path/'old'/MARKER_NAME).read_bytes()
    s=LearningService(tmp_path/'old',arts)
    try:await s.open_session()
    finally:await asyncio.to_thread(s.close)
    assert (tmp_path/'old'/MARKER_NAME).read_bytes()==before

async def test_review_preserves_seed_and_hidden_records(service):
    session=await service.open_session()
    await service.record(session.session_id,Judgment('demo-0001','like','judge'))
    def seed():
        db=service._db
        with db._transaction():
            db.conn.execute('UPDATE discovery_preference SET seed_saved=7')
            db.conn.execute('UPDATE discovery_author_preference SET seed_following=2')
            db.conn.execute("INSERT INTO discovery_hidden_works(work_id,reason) VALUES ('hidden-work','fixture')")
            db.conn.execute("INSERT INTO discovery_hidden_authors(author_id,reason) VALUES ('hidden-author','fixture')")
    await service._wait(service._submit(seed))
    old=await service.review_snapshot(session.session_id);r=old['rows'][0]
    await service._wait(service.submit_review_undo(session.session_id,r['work_id'],r['revision'],'undo'))
    after=await service.review_snapshot(session.session_id)
    assert all(v['seed_saved']==7 for v in after['tags'].values())
    assert all(v['seed_following']==2 for v in after['authors'].values())
    def hidden():return tuple(service._db.conn.execute('SELECT work_id FROM discovery_hidden_works'))[0][0],tuple(service._db.conn.execute('SELECT author_id FROM discovery_hidden_authors'))[0][0]
    assert await service._wait(service._submit(hidden))==('hidden-work','hidden-author')


async def test_review_ui_requeues_and_refuses_stale_closed_actions(tmp_path,monkeypatch):
    from nicegui import Client,ui
    from pixivvault_nicegui.services import ApplicationServices
    from pixivvault_nicegui.ui.learning_view import LearningView
    tasks=[]
    def start(awaitable,**_):
        task=asyncio.create_task(awaitable);tasks.append(task);return task
    monkeypatch.setattr('pixivvault_nicegui.ui.learning_view.background_tasks.create',start)
    services=ApplicationServices(count=2,learning_sandbox=tmp_path/'ui-learning')
    session=await services.learning.open_session()
    await services.learning.record(session.session_id,Judgment('demo-0001','like','record'))
    client=Client(ui.page('/_review_test'))
    with client,ui.card():view=LearningView(services)
    try:
        view.set_active(True)
        await tasks[0]
        assert view.state.current_artwork.work_id=='demo-0002'
        with client:await view.open_review()
        sid=view._review_session_id
        with client:await view.open_review()
        assert view._review_session_id==sid  # duplicate-open event does not leak a session
        row=view._review_rows[0]
        with client:await view._review_change(row)
        await asyncio.gather(*tasks)
        assert view.state.current_artwork.work_id=='demo-0001'
        operation=view._review_undone['demo-0001'][1]
        with client:await view._review_change(row,operation)
        await asyncio.gather(*tasks)
        assert view.state.current_artwork.work_id=='demo-0002'
        view._close_review()
        with client:await view._review_change(row)
        assert len((await services.learning.review_snapshot(session.session_id))['rows'])==1
    finally:
        view.close();await asyncio.gather(*tasks,return_exceptions=True)
        await asyncio.to_thread(services.close);client.delete()
