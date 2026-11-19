"""Account handoff regressions from the independent review of 6929397."""
from datetime import datetime, timedelta, timezone
from threading import Event, RLock
from types import SimpleNamespace as NS

import pytest
from nicegui import Client, ui

from account_test_support import observed_scope
from pixivvault_nicegui.account_scope import AccountMismatch
from pixivvault_nicegui.author_media import AuthorMediaService
from pixivvault_nicegui.batch_engine import BatchRunEngine, ServicesPort
from pixivvault_nicegui.batch_runs import BatchRunLedger
from pixivvault_nicegui.favourites import FavouriteBook
from pixivvault_nicegui.following_sync import FollowingSyncStore
from pixivvault_nicegui.managed_scheduler import ManagedScheduler, ManagedScheduleRunner, ScheduleAccountError
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.scheduler_view import SchedulerView
from pixivvault_nicegui.ui.unified_download_view import UnifiedDownloadView

from test_w_account_recheck_r2_20261119 import use_media_dispatch
from test_w_account_separation_20261119 import A, B, X, Y, Reader, resources, stars, switch


def services(root, following, reader, ledger):
    root.mkdir(exist_ok=True)
    svc = NS(closing=False, following_sync=following, live_client=reader,
             managed_network_stop=None, preferences=None,
             managed_downloads=NS(root=root), jobs=NS(snapshot=lambda: ()))
    svc.author_media = AuthorMediaService(svc)
    svc.batch_runs = BatchRunEngine(ServicesPort(svc), ledger, following=following)
    svc.batch_run_ledger = ledger
    for name in ("start_batch_run", "resume_batch_run", "discard_batch_run", "unified_download_authors",
                 "unified_download_author_snapshot",
                 "author_media_preview_owned"):
        setattr(svc, name, getattr(ApplicationServices, name).__get__(svc))
    return svc


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ("discard", "resume"))
async def test_other_account_cannot_change_author_run_history_after_restart(tmp_path, action):
    root, following_path, batch_path = (tmp_path / name for name in ("managed", "following", "batch"))
    a_reader = Reader(A)
    entered_listing, allow_listing = Event(), Event()

    def listing(_author):
        entered_listing.set()
        assert allow_listing.wait(5)
        return ()

    a_reader.get_author_artworks_complete = listing
    a_reader.get_author_novel_ids = lambda _author: ()
    following = FollowingSyncStore(following_path)
    ledger = BatchRunLedger(batch_path)
    a_svc = services(root, following, a_reader, ledger)
    try:
        following.sync(a_reader)
        a_run_id = a_svc.start_batch_run((X,), target_type="illust", account_id=A).run_id
        assert entered_listing.wait(5)
        a_svc.batch_runs.stop(ledger.run(a_run_id).handle)
        allow_listing.set()
        assert a_svc.batch_runs.join(5)
        assert ledger.run(a_run_id).state == "stopped"
        assert ledger.run(a_run_id).account_id == A
    finally:
        allow_listing.set()
        a_svc.batch_runs._shutdown(0)
        assert a_svc.batch_runs.join(5)
        ledger.close()
        following.close()

    b_reader = Reader(B)
    requested = []
    b_reader.get_author_artworks_complete = lambda author: requested.append(author) or ()
    b_reader.get_author_novel_ids = lambda _author: ()
    following = FollowingSyncStore(following_path)
    ledger = BatchRunLedger(batch_path)
    b_svc = services(root, following, b_reader, ledger)
    client = Client(ui.page(f"/r10-other-account-{action}"))
    view = None
    try:
        following.sync(b_reader)
        with client:
            view = UnifiedDownloadView(b_svc)
            await view.reload()
        assert view.authors and {row["account_id"] for row in view.authors} == {B}
        shown = view.resumable is not None and view.resumable.run_id == a_run_id
        if shown and action == "discard" and view.discard_button.visible:
            assert view.discard_interrupted() is False
            assert "再読込" in view.notice_text
        elif shown and action == "resume" and view.resume_button.visible:
            assert await view.resume_interrupted() is False
            assert "再読込" in view.notice_text
        assert b_svc.batch_runs.join(5)
        assert not requested
    finally:
        if view is not None:
            view.close()
        client.delete()
        b_svc.batch_runs._shutdown(0)
        assert b_svc.batch_runs.join(5)
        ledger.close()
        following.close()

    # Reopen on A again and read the durable records.
    following = FollowingSyncStore(following_path)
    ledger = BatchRunLedger(batch_path)
    try:
        following.sync(a_reader)
        rows = ledger.runs()
        offered = ledger.resumable_run(account_id=A)
        print(f"action={action} B-view-shows-A={shown} runs="
              f"{[(row.account_id, row.state, row.error_code) for row in rows]} "
              f"original-A-resumable={offered is not None and offered.run_id == a_run_id}")
        assert len(rows) == 1 and rows[0].run_id == a_run_id
        assert offered is not None and offered.run_id == a_run_id
    finally:
        ledger.close()
        following.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ("acknowledge", "refresh"))
