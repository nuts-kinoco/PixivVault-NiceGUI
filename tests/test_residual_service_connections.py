import asyncio
from pathlib import Path
from types import SimpleNamespace
import pytest
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.learning import Judgment
from pixivvault_nicegui.request_policy import RequestAdmission
from pixivvault_nicegui.pixiv_read_client import PublicImageReadClient, DestinationRejected, ReadDeadlineExceeded
from test_discovery_learning import Reader


@pytest.mark.asyncio
async def test_discovery_recommendations_preferences_are_product_services_and_restart(tmp_path):
    reader=Reader()
    kwargs=dict(live_client=reader,discovery_root=tmp_path/'discovery',
        discovery_following=(),preferences_root=tmp_path/'preferences',
        recommendation_preferences_root=tmp_path/'recommend',image_cache_root=tmp_path/'cache')
    reader.close=lambda:None
    s=ApplicationServices(1,**kwargs)
    try:
        assert reader.calls==[]
        assert s.preferences.get_setting('novel_save_format')=='epub'
        before=s.preferences.snapshot()
        s.preferences.update({'novel_save_format':'both','pixiv_shared_image_cache_size_mb':'512'},expected_revision=before.revision)
        s.apply_preferences()
        batch=await s.discovery.fetch_candidates()
        assert batch.artworks
        session=await s.discovery.open_session()
        await s.discovery.record(session.session_id,Judgment('1','superLike','super1'))
        snapshot=await asyncio.to_thread(s.recommendations.refresh)
        assert snapshot
        assert s.personal_ranking_context()['hated_ids']==frozenset()
        count=len(reader.calls)
    finally:s.close()
    s=ApplicationServices(1,**kwargs)
    try:
        assert len(reader.calls)==count
        assert s.preferences.get_setting('novel_save_format')=='both'
        assert (await s.discovery.open_session()).judged_ids=={'1'}
        await asyncio.to_thread(s.recommendations.refresh)
        assert len(reader.calls)==count
    finally:s.close()


def test_shared_request_interval_retains_deadline_and_never_retries():
    now=[0.0];sleeps=[]
    def sleep(value):sleeps.append(value);now[0]+=value
    admission=RequestAdmission(lambda:0.6,clock=lambda:now[0],sleep=sleep)
    admission(10);admission(10)
    assert sleeps==[0.6]
    with pytest.raises(ReadDeadlineExceeded):admission(0.8)
    assert sleeps==[0.6]


def test_public_image_transport_has_no_auth_or_api_capability():
    calls=[]
    def transport(request,*args):calls.append(request);return b'fixture bytes'
    client=PublicImageReadClient(transport=transport)
    try:
        assert client.get_image_bytes('https://i.pximg.net/fixture.png')==b'fixture bytes'
        assert calls[0].get_method()=='GET' and not calls[0].has_header('Cookie')
        with pytest.raises(DestinationRejected):client.get_image_bytes('https://evil.example/fixture.png')
        with pytest.raises(DestinationRejected):client._get('artwork','https://www.pixiv.net/ajax/illust/1',deadline=99)
        assert len(calls)==1
    finally:client.close()



def test_discovery_mode_drives_recommendation_and_overlay():
    from pixivvault_nicegui.recommendation_state import (
        DiscoveryRecommendationSource,CombinedRecommendationSource,RecommendationSnapshot,RecommendationError)
    values={'works':[],'superlike_ids':set(),'judged_ids':set(),'search_mode':'r18'}
    discovery=SimpleNamespace(recommendation_snapshot=lambda:values)
    current=DiscoveryRecommendationSource(discovery)
    legacy=SimpleNamespace(load=lambda:RecommendationSnapshot(search_mode='safe'))
    assert current.load().search_mode=='r18'
    assert CombinedRecommendationSource(legacy,current).load().search_mode=='r18'
    values['search_mode']='bad'
    with pytest.raises(RecommendationError):current.load()



def test_sidecars_cannot_nest_under_readonly_library_before_any_creation(tmp_path):
    parent=tmp_path/'legacy'
    parent.mkdir()
    with pytest.raises(ValueError):
        ApplicationServices(1,library_readonly_db=parent/'old.sqlite3',library_image_root=tmp_path/'images',
            preferences_root=parent/'preferences')
    assert not (parent/'preferences').exists()



