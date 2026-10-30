import asyncio
import hashlib
import json
import sqlite3
import pytest
from pixivvault_nicegui.discovery_learning import DiscoveryLearningService
from pixivvault_nicegui.legacy.database import Database
from pixivvault_nicegui.learning import Judgment
from test_discovery_learning import Reader,worker

def legacy_fixture(root):
    root.mkdir();path=root/'legacy.sqlite3'
    db=Database(path,sandbox_root=root)
    for i in range(1,21):
        db.record_discovery_judgment(str(i),'superLike' if i==1 else 'like',tags=['sky'],author_id=None if i==20 else '99',source='ranking',display_name='fixture author',thumbnail_url='https://i.pximg.net/img/1.jpg' if i==1 else None)
    db.conn.execute("UPDATE discovery_preference SET seed_saved=7,seed_bookmarked=3")
    db.conn.execute("UPDATE discovery_author_preference SET seed_bookmarked=4")
    db.set_setting('discovery_search_mode','safe')
    db.set_setting('cookie','fixture-only-not-for-import')
    db.set_setting('discoveryXsecret','fixture-lookalike-not-for-import')
    db.add_hidden_author('100',display_name='hidden fixture')
    db.close()
    return path

async def test_seed_exact_counts_history_metadata_and_no_source_writes(tmp_path):
    source=legacy_fixture(tmp_path/'original-fixture')
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    service=DiscoveryLearningService(tmp_path/'managed',Reader(),seed_readonly_db=source)
    try:
        session=await service.open_session()
        assert session.judged_ids=={str(i) for i in range(1,21)}
        rows=(await service.review_snapshot(session.session_id))['rows']
        assert len(rows)==20 and any(r['thumbnail_url'] is None for r in rows)
        assert service._metadata['20']['author_id'] is None
        assert len(await asyncio.to_thread(service.candidate_records))==1
        trend=await service.trend_snapshot();assert trend['ready'] and trend['judged']==20
        counts=await worker(service,lambda:service._db.get_discovery_preferences()['sky'])
        assert counts['like_count']==19 and counts['superlike_count']==1
        assert counts['seed_saved']==7 and counts['seed_bookmarked']==3
        assert await worker(service,lambda:service._db.get_setting('cookie')) is None
        assert await worker(service,lambda:service._db.get_setting('discoveryXsecret')) is None
        snapshot=await asyncio.to_thread(service.recommendation_snapshot)
        assert snapshot['superlike_order']==('1',) and snapshot['author_seed_bookmarked']=={'99':4}
        assert await worker(service,lambda:service._db.get_hidden_author_ids())=={'100'}
    finally:service.close()
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before
    # Reopening the seed does not replay imported counters, even if the supplied
    # immutable snapshot has since been replaced with another valid revision.
    db=Database(source,sandbox_root=source.parent)
    db.record_discovery_judgment('21','hate',tags=['sky'],author_id='99');db.close()
    service=DiscoveryLearningService(tmp_path/'managed',Reader(),seed_readonly_db=source)
    try:
        assert (await service.open_session()).judged_ids=={str(i) for i in range(1,21)}
        assert (await service.trend_snapshot())['judged']==20
        marker=await worker(service,lambda:json.loads(service._db.conn.execute('SELECT payload FROM managed_discovery_seed').fetchone()[0]))
        assert marker['source_sha256']==before
    finally:service.close()

async def test_seed_rollback_missing_schema_and_used_target_rejection(tmp_path):
    source=tmp_path/'incompatible.sqlite3';sqlite3.connect(source).close()
    service=DiscoveryLearningService(tmp_path/'bad-seed',Reader(),seed_readonly_db=source)
    try:
        with pytest.raises(ValueError,match='table unavailable'):await service.open_session()
        assert service._db.conn.execute('SELECT COUNT(*) FROM discovery_swipe_history').fetchone()[0]==0
        assert service._db.conn.execute('SELECT COUNT(*) FROM managed_discovery_seed').fetchone()[0]==0
    finally:service.close()
    root=tmp_path/'used';service=DiscoveryLearningService(root,Reader())
    await service.fetch_candidates();service.close()
    source=legacy_fixture(tmp_path/'good-seed')
    service=DiscoveryLearningService(root,Reader(),seed_readonly_db=source)
    try:
        with pytest.raises(ValueError,match='empty'):await service.open_session()
    finally:service.close()

async def test_seed_wal_snapshot_rejected_without_import(tmp_path):
    source=legacy_fixture(tmp_path/'wal-fixture')
    (source.parent/(source.name+'-wal')).write_bytes(b'fixture outstanding WAL')
    service=DiscoveryLearningService(tmp_path/'wal-rejected',Reader(),seed_readonly_db=source)
    try:
        with pytest.raises(ValueError,match='closed and standalone'):await service.open_session()
        assert service._db.conn.execute('SELECT COUNT(*) FROM discovery_swipe_history').fetchone()[0]==0
    finally:service.close()

async def test_nullable_legacy_history_resolves_explicitly_before_undo(tmp_path):
    class DetailReader(Reader):
        def get_discovery_work(self,wid):
            self.calls.append(('detail',wid))
            return {'id':wid,'title':'resolved','user_id':'99','user_name':'fixture author','thumbnail_url':f'https://i.pximg.net/img/{wid}.jpg','tags':['sky']}
    source=legacy_fixture(tmp_path/'detail-fixture');reader=DetailReader()
    service=DiscoveryLearningService(tmp_path/'detail-managed',reader,seed_readonly_db=source)
    try:
        session=await service.open_session();assert reader.calls==[]
        row=next(r for r in (await service.review_snapshot(session.session_id))['rows'] if r['work_id']=='2')
        await service.resolve_history_candidate('2')
        assert reader.calls==[('detail','2')]
        await service._wait(service.submit_review_undo(session.session_id,'2',row['revision'],'undo-legacy'))
        await service.open_session()
        assert '2' in {a.work_id for a in service.artworks}
        assert (await service.trend_snapshot())['judged']==19
    finally:service.close()

async def test_mid_import_failure_rolls_back_counters_history_and_seed_marker(tmp_path):
    root=tmp_path/'atomic';service=DiscoveryLearningService(root,Reader())
    await service.open_session();service.close()
    conn=sqlite3.connect(root/'learning.sqlite3')
    conn.execute("CREATE TRIGGER fixture_abort_seed BEFORE INSERT ON discovery_author_preference BEGIN SELECT RAISE(ABORT,'fixture import failure'); END")
    conn.close()
    source=legacy_fixture(tmp_path/'atomic-source')
    service=DiscoveryLearningService(root,Reader(),seed_readonly_db=source)
    try:
        with pytest.raises(sqlite3.IntegrityError,match='fixture import failure'):await service.open_session()
        for table in ('discovery_preference','discovery_author_preference','discovery_swipe_history','managed_discovery_seed','managed_discovery_legacy_history'):
            assert service._db.conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]==0
    finally:service.close()
