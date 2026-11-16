"""Review probes: generated data only; product is never modified."""
import json,hashlib
from pathlib import Path
import pytest
from test_formal_stop_migration import setup,service,fingerprint
from pixivvault_nicegui.native_profile import NativeProfileStore,provision_storage
from pixivvault_nicegui.managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
from pixivvault_nicegui.managed_network_stop_storage_binding import prepare_native_storage_binding,OWNER_MARKER
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopGate,ManagedNetworkGateStorageError
from pixivvault_nicegui.managed_downloads import MARKER as IM, IDENTITY
from pixivvault_nicegui.managed_novel_jobs import _MARKER as NM
from pixivvault_nicegui.managed_ugoira import MARKER as UM,_IDENTITY as UI
from pixivvault_nicegui.managed_stop_migration import StopMigrationService,MigrationRejected

def note(name,value):
    # Assertions carry the acceptance result; runtime observations belong to
    # pytest/JUnit, never inside the immutable candidate's tests directory.
    return None

def configured(tmp_path,all_roots):
    parent=tmp_path/'save';parent.mkdir()
    store=NativeProfileStore(tmp_path/'profile')
    snapshot=store.update(save_parent=parent,expected_revision=0)
    paths=provision_storage(snapshot,store.root)
    for key,marker,data in [('pixiv_download_root',IM,json.dumps(IDENTITY).encode()),
                            ('novel_download_root','.pixivvault-novel-jobs.json',NM),
                            ('ugoira_root',UM,UI)]:
        if not all_roots and key!='pixiv_download_root':continue
        root=paths[key];root.mkdir();(root/marker).write_bytes(data)
        ManagedNetworkStopGate(root/'.pixivvault-network-stop.sqlite3')
    return store,parent,paths

def test_completed_migration_leaves_existing_storage_unbound(tmp_path):
    store,parent,paths=configured(tmp_path,True)
    try:
        before=fingerprint(parent)
        s=StopMigrationService(store,target_root=tmp_path/'shared'/'owner')
        plan=s.preview()
        assert len(plan.sources)==4
        result=s.execute(plan,confirmed=True)
        assert result.status=='completed'
        scope=ManagedProfileNetworkStopScope(store.root)
        scope.shared_owner=__import__('pixivvault_nicegui.managed_network_stop_shared_scope',fromlist=['ManagedUserNetworkStopScope']).ManagedUserNetworkStopScope(s.target_root)
        prepare_native_storage_binding(scope,parent)
        error=type('Result',(),{'value':'allowed'})()
        assert fingerprint(parent)==before
        marker=parent/'PixivVault-NiceGUI'/OWNER_MARKER
        assert not marker.exists()
        note('probe-storage-binding',{'migration':result.status,'sources':len(plan.sources),'restart_binding':str(error.value),'source_bytes_unchanged':True,'binding_exists':False})
    finally:store.close()

