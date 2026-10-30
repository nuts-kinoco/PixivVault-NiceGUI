"""Strict v1/v2 native store acceptance using only private synthetic data."""
import io,json,sqlite3
import pytest
from PIL import Image
from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads
from pixivvault_nicegui.managed_download_requests import ManagedDownloadRequests
from pixivvault_nicegui.job_service import JobService
from pixivvault_nicegui.fs_paths import native_io_path
from tools.check_managed_download_native import verify_storage
from tools.check_download_jobs_native import verify_native_jobs,WORK_PAGES
from test_native_download_jobs import completed_sandbox


@pytest.fixture
def completed_managed(tmp_path):
    root=tmp_path/'managed';payload=io.BytesIO();Image.new('RGB',(8,8)).save(payload,'PNG');png=payload.getvalue()
    record=RemoteBookmark('123','illustration','show','synthetic','456','synthetic author',(),'', '2026-10-27',1,0,0)
    class Client:
        def verify_identity(self):return '456'
        def get_artwork_original_pages(self,wid):return ('https://i.pximg.net/synthetic/one.png',)
        def get_image_bytes(self,url):return png
    writer=ManagedPixivDownloads(root,Client(),resolve_record=lambda wid:record)
    ManagedDownloadRequests(root).store('synthetic-operation',record)
    jobs=JobService(writer.run,writer.storage_path)
    try:
        job=jobs.enqueue('pixiv-illust','123','synthetic',operation_id='synthetic-operation')
        with jobs._condition:
            assert jobs._condition.wait_for(lambda:any(j.job_id==job.job_id and j.state in ('done','error') for j in jobs.snapshot()),20)
        assert jobs.snapshot()[0].state=='done'
    finally:jobs.close();writer.close()
    return root,job.job_id


@pytest.mark.parametrize('version',[1,2])
def test_native_managed_accepts_exact_owned_versions_without_mutation(completed_managed,version):
    root,jid=completed_managed;db=native_io_path(root/'jobs.sqlite3')
    if version==1:
        with sqlite3.connect(db) as c:c.execute('DROP TABLE job_history');c.execute('PRAGMA user_version=1')
    before=db.read_bytes();state=verify_storage(root)
    assert state['jobs']==state['receipts']==state['pages']==1 and db.read_bytes()==before


@pytest.mark.parametrize('bad',['version','table','column','missing','unbound','target','attempt-bool','nonterminal','extra-field','duplicate-key','fingerprint'])
def test_native_managed_rejects_malformed_or_unbound_history(completed_managed,bad):
    root,jid=completed_managed
    with sqlite3.connect(native_io_path(root/'jobs.sqlite3')) as c:
        if bad=='version':c.execute('PRAGMA user_version=3')
        elif bad=='table':c.execute('CREATE TABLE foreign_history (value TEXT)')
        elif bad=='column':c.execute('ALTER TABLE job_history ADD COLUMN foreign_column TEXT')
        elif bad=='missing':c.execute('DELETE FROM job_history')
        elif bad=='unbound':c.execute('UPDATE job_history SET job_id=?',('f'*32,))
        elif bad=='fingerprint':c.execute('UPDATE operations SET fingerprint=?',(json.dumps(['pixiv-illust','999','synthetic']),))
        else:
            raw=c.execute('SELECT payload FROM job_history WHERE job_id=?',(jid,)).fetchone()[0];value=json.loads(raw)
            if bad=='target':value['target_id']='999'
            elif bad=='attempt-bool':value['attempt']=True
            elif bad=='nonterminal':value['state']='running'
            elif bad=='extra-field':value['foreign']=True
            raw=json.dumps(value)
            if bad=='duplicate-key':raw='{"attempt":1,'+raw[1:]
            c.execute('UPDATE job_history SET payload=? WHERE job_id=?',(raw,jid))
    before=native_io_path(root/'jobs.sqlite3').read_bytes()
    with pytest.raises(ValueError):verify_storage(root)
    assert native_io_path(root/'jobs.sqlite3').read_bytes()==before


def test_demo_native_checker_accepts_v2_and_checks_history_binding(completed_sandbox):
    root=completed_sandbox;db=native_io_path(root/'jobs.sqlite3')
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE job_history (job_id TEXT PRIMARY KEY,payload TEXT NOT NULL)');c.execute('PRAGMA user_version=2')
        for jid,raw in c.execute('SELECT job_id,payload FROM jobs').fetchall():
            value=json.loads(raw)
            c.execute('INSERT INTO operations VALUES (?,?,?)',(jid,jid,json.dumps([value[k] for k in ('kind','target_id','title')])))
            c.execute('INSERT INTO job_history VALUES (?,?)',(jid,raw))
    assert verify_native_jobs(root,remaining_ids=frozenset(WORK_PAGES))['passed']
    with sqlite3.connect(db) as c:c.execute('DELETE FROM job_history')
    with pytest.raises((ValueError,AssertionError)):verify_native_jobs(root,remaining_ids=frozenset(WORK_PAGES))
