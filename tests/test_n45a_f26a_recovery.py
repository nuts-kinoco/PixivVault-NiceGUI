"""Generated passwords remain in memory; no real Vault, Cookie or API."""
import asyncio
import hashlib
import os
import secrets

import pytest
from nicegui import Client, ui, core
from pixivvault_nicegui.external_vault import ExternalVaultService
from pixivvault_nicegui.premium_gate import PremiumAuthorization
from pixivvault_nicegui.ui.premium_gate_view import PremiumGateDialog
from test_external_vault import FixtureCrypto, FixtureTransport
from test_premium_authorization import Protection


def fixture(tmp_path):
    password=secrets.token_urlsafe(16)[:12]
    salt=os.urandom(32);iterations=1000;now=[1000]
    transport=FixtureTransport()
    vault=ExternalVaultService(tmp_path/'vault',crypto=FixtureCrypto(),transport=transport)
    verifier=(salt,hashlib.pbkdf2_hmac('sha256',password.encode(),salt,iterations),iterations)
    gate=PremiumAuthorization(vault,protect=Protection(),clock=lambda:now[0],verifier=verifier)
    vault.premium_authorization=gate
    return password,now,vault,gate,transport


async def open_prompt(dialog):
    task=asyncio.create_task(dialog.request_enable())
    await asyncio.sleep(0)
    return task


@pytest.mark.asyncio
async def test_prompt_replaces_even_an_already_empty_server_value(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'loop',asyncio.get_running_loop())
    password,now,vault,gate,transport=fixture(tmp_path)
    client=Client(ui.page('/_n45_empty'));task=None
    try:
        with client:dialog=PremiumGateDialog(gate,default_uid_path=None)
        old=dialog.password
        task=await open_prompt(dialog)
        fresh=dialog.password.id!=old.id and old.is_deleted
        assert fresh, 'Each prompt must use a new empty browser model'
        empty=dialog.password.value==''
        assert empty
        assert len([e for e in dialog.password_dialog.descendants() if isinstance(e,ui.input)])==2
        dialog.password_dialog.submit(False);await task
        empty=dialog.password.value==''
        assert empty and not gate.status()['enabled'] and not transport.calls
    finally:
        dialog.close()
        if task and not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
        client.delete();gate.close();vault.close();password=''


@pytest.mark.asyncio
async def test_failed_then_correct_authentication_discards_old_input(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'loop',asyncio.get_running_loop())
    password,now,vault,gate,transport=fixture(tmp_path)
    monkeypatch.setattr(vault,'start_keeper',lambda *a,**k:None)  # the keeper's own refresh is not under test here
    client=Client(ui.page('/_n45_failed'));task=None
    try:
        with client:dialog=PremiumGateDialog(gate,default_uid_path=None)
        task=await open_prompt(dialog)
        failed_input=dialog.password
        failed_input.set_value(password+'!');dialog.password_dialog.submit(True)
        for _ in range(300):
            if 'パスワードが違います' in dialog.dialog_error.text:break
            await asyncio.sleep(.01)
        assert not task.done(), 'a wrong password keeps the dialog open'
        cleared=failed_input.value=='' and dialog.password.value==''
        assert cleared and failed_input.is_deleted
        assert 'パスワードが違います' in dialog.dialog_error.text and not gate.status()['enabled']
        now[0]+=10
        empty=dialog.password.value==''
        assert empty and dialog.password.id!=failed_input.id
        dialog.password.set_value(password);dialog.secret.set_value('fixture-key');dialog._load_uid_bytes(b'1')
        dialog.password_dialog.submit(True)
        await asyncio.wait_for(task,5)
        assert dialog.password.value==''
        assert gate.status()['enabled'] and not transport.calls
    finally:
        dialog.close()
        if task and not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
        client.delete();gate.close();vault.close();password=''


@pytest.mark.asyncio
async def test_cancel_discards_input_before_reopening(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'loop',asyncio.get_running_loop())
    password,now,vault,gate,transport=fixture(tmp_path)
    client=Client(ui.page('/_n45_cancel'));task=None
    try:
        with client:dialog=PremiumGateDialog(gate,default_uid_path=None)
        task=await open_prompt(dialog);old=dialog.password
        old.set_value(password);dialog.password_dialog.submit(False);await task
        cleared=old.value=='' and dialog.password.value==''
        assert cleared and old.is_deleted and not gate.status()['enabled']
        task=await open_prompt(dialog)
        empty=dialog.password.value==''
        assert empty
        dialog.password_dialog.submit(False);await task
        assert not transport.calls
    finally:
        dialog.close()
        if task and not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
        client.delete();gate.close();vault.close();password=''


def test_managed_startup_creates_missing_parent_chain_without_network(tmp_path):
    from pixivvault_nicegui.services import ApplicationServices
    from pixivvault_nicegui.managed_downloads import MARKER
    from test_managed_download_services import FakeSource
    source=FakeSource();root=tmp_path/'new-parent'/'nested'/'managed'
    assert not root.parent.exists()
    service=ApplicationServices(live_client=source,pixiv_download_root=root)
    try:
        assert (root/MARKER).is_file() and root.parent.is_dir()
        assert service.library_snapshot().works==() and source.calls==[]
    finally:service.close()
    assert source.closed


def test_missing_parent_support_does_not_overwrite_foreign_files(tmp_path):
    from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads, ManagedDownloadError
    from test_duplicate_inspection import Client as ImageClient
    parent=tmp_path/'existing-file';parent.write_bytes(b'generated keep')
    with pytest.raises(ManagedDownloadError):
        ManagedPixivDownloads(parent/'child',ImageClient(),resolve_record=lambda _:None)
    assert parent.read_bytes()==b'generated keep'
    foreign=tmp_path/'foreign';foreign.mkdir();(foreign/'keep').write_bytes(b'generated keep')
    with pytest.raises(ManagedDownloadError):
        ManagedPixivDownloads(foreign,ImageClient(),resolve_record=lambda _:None)
    assert (foreign/'keep').read_bytes()==b'generated keep'
