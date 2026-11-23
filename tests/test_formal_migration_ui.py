"""Exercise product callbacks, not an alternative migration implementation."""
import asyncio
from pathlib import Path
import pytest
from nicegui import Client
from nicegui.page import page as Page
from nicegui.slot import Slot
from test_formal_stop_migration import setup,service,fingerprint
from pixivvault_nicegui.ui.stop_migration_view import StopMigrationView
from pixivvault_nicegui.native_startup import NativeProfileBinding


@pytest.fixture(autouse=True)
def preserve_ui_context():
    # ui.context.slot_stack creates a script-mode client when there is no UI.
    previous = Slot.get_stack().copy()
    try:
        yield
    finally:
        Slot.get_stack()[:] = previous
        Slot.prune_stack()


@pytest.mark.asyncio
async def test_ui_confirmation_and_result_are_disk_based(tmp_path):
    store,images,_=setup(tmp_path,status=403)
    client=Client(Page('/generated-migration'))
    with client:
        panel=StopMigrationView(service(tmp_path,store))
    try:
        panel.path.set_value(str(images));panel.add()
        assert not panel.run_button.enabled
        await panel.preview()
        assert panel.plan is not None and not panel.confirm.value
        await panel.execute()
        assert not panel.service.target_root.exists()
        panel.confirm.set_value(True)
        await panel.execute()
        assert panel.service.target_root.exists()
        assert '完了' in panel.status.text and '停止履歴' in panel.status.text
        assert not panel.run_button.enabled
    finally:panel.close();client.delete();store.close()


@pytest.mark.asyncio
async def test_ui_cancellation_preserves_source_and_honest_result(tmp_path):
    store,images,_=setup(tmp_path,local_scope=False)
    client=Client(Page('/generated-cancel'))
    with client:panel=StopMigrationView(service(tmp_path,store))
    try:
        panel.additional=[('images',images)];await panel.preview()
        before=fingerprint(store.root)
        panel.service._checkpoint=lambda phase:panel.cancel_event.set() if phase=='native:0' else None
        panel.confirm.set_value(True);await panel.execute()
        assert '中止' in panel.status.text
        assert fingerprint(store.root)==before and not panel.service.target_root.exists()
    finally:panel.close();client.delete();store.close()


@pytest.mark.asyncio
async def test_edit_after_preview_clears_confirmation(tmp_path):
    store,images,_=setup(tmp_path)
    client=Client(Page('/generated-edit'))
    with client:panel=StopMigrationView(service(tmp_path,store))
    try:
        panel.additional=[('images',images)];await panel.preview();panel.confirm.set_value(True)
        panel.remove(('images',images))
        assert panel.plan is None and panel.confirm.value is False
        assert not panel.run_button.enabled
    finally:panel.close();client.delete();store.close()


def test_settings_entry_uses_same_offline_flow(tmp_path):
    store,_,_=setup(tmp_path)
    binding=NativeProfileBinding(store)
    binding.create_migration_service=lambda:service(tmp_path,store)
    client=Client(Page('/generated-settings'))
    with client:
        view=binding.create_view(allow_auth=False);view.build()
        view.open_migration()
    try:
        assert isinstance(view.migration_view,StopMigrationView)
        assert view.verify_auth is None and view.migration_view.dialog.value
    finally:view.close();client.delete();binding.close()


@pytest.mark.parametrize('phase',['success','failed','cancelled'])
def test_password_unlock_and_vault_bytes_are_untouched(tmp_path,phase):
    from test_external_vault import FixtureCrypto
    from pixivvault_nicegui.external_vault import ExternalVaultService
    store,images,_=setup(tmp_path,local_scope=False)
    vault=ExternalVaultService(tmp_path/'vault',crypto=FixtureCrypto())
    try:
        vault.initialize('generated-password','1','generated-key')
        vault.lock()
        vault.close()
        before=fingerprint(vault.root)
        s=service(tmp_path,store);plan=s.preview([('images',images)])
        from threading import Event
        cancel=Event()
        def fault(current):
            if current=='native:0':
                if phase=='failed':raise OSError('synthetic')
                if phase=='cancelled':cancel.set()
        s._checkpoint=fault
        result=s.execute(plan,confirmed=True,cancel=cancel)
        assert result.status==('completed' if phase=='success' else phase)
        assert fingerprint(vault.root)==before
        vault=ExternalVaultService(tmp_path/'vault',crypto=FixtureCrypto())
        vault.unlock('generated-password')
        assert vault.status()['open']
    finally:vault.close();store.close()
