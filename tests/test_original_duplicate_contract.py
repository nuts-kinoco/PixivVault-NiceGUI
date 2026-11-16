from required_network_guard import no_network
from dataclasses import replace
from types import SimpleNamespace
from hashlib import sha256
import pytest
from nicegui import Client, ui
from test_required_legacy_duplicates import setup, png
from pixivvault_nicegui.library_models import LibrarySnapshot, LibraryWork
from pixivvault_nicegui.models import Artwork


def test_x01_missing_logical_pages_do_not_consume_physical_scan_limit(tmp_path):
    source,legacy,owner,snapshot,*_=setup(tmp_path)
    try:
        missing=tuple(LibraryWork(Artwork(str(100+i),'Missing','Generated'), '91') for i in range(2001))
        expanded=LibrarySnapshot(snapshot.works+missing)
        legacy.original_snapshot=expanded;legacy.catalog.replace_snapshot(expanded)
        scan=source.scan()
        assert not scan.skipped_authors
        assert any(g.author_id=='91' for g in scan.groups)
        assert len(scan.unreadable)==2001
    finally:source.close();owner.close()


def test_x01_stream_digest_has_no_two_gib_container_rejection(tmp_path,monkeypatch):
    from pixivvault_nicegui import legacy_quarantine as module
    path=tmp_path/'archive';path.write_bytes(b'generated streaming contents')
    # Large container metadata must not reject a bounded-memory streaming hash.
    monkeypatch.setattr(module.os,'fstat',lambda _:SimpleNamespace(st_size=3*1024**3))
    assert module.digest_file(path)==sha256(path.read_bytes()).hexdigest()


@pytest.mark.asyncio
async def test_original_preview_and_row_controls_show_filename_size_and_impact(tmp_path):
    from pixivvault_nicegui.ui.duplicate_view import DuplicateView
    source,legacy,owner,*_=setup(tmp_path)
    client=Client(ui.page('/_original_duplicate_contract'))
    try:
        with client:
            view=DuplicateView(source);view.set_active(True);await view.scan();view.set_mode('near')
            group=next(g for g in view.scan_result.groups if g.author_id=='92')
            text=' '.join(str(getattr(n,'text','')) for n in view.rows.descendants())
            assert 'Zip(2).png' in text and 'bytes' in text
            assert group.group_id in view.group_low_buttons
            view.toggle(group.group_id,'2:1',True,view.selection_revision)
            assert '1組' in view.summary.text and 'bytes' in view.summary.text
            await view.confirm(group.group_id,view.selection_revision)
            assert 'Zip(2).png' in ' '.join(str(getattr(n,'text','')) for n in view.confirm_rows.descendants())
            assert view.relocation_choice.value is True
            assert '退避' in view.confirm_impact.text and 'ZIP' in view.confirm_impact.text
            view.cancel_confirmation();assert not legacy.state()['exclusions']
            view.close()
    finally:client.delete();source.close();owner.close()


@pytest.mark.asyncio
async def test_near_group_display_reports_remaining_count(tmp_path):
    from pixivvault_nicegui.ui.duplicate_view import DuplicateView
    source,legacy,owner,*_=setup(tmp_path)
    client=Client(ui.page('/_original_duplicate_remaining'))
    try:
        with client:
            view=DuplicateView(source);view.set_active(True);await view.scan();view.set_mode('near')
            group=view.scan_result.groups[0]
            view.scan_result=replace(view.scan_result,groups=tuple(replace(group,group_id=str(i)) for i in range(51)))
            view.render()
            assert '残り1件' in ' '.join(str(getattr(n,'text','')) for n in view.rows.descendants())
            view.close()
    finally:client.delete();source.close();owner.close()


@pytest.mark.asyncio
async def test_ui_reports_each_item_and_recovery_allows_explicit_retry(tmp_path,monkeypatch):
    from pixivvault_nicegui.ui.duplicate_view import DuplicateView
    from zipfile import ZipFile
    source,legacy,owner,_,original,archive=setup(tmp_path,extra_zip=True)
    client=Client(ui.page('/_original_duplicate_recovery_ui'))
    try:
        with client:
            view=DuplicateView(source);view.set_active(True);await view.scan();view.set_mode('near')
            for group in view.scan_result.groups:
                for item in group.items:
                    if item.key in {'1:1','2:1','5:1'}:view.toggle(group.group_id,item.key,True,view.selection_revision)
            await view.confirm()
            def fail(stage):
                if stage=='physical-installed:1:1':raise OSError('generated interruption')
            with monkeypatch.context() as patch:
                patch.setattr(legacy,'_checkpoint',fail)
                patch.setattr(legacy._physical,'_rollback',lambda _:(_ for _ in ()).throw(PermissionError('generated denial')))
                await view.execute_confirmation()
            text=' '.join(str(getattr(n,'text','')) for n in view.result_rows.descendants())
            assert 'Folder(1).png' in text and '失敗' in text and '未処理' in text
            assert view.legacy_recover_button.visible
            await view.recover_legacy()
            assert not view.legacy_recover_button.visible and (original/'Author(91)'/'Folder(1).png').exists()
            await view.confirm();await view.execute_confirmation()
            assert not (original/'Author(91)'/'Folder(1).png').exists()
            with ZipFile(archive) as z:assert 'Zip(2).png' not in z.namelist()
            assert len(view._last_result.committed)==3
            view.close()
    finally:client.delete();source.close();owner.close()
