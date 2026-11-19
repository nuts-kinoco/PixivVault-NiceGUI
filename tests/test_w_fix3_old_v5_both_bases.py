import json

import pytest

from pixivvault_nicegui import managed_generations as mg
from pixivvault_nicegui.managed_downloads import ManagedDownloadError, ManagedPixivDownloads
from test_v_dl_cap_20261117 import make_generation, make_root, publish
from test_w_fix3_migration import historical_module


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() if p.is_file() else None
            for p in root.rglob('*')}


@pytest.mark.parametrize('restore_old_index', [False, True])
@pytest.mark.parametrize('opener', ['generations', 'downloads'])
def test_old_v5_same_id_in_both_bases_is_refused_without_writes(tmp_path, restore_old_index, opener):
    old = historical_module('823871e')
    root = make_root(tmp_path)
    writer = old.ManagedGenerations(root)
    writer.prepare_for_publication()
    first = publish(writer, root, '1000')
    writer._compact(writer._loaded)
    idx = root / 'active-generations.json'
    old_index = idx.read_bytes()

    def updated(before, suffix):
        relative, digest = make_generation(root, '1000', suffix=suffix * 32)
        return writer.publish('1000', relative, digest, before['update_date'],
                              before['sha256'], before['relative_receipt'], suffix * 64)

    second = updated(first, 'a')
    writer._compact(writer._loaded)
    updated(second, 'b')
    assert '1000' in json.loads(old_index)['entries']
    assert '1000' in json.loads(idx.read_bytes())['entries']
    if restore_old_index:
        idx.write_bytes(old_index)
    # A damaged or incomplete old root must not have its missing directories
    # silently created before the decision is made.
    (root / 'generation-incomplete').rmdir()
    before = snapshot(root)
    with pytest.raises(ManagedDownloadError) as error:
        if opener == 'generations':
            mg.ManagedGenerations(root)
        else:
            ManagedPixivDownloads(root, None, resolve_record=lambda *_: None)
    assert '旧形式' in str(error.value)
    assert snapshot(root) == before


@pytest.mark.parametrize('revision', [None, 'efabe62'])
def test_sequenced_v5_latest_opens_and_rollback_is_refused(tmp_path, revision):
    module = mg if revision is None else historical_module(revision)
    root = make_root(tmp_path)
    writer = module.ManagedGenerations(root)
    writer.prepare_for_publication()
    first = publish(writer, root, '1000')
    old_index = (root / 'active-generations.json').read_bytes()
    assert json.loads(old_index)['journal']['generation'] >= 1
    writer._compact(writer._loaded)
    relative, digest = make_generation(root, '1000', suffix='a' * 32)
    writer.publish('1000', relative, digest, first['update_date'], first['sha256'],
                   first['relative_receipt'], 'a' * 64)
    assert mg.ManagedGenerations(root).current('1000')['relative_receipt'] == relative
    (root / 'active-generations.json').write_bytes(old_index)
    with pytest.raises(ManagedDownloadError, match='index rejected'):
        mg.ManagedGenerations(root)


def test_warm_reader_refuses_replacement_with_generationless_index(tmp_path):
    root = make_root(tmp_path)
    writer = mg.ManagedGenerations(root)
    writer.prepare_for_publication()
    publish(writer, root, '1000')
    observer = mg.ManagedGenerations(root)
    assert observer.current('1000') is not None
    index = root / 'active-generations.json'
    packet = json.loads(index.read_bytes())
    packet['journal'] = {'id': packet['journal']['id']}
    index.write_bytes(json.dumps(packet).encode('utf-8'))
    before = snapshot(root)
    with pytest.raises(ManagedDownloadError, match='旧形式'):
        observer.current('1000')
    assert snapshot(root) == before


def test_unreadable_index_is_refused_before_any_directory_creation(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    old = historical_module('823871e').ManagedGenerations(root)
    old.prepare_for_publication()
    (root / 'generation-incomplete').rmdir()
    before = snapshot(root)
    read = mg._read_shared
    def unreadable(path, limit):
        if path.name == 'active-generations.json':
            raise OSError('synthetic read failure')
        return read(path, limit)
    monkeypatch.setattr(mg, '_read_shared', unreadable)
    with pytest.raises(ManagedDownloadError):
        mg.ManagedGenerations(root)
    assert snapshot(root) == before


def test_native_startup_routes_old_v5_to_offline_recovery(tmp_path, monkeypatch):
    from pixivvault_nicegui import bootstrap
    from pixivvault_nicegui.managed_downloads import LegacyV5Uncertain
    from pixivvault_nicegui.native_profile import NativeProfileStore
    from test_stage5_r1_native_first_draft_shared import _stub_native, _generated_shared_owner
    from pixivvault_nicegui import managed_network_stop_shared_scope as shared

    scope = _generated_shared_owner(tmp_path)
    monkeypatch.setattr(shared, 'open_existing_windows_user_stop', lambda: scope)
    calls, recovered, registered = _stub_native(monkeypatch)
    def rejected(**kwargs):
        raise LegacyV5Uncertain()
    monkeypatch.setattr(bootstrap, 'register_pages', rejected)
    profile = tmp_path / 'profile'
    store = NativeProfileStore(profile)
    store.close()
    assert bootstrap.main(['--native', '--profile-root', str(profile)]) == 0
    assert len(calls) == len(recovered) == 1 and registered == []
    assert recovered[0][1] == {'legacy_v5_uncertain': True}


def test_old_v5_recovery_page_explains_and_disables_auth(monkeypatch):
    from types import SimpleNamespace
    from nicegui import Client, app, ui
    from nicegui.page import page as Page
    from pixivvault_nicegui import native_startup

    pages, labels, allowed = {}, [], []
    monkeypatch.setattr(ui, 'page', lambda route: lambda fn: pages.setdefault(route, fn))
    monkeypatch.setattr(app, 'get', lambda *a, **kw: lambda fn: fn)
    monkeypatch.setattr(app, 'on_shutdown', lambda fn: None)
    original = ui.label
    def capture(text=''):
        labels.append(text)
        return original(text)
    monkeypatch.setattr(ui, 'label', capture)
    binding = SimpleNamespace(create_view=lambda *, allow_auth: (
        allowed.append(allow_auth) or SimpleNamespace(build=lambda: None, close=lambda: None)),
        close=lambda: None)
    native_startup.register_profile_recovery(binding, legacy_v5_uncertain=True)
    client = Client(Page('/generated-old-v5-recovery'))
    try:
        with client:
            pages['/']()
        assert allowed == [False]
        assert any('旧形式' in s and 'バックアップから復元' in s for s in labels)
    finally:
        client.delete()