def test_managed_duplicate_ui_reads_only_explicit_frozen_legacy_file_ledger(tmp_path):
    """X02 unresolved work mode uses the existing frozen old DB, not guesses."""
    import hashlib
    import sqlite3

    source_dir=tmp_path/'legacy-copy';source_dir.mkdir()
    frozen=source_dir/'pixivvault-copy.sqlite3'
    with sqlite3.connect(frozen) as db:
        db.executescript('''
            CREATE TABLE failed_queue(work_id TEXT,user_id TEXT,title TEXT,
                work_type TEXT,error_reason TEXT,failed_at TEXT,retry_count INTEGER);
            CREATE TABLE works(work_id TEXT PRIMARY KEY,title TEXT,user_id TEXT);
            CREATE TABLE following_users(user_id TEXT PRIMARY KEY,name TEXT);
            CREATE TABLE file_ledger(work_id TEXT,page INTEGER,status TEXT,
                store_path TEXT,entry_name TEXT,reason TEXT,ambiguous_group TEXT,resolved_by TEXT);
            INSERT INTO works VALUES('901','Ambiguous Title','90'),('902','Ambiguous Title','90');
            INSERT INTO following_users VALUES('90','Original author');
            INSERT INTO file_ledger VALUES
                ('901',1,'unsure','folder','01.png','same title','group-90',NULL),
                ('902',1,'unsure','folder','01.png','same title','group-90',NULL);
        ''')
    before=hashlib.sha256(frozen.read_bytes()).hexdigest()
    reader=SimpleNamespace(close=lambda:None)
    services=ApplicationServices(1,live_client=reader,pixiv_download_root=tmp_path/'managed',
                                 failed_queue_readonly_db=frozen)
    try:
        source=services.duplicate_inspection
        assert source is not None and source.undecidable_available
        groups=source.undecidable_groups()
        assert len(groups)==1 and groups[0]['ambiguous_group']=='group-90'
        assert [c['work_id'] for c in groups[0]['candidates']]==['901','902']
        assert source.scan().groups==()  # No managed saves; old rows are not near matches.
    finally:
        services.close()
    assert hashlib.sha256(frozen.read_bytes()).hexdigest()==before
    assert not any((source_dir/(frozen.name+suffix)).exists()
                   for suffix in ('-wal','-shm','-journal'))


@pytest.mark.asyncio
async def test_personal_screen_can_open_before_recommendations_have_been_visited(tmp_path):
    from nicegui import Client,ui
    from pixivvault_nicegui.ui.personal_ranking_view import PersonalRankingView
    reader=Reader();reader.close=lambda:None
    s=ApplicationServices(1,live_client=reader,discovery_root=tmp_path/'discovery',
        discovery_following=(),recommendation_preferences_root=tmp_path/'recommend',
        external_vault_root=tmp_path/'vault',external_vault_transport=SimpleNamespace())
    client=Client(ui.page('/personal-first-fixture'))
    try:
        with client,ui.card():view=PersonalRankingView(s)
        assert reader.calls==[]
        assert s.personal_ranking_context()['hidden_work_ids']==set()
        view.close()
    finally:client.delete();s.close()



def test_notification_backend_is_explicit_and_setting_gated(tmp_path):
    sent=[]
    backend=SimpleNamespace(send=lambda title,body:sent.append((title,body)))
    s=ApplicationServices(1,preferences_root=tmp_path/'preferences',notification_backend=backend)
    try:
        assert sent==[] and 'enable_notifications' in s.preference_consumers()
        result=s.notifications.notify('fixture','saved',event_id='saved-operation').result(timeout=5)
        assert result.status=='sent' and sent==[('fixture','saved')]
        s.preferences.update({'enable_notifications':'0'},expected_revision=s.preferences.snapshot().revision)
        assert s.notifications.notify('fixture','suppressed') is None
    finally:s.close()



def test_discovery_first_image_resolves_unknown_multi_page_count_without_rejection():
    from io import BytesIO
    from PIL import Image
    from pixivvault_nicegui.remote_media import RemoteBookmarkImages
    from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
    image=BytesIO();Image.new('RGB',(3,4),'blue').save(image,'PNG');calls=[]
    def pages(wid):calls.append(('pages',wid));return ('https://i.pximg.net/one.png','https://i.pximg.net/two.png')
    reader=SimpleNamespace(get_artwork_pages=pages,get_image_bytes=lambda url:(calls.append(('image',url)) or image.getvalue()))
    record=RemoteBookmark('123','illustration','show','manga','91','author',(),'https://i.pximg.net/thumb.png','',1,None,0,page_count_known=False)
    catalog=RemoteBookmarkImages(reader,(record,))
    try:
        assert catalog.get('123',0,'view').data
        assert catalog.works['123'].page_count==2 and catalog.works['123'].page_count_known
        assert catalog.get('123',1,'view').data
        assert calls==[('pages','123'),('image','https://i.pximg.net/one.png'),('image','https://i.pximg.net/two.png')]
    finally:catalog.close()
