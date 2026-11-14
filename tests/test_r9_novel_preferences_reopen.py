"""R9: distinguish persisted preference writes from novel retry consumption."""
import hashlib
from uuid import uuid4
import pytest
from pixivvault_nicegui.managed_novel_jobs import PinnedNovelSource
from pixivvault_nicegui.services import ApplicationServices
from test_r9_novel_preferences_retry import fixture, GeneratedReader, terminal

@pytest.mark.asyncio
@pytest.mark.parametrize('selected,expected',[('epub','EPUB'),('txt','TXT'),('both','BOTH')])
async def test_saved_format_survives_service_reopen_and_controls_retry(tmp_path,selected,expected):
    first,reader,db=fixture(tmp_path)
    signature=hashlib.sha256(db.read_bytes()).hexdigest()
    try:
        snapshot=first.preferences.snapshot()
        first.preferences.update({'novel_save_format':selected},expected_revision=snapshot.revision)
        assert first.preferences.snapshot().get_setting('novel_save_format')==selected
    finally:
        first.close()
    reader=GeneratedReader()
    service=ApplicationServices(live_client=reader,
        pixiv_download_root=tmp_path/'images',failed_queue_readonly_db=db,
        novel_fixture_root=tmp_path/'novels',
        novel_fixture_source=PinnedNovelSource(reader,{}),
        preferences_root=tmp_path/'prefs')
    try:
        assert service.preferences.snapshot().get_setting('novel_save_format')==selected
        assert reader.calls==[]
        failure=(await service.read_failed_records())[0]
        await service.wait_legacy_retry_capacity(failure,is_current=lambda:True)
        intent=await service.lookup_legacy_failed_original(failure,owner='d'*32)
        assert intent.output_format==expected
        job=service.enqueue_legacy_failed_original(failure,intent,uuid4().hex,owner='d'*32)
        finished=await terminal(service,job.job_id)
        assert finished.state=='done'
        saved=service.novel_jobs.current('1001',expected)
        assert saved is not None and saved.relative_receipt==finished.result
        assert saved.output_format==expected
        assert reader.calls==[('1001','23')]
        assert hashlib.sha256(db.read_bytes()).hexdigest()==signature
    finally:
        service.close()
