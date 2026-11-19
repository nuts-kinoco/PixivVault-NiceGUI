"""Contracts for isolated account-scoped author operations."""
from contextlib import nullcontext
from datetime import datetime, timezone
from inspect import getmembers, isroutine
from threading import Event, RLock
from types import SimpleNamespace as NS

import pytest

from pixivvault_nicegui import unified_download_state as U
from pixivvault_nicegui.account_scope import AccountMismatch, AccountScope, BoundSchedulerLedger, OwnedListing
from pixivvault_nicegui.batch_engine import BatchRunEngine, ServicesPort
from pixivvault_nicegui.batch_runs import BatchRunError, BatchRunLedger
from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.favourites import FavouriteBook
from pixivvault_nicegui.following_sync import FollowingSyncStore
from pixivvault_nicegui.managed_scheduler import ManagedScheduler, ScheduleAccountError
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.unified_download_view import UnifiedDownloadView

from test_w_account_recheck_r2_20261119 import use_media_dispatch
from test_w_account_separation_20261119 import A, B, X, Y, resources

DATE = "2026-01-01T00:00:00+00:00"
WORK = dict(id="200", user_id=X, type=0, page_count=1, title="work", update_date=DATE)
HELD = dict(id="300", update_date=DATE, page_count=1, type=0)


def sync_accounts(store, reader):
    for account, author in ((B, Y), (A, X)):
        reader._verified_identity = account
        store.sync(reader)
        store.set_favorite(account, author, True)
        store.record_downloaded(account, X, DATE)


def dump(store, scheduler, batch):
    return tuple(tuple(db.iterdump()) for db in (store._connection, scheduler._db, batch._connection))


# Reflection is the denominator. Every public method added to one of these
# persistence boundaries must be classified here; an unclassified method fails.
# READ_ONLY means the method cannot change SQLite, including in-memory-only lifecycle.
READ_ONLY = {
    ManagedScheduler: {
        "attach_favourites": "connects an in-memory follow source",
        "close": "releases the connection and lock",
        "configured": "checks an existing path",
        "snapshot": "reads schedule settings",
        "favourite_account": "reads latest follow-sync owner",
        "favourites": "reads stars for the specified account",
        "legacy_favourites": "reads the standalone scheduler list",
        "deferred_ids": "reads pending work IDs",
        "deferred_count": "counts pending work",
        "has_deferred": "looks for pending work of starred authors",
        "dispatch_operation": "looks up an operation",
        "history": "reads dispatch records",
        "pending_notifications": "lists unclaimed completion notices",
        "reconcile_candidates": "reads candidate work IDs",
        "reconcile_expected_fingerprints": "reads saved receipt expectations",
    },
    BatchRunLedger: {
        "run": "reads one run",
        "runs": "reads run history",
        "active_run": "reads the currently active run",
        "resumable_run": "reads a candidate for resumption",
        "has_verified_bookmark_account": "reads the bookmark proof flag",
        "unfinished_authors": "reads incomplete rows",
        "authors": "reads author history",
        "items": "reads recorded work",
        "close": "releases the connection and lock",
    },
    FavouriteBook: {"account": "reads the latest follow-sync owner", "ids": "reads one account's stars"},
    FollowingSyncStore: {
        "snapshot": "reads one account's snapshot",
        "last_synced_account": "reads the latest completed sync owner",
        "close": "releases the connection and lock",
    },
    BoundSchedulerLedger: {},
}