async def test_b_scheduler_ui_leaves_a_interruption_and_notices(tmp_path, action):
    with resources(tmp_path) as (root, store, a_ledger, a_reader, svc):
        store.sync(a_reader)
        store.set_favorite(A, X, True)
        assert svc.scheduler_favourite_authors(ledger=a_ledger) == (X,)
        now = datetime.now(timezone.utc)

        if action == "acknowledge":
            slot = a_ledger._claim_due(now + timedelta(days=2))
            assert slot and a_ledger.snapshot().last_status == "interrupted"
        else:
            slot = "manual-fixture"
            a_ledger.begin_dispatch(slot, now, 1, account_id=A)
            a_ledger.prepare_dispatch_item(slot=slot, work_id="200", author_id=X, operation_id="operation", account_id=A)
            a_ledger.bind_dispatch_job(slot=slot, work_id="200", operation_id="operation",
                                       job_id="job", accepted_fingerprint="verified-fingerprint", account_id=A)
            a_ledger.finish_dispatch(slot, status="queued", accepted=1, deferred=0, account_id=A)
            saved = a_ledger.reconcile_jobs([NS(job_id="job", state="done")], {"200": "verified-fingerprint"}, account_id=A)
            assert saved == ("200",) and a_ledger.pending_notifications() == ("200",)

        a_ledger.close()
        b_reader = Reader(B)
        store.sync(b_reader)
        ledger = ManagedScheduler(root)
        svc.live_client = b_reader
        svc.managed_scheduler = ledger
        ledger.attach_favourites(FavouriteBook(store, scheduler=ledger))
        svc.managed_schedule_runner = ManagedScheduleRunner(ledger, lambda *_: None)
        svc.managed_downloads = NS(root=root)
        svc.jobs = NS(snapshot=lambda: ())
        svc._scheduler_dispatch_lock = RLock()
        svc.reconcile_managed_scheduler = ApplicationServices.reconcile_managed_scheduler.__get__(svc)

        client = Client(ui.page(f"/r10-scheduler-history-{action}"))
        with client:
            view = SchedulerView(svc)
        try:
            view._load(ledger)
            assert view._settings_account == B and ledger._bound_account() == A
            if action == "acknowledge":
                await view.acknowledge()
                assert "再読込" in view.action_notice.text
            else:
                await view.refresh()
        finally:
            view.close()
            client.delete()
            ledger.close()

        ledger = ManagedScheduler(root)
        try:
            snapshot = ledger.snapshot()
            pending = ledger.pending_notifications()
            print(f"action={action} owner-is-A={ledger._bound_account() == A} "
                  f"last-status={snapshot.last_status} notices-pending={len(pending)}")
            if action == "acknowledge":
                assert snapshot.last_status == "interrupted"
            else:
                assert pending == ("200",)
                assert ledger.claim_pending_notifications(account_id=A) == ("200",)
                assert ledger.pending_notifications() == ()
        finally:
            ledger.close()


