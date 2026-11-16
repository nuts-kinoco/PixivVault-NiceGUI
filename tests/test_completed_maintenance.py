import hashlib,json,threading
from pathlib import Path
from types import SimpleNamespace
import pytest
from required_network_guard import no_network
from pixivvault_nicegui.external_vault import ExternalVaultService
from pixivvault_nicegui.completed_maintenance import MaintenancePolicy,collect_zip_duplicates,remove_verified,BackgroundCollection
from pixivvault_nicegui.legacy_physical_quarantine import PhysicalLegacyQuarantine
from test_z02_policy import PolicyCrypto

def vault(tmp_path):
    root=tmp_path/'v';policy=MaintenancePolicy(synthetic_roots=[root])
    v=ExternalVaultService(root,crypto=PolicyCrypto(),maintenance=policy);v.initialize('generated-password','17','generated-secret')
    return v

def normal(v):
    base=v._pointer();source=(v.root/'generations'/base/'vault.enc').read_bytes()
    data=v._snapshot()[1];result=v.crypto.encrypt(data,v._key)
    proof=v.crypto.encrypt({'purpose':'oauth-known-result-v1','base':base,'uid':17,
       'source_sha256':hashlib.sha256(source).hexdigest(),'result_sha256':hashlib.sha256(result).hexdigest()},v._key)
    directory=v.root/'known-results'/base;directory.mkdir(parents=True)
    (directory/'result.enc').write_bytes(result);(directory/'proof.enc').write_bytes(proof)
    digest=hashlib.sha256(proof).hexdigest()
    v._known_result_retention.reserve_normal(base,digest,v._key,v.crypto)
    v._known_result_retention.mark_success(base,digest,v._key,v.crypto)
    v._known_result_retention.confirm_generation(base,v._key,v.crypto)
    return base,directory

def test_rolling_insurance_continues_beyond_sixteen(tmp_path):
    v=vault(tmp_path)
    try:
        for i in range(20):
            assert v._can_record_normal_result()
            base,d=normal(v)
            assert v._collect_completed_records()==()
            v.update_config(secret_key='generated-'+str(i))
            assert base in v._collect_completed_records()
            assert not (d/'result.enc').exists()
        assert v._can_record_normal_result()
    finally:v.close()

def test_pending_preserves_complete_and_incomplete(tmp_path):
    v=vault(tmp_path)
    try:
        base,d=normal(v)
        pending=v.root/'known-results'/('f'*32);pending.mkdir();(pending/'result.enc').write_bytes(b'generated unknown')
        epoch,data=v._snapshot();data['slots']['17']['oauth_send_pending']=True;v._publish(epoch,data)
        assert not v._collect_completed_records()
        assert (d/'result.enc').exists() and (pending/'result.enc').read_bytes()==b'generated unknown'
    finally:v.close()

def test_cleanup_failure_and_restart_preserve_then_retry(tmp_path,monkeypatch):
    import pixivvault_nicegui.completed_maintenance as m
    v=vault(tmp_path)
    try:
        base,d=normal(v);v.update_config(uid_text='17\n18')
        with monkeypatch.context() as p:
            p.setattr(m,'remove_verified',lambda *a,**k:(_ for _ in ()).throw(PermissionError('generated')))
            assert v._collect_completed_records()==()
            assert (d/'normal-complete.enc').exists()
        root=v.root;v.close();v=ExternalVaultService(root,crypto=PolicyCrypto(),maintenance=MaintenancePolicy(synthetic_roots=[root]))
        v.unlock('generated-password')
        assert base in v._collect_completed_records()
    finally:v.close()

def physical(tmp_path):
    managed=tmp_path/'managed';managed.mkdir();images=tmp_path/'images';images.mkdir()
    store=SimpleNamespace(root=managed/'legacy',owner=SimpleNamespace(root=managed),catalog=SimpleNamespace(root=images),
        identity={'source':'a'*64},_lock=threading.RLock())
    store.root.mkdir();store._path=lambda s:store.root/s
    store.state=lambda:state
    p=PhysicalLegacyQuarantine(store);p.root.mkdir(parents=True);p.journals.mkdir(parents=True)
    (p.root/'IDENTITY.json').write_text(json.dumps(p.identity))
    p.recover=lambda:None
    state={'operations':{}}
    for i in range(4):
        uid=f'{i:032x}';unit=p._unit(uid);unit.mkdir()
        stage=store.root/'operations'/uid;stage.mkdir(parents=True)
        for name,raw in [('before',b'generated-before'),('retained',b'generated-before'),('after',b'generated-after')]:
            (unit/name).write_bytes(raw)
        (stage/'active.zip').write_bytes(b'generated-after')
        (stage/'selected.image').write_bytes(b'generated-selected')
        intent={'identity':p.identity,'unit':uid,'kind':'zip','source':'generated.zip',
          'before_sha256':hashlib.sha256(b'generated-before').hexdigest(),'after_sha256':hashlib.sha256(b'generated-after').hexdigest()}
        directory=p.journals/uid;directory.mkdir()
        raw=json.dumps(intent).encode();(directory/'intent.json').write_bytes(raw)
        (directory/'settled.json').write_text(json.dumps({'intent':hashlib.sha256(raw).hexdigest(),'status':'committed'}))
        state['operations'][uid]=['generated']
    return p