OWNER_BOUND = {
    ManagedScheduler: {
        "touch_revision", "configure", "claim_due", "complete", "acknowledge_interrupted",
        "begin_dispatch", "finish_dispatch", "update_dispatch_progress", "prepare_dispatch_item",
        "bind_dispatch_job", "fail_dispatch_item", "defer_work", "resolve_deferred", "reconcile_jobs",
        "claim_pending_notifications", "mark_notified", "set_favourites", "set_star",
        "configure_all", "bind_account", "sync_favourites",
    },
    BatchRunLedger: {
        "begin_run", "set_run_state", "recover_interrupted", "discard_run", "update_author",
        "complete_author", "complete_bookmark_row", "finish_author", "record_item", "record_items",
    },
    FavouriteBook: {"set"},
    FollowingSyncStore: {
        "sync", "clear_account", "record_followed", "record_unfollowed", "set_favorite",
        "replace_favorites", "record_downloaded",
    },
    BoundSchedulerLedger: {"defer_work", "resolve_deferred"},
}


def public_methods(cls):
    return {name for name, value in getmembers(cls, isroutine) if not name.startswith("_")}


@pytest.mark.parametrize("cls", READ_ONLY, ids=lambda cls: cls.__name__)
def test_every_public_method_has_one_classification(cls):
    reads, writes = READ_ONLY[cls], OWNER_BOUND[cls]
    assert not set(reads).intersection(writes)
    assert all(reads.values())
    assert public_methods(cls) == set(reads).union(writes)


# Register every new scoped read or durable write exit here.
# An explicit ID can read its own rows. An OwnedListing cannot stand in for an ID.
SINKS = (
    ("book.ids", lambda c, aid, listing: c.book.ids(aid), False),
    ("book.set", lambda c, aid, listing: c.book.set(X, False, account_id=aid), True),
    ("scheduler.favourites", lambda c, aid, listing: c.ledger.favourites(aid), False),
    ("scheduler.set_favourites", lambda c, aid, listing: c.ledger.set_favourites(
        (), expected_revision=c.revision, account_id=aid), True),
    ("scheduler.set_star", lambda c, aid, listing: c.ledger.set_star(c.book, X, False, account_id=aid), True),
    ("scheduler.configure_all", lambda c, aid, listing: c.ledger.configure_all(
        author_ids=(), interval_hours=6, startup_check=True, expected_revision=c.revision,
        now=c.now, account_id=aid), True),
    ("scheduler.configure", lambda c, aid, listing: c.ledger.configure(
        interval_hours=6, startup_check=True, expected_revision=c.revision,
        now=c.now, account_id=aid), True),
    ("scheduler.defer_work", lambda c, aid, listing: c.ledger.defer_work(
        author_id=X, work=WORK, now=c.now, account_id=aid), True),
    ("scheduler.resolve_deferred", lambda c, aid, listing: c.ledger.resolve_deferred(
        X, HELD["id"], account_id=aid), True),
    ("batch.complete_author", lambda c, aid, listing: c.batch.complete_author(
        c.run, X, listing=listing, following=c.store), True),
)


@pytest.mark.parametrize("name,invoke,uses_account_id", SINKS, ids=[entry[0] for entry in SINKS])
@pytest.mark.parametrize("evidence", ("absent", "unbound", "other_account"))
def test_account_sinks_reject_without_writing_any_table(tmp_path, name, invoke, uses_account_id, evidence):
    with resources(tmp_path) as (_, store, ledger, reader, svc):
        sync_accounts(store, reader)
        now = datetime.now(timezone.utc)
        ledger.bind_account(A)
        ledger.defer_work(author_id=X, work=HELD, now=now, account_id=A)
        batch = BatchRunLedger(tmp_path / "batch")
        try:
            run = batch.begin_run("authors", "manual", "both", (X,), account_id=A)
            context = NS(book=svc.favourite_book(), ledger=ledger, batch=batch, run=run,
                         store=store, now=now, revision=ledger.snapshot().revision)
            unbound = OwnedListing(AccountScope(A), X, "both", ())
            other = OwnedListing(AccountScope(B, NS(_verified_identity=B)), X, "both", ())
            proof = {"absent": None, "unbound": unbound, "other_account": other}[evidence]
            account_arg = B if uses_account_id and evidence == "other_account" else proof
            before = dump(store, ledger, batch)
            with pytest.raises((AccountMismatch, ScheduleAccountError)):
                invoke(context, account_arg, proof)
            assert dump(store, ledger, batch) == before, name
        finally:
            batch.close()


