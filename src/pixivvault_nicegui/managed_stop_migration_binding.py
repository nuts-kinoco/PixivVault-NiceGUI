"""Read-only consumption of an atomically published migration association."""
import json,hashlib
from pathlib import Path
from .managed_network_stop_gate import (
    _file_identity, _reject_links, ManagedNetworkGateBusy, ManagedNetworkGateStorageError,
)
from .managed_network_stop_shared_scope import open_existing_windows_user_stop,_OWNER_FILE,_file_identity_directory
from .native_profile import STORAGE_MARKER,STORAGE_IDENTITY,_owned_marker

def _association(scope,base,*,strict=False):
    try:
        owner=getattr(scope,'shared_owner',None) or open_existing_windows_user_stop()
        owner._check_owner()
        # Old sealed owners without this migration format retain the old
        # admission rules. A new-format journal, however, must never lose its
        # proof and silently fall back to those first-use rules.
        if not (owner.root/'migration-record.json').exists() and not (owner.root.parent/'.pixivvault-stop-migrations').exists():return None
        seal=json.loads((owner.root/_OWNER_FILE).read_text())
        from .managed_stop_migration import StopMigrationService
        proof=StopMigrationService._verify_archive(owner.root,seal['history_proof'])
        for value in proof.get('storage_bindings',[]):
            if (value['profile']==str(scope.root.resolve()) and tuple(value['profile_identity'])==_file_identity_directory(scope.root)
                and tuple(value['gate_identity'])==scope.gate._database_identity
                and value['base']==str(Path(base).resolve()) and tuple(value['base_identity'])==_file_identity_directory(Path(base))):
                _owned_marker(Path(base),STORAGE_MARKER,STORAGE_IDENTITY)
                return owner,proof,value
    except ManagedNetworkGateBusy:
        raise
    except Exception:
        if strict:raise ManagedNetworkGateStorageError('migration association unverified') from None
        return None
    return None

def verified_storage(scope,base):
    return _association(scope,base) is not None

def verified_media(scope,root):
    root=Path(root)
    found=_association(scope,root.parent,strict=True)
    if found is None:return False
    owner,proof,association=found
    records=[x for x in association['roots'] if x['root']==str(root.resolve())]
    if len(records)!=1:return False
    record=records[0]
    from .managed_stop_migration import _FileLock, _FileLockBusy, _fingerprint
    try:
        _reject_links(root)
        if not record['absent'] and tuple(record['identity'])!=_file_identity_directory(root):raise ValueError()
        from .managed_network_stop_history_readonly import _check_marker,_read_gate_immutable
        _check_marker(root,record['kind'])
        gate=root/'.pixivvault-network-stop.sqlite3'
        sidecars=[gate.with_name(gate.name+s) for s in ('-journal','-wal','-shm')]
        if not gate.exists():
            if (record['absent'] and not gate.with_name(gate.name+'.lock').exists()
                    and not any(p.exists() or p.is_symlink() for p in sidecars)):
                return True
            raise ValueError()
        lock=gate.with_name(gate.name+'.lock')
        root_identity=_file_identity_directory(root)
        gate_identity=_file_identity(gate)
        lock_identity=_file_identity(lock)
        if not record['absent']:
            source=next(x for x in proof['sources'] if x['root']==str(root.resolve()))
            for f in source['files']:
                if f['name'] in (gate.name,gate.name+'.lock') and _file_identity(root/f['name'])!=tuple(f['identity']):raise ValueError()
        ticket=_FileLock(lock)
        try:
            def check_identity():
                if (_file_identity_directory(root)!=root_identity
                        or _file_identity(gate)!=gate_identity
                        or _file_identity(lock)!=lock_identity):
                    raise ValueError()
                _check_marker(root,record['kind'])
                for path in sidecars:
                    _reject_links(path)
                    if path.exists() or path.is_symlink():raise ValueError()
            # The lock protects cooperating writers; pin and recheck the DB
            # itself as well, since a separate process can rename that path.
            check_identity()
            before=(_fingerprint(gate),_fingerprint(lock,ticket))
            state=_read_gate_immutable(gate)
            after=(_fingerprint(gate),_fingerprint(lock,ticket))
            check_identity()
            if before!=after:raise ValueError()
            # A later local fatal STOP is never ignored, even for a root first
            # created after migration. Persist it monotonically in both fences.
            if state[0]:
                scope.gate.trip(state[1]);owner.gate.trip(state[1])
            return True
        finally:ticket.close()
    except _FileLockBusy:
        raise ManagedNetworkGateBusy('migrated media history is busy') from None
    except ManagedNetworkGateBusy:
        raise
    except Exception:raise ManagedNetworkGateStorageError('migrated media history unverified') from None

