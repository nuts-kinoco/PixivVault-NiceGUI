"""X09: one missing trash entry must not turn the whole summary into a read failure."""
import shutil
from hashlib import sha256

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.ui.duplicate_view import DuplicateView
from test_managed_quarantine import owned, plan_for
from test_required_legacy_duplicates import setup, select


def files(root):
    return {p.relative_to(root).as_posix(): sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


@pytest.fixture(params=['payload-all-gone', 'operation-folder-gone', 'manifest-gone'])
def two_ops(request, tmp_path):
    gen = owned.__wrapped__(tmp_path)
    owner, source, remote = next(gen)
    source.execute(plan_for(source, '123:2'), operation_id='a' * 32, mode='copy')
    source.execute(plan_for(source, '223:1', '123:3'), operation_id='b' * 32, mode='copy')
    folder = owner.quarantine.operations / ('a' * 32)
    healthy = sorted((owner.quarantine.operations / ('b' * 32)).glob('*.png'))
    try:
        yield request.param, owner, source, folder, healthy, tmp_path
    finally:
        next(gen, None)


def _break(kind, folder):
    if kind == 'payload-all-gone':
        for p in folder.glob('*.png'):
            p.unlink()
    elif kind == 'operation-folder-gone':
        shutil.rmtree(folder)
    else:
        (folder / 'manifest.json').unlink()


def test_missing_operation_keeps_other_counts_and_reports_unconfirmable(two_ops):
    kind, owner, source, folder, healthy, root = two_ops
    expected = sum(p.stat().st_size for p in healthy)
    _break(kind, folder)
    before = files(root)
    snap = source.quarantine_snapshot()
    assert snap.state == 'ready'
    assert (snap.copy_count, snap.copy_bytes) == (2, expected)
    assert snap.missing_count + snap.unconfirmed_count == 1
    assert files(root) == before


@pytest.mark.asyncio
async def test_view_shows_unconfirmed_count_and_other_counts(two_ops):
    kind, owner, source, folder, healthy, root = two_ops
    _break(kind, folder)
    client = Client(ui.page('/x09-missing-' + kind))
    view = None
    try:
        with client:
            view = DuplicateView(source)
            view.set_active(True)
            await view.open_trash()
            assert view._trash_state == 'ready'
            assert view.trash_excluded.text.endswith('3ページ')
            assert '2件' in view.trash_copy_count.text
            assert '確認できない項目: 1件' in view.trash_status.text or '見つからない隔離コピー: 1件' in view.trash_status.text
    finally:
        if view: view.close()
        client.delete()