def test_unbound_scheduler_and_bookmark_only_exit_cannot_write_author_state(tmp_path):
    with resources(tmp_path) as (_, store, ledger, reader, svc):
        sync_accounts(store, reader)
        batch = BatchRunLedger(tmp_path / "batch")
        try:
            run = batch.begin_run("authors", "manual", "both", (X,), account_id=A)
            before = dump(store, ledger, batch)
            with pytest.raises(ScheduleAccountError):
                ledger.defer_work(author_id=X, work=HELD, now=datetime.now(timezone.utc), account_id=A)
            with pytest.raises(ScheduleAccountError):
                ledger.resolve_deferred(X, HELD["id"], account_id=A)
            with pytest.raises(BatchRunError):
                batch.complete_bookmark_row(run, X)
            assert dump(store, ledger, batch) == before
        finally:
            batch.close()


def author_view(svc, targets):
    view = NS(mode="authors", targets=targets, services=svc, target_type="both",
              target_ids=lambda: [row["user_id"] for row in targets],
              run_block_reason=lambda: "", notices=[], refresh_chip_status=lambda: None,
              render_controls=lambda: None, _render_band=lambda: None)
    view.set_notice = view.notices.append
    return view


def test_manual_start_refuses_stale_follow_target_before_creating_run(tmp_path):
    with resources(tmp_path) as (_, store, ledger, reader, svc):
        sync_accounts(store, reader)
        started = []
        svc.batch_runs = NS(account_id=None, start=lambda *args, **kwargs: started.append(kwargs))
        svc.start_batch_run = ApplicationServices.start_batch_run.__get__(svc)
        row = next(row for row in ApplicationServices.unified_download_authors(svc) if row["user_id"] == X)
        view = author_view(svc, U.add_targets_bulk([], [row]))
        reader._verified_identity = B
        store.sync(reader)
        assert UnifiedDownloadView.run(view) is False
        assert started == []
        assert view.notices and "再読込" in view.notices[-1]


def dispatch_fixture(root, store, ledger, reader, svc, route, observation):
    sync_accounts(store, reader)
    ledger.bind_account(A)
    ledger.defer_work(author_id=X, work=HELD, now=datetime.now(timezone.utc), account_id=A)
    use_media_dispatch(root, reader, svc, switch_at=None)
    svc._media_admission_lock = RLock()
    svc.managed_downloads.saved_receipts_with_dates = lambda: ()
    svc.managed_downloads.verified_saved_receipt = lambda *args, **kwargs: None
    svc.managed_downloads.work_lock = lambda _: nullcontext()
    svc.static_jobs = svc.jobs
    svc.jobs.operation_ids = lambda _: ()
    svc.release_managed_owner = lambda _: None
    svc.forget_accepted_managed_author_batch = lambda *args: None
    svc.remember_managed_author_batch = lambda _, records: records
    accepted, seen = [], []
    svc.enqueue_managed_download = lambda *args, **kwargs: accepted.append(args)
    svc.enqueue_author_media = ApplicationServices.enqueue_author_media.__get__(svc)
    if route == "direct":
        svc.author_media = None

    def verify():
        if observation in ("empty", "nonempty"):
            reader._verified_identity = B
        seen.append(("identity", reader._verified_identity))
        return reader._verified_identity

    def inventory(_):
        seen.append(("inventory", reader._verified_identity))
        return () if observation == "empty" else (WORK,)

    def revision(wid, aid):
        seen.append(("detail", reader._verified_identity))
        reader._verified_identity = B
        return dict(work_id=wid, author_id=aid, illustration_type=0, page_count=1, update_date=DATE)

    def detail(wid):
        revision(wid, X)
        return RemoteBookmark(wid, "illustration", "show", "synthetic", X, "author", (),
                              "", "", 1, 0, 0, page_count_known=True)

    reader.verify_identity = verify
    reader.get_author_artworks_complete = inventory
    reader.get_author_novel_ids = lambda _: ()
    reader.get_artwork_update_revision = revision
    reader.get_artwork_download_record = detail
    if route != "direct" and observation != "during_detail":
        produce = svc.author_media_preview_owned

        def owned_other_account(aid, target=None, *, expected_account):
            # Model an adapter that returns a valid B listing even when A was requested.
            return produce(aid, target, expected_account=B)

        svc.author_media_preview_owned = owned_other_account
    return accepted, seen


