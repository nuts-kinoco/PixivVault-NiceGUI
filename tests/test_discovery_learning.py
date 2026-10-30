import asyncio
import pytest
from pixivvault_nicegui.discovery_learning import DiscoveryLearningService
from pixivvault_nicegui.learning import Judgment
from pixivvault_nicegui.learning_repository import LearningConflict
from pixivvault_nicegui import discovery_engine, discovery_ranking

class Reader:
    def __init__(self): self.calls=[];self.fail=False
    def require_identity(self):self.calls.append('identity')
    def get_ranking(self,**kwargs):
        self.calls.append(('ranking',kwargs))
        if self.fail:raise RuntimeError('fixture failure')
        return [dict(illust_id=str(i),title=f'fixture {i}',user_id='99',user_name='author',url=f'https://i.pximg.net/img/{i}.jpg',tags=['landscape','sky']) for i in range(1,26)]
    def get_user_works(self,user_id,**kwargs):
        self.calls.append(('following',user_id))
        return [dict(id='30',title='followed',user_name='author',thumbnail_url='https://i.pximg.net/img/30.jpg',tags=['sky'])]
    def get_recommended_users(self,user_id,**kwargs):
        self.calls.append(('related',user_id));return {'users':[],'works':[]}
    def search_recommendation_illusts(self,word,**kwargs):
        self.calls.append(('search',word));return {'works':[]}

async def worker(service,fn):return await service._wait(service._submit(fn))

async def test_fetch_judge_review_undo_restart(tmp_path):
    reader=Reader();root=tmp_path/'discovery'
    service=DiscoveryLearningService(root,reader)
    assert not root.exists() and reader.calls==[]
    try:
        batch=await service.fetch_candidates()
        assert len(batch.artworks)==25 and not batch.failed
        session=await service.open_session()
        judgment=Judgment('1','like','op1')
        await service.record(session.session_id,judgment)
        assert (await service.review_snapshot(session.session_id))['rows'][0]['title']=='fixture 1'
    finally:service.close()
    service=DiscoveryLearningService(root,reader)
    try:
        session=await service.open_session()
        assert session.judged_ids=={'1'}
        row=(await service.review_snapshot(session.session_id))['rows'][0]
        await service._wait(service.submit_review_undo(session.session_id,'1',row['revision'],'undo'))
        assert '1' in {a.work_id for a in (await service.fetch_candidates()).artworks}
        await service._wait(service.submit_review_restore(session.session_id,'undo','restore'))
        assert (await service.open_session()).judged_ids=={'1'}
    finally:service.close()

async def test_four_sources_saved_exclusion_and_failure_not_empty_success(tmp_path):
    reader=Reader(); service=DiscoveryLearningService(tmp_path/'source',reader,following=[{'user_id':'12','name':'followed'}],saved_ids=['1'])
    try:
        batch=await service.fetch_candidates()
        assert '1' not in {a.work_id for a in batch.artworks}
        assert '30' in {a.work_id for a in batch.artworks}
        assert {'following','ranking','related_author','tag_search'}==set(batch.succeeded)
        reader.fail=True
        empty=await service.fetch_candidates(exclude_ids=[str(i) for i in range(1,31)])
        assert empty.nothing_and_failed and 'ranking' in empty.failed
    finally:service.close()

async def test_trend_original_threshold_and_formula(tmp_path):
    service=DiscoveryLearningService(tmp_path/'trend',Reader())
    try:
        await service.fetch_candidates();session=await service.open_session()
        for i in range(1,20):await service.record(session.session_id,Judgment(str(i),'like',f'like{i}'))
        assert not (await service.trend_snapshot())['ready']
        await service.record(session.session_id,Judgment('20','superLike','super20'))
        trend=await service.trend_snapshot();assert trend['ready'] and trend['judged']==20
        expected=await worker(service,lambda:discovery_ranking.trend_rows(service._database().get_discovery_preferences(),total_works=0,get_setting=service._database().get_setting))
        assert trend['rows']==tuple(expected)
    finally:service.close()

async def test_session_cas_and_synthetic_marker_rejected(tmp_path):
    from pixivvault_nicegui.learning_service import LearningService
    from pixivvault_nicegui.fixtures import generate_demo_artworks
    root=tmp_path/'marker';old=LearningService(root,generate_demo_artworks(1));await old.open_session();old.close()
    service=DiscoveryLearningService(root,Reader())
    try:
        with pytest.raises(ValueError):await service.open_session()
    finally:service.close()
    service=DiscoveryLearningService(tmp_path/'cas',Reader())
    try:
        await service.fetch_candidates();one=await service.open_session();two=await service.open_session()
        first=Judgment('1','like','one');await service.record(one.session_id,first)
        await service.record(two.session_id,Judgment('1','hate','two'))
        with pytest.raises(LearningConflict):await service.revert(one.session_id,first,'undo')
    finally:service.close()

def test_no_ambient_database_or_client_and_typed_404():
    with pytest.raises(ValueError):discovery_engine._get_db(None)
    with pytest.raises(ValueError):discovery_engine._get_client(None,None)
    assert not discovery_engine._is_not_found(RuntimeError('404 token'))
    err=RuntimeError();err.status_code=404
    assert discovery_engine._is_not_found(err)

async def test_verified_saved_projection_and_recommendation_snapshot(tmp_path):
    saved=[];service=DiscoveryLearningService(tmp_path/'saved',Reader(),saved_ids=lambda:tuple(saved))
    try:
        await service.fetch_candidates();session=await service.open_session()
        await service.record(session.session_id,Judgment('1','superLike','one'))
        assert not (await service.review_snapshot(session.session_id))['rows'][0]['work_meta']
        saved.append('1')
        assert (await service.review_snapshot(session.session_id))['rows'][0]['work_meta']=={'verified_managed_receipt':True}
        result=await asyncio.to_thread(service.recommendation_snapshot)
        assert result['superlike_ids']=={'1'} and result['superlike_order']==('1',)
        assert result['judged_ids']=={'1'} and len(result['works'])==25
        assert '1' not in {a.work_id for a in (await service.fetch_candidates()).artworks}
    finally:service.close()

async def test_tag_search_uses_original_mode_and_no_mock_fallback(tmp_path):
    reader=Reader();service=DiscoveryLearningService(tmp_path/'tags',reader)
    try:
        await service.fetch_candidates();session=await service.open_session()
        for i in range(1,21):await service.record(session.session_id,Judgment(str(i),'like',str(i)))
        await service.fetch_candidates()
        assert any(isinstance(call,tuple) and call[0]=='search' for call in reader.calls)
        assert all(c['source']!='mock' for c in (await asyncio.to_thread(service.recommendation_snapshot))['works'])
    finally:service.close()

async def test_database_failure_does_not_become_empty_judgment_snapshot(tmp_path,monkeypatch):
    service=DiscoveryLearningService(tmp_path/'failure',Reader())
    try:
        await service.fetch_candidates()
        def fail():raise RuntimeError('unavailable fixture DB')
        monkeypatch.setattr(service._db,'get_judged_work_ids',fail)
        with pytest.raises(RuntimeError):await service.open_session()
    finally:service.close()

async def test_hidden_candidates_and_saved_source_do_not_reappear(tmp_path):
    service=DiscoveryLearningService(tmp_path/'hidden',Reader())
    try:
        await service.fetch_candidates()
        await worker(service,lambda:service._db.add_hidden_author('99',display_name='author'))
        session=await service.open_session()
        assert not service.artworks
        assert not (await service.fetch_candidates()).artworks
    finally:service.close()