def test_age_and_two_generations_keep_recovery_and_visible_copies(tmp_path):
    p=physical(tmp_path);now=[1000]
    policy=MaintenancePolicy(synthetic_roots=[p.store.owner.root],clock=lambda:now[0])
    assert collect_zip_duplicates(p,policy)==()
    now[0]+=6*86400;assert collect_zip_duplicates(p,policy)==()
    now[0]+=2*86400
    removed=collect_zip_duplicates(p,policy)
    assert len(removed)==4
    for unit,name in removed:
        assert not (p._unit(unit)/name).exists()
        assert (p._unit(unit)/'before').read_bytes()==b'generated-before'
        assert (p.store.root/'operations'/unit/'active.zip').read_bytes()==b'generated-after'

def test_failed_unsettled_and_blocked_sources_are_kept(tmp_path):
    p=physical(tmp_path);now=[1000];policy=MaintenancePolicy(synthetic_roots=[p.store.owner.root],clock=lambda:now[0])
    collect_zip_duplicates(p,policy);now[0]+=8*86400
    p.blocked_sources={'generated.zip'}
    assert not collect_zip_duplicates(p,policy)
    assert all((p._unit(f'{i:032x}')/'before').exists() and (p._unit(f'{i:032x}')/'retained').exists() for i in range(4))

def test_wrong_digest_and_cancel_never_delete(tmp_path):
    p=tmp_path/'generated';p.write_bytes(b'generated')
    with pytest.raises(OSError):remove_verified(p,'0'*64)
    with pytest.raises(OSError):remove_verified(p,hashlib.sha256(p.read_bytes()).hexdigest(),cancelled=lambda:True)
    assert p.read_bytes()==b'generated'

def test_default_and_other_root_cannot_collect(tmp_path):
    v=vault(tmp_path)
    try:
        base,d=normal(v);v.update_config(uid_text='17\n18')
        v._maintenance_policy=None
        assert not v._collect_completed_records() and (d/'result.enc').exists()
        v._maintenance_policy=MaintenancePolicy(synthetic_roots=[tmp_path/'other'])
        assert not v._collect_completed_records() and (d/'result.enc').exists()
    finally:v.close()

def test_full_budget_releases_only_completed_after_publication(tmp_path):
    v=vault(tmp_path)
    try:
        for i in range(16):
            normal(v);v.update_config(secret_key='generated-'+str(i))
        assert not v._known_result_retention.can_record(v._key,v.crypto)
        assert v._can_record_normal_result()
        base,d=normal(v)
        assert (d/'result.enc').exists()
    finally:v.close()

def test_reserved_and_unmarked_failure_files_survive(tmp_path):
    v=vault(tmp_path)
    try:
        parent=v.root/'known-results'/('e'*32);parent.mkdir(parents=True)
        (parent/'proof.enc').write_bytes(b'generated partial')
        v._known_result_retention.reserve_normal('e'*32,'d'*64,v._key,v.crypto)
        v.update_config(uid_text='17\n18')
        assert not v._collect_completed_records()
        assert (parent/'proof.enc').read_bytes()==b'generated partial'
    finally:v.close()

@pytest.mark.parametrize('mode',['missing','rolled-back','mismatch'])
def test_noncommitted_backup_is_never_collected(tmp_path,mode):
    p=physical(tmp_path);now=[1000]
    policy=MaintenancePolicy(synthetic_roots=[p.store.owner.root],clock=lambda:now[0])
    collect_zip_duplicates(p,policy)
    first=sorted(p.journals.glob('*/intent.json'))[0]
    marker=first.parent/'settled.json'
    if mode=='missing':marker.unlink()
    else:
        data=json.loads(marker.read_text())
        if mode=='rolled-back':data['status']='rolled-back'
        else:data['intent']='0'*64
        marker.write_text(json.dumps(data))
    now[0]+=8*86400;collect_zip_duplicates(p,policy)
    unit=p._unit(first.parent.name)
    assert (unit/'before').exists() and (unit/'retained').exists() and (unit/'after').exists()

def test_background_worker_runs_off_caller_and_stops():
    done=threading.Event();threads=[]
    worker=BackgroundCollection(lambda stop:(threads.append(threading.get_ident()),done.set()),interval=.05)
    try:
        assert done.wait(2)
        assert threads[0]!=threading.get_ident()
    finally:worker.close()
    assert not worker.thread.is_alive()

def test_maintenance_error_does_not_block_primary_account_publication(tmp_path,monkeypatch):
    from test_external_vault import FixtureTransport
    v=vault(tmp_path);v.transport=FixtureTransport()
    try:
        monkeypatch.setattr(v._known_result_retention,'collect_completed',lambda *a:(_ for _ in ()).throw(OSError('generated index read failure')))
        v.refresh()
        assert v._snapshot()[1]['slots']['17']['access_token']
    finally:v.close()

