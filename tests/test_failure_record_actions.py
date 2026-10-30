"""Q18/Q19 local list removal through application services and real UI class."""
import asyncio
from dataclasses import replace
from hashlib import sha256

from nicegui import Client, ui
import pytest

from pixivvault_nicegui.failed_record_dismissals import FailureDismissalConflict, FailureDismissalError
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.failed_records_view import FailedRecordsView


@pytest.mark.asyncio
async def test_snapshot_dismiss_restart_and_foreign_owner(tmp_path):
    preferences = tmp_path/'preferences'
    service = ApplicationServices(preferences_root=preferences)
    try:
        assert service.failed_record_removal_available
        snapshot = await service.read_failed_records_view()
        assert len(snapshot.records) == 17
        assert service.dismiss_failed_records(snapshot, (snapshot.records[0],)) == 1
        current = await service.read_failed_records_view()
        assert len(current.records) == 16 and current.revision == 1
        with pytest.raises(FailureDismissalConflict):
            service.dismiss_failed_records(snapshot, (snapshot.records[1],))
        with pytest.raises(FailureDismissalError):
            service.dismiss_failed_records(replace(current, _owner=object()), (current.records[0],))
        assert len(await service.read_failed_records()) == 17, 'raw immutable retry source remains intact'
    finally:
        service.close()
    restarted = ApplicationServices(preferences_root=preferences)
    try:
        assert len((await restarted.read_failed_records_view()).records) == 16
        assert len(await restarted.read_failed_records()) == 17
    finally:
        restarted.close()


@pytest.mark.asyncio
async def test_two_services_refuse_cross_client_stale_clear(tmp_path):
    first = ApplicationServices(preferences_root=tmp_path/'preferences')
    second = ApplicationServices(preferences_root=tmp_path/'preferences')
    try:
        a, b = await first.read_failed_records_view(), await second.read_failed_records_view()
        first.dismiss_failed_records(a, (a.records[0],))
        with pytest.raises(FailureDismissalConflict):
            second.dismiss_failed_records(b, b.records)
        fresh = await second.read_failed_records_view()
        second.dismiss_failed_records(fresh, fresh.records)
        assert (await first.read_failed_records_view()).records == ()
        assert len(await first.read_failed_records()) == 17
    finally:
        second.close()
        first.close()


@pytest.mark.asyncio
async def test_managed_original_failure_copy_kept_byte_identical(tmp_path):
    from test_legacy_failed_managed_retry import make_snapshot, OfflinePixiv
    original, copied = make_snapshot(tmp_path)
    # The fixture's writer context releases on exit/GC; explicitly only the
    # opened app reads immutable source. No new original writers are used.
    signature = {path:(sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
                 for path in (original, copied)}
    service = ApplicationServices(live_client=OfflinePixiv(), pixiv_download_root=tmp_path/'managed',
                                  failed_queue_readonly_db=copied)
    try:
        snapshot = await service.read_failed_records_view()
        service.dismiss_failed_records(snapshot, snapshot.records)
        assert (await service.read_failed_records_view()).records == ()
        assert len(await service.read_failed_records()) == 4
        assert service.jobs.snapshot() == ()
    finally:
        service.close()
    assert {path:(sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
            for path in signature} == signature


@pytest.mark.asyncio
@pytest.mark.parametrize('decision', ['cancel','confirm','changed-view'])
async def test_clear_confirmation_captures_all_records_not_filter(tmp_path, monkeypatch, decision):
    from pixivvault_nicegui.ui import failed_records_view as module
    service = ApplicationServices(preferences_root=tmp_path/'preferences')
    client = Client(ui.page('/_failure_local_delete'))
    captured, prompts = [], []
    monkeypatch.setattr(module.background_tasks, 'create', captured.append)
    try:
        with client:
            view = FailedRecordsView(service)
            await view._refresh(view.state.begin_refresh())
            view.select_category('format')
            assert len(view.state.matched_records) == 3
            assert len(view.state.records) == 17
            async def confirm(count):
                prompts.append(count)
                if decision == 'changed-view':
                    view.search('something else')
                return decision != 'cancel'
            monkeypatch.setattr(view, '_confirm_remove', confirm)
            view.request_remove()
            assert len(captured) == 1
            assert await captured.pop(0) is (decision == 'confirm')
            while captured:
                await captured.pop(0)
            assert prompts == [17]
            assert len((await service.read_failed_records_view()).records) == (
                0 if decision == 'confirm' else 17)
            assert len(await service.read_failed_records()) == 17
    finally:
        for coroutine in captured:
            coroutine.close()
        client.delete()
        service.close()
