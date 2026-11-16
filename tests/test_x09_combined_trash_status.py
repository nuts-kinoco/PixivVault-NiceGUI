"""The trash message and totals must describe the same validated stores."""
import pytest
from nicegui import Client, ui

from pixivvault_nicegui.ui.duplicate_view import DuplicateView
from test_required_legacy_duplicates import setup, select


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['copy', 'move'])
async def test_legacy_only_quarantine_is_ready_in_combined_trash(tmp_path, mode):
    source, legacy, owner, *_ = setup(tmp_path)
    client = Client(ui.page('/_x09_combined'))
    try:
        with client:
            view = DuplicateView(source)
            view.set_active(True)
            await view.open_trash()
            assert 'まだ作成されていません' in view.trash_status.text
            assert view.trash_copy_count.text.endswith('0件')
            view.close_trash()
            plan = select(source, {'1'})
            result = source.execute(plan, operation_id='a' * 32, mode=mode,
                                    confirmed_move=mode == 'move')
            assert len(result.committed) == 1 and not result.failed
            assert owner.quarantine.snapshot().state == 'uncreated'
            snapshot = source.quarantine_snapshot()
            assert snapshot.state == 'ready'
            assert snapshot.copy_count == 1 and snapshot.copy_bytes > 0
            assert snapshot.excluded_pages == 1 and snapshot.location == owner.root
            await view.open_trash()
            assert view.trash_status.text == 'ごみ箱の内容を確認しました。'
            assert view.trash_copy_count.text == '公開済みの隔離コピー: 1件'
            assert view.trash_excluded.text == '通常の一覧から除外中: 1ページ'
            await view.refresh_trash()
            assert 'まだ作成されていません' not in view.trash_status.text
    finally:
        client.delete()
        source.close()
        owner.close()


def test_existing_empty_legacy_trash_is_empty_not_uncreated(tmp_path):
    source, legacy, owner, *_ = setup(tmp_path)
    try:
        # An owned but empty directory is distinct from no directory at all.
        with legacy._lock:
            legacy._ensure()
        snapshot = source.quarantine_snapshot()
        assert snapshot.state == 'ready'
        assert snapshot.copy_count == snapshot.copy_bytes == snapshot.excluded_pages == 0
    finally:
        source.close()
        owner.close()
