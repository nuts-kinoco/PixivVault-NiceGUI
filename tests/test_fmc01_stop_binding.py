"""Generated STOP fixtures only; never use a real owner, Cookie or API."""
import hashlib,json,os,shutil
from pathlib import Path
import pytest
from test_migration_connections import configured
from pixivvault_nicegui import managed_stop_migration as migration
from pixivvault_nicegui import managed_network_stop_history_readonly as history
from pixivvault_nicegui.managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
from pixivvault_nicegui.managed_network_stop_shared_scope import ManagedUserNetworkStopScope
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopGate,ManagedNetworkGateStorageError,_file_identity
from pixivvault_nicegui.managed_stop_migration_binding import verified_media

@pytest.mark.parametrize('phase',['after-lock','after-read'])
def test_replaced_media_gate_is_rejected(tmp_path,monkeypatch,phase):
    store,parent,paths=configured(tmp_path,True)
    try:
        s=migration.StopMigrationService(store,target_root=tmp_path/'shared'/'owner')
        assert s.execute(s.preview(),confirmed=True).status=='completed'
        scope=ManagedProfileNetworkStopScope(store.root)
        shared=ManagedUserNetworkStopScope(s.target_root);scope.shared_owner=shared
        gate=paths['pixiv_download_root']/'.pixivvault-network-stop.sqlite3'
        clean=tmp_path/'clean.sqlite3';shutil.copyfile(gate,clean)
        if phase=='after-lock':ManagedNetworkStopGate(gate).trip(429)
        original_id=_file_identity(gate)
        real_lock=migration._FileLock;real_read=history._read_gate_immutable
        preserved=tmp_path/'preserved-stop.sqlite3'
        def swap():
            assert _file_identity(gate)==original_id
            gate.rename(preserved);shutil.copyfile(clean,gate)
        def lock(path,**kwargs):
            ticket=real_lock(path,**kwargs)
            if Path(path)==gate.with_name(gate.name+'.lock') and phase=='after-lock':swap()
            return ticket
        # after-read cannot use a regular gate writer while holding the lock;
        # emulate the non-cooperating external writer directly with SQLite.
        def read_after(path):
            state=real_read(path)
            if Path(path)==gate:
                import sqlite3
                db=sqlite3.connect(gate)
                try:
                    db.execute('UPDATE gate SET blocked=1,status=429,revision=1')
                    db.commit()
                finally:db.close()
                gate.rename(preserved);shutil.copyfile(clean,gate)
            return state
        monkeypatch.setattr(migration,'_FileLock',lock)
        if phase=='after-read':monkeypatch.setattr(history,'_read_gate_immutable',read_after)
        error=None;allowed=None
        try:allowed=verified_media(scope,gate.parent)
        except ManagedNetworkGateStorageError as ex:error=str(ex)
        assert preserved.is_file()
        assert real_read(preserved)==(True,429,1)
        assert _file_identity(preserved)==original_id
        observation={'phase':phase,'allowed':allowed,'error':error,'preserved_stop':real_read(preserved),
                     'old_identity':original_id,'new_identity':_file_identity(gate),
                     'preserved_sha256':hashlib.sha256(preserved.read_bytes()).hexdigest(),'network_requests':0}
        out=Path(os.environ.get('PV_FMC_PROOF',str(tmp_path/'proof')));out.mkdir(exist_ok=True)
        (out/(phase+'.json')).write_text(json.dumps(observation,indent=2))
        assert error is not None and allowed is None,observation
    finally:store.close()