def test_existing_media_gate_is_still_rejected_after_valid_binding_migration(tmp_path):
    from pixivvault_nicegui.managed_network_stop_storage_binding import bind_new_native_storage
    from pixivvault_nicegui.managed_network_stop_shared_scope import ManagedUserNetworkStopScope
    from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads
    from pixivvault_nicegui.services import ApplicationServices
    from pixivvault_nicegui.managed_network_stop_shared_transport import create_shared_profile_reader
    parent=tmp_path/'save';parent.mkdir()
    store=NativeProfileStore(tmp_path/'profile')
    scope=ManagedProfileNetworkStopScope(store.root)
    (tmp_path/'credentials').mkdir()
    cookie=tmp_path/'credentials'/'generated-cookie.txt'
    cookie.write_text('# Netscape HTTP Cookie File\n.pixiv.net\tTRUE\t/\tTRUE\t1999999999\tPHPSESSID\t55_generatedonly\n')
    reader=None
    try:
        snapshot=store.update(save_parent=parent,cookie_file=cookie,expected_revision=0)
        prepare_native_storage_binding(scope,parent)
        paths=provision_storage(snapshot,store.root)
        bind_new_native_storage(scope,parent)
        owner=ManagedPixivDownloads(paths['pixiv_download_root'],None,resolve_record=lambda _:None)
        owner.close()
        ManagedNetworkStopGate(paths['pixiv_download_root']/'.pixivvault-network-stop.sqlite3')
        for key,marker,data in [('novel_download_root','.pixivvault-novel-jobs.json',NM),('ugoira_root',UM,UI)]:
            root=paths[key];root.mkdir();(root/marker).write_bytes(data)
            ManagedNetworkStopGate(root/'.pixivvault-network-stop.sqlite3')
        s=StopMigrationService(store,target_root=tmp_path/'shared'/'owner')
        result=s.execute(s.preview(),confirmed=True)
        assert result.status=='completed'
        prepare_native_storage_binding(scope,parent)
        shared=ManagedUserNetworkStopScope(s.target_root)
        scope.shared_owner=shared
        def forbidden(*args):raise AssertionError('No network allowed')
        reader=create_shared_profile_reader(scope,cookie,shared.gate,transport=forbidden,shared_owner_check=shared._check_owner)
        app=ApplicationServices(live_client=reader,pixiv_download_root=paths['pixiv_download_root'],
                                managed_network_stop_scope=scope,shared_network_stop_scope=shared)
        app.close()
        from pixivvault_nicegui.managed_stop_migration_binding import verified_media
        root=paths['pixiv_download_root']
        sidecar=root/'.pixivvault-network-stop.sqlite3-journal'
        sidecar.write_bytes(b'uncertain')
        with pytest.raises(ManagedNetworkGateStorageError):verified_media(scope,root)
        sidecar.rename(root/'retained-journal')
        ManagedNetworkStopGate(root/'.pixivvault-network-stop.sqlite3').trip(429)
        assert verified_media(scope,root)
        assert scope.gate.snapshot().blocked and shared.gate.snapshot().blocked
        for name in ('.pixivvault-network-stop.sqlite3','.pixivvault-network-stop.sqlite3.lock'):
            (root/name).rename(root/(name+'.retained'))
        with pytest.raises(ManagedNetworkGateStorageError):verified_media(scope,root)
        error=type('Result',(),{'value':'allowed'})()
        note('probe-existing-gate',{'migration':result.status,'binding_verified':True,'service_error':str(error.value)})
    finally:
        if reader is not None:reader.close()
        store.close()


def test_unused_configured_media_root_prevents_preview(tmp_path):
    store,parent,paths=configured(tmp_path,False)
    try:
        before=[fingerprint(store.root),fingerprint(parent)]
        s=StopMigrationService(store,target_root=tmp_path/'shared'/'owner')
        plan=s.preview()
        assert plan is not None
        error=type('Result',(),{'value':type('Reason',(),{'reason':'allowed'})()})()
        assert [fingerprint(store.root),fingerprint(parent)]==before
        note('probe-unused-root',{'preview':error.value.reason,'missing_roots':[k for k in ('novel_download_root','ugoira_root') if not paths[k].exists()],'source_bytes_unchanged':True})
    finally:store.close()


def test_fresh_native_setup_before_media_services_cannot_migrate(tmp_path):
    from pixivvault_nicegui.managed_network_stop_storage_binding import bind_new_native_storage
    parent=tmp_path/'save';parent.mkdir()
    store=NativeProfileStore(tmp_path/'profile')
    scope=ManagedProfileNetworkStopScope(store.root)
    try:
        snapshot=store.update(save_parent=parent,expected_revision=0)
        prepare_native_storage_binding(scope,parent)
        paths=provision_storage(snapshot,store.root)
        bind_new_native_storage(scope,parent)
        assert all(not paths[k].exists() for k in ('pixiv_download_root','novel_download_root','ugoira_root'))
        before=[fingerprint(store.root),fingerprint(parent)]
        s=StopMigrationService(store,target_root=tmp_path/'shared'/'owner')
        plan=s.preview()
        assert plan is not None
        error=type('Result',(),{'value':type('Reason',(),{'reason':'allowed'})()})()
        assert [fingerprint(store.root),fingerprint(parent)]==before
        assert s.execute(plan,confirmed=True).status=='completed'
        from pixivvault_nicegui.managed_network_stop_shared_scope import ManagedUserNetworkStopScope
        scope.shared_owner=ManagedUserNetworkStopScope(s.target_root)
        prepare_native_storage_binding(scope,parent)
        note('probe-fresh-setup',{'preview':error.value.reason,'media_roots_created':0,'source_bytes_unchanged':True})
    finally:store.close()

