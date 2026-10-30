"""Every binding is checked; good aliases cannot hide forged extra rows."""
import hashlib,json,sqlite3
import pytest
from pixivvault_nicegui.fs_paths import native_io_path
from pixivvault_nicegui.managed_download_requests import ManagedDownloadRequests
from tools.check_download_jobs_native import verify_native_jobs,WORK_PAGES
from tools.check_managed_download_native import verify_storage
from test_native_download_jobs import completed_sandbox
from test_native_job_history_compat import completed_managed


def demo_bindings(root,version):
    with sqlite3.connect(native_io_path(root/'jobs.sqlite3')) as db:
        rows=db.execute('SELECT job_id,payload FROM jobs ORDER BY ordinal').fetchall()
        if version==2:
            db.execute('CREATE TABLE job_history (job_id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
            db.execute('PRAGMA user_version=2')
        result=[]
        for index,(jid,raw) in enumerate(rows):
            job=json.loads(raw);fingerprint=json.dumps([job[k] for k in ('kind','target_id','title')])
            operation='original operation '+str(index)
            db.execute('INSERT INTO operations VALUES (?,?,?)',(operation,jid,fingerprint))
            if version==2:db.execute('INSERT INTO job_history VALUES (?,?)',(jid,raw))
            result.append((operation,jid,fingerprint))
        return result


def malformed_row(original,bad):
    operation,jid,raw=original;operation='additional valid operation'
    if bad=='wrong-fingerprint':
        value=json.loads(raw);value[2]='forged';raw=json.dumps(value)
    elif bad=='orphan':jid='0'*32
    elif bad=='malformed-fingerprint':raw='{broken'
    elif bad=='control-operation':operation='invalid\noperation'
    elif bad=='oversize-operation':operation='x'*129
    elif bad=='empty-operation':operation=''
    return operation,jid,raw


@pytest.mark.parametrize('version',[1,2])
@pytest.mark.parametrize('bad',['wrong-fingerprint','orphan','malformed-fingerprint','control-operation','oversize-operation','empty-operation'])
def test_demo_rejects_each_extra_bad_binding_beside_good_binding(completed_sandbox,version,bad):
    root=completed_sandbox;originals=demo_bindings(root,version);row=malformed_row(originals[0],bad);dbpath=native_io_path(root/'jobs.sqlite3')
    with sqlite3.connect(dbpath) as db:
        db.execute('INSERT INTO operations VALUES (?,?,?)',row)
        assert all(db.execute('SELECT job_id,fingerprint FROM operations WHERE operation_id=?',(op,)).fetchone()==(jid,fp) for op,jid,fp in originals)
    before=dbpath.read_bytes()
    with pytest.raises((ValueError,AssertionError)):verify_native_jobs(root,remaining_ids=frozenset(WORK_PAGES))
    assert dbpath.read_bytes()==before


@pytest.mark.parametrize('version',[1,2])
def test_demo_accepts_legitimate_non_uuid_aliases(completed_sandbox,version):
    root=completed_sandbox;originals=demo_bindings(root,version)
    with sqlite3.connect(native_io_path(root/'jobs.sqlite3')) as db:
        op,jid,raw=originals[0]
        db.execute('INSERT INTO operations VALUES (?,?,?)',('別の有効な受付 名称',jid,json.dumps(json.loads(raw),ensure_ascii=False,separators=(',',':'))))
    assert verify_native_jobs(root,remaining_ids=frozenset(WORK_PAGES))['passed']


@pytest.mark.parametrize('forged',[False,True])
def test_demo_v2_deleted_history_keeps_binding_and_checks_every_alias(completed_sandbox,forged):
    root=completed_sandbox;originals=demo_bindings(root,2);op,jid,raw=originals[0]
    with sqlite3.connect(native_io_path(root/'jobs.sqlite3')) as db:
        target=json.loads(db.execute('SELECT payload FROM jobs WHERE job_id=?',(jid,)).fetchone()[0])['target_id']
        db.execute('DELETE FROM jobs WHERE job_id=?',(jid,))
        if forged:db.execute('INSERT INTO operations VALUES (?,?,?)',malformed_row(originals[0],'wrong-fingerprint'))
    remaining=frozenset(WORK_PAGES)-{target}
    if forged:
        with pytest.raises((ValueError,AssertionError)):verify_native_jobs(root,remaining_ids=remaining)
    else:assert verify_native_jobs(root,remaining_ids=remaining)['passed']


def test_v1_unverifiable_retired_binding_is_not_inferred_or_migrated(completed_sandbox):
    root=completed_sandbox;originals=demo_bindings(root,1);op,jid,raw=originals[0];path=native_io_path(root/'jobs.sqlite3')
    with sqlite3.connect(path) as db:
        target=json.loads(db.execute('SELECT payload FROM jobs WHERE job_id=?',(jid,)).fetchone()[0])['target_id']
        db.execute('DELETE FROM jobs WHERE job_id=?',(jid,))
    before=path.read_bytes()
    with pytest.raises(ValueError,match='unverifiable'):verify_native_jobs(root,remaining_ids=frozenset(WORK_PAGES)-{target})
    assert path.read_bytes()==before
    with sqlite3.connect(path) as db:assert db.execute('PRAGMA user_version').fetchone()[0]==1


def managed_version(root,version):
    path=native_io_path(root/'jobs.sqlite3')
    with sqlite3.connect(path) as db:
        if version==1:db.execute('DROP TABLE job_history');db.execute('PRAGMA user_version=1')
        original=db.execute('SELECT operation_id,job_id,fingerprint FROM operations').fetchone()
    return original


def copy_accepted_request(root,original,alias):
    # Deliberately make the alias input present, so rejection cannot pass merely
    # because a fabricated operation has no corresponding request file.
    folder=native_io_path(root/'requests')
    original_path=folder/(hashlib.sha256(original.encode()).hexdigest()+'.json')
    alias_path=folder/(hashlib.sha256(alias.encode()).hexdigest()+'.json')
    alias_path.write_bytes(original_path.read_bytes())


@pytest.mark.parametrize('version',[1,2])
@pytest.mark.parametrize('bad',['wrong-fingerprint','orphan','malformed-fingerprint','control-operation','oversize-operation','empty-operation'])
def test_managed_rejects_each_extra_bad_binding_with_valid_alias_input(completed_managed,version,bad):
    root,_=completed_managed;original=managed_version(root,version);row=malformed_row(original,bad)
    copy_accepted_request(root,original[0],row[0]);path=native_io_path(root/'jobs.sqlite3')
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO operations VALUES (?,?,?)',row)
        assert db.execute('SELECT job_id,fingerprint FROM operations WHERE operation_id=?',(original[0],)).fetchone()==original[1:]
    before=path.read_bytes()
    with pytest.raises(ValueError):verify_storage(root)
    assert path.read_bytes()==before


@pytest.mark.parametrize('version',[1,2])
def test_managed_accepts_legitimate_non_uuid_aliases(completed_managed,version):
    root,_=completed_managed;original=managed_version(root,version);alias='別の有効な受付 名称'
    store=ManagedDownloadRequests(root);store.store(alias,store.load(original[0]))
    with sqlite3.connect(native_io_path(root/'jobs.sqlite3')) as db:
        db.execute('INSERT INTO operations VALUES (?,?,?)',(alias,original[1],json.dumps(json.loads(original[2]),separators=(',',':'))))
    assert verify_storage(root)['jobs']==1