def test_periodic_check_can_run_for_bound_a_after_return_from_b(tmp_path):
    with resources(tmp_path) as (root, store, schedule, a_reader, initial):
        store.sync(a_reader)
        store.set_favorite(A, X, True)
        assert initial.scheduler_favourite_authors(ledger=schedule) == (X,)
        store.sync(Reader(B))
        assert store.last_synced_account() == B

        # Application is opened again for A. The download-screen follow cache
        # still shows the last completed sync (B); the timer still owns A.
        reader = Reader(A)
        calls = []
        reader.get_author_artworks_complete = lambda author: calls.append(author) or ()
        reader.get_author_novel_ids = lambda _author: ()
        batch = BatchRunLedger(tmp_path / "batch")
        svc = services(root, store, reader, batch)
        svc.scheduler_favourite_authors = ApplicationServices.scheduler_favourite_authors.__get__(svc)
        svc.managed_scheduler = schedule
        error = None
        try:
            assert svc.scheduler_favourite_authors(ledger=schedule) == (X,)
            try:
                ApplicationServices.dispatch_author_check(svc, schedule, "due-a", Event())
            except RuntimeError as rejected:
                error = type(rejected).__name__
            assert svc.batch_runs.join(5)
            print(f"verified-owner=A latest-sync=B refusal={error} accepted-listings={len(calls)} "
                  f"created-runs={len(batch.runs())}")
            assert error is None
            assert calls == [X]
            rows = batch.runs()
            assert len(rows) == 1 and rows[0].state == "done" and rows[0].account_id == A
        finally:
            svc.batch_runs._shutdown(0)
            assert svc.batch_runs.join(5)
            batch.close()


@pytest.mark.asyncio
async def test_check_now_records_mismatch_without_finishing_dispatch(tmp_path):
    with resources(tmp_path) as (root, store, ledger, reader, svc):
        store.sync(reader)
        store.set_favorite(A, X, True)
        store.record_downloaded(A, X, "2026-01-01T00:00:00+00:00", scope=observed_scope(A))
        before = (store.snapshot(A), store.snapshot(B))
        batch = BatchRunLedger(tmp_path / "batch")
        engine = BatchRunEngine(ServicesPort(svc), batch, following=store)
        svc.batch_runs = engine
        svc._scheduler_dispatch_lock = RLock()
        svc._scheduler_cancel_event = Event()
        calls = use_media_dispatch(root, reader, svc)
        svc.dispatch_author_check = ApplicationServices.dispatch_author_check.__get__(svc)
        client = Client(ui.page("/review-check-now-history"))
        with client:
            view = SchedulerView(svc)
        try:
            view._load(ledger)
            await view.run_once()
            assert engine.join(5)
            run = batch.run(engine.run_id)
            (author,) = batch.authors(run.run_id)
            (history,) = ledger.history()
            print(f"reader={reader._verified_identity} expected={B} calls={calls} "
                  f"run=({run.state},{run.finished_utc},{run.error_code}) "
                  f"dispatch=({history['status']},{history['finished_utc']},{history['error_code']})")
            assert reader._verified_identity == B and not calls
            assert run.state == "failed" and run.error_code == AccountMismatch.code
            assert run.finished_utc is None and author.finished_utc is None
            assert (store.snapshot(A), store.snapshot(B)) == before
            assert history["finished_utc"] is None and history["status"] == "failed"
        finally:
            view.close()
            client.delete()
            engine._shutdown(0)
            assert engine.join(5)
            batch.close()


@pytest.mark.asyncio
async def test_disable_automatic_check_before_first_follow_sync(tmp_path):
    with resources(tmp_path) as (root, store, ledger, reader, svc):
        client = Client(ui.page("/review-unsynced-off"))
        with client:
            view = SchedulerView(svc)
        try:
            view._load(ledger)
            assert view._stars_unknown_in_form and store.last_synced_account() is None
            view.interval.set_value("0")
            view.startup.set_value(False)
            await view.save()
            state = ledger.snapshot()
            print(f"configured={state.interval_hours} next_due_present={state.next_due_utc is not None} "
                  f"account={store.last_synced_account()} notice={view.action_notice.text}")
            assert not store.snapshot(A).authors and not store.snapshot(B).authors
            assert state.interval_hours == 0 and state.next_due_utc is None and not state.startup_check
        finally:
            view.close()
            client.delete()


