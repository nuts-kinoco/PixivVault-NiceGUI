"""New payload-corruption cases only; generated files, no GUI or remote I/O."""
from hashlib import sha256
from types import SimpleNamespace as NS

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.duplicate_view import DuplicateView
from pixivvault_nicegui import managed_quarantine as mq, legacy_quarantine as lq
from test_managed_quarantine import owned, plan_for
from test_required_legacy_duplicates import setup, select


def files(root):
    return {p.relative_to(root).as_posix(): sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


@pytest.fixture(params=['managed', 'managed-move', 'legacy'])
def trash(request, tmp_path):
    if request.param.startswith('managed'):
        gen = owned.__wrapped__(tmp_path)
        owner, source, remote = next(gen)
        move = request.param == 'managed-move'
        source.execute(plan_for(source, '123:2', '123:3', '223:1'), operation_id='a'*32,
                       mode='move' if move else 'copy', confirmed_move=move)
        paths = sorted((owner.quarantine.operations / ('a'*32)).glob('*.png'))
        store = owner.quarantine
        finish = lambda: next(gen, None)
    else:
        source, store, owner, *_ = setup(tmp_path)
        source.execute(select(source, {'1', '2', '3'}), operation_id='a'*32)
        state = store.state()
        paths = [store._path(item['copy']) for item in state['exclusions'].values()]
        finish = lambda: (source.close(), owner.close())
    try:
        yield request.param, owner, source, store, paths, tmp_path
    finally:
        finish()


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['size', 'sha'])
async def test_bad_payload_retains_healthy_summary_and_real_open_route(trash, damage, monkeypatch):
    kind, owner, source, store, paths, root = trash
    expected = paths[0].stat().st_size
    if damage == 'size':
        paths[1].write_bytes(b'changed length')
    else:
        data = paths[1].read_bytes()
        paths[1].write_bytes(bytes([data[0] ^ 1]) + data[1:])
    paths[2].unlink()
    before = files(root)
    if kind == 'legacy':
        recover = store._physical.recover
        monkeypatch.setattr(store._physical, 'recover', lambda **kw: pytest.fail('summary must not recover/write'))
    snapshot = source.quarantine_snapshot()
    assert (snapshot.copy_count, snapshot.copy_bytes, snapshot.missing_count,
            snapshot.corrupt_count, snapshot.excluded_pages) == (1, expected, 1, 1, 3)
    opened = []
    service = NS(closing=False, duplicate_inspection=source,
                 desktop=NS(open_saved=lambda path: opened.append(path)))
    async def open_trash(**kwargs):
        await ApplicationServices.open_duplicate_quarantine(service, **kwargs)
    client = Client(ui.page('/x09-new-payload-' + kind + damage))
    view = None
    try:
        with client:
            view = DuplicateView(source, open_trash=open_trash)
            view.set_active(True)
            await view.open_trash()
            assert view._trash_state == 'ready' and view.trash_open_button.enabled
            assert view.trash_copy_count.text == '検証できた健全な隔離コピー: 1件'
            assert '実測容量' in view.trash_copy_bytes.text
            assert '見つからない隔離コピー: 1件' in view.trash_status.text
            assert '一致しない隔離コピー: 1件' in view.trash_status.text
            await view.open_trash_location()
        assert opened == [source.quarantine_location()]
        assert files(root) == before
        # Mutation verification is unchanged and must reject the damaged payload.
        if kind.startswith('managed'):
            state = owner._generations.state_snapshot(verify_quarantine_payloads=False)
            manifest = store._manifest('a'*32, state['operations']['a'*32])
            with pytest.raises(ValueError): store._verify_copies(manifest)
        else:
            monkeypatch.setattr(store._physical, 'recover', recover)
            with pytest.raises(ValueError): store.state()
    finally:
        if view: view.close()
        client.delete()


def test_normal_summary_is_measured_without_side_effects(trash):
    kind, owner, source, store, paths, root = trash
    before = files(root)
    snap = source.quarantine_snapshot()
    assert snap.copy_count == 3 and snap.copy_bytes == sum(p.stat().st_size for p in paths)
    assert snap.corrupt_count == snap.missing_count == 0
    assert files(root) == before


@pytest.mark.asyncio
async def test_all_payloads_corrupt_reports_no_healthy_copies_not_empty(trash):
    kind, owner, source, store, paths, root = trash
    for path in paths:
        raw = path.read_bytes()
        path.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
    before = files(root)
    snapshot = source.quarantine_snapshot()
    assert (snapshot.copy_count, snapshot.copy_bytes, snapshot.corrupt_count,
            snapshot.missing_count, snapshot.excluded_pages) == (0, 0, 3, 0, 3)
    client = Client(ui.page('/x09-all-corrupt-' + kind))
    view = None
    try:
        with client:
            view = DuplicateView(source)
            view.set_active(True)
            await view.open_trash()
            assert view._trash_state == 'ready'
            assert view.trash_status.text.startswith('健全な隔離コピーはありません。')
            assert '一致しない隔離コピー: 3件' in view.trash_status.text
            assert 'ごみ箱に隔離コピーはありません。' not in view.trash_status.text
            assert view.trash_copy_count.text == '検証できた健全な隔離コピー: 0件'
            assert view.trash_excluded.text == '通常の一覧から除外中: 3ページ'
        assert files(root) == before
    finally:
        if view: view.close()
        client.delete()


@pytest.mark.parametrize('damage', ['manifest', 'root', 'revision', 'index'])
def test_structural_and_revision_failures_still_reject_whole_summary(trash, damage, monkeypatch):
    kind, owner, source, store, paths, root = trash
    if damage == 'manifest':
        path = (store.operations / ('a'*32) / 'manifest.json' if kind.startswith('managed')
                else store._path('operations/' + 'a'*32 + '/state.json'))
        path.write_bytes(b'changed manifest')
    elif damage == 'root':
        marker = store.root / (mq._QMARKER if kind.startswith('managed') else 'IDENTITY.json')
        marker.write_bytes(b'changed identity')
    elif damage == 'index':
        path = owner._generations.index_path if kind.startswith('managed') else store.root/'CURRENT'
        path.write_bytes(b'changed index')
    else:
        target = owner._generations if kind.startswith('managed') else store
        name = 'state_snapshot' if kind.startswith('managed') else 'state'
        original = getattr(target, name)
        calls = []
        def changing(*a, **kw):
            value = original(*a, **kw); calls.append(True)
            if len(calls) > 1: value = {**value, 'revision': 'b'*32}
            return value
        monkeypatch.setattr(target, name, changing)
    before = files(root)
    with pytest.raises(ValueError): source.quarantine_snapshot()
    assert files(root) == before


def test_managed_restore_still_refuses_payload_corruption(owned):
    owner, source, _ = owned
    result = source.execute(plan_for(source, '123:2'), operation_id='c'*32,
                            mode='move', confirmed_move=True)
    path = owner.quarantine.operations / result.operation_id / '123-p2.png'
    revision = owner._generations.state_snapshot()['revision']
    raw = path.read_bytes()
    path.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
    before = files(owner.root)
    with pytest.raises(ValueError):
        source.restore_move(result.operation_id, expected_revision=revision)
    assert files(owner.root) == before
