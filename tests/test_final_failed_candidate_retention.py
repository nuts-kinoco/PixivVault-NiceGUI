"""Instruction 5 item 5: generated real ZIP, service, and UI callback path."""
from required_network_guard import no_network
import pytest
from nicegui import Client, ui
from pixivvault_nicegui import duplicate_inspection as module
from pixivvault_nicegui.ui.duplicate_view import DuplicateView
from test_required_legacy_duplicates import setup, png
from zipfile import ZipFile

@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['physical-intent:2:1','physical-installed:2:1'])
async def test_failed_item_remains_visible_selected_and_explicitly_retryable_after_bridge_success(tmp_path,monkeypatch,fault):
    source,legacy,owner,_,_,archive=setup(tmp_path)
    client=Client(ui.page('/_final_failed_candidate'))
    try:
        with client:
            view=DuplicateView(source);view.set_active(True)
            values=iter((0,0,0,15,255))
            with monkeypatch.context() as patch:
                patch.setattr(module,'dhash',lambda _:next(values))
                await view.scan()
            view.set_mode('near')
            group=next(g for g in view.scan_result.groups if g.author_id=='92')
            for key in ('2:1','3:1'):view.toggle(group.group_id,key,True,view.selection_revision)
            await view.confirm()
            def fail(stage):
                if stage==fault:raise PermissionError('generated item failure')
            with monkeypatch.context() as patch:
                patch.setattr(legacy,'_checkpoint',fail)
                await view.execute_confirmation()
            result=view._last_result
            assert {p.key for p in result.failed}=={'2:1'}
            assert {p.key for p in result.committed}=={'3:1'}
            assert not result.unprocessed and not result.recovery_required
            current=next(g for g in view.scan_result.groups if g.author_id=='92')
            assert {p.key for p in current.items}=={'2:1','3:2'}
            assert view.selections[current.group_id]=={'2:1'}
            labels=[str(getattr(n,'text','')) for n in view.result_rows.descendants()]
            assert any('Zip(2).png' in s and '失敗' in s for s in labels)
            assert any('Pages(3)_1.png' in s and '成功' in s for s in labels)
            assert any(getattr(n,'value',None) is True for n in view.rows.descendants() if isinstance(n,ui.checkbox))
            assert '失敗1枚' in view.notice.text
            with ZipFile(archive) as z:
                assert z.read('Zip(2).png')==png()
                assert 'Pages(3)_1.png' not in z.namelist()
                assert z.read('Pages(3)_2.png')==png()
            before=archive.read_bytes()
            view.render();view.set_mode('undecidable');view.set_mode('near')
            assert view.selections[current.group_id]=={'2:1'} and archive.read_bytes()==before
            await view.confirm()
            assert archive.read_bytes()==before  # Preview does not retry the failure.
            await view.execute_confirmation()
            assert {p.key for p in view._last_result.committed}=={'2:1'}
            assert not view._last_result.failed
            with ZipFile(archive) as z:
                assert 'Zip(2).png' not in z.namelist()
                assert z.read('Pages(3)_2.png')==png()
            view.close()
    finally:
        client.delete();source.close();owner.close()

