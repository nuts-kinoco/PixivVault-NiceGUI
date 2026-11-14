"""Fault seams run only against generated roots."""
import json
from pathlib import Path
import pytest
from test_formal_stop_migration import setup, service, api, fingerprint
from pixivvault_nicegui.managed_network_stop_profile_scope import ManagedProfileNetworkStopScope


def journal(s):
    path=next((s.target_root.parent/'.pixivvault-stop-migrations').glob('*/journal.json'))
    return path,json.loads(path.read_text())


@pytest.mark.parametrize('phase',['before-publish','published'])
def test_archive_is_rehashed_before_and_after_publish(tmp_path,phase):
    store,images,_=setup(tmp_path)
    try:
        s=service(tmp_path,store);plan=s.preview([('images',images)])
        before=fingerprint(images)
        def corrupt(current):
            if current==phase:
                root=s.target_root if phase=='published' else next((s.target_root.parent/'.pixivvault-stop-migrations').glob('*/owner'))
                file=next((root/'sources').rglob('*.sqlite3'));file.write_bytes(b'bad-copy')
        s._checkpoint=corrupt
        result=s.execute(plan,confirmed=True)
        assert result.status==('uncertain' if phase=='published' else 'failed')
        assert fingerprint(images)==before
        assert s.target_root.exists()==(phase=='published')
    finally:store.close()


def test_late_source_change_cannot_publish(tmp_path):
    store,images,gate=setup(tmp_path)
    try:
        s=service(tmp_path,store);plan=s.preview([('images',images)])
        # A non-cooperating writer changes a marker, not a legitimate STOP gate.
        marker=plan.sources[1].files[0][0]
        s._checkpoint=lambda phase:(images/marker).write_bytes(b'changed') if phase=='before-publish' else None
        result=s.execute(plan,confirmed=True)
        assert result.status=='failed' and result.reason=='source_changed'
        assert not s.target_root.exists()
    finally:store.close()


def test_native_additions_carry_existing_stop(tmp_path):
    store,images,_=setup(tmp_path,local_scope=False,status=429)
    try:
        s=service(tmp_path,store);plan=s.preview([('images',images)])
        assert s.execute(plan,confirmed=True).status=='completed'
        assert ManagedProfileNetworkStopScope(store.root).gate.snapshot().status_code==429
    finally:store.close()


def test_rollback_failure_is_retryable_and_does_not_claim_recovered(tmp_path,monkeypatch):
    store,images,_=setup(tmp_path,local_scope=False,status=403)
    try:
        s=service(tmp_path,store);plan=s.preview([('images',images)])
        before=[fingerprint(store.root),fingerprint(images)]
        real=api()._rename_new
        def denied(source,dest,**kwargs):
            if Path(source).parent==store.root:raise PermissionError('sharing violation')
            return real(source,dest,**kwargs)
        monkeypatch.setattr(api(),'_rename_new',denied)
        def fail(phase):
            if phase=='native:0':raise OSError('synthetic')
        s._checkpoint=fail
        result=s.execute(plan,confirmed=True)
        assert result.reason=='recovery_conflict'
        assert s.recover_pending().status=='failed'
        with pytest.raises(api().MigrationRejected):s.preview([])
        monkeypatch.setattr(api(),'_rename_new',real)
        assert s.recover_pending().status=='recovered'
        assert [fingerprint(store.root),fingerprint(images)]==before
        assert images in [x.root for x in s.preview().sources]
    finally:store.close()


def test_journal_cannot_redirect_rollback_to_foreign_stop(tmp_path):
    store,images,_=setup(tmp_path,local_scope=False,status=401)
    (tmp_path/'foreign').mkdir()
    other,_,_=setup(tmp_path/'foreign',status=429)
    try:
        s=service(tmp_path,store);plan=s.preview([('images',images)])
        class Crash(BaseException):pass
        def crash(phase):
            if phase=='native:0':raise Crash()
        s._checkpoint=crash
        with pytest.raises(Crash):s.execute(plan,confirmed=True)
        before=fingerprint(other.root)
        path,record=journal(s)
        for move in record['native_moves']:
            move['root']=str(other.root)
            move['destination']=str(other.root/Path(move['destination']).name)
        path.write_text(json.dumps(record))
        assert s.recover_pending().status=='failed'
        assert fingerprint(other.root)==before
    finally:store.close();other.close()


def test_loss_of_owner_does_not_reset_known_stop(tmp_path):
    store,images,_=setup(tmp_path,status=429)
    try:
        s=service(tmp_path,store);assert s.execute(s.preview([('images',images)]),confirmed=True).status=='completed'
        s.target_root.rename(s.target_root.with_name('preserved-owner'))
        fresh=service(tmp_path,store)
        with pytest.raises(api().MigrationRejected,match='history_unverified'):fresh.preview()
        assert fresh.recover_pending().status=='failed'
        assert not fresh.target_root.exists()
    finally:store.close()


def test_preview_from_other_service_cannot_publish(tmp_path):
    store,images,_=setup(tmp_path)
    try:
        s=service(tmp_path,store);plan=s.preview([('images',images)])
        assert service(tmp_path,store).execute(plan,confirmed=True).reason=='preview_required'
        assert not s.target_root.exists()
    finally:store.close()


def test_all_media_kinds_and_all_stop_codes_are_recorded(tmp_path):
    from pixivvault_nicegui.managed_novel_jobs import _MARKER
    from pixivvault_nicegui.managed_ugoira import MARKER, _IDENTITY
    from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopGate
    store,images,_=setup(tmp_path,local_scope=False,status=401)
    try:
        novel=tmp_path/'novel';novel.mkdir()
        (novel/'.pixivvault-novel-jobs.json').write_bytes(_MARKER)
        ManagedNetworkStopGate(novel/'.pixivvault-network-stop.sqlite3').trip(403)
        ugoira=tmp_path/'ugoira';ugoira.mkdir();(ugoira/MARKER).write_bytes(_IDENTITY)
        ManagedNetworkStopGate(ugoira/'.pixivvault-network-stop.sqlite3').trip(429)
        before=[fingerprint(x) for x in (images,novel,ugoira)]
        s=service(tmp_path,store)
        result=s.execute(s.preview([('images',images),('novel',novel),('ugoira',ugoira)]),confirmed=True)
        assert result.status=='completed' and len(result.items)==4
        proof=json.loads((s.target_root/'migration-record.json').read_text())
        assert proof['preserved_stop_statuses']==[401,403,429]
        assert [fingerprint(x) for x in (images,novel,ugoira)]==before
    finally:store.close()


def test_busy_source_is_reported_for_the_correct_item(tmp_path):
    store,images,_=setup(tmp_path)
    try:
        s=service(tmp_path,store);plan=s.preview([('images',images)])
        before=[fingerprint(store.root),fingerprint(images)]
        lock=api()._FileLock(images/'.pixivvault-network-stop.sqlite3.lock')
        try:result=s.execute(plan,confirmed=True)
        finally:lock.close()
        assert result.reason=='source_busy'
        assert result.items[0].status=='unprocessed' and result.items[1].status=='failed'
        assert [fingerprint(store.root),fingerprint(images)]==before
    finally:store.close()