def test_survivor_is_pinned_through_deletion_window(tmp_path):
    from pixivvault_nicegui.completed_maintenance import verified_survivor
    p=tmp_path/'generated-survivor';p.write_bytes(b'generated')
    with verified_survivor(p,hashlib.sha256(p.read_bytes()).hexdigest()):
        with pytest.raises(OSError):p.write_bytes(b'changed')
        with pytest.raises(OSError):p.rename(tmp_path/'other')
    assert p.read_bytes()==b'generated'

def test_foreign_physical_identity_is_not_collected(tmp_path):
    p=physical(tmp_path);now=[1000];policy=MaintenancePolicy(synthetic_roots=[p.store.owner.root],clock=lambda:now[0])
    collect_zip_duplicates(p,policy);now[0]+=8*86400
    (p.root/'IDENTITY.json').write_text('{}')
    assert collect_zip_duplicates(p,policy)==()
    assert all((p._unit(f'{i:032x}')/'retained').exists() for i in range(4))

def test_refresh_callbacks_keep_insurance_after_twenty(tmp_path):
    from contextlib import contextmanager
    from test_external_vault import FixtureTransport
    class KnownTransport(FixtureTransport):
        @contextmanager
        def oauth_result_guard(self, callback, **kwargs):
            self.result_callback=callback
            try:yield
            finally:self.result_callback=None
        def fetch_account(self, uid, secret, slot, now):
            self._oauth_session.before_send()
            result=super().fetch_account(uid,secret,slot,now)
            self.result_callback(result)
            return result
    v=vault(tmp_path);t=KnownTransport();v.transport=t;now=[1000];v.clock=lambda:now[0]
    observed=[]
    original=v._store_known_result
    def observe(epoch,record):
        observed.append(record[0])
        return original(epoch,record)
    v._store_known_result=observe
    try:
        for i in range(20):
            now[0]+=4000
            v.refresh(17)
            assert not v.status()['oauth_review_required']
        assert len(observed)==20 and len(t.calls)==20
        _,entries=v._known_result_retention._read(v.root,v._key,v.crypto)
        assert 0<len(entries)<16 and all(e[2]=='normal' for e in entries)
        assert all((v.root/'known-results'/e[0]/'normal-complete.enc').exists() for e in entries)
        assert (v.root/'known-results'/observed[-1]/'result.enc').exists()
        assert not (v.root/'known-results'/observed[0]/'result.enc').exists()
    finally:v.close()

def test_real_zip_generations_remain_readable_after_background_collection(tmp_path):
    from zipfile import ZipFile,ZIP_STORED
    from uuid import uuid4
    from test_required_legacy_duplicates import png,select
    from pixivvault_nicegui.models import Artwork
    from pixivvault_nicegui.library_models import LibraryWork,LibrarySnapshot
    from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads
    from pixivvault_nicegui.legacy_quarantine import LegacyQuarantine
    from pixivvault_nicegui.duplicate_inspection import ManagedDuplicateInspection
    original=tmp_path/'generated-images';original.mkdir()
    archive=original/'Generated(92).zip'
    with ZipFile(archive,'w',ZIP_STORED) as z:
        for i in range(1,7):z.writestr(f'Generated({i}).png',png())
    snapshot=LibrarySnapshot(tuple(LibraryWork(Artwork(str(i),'Generated','Generated'), '92') for i in range(1,7)))
    owner=ManagedPixivDownloads(tmp_path/'generated-managed',object(),resolve_record=lambda _:None)
    now=[1000];policy=MaintenancePolicy(synthetic_roots=[owner.root],clock=lambda:now[0])
    legacy=LegacyQuarantine(original,snapshot,owner=owner,maintenance=policy)
    source=ManagedDuplicateInspection(owner,legacy=legacy)
    try:
        for i in range(1,5):
            result=source.execute(select(source,{str(i)}),operation_id=uuid4().hex,mode='move',confirmed_move=True)
            assert len(result.committed)==1 and not result.failed
        state=legacy.state();worker=legacy._maintenance_worker
        assert worker is not None
        worker.callback(worker.stop)
        now[0]+=8*86400;worker.callback(worker.stop)
        units=[legacy._physical._unit(p.parent.name) for p in legacy._physical.journals.glob('*/intent.json')]
        assert len(units)==4
        assert sum(not (u/'retained').exists() for u in units)==2
        assert sum(not (u/'after').exists() for u in units)==2
        assert all((u/'before').exists() for u in units)
        assert legacy.state()==state
        with ZipFile(archive) as z:
            assert z.namelist()==['Generated(5).png','Generated(6).png']
        view,catalog=legacy.project(snapshot)
        try:
            assert {w.work_id for w in view.works}=={'5','6'}
            assert catalog.get('5',0,'original').data==png()
        finally:catalog.close()
        source.close()
        legacy=LegacyQuarantine(original,snapshot,owner=owner,maintenance=policy)
        assert legacy.state()==state
    finally:source.close();legacy.close();owner.close()
