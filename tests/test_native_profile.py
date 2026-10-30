"""Native-only settings profile acceptance with generated local paths and fake callbacks."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import stat
import sqlite3

from nicegui import Client, ui
import pytest

from pixivvault_nicegui.bootstrap import parse_args
from pixivvault_nicegui.native_profile import (
    NativeProfileStore, NativeProfileError, NativeProfileConflict,
    NativeProfileReferenceUnavailable,
    plan_storage, provision_storage, native_kwargs,
)
from pixivvault_nicegui.ui.native_profile_view import NativeProfileView


def paths(tmp_path):
    parent = tmp_path / 'chosen-save-parent'
    parent.mkdir()
    cookie = tmp_path / 'credential-source' / 'cookies.txt'
    cookie.parent.mkdir()
    cookie.write_bytes(b'generated fake cookies, never opened by tests')
    return parent, cookie


def test_first_run_stays_unconfigured_and_restart_keeps_selected_references(tmp_path):
    parent, cookie = paths(tmp_path)
    profile_root = tmp_path / 'settings' / 'native'
    profile_root.parent.mkdir()
    assert NativeProfileStore.peek(profile_root).revision==0
    assert not profile_root.exists()
    store = NativeProfileStore(profile_root)
    empty = store.snapshot()
    assert empty.revision == 0 and empty.cookie_file is None and empty.save_parent is None
    saved = store.update(cookie_file=cookie, save_parent=parent, expected_revision=0)
    assert saved.revision == 1
    assert saved.cookie_file == cookie and saved.save_parent == parent
    # A selected parent is never altered just because settings were saved.
    assert list(parent.iterdir()) == []
    assert cookie.read_bytes() == b'generated fake cookies, never opened by tests'
    reopened = NativeProfileStore(profile_root).snapshot()
    assert reopened == saved
    assert NativeProfileStore.peek(profile_root)==saved
    assert b'generated fake cookies, never opened by tests' not in (
        profile_root/'profile.sqlite3').read_bytes()
    roots = plan_storage(reopened, profile_root)
    assert len(set(roots.values())) == len(roots)
    assert all(root.parent == parent / 'PixivVault-NiceGUI' for root in roots.values())
    runtime = store.native_kwargs(reopened)
    assert native_kwargs(reopened, profile_root)==runtime
    assert runtime['pixiv_cookie_file'] == cookie
    assert runtime['pixiv_download_root'] == roots['pixiv_download_root']
    assert runtime['preferences_root'] == roots['preferences_root']
    assert 'library_readonly_db' not in runtime
    assert set(runtime)=={'pixiv_cookie_file','preferences_root',
        'pixiv_download_root','novel_download_root','ugoira_root',
        'legacy_zip_export_root','discovery_root','image_cache_root',
        'external_vault_root','save_export_root','recommendation_preferences_root','following_sync_root'}
    # All flat root kwargs obey the existing explicit CLI parser's boundaries.
    argv=['--native']
    for key,value in runtime.items():
        argv.extend(('--'+key.replace('_','-'),str(value)))
    cli=parse_args(argv)
    assert cli.pixiv_cookie_file==cookie and cli.ugoira_root==roots['ugoira_root']
    # Only an explicit next-start operation creates the owned parent marker.
    assert not (parent/'PixivVault-NiceGUI').exists()
    provision_storage(reopened, profile_root)
    assert (parent/'PixivVault-NiceGUI'/'.pixivvault-native-storage.json').is_file()
    assert plan_storage(reopened, profile_root)==roots


def test_cas_between_independent_windows_and_atomic_failed_update(tmp_path):
    parent, cookie = paths(tmp_path)
    home = tmp_path / 'native-profile'
    first, second = NativeProfileStore(home), NativeProfileStore(home)
    old = first.snapshot()
    committed = first.update(save_parent=parent, expected_revision=old.revision)
    with pytest.raises(NativeProfileConflict):
        second.update(cookie_file=cookie, expected_revision=old.revision)
    assert first.snapshot() == committed
    with pytest.raises(NativeProfileError):
        first.update(save_parent=tmp_path/'missing', expected_revision=committed.revision)
    assert NativeProfileStore(home).snapshot() == committed


def test_sqlite_failure_rolls_back_value_and_revision(tmp_path):
    parent, cookie = paths(tmp_path)
    home = tmp_path/'native'
    store = NativeProfileStore(home)
    saved = store.update(save_parent=parent,expected_revision=0)
    # A real SQLite write error after BEGIN IMMEDIATE must leave the prior row.
    with sqlite3.connect(str(home/'profile.sqlite3')) as db:
        db.execute('''CREATE TRIGGER fail_profile_write BEFORE UPDATE ON native_profile_state
                      BEGIN SELECT RAISE(ABORT, 'synthetic-fault'); END''')
    with pytest.raises(NativeProfileError, match='保存できません'):
        store.update(cookie_file=cookie, expected_revision=saved.revision)
    assert store.snapshot()==saved
    assert NativeProfileStore(home).snapshot()==saved


def test_peek_never_creates_or_recovers_sidecars(tmp_path):
    home=tmp_path/'uncreated'
    assert NativeProfileStore.peek(home).revision==0
    assert not home.exists()
    store=NativeProfileStore(home)
    assert NativeProfileStore.peek(home)==store.snapshot()
    db=home/'profile.sqlite3'
    for suffix in ('-wal','-shm','-journal'):
        sidecar=Path(str(db)+suffix)
        sidecar.write_bytes(b'incomplete fixture')
        original=sidecar.read_bytes()
        with pytest.raises(NativeProfileError):
            NativeProfileStore.peek(home)
        assert sidecar.read_bytes()==original
        sidecar.unlink()
    store.close()
    store.close()


@pytest.mark.parametrize('missing_reference',('cookie','save_parent','library_db'))
def test_missing_selected_reference_keeps_profile_readable_for_repair(tmp_path,missing_reference):
    parent,cookie=paths(tmp_path)
    home=tmp_path/'native'
    store=NativeProfileStore(home)
    if missing_reference=='library_db':
        old_db=tmp_path/'frozen'/'works.db'
        old_db.parent.mkdir()
        old_db.write_bytes(b'fake db')
        old_images=tmp_path/'old-images'
        old_images.mkdir()
        selected=store.update(save_parent=parent,library_readonly_db=old_db,
                              library_image_root=old_images,expected_revision=0)
        old_db.unlink()
    else:
        selected=store.update(save_parent=parent,cookie_file=cookie,expected_revision=0)
        if missing_reference=='cookie':
            cookie.unlink()
        else:
            parent.rmdir()
    assert store.snapshot()==selected
    assert NativeProfileStore.peek(home)==selected
    with pytest.raises(NativeProfileReferenceUnavailable):
        native_kwargs(selected,home)
    assert store.snapshot().revision==1
    store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('missing_reference',('cookie','save_parent'))
async def test_repair_page_can_replace_missing_reference_without_touching_old_path(
        tmp_path, missing_reference):
    original_parent, original_cookie=paths(tmp_path)
    store=NativeProfileStore(tmp_path/'native')
    current=store.update(cookie_file=original_cookie,save_parent=original_parent,
                         expected_revision=0)
    next_parent=tmp_path/'new-save-parent'
    next_parent.mkdir()
    next_cookie=tmp_path/'replacement-credentials'/'replacement-cookies.txt'
    next_cookie.parent.mkdir()
    next_cookie.write_bytes(b'generated replacement ref')
    if missing_reference=='cookie':
        original_cookie.unlink()
    else:
        original_parent.rmdir()
    assert NativeProfileStore.peek(store.root)==current
    client=Client(ui.page('/_native_reference_repair_'+missing_reference))
    try:
        with client:
            view=NativeProfileView(store,pick_file=lambda:next_cookie,
                                   pick_folder=lambda:next_parent)
        view.open()
        if missing_reference=='cookie':
            await view.choose_cookie()
        else:
            await view.choose_folder()
        assert view.save(), view.status.text
        after=store.snapshot()
        assert after.revision==2
        assert after.cookie_file==(next_cookie if missing_reference=='cookie' else original_cookie)
        assert after.save_parent==(next_parent if missing_reference=='save_parent' else original_parent)
        assert native_kwargs(after,store.root)['preferences_root'].name=='preferences'
        assert not (next_parent/'PixivVault-NiceGUI').exists()
    finally:
        client.delete()
        store.close()


def test_reject_unowned_and_reparse_and_preserve_existing_profile(tmp_path, monkeypatch):
    parent, cookie = paths(tmp_path)
    home = tmp_path/'native'
    store = NativeProfileStore(home)
    baseline = store.update(cookie_file=cookie,save_parent=parent,expected_revision=0)
    unknown=tmp_path/'foreign'
    unknown.mkdir()
    (unknown/'keep.bin').write_bytes(b'foreign data')
    with pytest.raises(NativeProfileError):
        NativeProfileStore(unknown)
    assert (unknown/'keep.bin').read_bytes()==b'foreign data'
    target=parent/'PixivVault-NiceGUI'
    target.mkdir()
    (target/'unknown.txt').write_bytes(b'old input')
    with pytest.raises(NativeProfileError):
        store.update(save_parent=parent, expected_revision=baseline.revision)
    assert (target/'unknown.txt').read_bytes()==b'old input'
    assert store.snapshot()==baseline
    with pytest.raises(NativeProfileError):
        store.native_kwargs()  # Current storage is no longer owned.
    (target/'unknown.txt').unlink()
    target.rmdir()
    redirect=tmp_path/'reparse-fixture'
    redirect.mkdir()
    original_lstat=Path.lstat
    def simulate_reparse(path):
        if path == redirect:
            return SimpleNamespace(st_mode=stat.S_IFDIR|0o700, st_file_attributes=0x400)
        return original_lstat(path)
    monkeypatch.setattr(Path,'lstat',simulate_reparse)
    with pytest.raises(NativeProfileError):
        plan_storage(replace(baseline,save_parent=redirect),home)


def test_cookie_and_explicit_readonly_refs_cannot_overlap_writable_root(tmp_path):
    parent,cookie=paths(tmp_path)
    home=tmp_path/'native'
    store=NativeProfileStore(home)
    old_db=tmp_path/'frozen'/'works.db'
    old_db.parent.mkdir()
    old_db.write_bytes(b'fake readonly fixture')
    old_images=tmp_path/'readonly_images'
    old_images.mkdir()
    initial=store.update(library_readonly_db=old_db,
                         library_image_root=old_images,expected_revision=0)
    readonly=store.update(save_parent=parent,expected_revision=initial.revision)
    assert readonly.library_readonly_db==old_db and readonly.library_image_root==old_images
    assert 'pixiv_download_root' not in store.native_kwargs(readonly)
    assert 'recommendation_preferences_root' in store.native_kwargs(readonly)
    combined=store.update(cookie_file=cookie,expected_revision=readonly.revision)
    assert combined.cookie_file==cookie and combined.library_readonly_db==old_db
    assert combined.library_image_root==old_images
    assert store.native_kwargs(combined)['library_readonly_db']==old_db
    assert old_db.read_bytes()==b'fake readonly fixture'
    assert not list(parent.iterdir())
    # Clearing the optional old references remains an explicit operation.
    saved=store.update(cookie_file=cookie,library_readonly_db=None,
                       library_image_root=None,expected_revision=combined.revision)
    assert saved.library_readonly_db is None and saved.library_image_root is None
    kwargs=store.native_kwargs(saved)
    assert 'library_readonly_db' not in kwargs and 'library_image_root' not in kwargs
    assert kwargs['pixiv_download_root'].parent==parent/'PixivVault-NiceGUI'
    combined=store.update(cookie_file=cookie,library_readonly_db=old_db,
                          library_image_root=old_images,expected_revision=saved.revision)
    assert combined.library_readonly_db==old_db and combined.cookie_file==cookie
    # Cookie inside selected save parent is not permission to reuse the source.
    cookie_in_parent=parent/'selected-cookie.txt'
    cookie_in_parent.write_bytes(b'fake')
    with pytest.raises(NativeProfileError):
        store.update(cookie_file=cookie_in_parent,expected_revision=combined.revision)
    assert store.snapshot()==combined
    assert old_db.read_bytes()==b'fake readonly fixture'


def test_corrupted_profile_never_reinitialized_or_overwritten(tmp_path):
    parent, _ = paths(tmp_path)
    home=tmp_path/'native'
    store=NativeProfileStore(home)
    initial=store.update(save_parent=parent,expected_revision=0)
    database=home/'profile.sqlite3'
    preserved=database.read_bytes()
    store.close()
    database.write_bytes(b'garbled synthetic sqlite')
    before=database.read_bytes()
    with pytest.raises(NativeProfileError):
        NativeProfileStore(home)
    with pytest.raises(NativeProfileError):
        NativeProfileStore.peek(home)
    assert database.read_bytes()==before and before != preserved
    assert initial.revision==1


@pytest.mark.asyncio
async def test_view_picker_cancel_save_reload_and_fake_explicit_auth(tmp_path):
    parent, cookie=paths(tmp_path)
    profile=NativeProfileStore(tmp_path/'native')
    picks={'file':None,'folder':None}
    calls=[]
    def file_picker(): return picks['file']
    def folder_picker(): return picks['folder']
    def bounded_fake(path):
        calls.append(path)
        return True
    client=Client(ui.page('/_native_profile_fixture'))
    try:
        with client:
            view=NativeProfileView(profile,pick_file=file_picker,pick_folder=folder_picker,
                                   verify_auth=bounded_fake)
        view.open()
        assert view.dialog.value and view.snapshot.revision==0
        await view.choose_cookie()
        await view.choose_folder()
        assert view.draft_cookie is None and view.draft_parent is None
        picks['file']=cookie
        picks['folder']=parent
        await view.choose_cookie()
        await view.choose_folder()
        assert calls==[]  # selection must never initiate a GET
        assert view.draft_cookie==cookie and view.draft_parent==parent
        view.cancel()
        assert profile.snapshot().revision==0
        view.open()
        await view.choose_cookie()
        await view.choose_folder()
        assert view.save()
        assert profile.snapshot().revision==1
        assert '次回起動' in view.status.text
        await view.check_auth()
        assert calls==[cookie]
        assert profile.snapshot().revision==1
        view.open()
        assert view.draft_cookie==cookie and view.draft_parent==parent
    finally:
        client.delete()
        profile.close()


@pytest.mark.asyncio
async def test_view_stale_save_keeps_draft_and_current_disk_values(tmp_path):
    parent, cookie=paths(tmp_path)
    profile=NativeProfileStore(tmp_path/'profile')
    other=NativeProfileStore(tmp_path/'profile')
    client=Client(ui.page('/_profile_conflict'))
    try:
        with client:
            view=NativeProfileView(profile,pick_file=lambda: cookie,
                                   pick_folder=lambda: parent)
        view.open()
        await view.choose_cookie()
        other.update(save_parent=parent,expected_revision=0)
        assert view.save() is False
        assert view.draft_cookie==cookie
        assert profile.snapshot().cookie_file is None
        assert profile.snapshot().save_parent==parent
        assert '別の画面' in view.status.text
        view.cancel()
        view.open()
        assert view.draft_parent==parent and view.draft_cookie is None
    finally:
        client.delete()
        other.close()
        profile.close()


@pytest.mark.asyncio
async def test_deactivated_view_rejects_late_picker_and_auth(tmp_path):
    import asyncio
    parent, cookie=paths(tmp_path)
    store=NativeProfileStore(tmp_path/'native')
    started=asyncio.Event()
    finish=asyncio.Event()

    async def delayed_picker():
        started.set()
        await finish.wait()
        return cookie

    client=Client(ui.page('/_profile_pending_picker'))
    try:
        with client:
            view=NativeProfileView(store,pick_file=delayed_picker)
        view.open()
        pending=asyncio.create_task(view.choose_cookie())
        await started.wait()
        view.deactivate()
        finish.set()
        await pending
        assert view.draft_cookie is None and store.snapshot().revision==0
        assert view.save() is False
        view.open()
        assert view.save() is True
        view.close()
        view.close()
        assert view.save() is False
    finally:
        client.delete()
        store.close()


@pytest.mark.asyncio
async def test_auth_result_never_applies_after_cookie_selection_changes(tmp_path):
    import asyncio
    _, cookie = paths(tmp_path)
    started, release = asyncio.Event(), asyncio.Event()
    called=[]

    async def fake_get(path):
        called.append(path)
        started.set()
        await release.wait()
        return True

    store=NativeProfileStore(tmp_path/'native')
    client=Client(ui.page('/_profile_auth_stale'))
    try:
        with client:
            view=NativeProfileView(store,pick_file=lambda:cookie,verify_auth=fake_get)
        view.open()
        await view.choose_cookie()
        pending=asyncio.create_task(view.check_auth())
        await started.wait()
        view.clear_cookie()
        assert view.draft_cookie is None
        assert not view.auth_button.enabled
        release.set()
        await pending
        assert called==[cookie]
        assert '認証を確認できました' not in view.status.text
        assert store.snapshot().revision==0  # GET check did not save settings.
        view.close()
    finally:
        client.delete()
        store.close()


@pytest.mark.asyncio
async def test_optional_readonly_library_ui_replace_clear_and_conflict(tmp_path):
    parent,cookie=paths(tmp_path)
    old=tmp_path/'old-frozen'
    old.mkdir()
    old_db=old/'works.db'
    old_db.write_bytes(b'old fake db')
    old_images=tmp_path/'old-images'
    old_images.mkdir()
    newest=tmp_path/'replacement-frozen'
    newest.mkdir()
    new_db=newest/'works.db'
    new_db.write_bytes(b'new fake db')
    new_images=tmp_path/'replacement-images'
    new_images.mkdir()
    store=NativeProfileStore(tmp_path/'profile')
    selected=store.update(library_readonly_db=old_db,
        library_image_root=old_images,save_parent=parent,expected_revision=0)
    old_db.unlink()
    client=Client(ui.page('/_profile_library_repair'))
    try:
        with client:
            view=NativeProfileView(store,pick_file=lambda:cookie,
                                   pick_database=lambda:new_db,pick_images=lambda:new_images)
        view.open()
        assert view.draft_database==old_db and view.draft_images==old_images
        await view.choose_database()
        await view.choose_images()
        assert view.draft_database==new_db and view.draft_images==new_images
        view.cancel()
        assert store.snapshot()==selected
        view.open()
        await view.choose_database()
        await view.choose_images()
        assert view.save()
        assert store.snapshot().library_readonly_db==new_db
        assert store.snapshot().library_image_root==new_images
        assert new_db.read_bytes()==b'new fake db'
        view.open()
        await view.choose_cookie()
        assert view.save()
        assert store.snapshot().cookie_file==cookie
        assert store.snapshot().library_readonly_db==new_db
        assert store.snapshot().library_image_root==new_images
        view.clear_library()  # Optional old references are removed only explicitly.
        assert view.save()
        assert store.snapshot().cookie_file==cookie
        assert store.snapshot().library_readonly_db is None
        assert new_db.read_bytes()==b'new fake db'
    finally:
        client.delete()
        store.close()


def test_view_embeds_in_existing_preferences_card_without_native_picker(tmp_path):
    store=NativeProfileStore(tmp_path/'profile')
    client=Client(ui.page('/_profile_embedded'))
    try:
        with client:
            with ui.card():
                view=NativeProfileView(store)
                view.build()
        assert view.snapshot.revision==0
        assert view.cookie_button.enabled is False
        assert view.folder_button.enabled is False
        assert view.auth_button is None
        assert view.save_button.enabled
        assert view.save() is True
    finally:
        client.delete()
        store.close()


@pytest.mark.asyncio
async def test_inline_cancel_keeps_form_active_and_rejects_late_picker(tmp_path):
    import asyncio
    parent, old_cookie = paths(tmp_path)
    newer = tmp_path / 'other-credentials'
    newer.mkdir()
    next_cookie = newer / 'replacement.txt'
    next_cookie.write_bytes(b'fake replacement reference')
    pending_started, release_old = asyncio.Event(), asyncio.Event()
    invocations = 0

    async def pick_cookie():
        nonlocal invocations
        invocations += 1
        if invocations == 1:
            pending_started.set()
            await release_old.wait()
            return old_cookie
        return next_cookie

    store = NativeProfileStore(tmp_path / 'native')
    client = Client(ui.page('/_native_inline_cancel_race'))
    try:
        with client, ui.card():
            view = NativeProfileView(store, pick_file=pick_cookie,
                                     pick_folder=lambda: parent)
            view.build()  # Inline, no standalone dialog.open().
        initial_epoch = view._epoch
        pending = asyncio.create_task(view.choose_cookie())
        await pending_started.wait()
        view.cancel()
        assert view._epoch == initial_epoch
        assert view._active and not view.dialog.value
        assert view.draft_cookie is None and store.snapshot().revision == 0
        assert view.cookie_button.enabled and view.save_button.enabled
        await view.choose_cookie()
        assert view.draft_cookie == next_cookie
        release_old.set()
        await pending
        assert view.draft_cookie == next_cookie
        assert view.save()
        assert store.snapshot().cookie_file == next_cookie
        assert store.snapshot().revision == 1
    finally:
        client.delete()
        store.close()