@pytest.mark.asyncio
async def test_manual_check_rejects_stale_displayed_account_before_admission(tmp_path):
    with resources(tmp_path) as (root, store, ledger, reader, svc):
        store.sync(reader)
        store.set_favorite(A, X, True)
        assert svc.scheduler_favourite_authors() == (X,)
        calls = use_media_dispatch(root, reader, svc, switch_at=None)
        batch = BatchRunLedger(tmp_path / "batch")
        engine = BatchRunEngine(ServicesPort(svc), batch, following=store)
        svc.batch_runs = engine
        svc._scheduler_dispatch_lock = RLock()
        svc._scheduler_cancel_event = Event()
        svc.dispatch_author_check = ApplicationServices.dispatch_author_check.__get__(svc)
        client = Client(ui.page("/review-stale-manual-check"))
        with client:
            view = SchedulerView(svc)
        try:
            view._load(ledger)
            assert view._settings_account == A
            switch(store, reader)
            reader._verified_identity = A  # A is verified, but this displayed form is stale.
            await view.run_once()
            assert engine.join(5)
            assert not calls and not batch.runs() and not ledger.history()
            assert "再読込" in view.action_notice.text
        finally:
            view.close()
            client.delete()
            engine._shutdown(0)
            assert engine.join(5)
            batch.close()


@pytest.mark.asyncio
async def test_stop_and_explicit_star_edit_are_saved_for_the_displayed_account(tmp_path):
    with resources(tmp_path) as (_, store, ledger, reader, svc):
        store.sync(reader)
        store.set_favorite(A, X, True)
        client = Client(ui.page("/review-off-with-star-edit"))
        with client:
            view = SchedulerView(svc)
        try:
            view._load(ledger)
            assert not view._stars_unknown_in_form and view._settings_account == A
            view.interval.set_value("0")
            view.startup.set_value(False)
            view.authors.set_value(f"{X},{Y}")
            await view.save()
            assert ledger.snapshot().interval_hours == 0
            assert stars(store, A) == (X, Y)
        finally:
            view.close()
            client.delete()


def test_reopened_bound_history_requires_the_owner_before_attaching_following(tmp_path):
    with resources(tmp_path) as (root, store, ledger, reader, svc):
        store.sync(reader)
        assert svc.scheduler_favourite_authors() == ()
        now = datetime.now(timezone.utc)
        ledger.begin_dispatch("saved-slot", now, 1, account_id=A)
        ledger.close()
        reopened = ManagedScheduler(root)
        try:
            before = tuple(reopened._db.iterdump())
            with pytest.raises(ScheduleAccountError):
                reopened.finish_dispatch("saved-slot", status="empty", accepted=0, deferred=0, account_id=B)
            with pytest.raises(ScheduleAccountError):
                reopened.configure(interval_hours=6, startup_check=True,
                                   expected_revision=reopened.snapshot().revision, now=now, account_id=None)
            assert tuple(reopened._db.iterdump()) == before
        finally:
            reopened.close()


@pytest.mark.asyncio
async def test_displayed_account_can_discard_its_run_with_no_remaining_follows(tmp_path):
    root, following_path, batch_path = (tmp_path / name for name in ("managed", "following", "batch"))
    reader = Reader(A)
    reader.get_following_users_detailed = lambda *_: ()
    store = FollowingSyncStore(following_path)
    batch = BatchRunLedger(batch_path)
    svc = services(root, store, reader, batch)
    run_id = batch.begin_run("authors", "manual", "both", (X,), account_id=A).run_id
    batch.set_run_state(batch.run(run_id).handle, "stopped")
    client = Client(ui.page("/review-empty-following-history"))
    try:
        store.sync(reader)
        with client:
            view = UnifiedDownloadView(svc)
            await view.reload()
        assert view.authors == [] and view.resumable.run_id == run_id
        assert view.discard_interrupted() is True
        assert batch.resumable_run(account_id=A) is None
        view.close()
    finally:
        client.delete()
        batch.close()
        store.close()