@pytest.mark.parametrize("route", ("direct", "media", "batch", "manual"))
@pytest.mark.parametrize("observation", ("empty", "nonempty", "during_detail"))
def test_cross_path_mismatch_preserves_state(tmp_path, route, observation):
    with resources(tmp_path) as (root, store, ledger, reader, svc):
        accepted, seen = dispatch_fixture(root, store, ledger, reader, svc, route, observation)
        before = (store.snapshot(A), store.snapshot(B), ledger.deferred_ids(X))
        batch = BatchRunLedger(tmp_path / "batch")
        engine = BatchRunEngine(ServicesPort(svc), batch, following=store)
        svc.batch_runs = engine if route in ("batch", "manual") else None
        try:
            if route == "manual":
                rows = ApplicationServices.unified_download_authors(svc)
                view = author_view(svc, U.add_targets_bulk([], [next(r for r in rows if r["user_id"] == X)]))
                assert UnifiedDownloadView.run(view)
                assert engine.join(5)
            else:
                with pytest.raises(ScheduleAccountError) as error:
                    ApplicationServices.dispatch_author_check(svc, ledger, "contract", Event(), authors=(X,))
                assert error.value.reason == "mismatch"
            if observation == "during_detail":
                assert ("detail", A) in seen
            else:
                assert ("identity", B) in seen
                if route != "direct":
                    assert ("inventory", B) in seen
            assert not accepted
            assert (store.snapshot(A), store.snapshot(B), ledger.deferred_ids(X)) == before
            for row in ledger.history():
                assert row["accepted"] == 0 and row["finished_utc"] is None and row["status"] == "failed"
            if route in ("batch", "manual"):
                run = batch.run(engine.run_id)
                assert run.state == "failed" and run.finished_utc is None
                assert run.error_code == AccountMismatch.code
                (row,) = batch.authors(run.run_id)
                assert row.finished_utc is None and row.last_downloaded_utc is None
                assert batch.items(run.run_id) == ()
        finally:
            engine.stop()
            assert engine.join(5)
            batch.close()


def test_explicit_id_only_run_has_no_follow_account_or_stamp(tmp_path):
    with resources(tmp_path) as (root, store, ledger, reader, svc):
        sync_accounts(store, reader)
        use_media_dispatch(root, reader, svc, switch_at=None)
        before = (store.snapshot(A), store.snapshot(B))
        batch = BatchRunLedger(tmp_path / "batch")
        svc.batch_runs = engine = BatchRunEngine(ServicesPort(svc), batch, following=store)
        try:
            with pytest.raises(AccountMismatch):
                svc.start_batch_run((X,), account_id=None)
            targets, _ = U.add_target([], {"user_id": X}, "id")
            view = author_view(svc, targets)
            assert UnifiedDownloadView.run(view)
            assert engine.join(5)
            run = batch.run(engine.run_id)
            assert run.account_id is None and run.state == "done"
            (author,) = batch.authors(run.run_id)
            assert author.last_downloaded_utc is None and not author.stamp_written
            assert (store.snapshot(A), store.snapshot(B)) == before
        finally:
            engine.stop()
            assert engine.join(5)
            batch.close()
