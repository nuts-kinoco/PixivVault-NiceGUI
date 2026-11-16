"""Generated roots only; no real Windows owner, Cookie or network."""
import importlib
import json
from pathlib import Path
from threading import Event
import hashlib
import pytest

from pixivvault_nicegui.native_profile import NativeProfileStore
from pixivvault_nicegui.managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopGate
from pixivvault_nicegui.managed_downloads import IDENTITY, MARKER
from pixivvault_nicegui.managed_network_stop_shared_scope import ManagedUserNetworkStopScope, _OWNER_FILE


def api():
    return importlib.import_module('pixivvault_nicegui.managed_stop_migration')


def fingerprint(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def setup(tmp_path, *, local_scope=True, status=None):
    store=NativeProfileStore(tmp_path/'profile')
    if local_scope: ManagedProfileNetworkStopScope(store.root)
    images=tmp_path/'old-images'; images.mkdir()
    (images/MARKER).write_text(json.dumps(IDENTITY))
    gate=ManagedNetworkStopGate(images/'.pixivvault-network-stop.sqlite3')
    if status: gate.trip(status)
    return store,images,gate


def service(tmp_path, store):
    return api().StopMigrationService(store, target_root=tmp_path/'common'/'owner')


def test_confirmation_and_preview_are_required(tmp_path):
    store,images,_=setup(tmp_path)
    try:
        s=service(tmp_path,store); plan=s.preview([('images',images)])
        result=s.execute(plan, confirmed=False)
        assert result.status=='failed' and result.reason=='confirmation_required'
        assert not s.target_root.exists()
    finally: store.close()


@pytest.mark.parametrize('status',[None,401,403,429])
def test_verified_copy_publishes_and_preserves_every_source(tmp_path,status):
    store,images,_=setup(tmp_path,status=status)
    try:
        s=service(tmp_path,store); plan=s.preview([('images',images)])
        before=[fingerprint(store.root),fingerprint(images)]
        result=s.execute(plan,confirmed=True)
        assert result.status=='completed'
        assert all(x.status=='success' for x in result.items)
        assert [fingerprint(store.root),fingerprint(images)]==before
        owner=ManagedUserNetworkStopScope(s.target_root)
        assert owner.gate.snapshot().status_code==status
        record=(s.target_root/'migration-record.json').read_bytes()
        seal=json.loads((s.target_root/_OWNER_FILE).read_text())
        assert seal['history_proof']==hashlib.sha256(record).hexdigest()
        proof=json.loads(record)
        assert proof['user_confirmed_complete'] is True
        assert len(proof['sources'])==2
        for source in proof['sources']:
            assert source['locked_utc'] and source['verified_utc']
            for f in source['files']:
                assert hashlib.sha256((s.target_root/f['copy']).read_bytes()).hexdigest()==f['sha256']
    finally: store.close()


@pytest.mark.parametrize('phase',['copied:0','verified','native:0','before-publish'])
def test_cancel_keeps_original_and_never_publishes(tmp_path,phase):
    store,images,_=setup(tmp_path,local_scope=False)
    try:
        s=service(tmp_path,store);plan=s.preview([('images',images)])
        before=[fingerprint(store.root),fingerprint(images)]
        cancel=Event()
        s._checkpoint=lambda current:cancel.set() if current==phase else None
        result=s.execute(plan,confirmed=True,cancel=cancel)
        assert result.status=='cancelled'
        assert not s.target_root.exists()
        assert [fingerprint(store.root),fingerprint(images)]==before
    finally:store.close()


@pytest.mark.parametrize('phase',['copied:1','verified','native:0','before-publish'])
def test_failure_keeps_original_and_safe_reason(tmp_path,phase):
    store,images,_=setup(tmp_path,local_scope=False)
    try:
        s=service(tmp_path,store);plan=s.preview([('images',images)])
        before=[fingerprint(store.root),fingerprint(images)]
        def fail(current):
            if current==phase: raise PermissionError('SECRET-cookie-token-private-path')
        s._checkpoint=fail
        result=s.execute(plan,confirmed=True)
        assert result.status=='failed'
        assert 'SECRET' not in repr(result)
        assert not s.target_root.exists()
        assert [fingerprint(store.root),fingerprint(images)]==before
    finally:store.close()


def test_missing_history_rejected_without_creating_it(tmp_path):
    store,images,_=setup(tmp_path)
    try:
        root=tmp_path/'unknown';root.mkdir();(root/MARKER).write_text(json.dumps(IDENTITY))
        before=fingerprint(root)
        s=service(tmp_path,store)
        with pytest.raises(api().MigrationRejected):s.preview([('images',root)])
        assert fingerprint(root)==before and not s.target_root.exists()
    finally:store.close()


def test_changed_source_after_preview_is_rejected(tmp_path):
    store,images,gate=setup(tmp_path)
    try:
        s=service(tmp_path,store);plan=s.preview([('images',images)])
        gate.trip(429);before=fingerprint(images)
        result=s.execute(plan,confirmed=True)
        assert result.status=='failed' and result.reason=='source_changed'
        assert fingerprint(images)==before and not s.target_root.exists()
    finally:store.close()


def test_crash_after_native_publication_is_explicitly_recoverable(tmp_path):
    store,images,_=setup(tmp_path,local_scope=False)
    try:
        s=service(tmp_path,store);plan=s.preview([('images',images)])
        before=[fingerprint(store.root),fingerprint(images)]
        class Crash(BaseException):pass
        def crash(phase):
            if phase=='native:0':raise Crash()
        s._checkpoint=crash
        with pytest.raises(Crash):s.execute(plan,confirmed=True)
        assert not s.target_root.exists()
        recovered=service(tmp_path,store).recover_pending()
        assert recovered.status=='recovered'
        assert [fingerprint(store.root),fingerprint(images)]==before
    finally:store.close()


def test_cancel_after_atomic_publication_reports_completed(tmp_path):
    store,images,_=setup(tmp_path)
    try:
        s=service(tmp_path,store);plan=s.preview([('images',images)]);cancel=Event()
        s._checkpoint=lambda phase:cancel.set() if phase=='published' else None
        result=s.execute(plan,confirmed=True,cancel=cancel)
        assert result.status=='completed' and s.target_root.exists()
    finally:store.close()
