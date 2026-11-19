"""Contracts for isolated account-scoped author operations."""
from contextlib import nullcontext
from datetime import datetime, timezone
from inspect import getmembers, isroutine
from threading import Event, RLock
from types import SimpleNamespace as NS

import pytest

from pixivvault_nicegui import unified_download_state as U
from account_test_support import observed_scope
from pixivvault_nicegui.account_scope import AccountMismatch, AccountScope, BoundSchedulerLedger, OwnedListing
from pixivvault_nicegui.batch_engine import BatchRunEngine, ServicesPort
from pixivvault_nicegui.batch_runs import BatchRunError, BatchRunLedger, RunHandle
from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.favourites import FavouriteBook
from pixivvault_nicegui.following_sync import FollowingSyncError, FollowingSyncStore
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
        store.record_downloaded(account, X, DATE, scope=observed_scope(account))


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

# The value supplies the valid non-ownership arguments for each reflected call.
# Adding a method here also adds its negative ownership tests automatically.
OWNER_BOUND = {
    ManagedScheduler: {
        "configure": lambda c, p: c.ledger.configure(interval_hours=6, startup_check=True,
            expected_revision=c.revision, now=c.now, account_id=p.account),
        "configure_all": lambda c, p: c.ledger.configure_all(author_ids=(), interval_hours=6, startup_check=True,
            expected_revision=c.revision, now=c.now, account_id=p.account),
        "set_favourites": lambda c, p: c.ledger.set_favourites((), expected_revision=c.revision, account_id=p.account),
        "set_star": lambda c, p: c.ledger.set_star(c.book, X, False, account_id=p.account),
        "bind_account": lambda c, p: c.ledger.bind_account(A, scope=p.scope),
        "acknowledge_interrupted": lambda c, p: c.ledger.acknowledge_interrupted(
            expected_revision=c.revision, account_id=p.account),
        "begin_dispatch": lambda c, p: c.ledger.begin_dispatch("new", c.now, 1, account_id=p.account),
        "finish_dispatch": lambda c, p: c.ledger.finish_dispatch(
            "existing", status="empty", accepted=0, deferred=0, account_id=p.account),
        "update_dispatch_progress": lambda c, p: c.ledger.update_dispatch_progress(
            "existing", accepted=1, deferred=0, account_id=p.account),
        "prepare_dispatch_item": lambda c, p: c.ledger.prepare_dispatch_item(
            slot="existing", work_id="200", author_id=X, operation_id="op", account_id=p.account),
        "bind_dispatch_job": lambda c, p: c.ledger.bind_dispatch_job(
            slot="existing", work_id="200", operation_id="op", job_id="job",
            accepted_fingerprint="fingerprint", account_id=p.account),
        "fail_dispatch_item": lambda c, p: c.ledger.fail_dispatch_item(
            slot="existing", work_id="200", operation_id="op", account_id=p.account),
        "defer_work": lambda c, p: c.ledger.defer_work(author_id=X, work=WORK, now=c.now, account_id=p.account),
        "resolve_deferred": lambda c, p: c.ledger.resolve_deferred(X, HELD["id"], account_id=p.account),
        "reconcile_jobs": lambda c, p: c.ledger.reconcile_jobs((), {}, account_id=p.account),
        "claim_pending_notifications": lambda c, p: c.ledger.claim_pending_notifications(account_id=p.account),
        "mark_notified": lambda c, p: c.ledger.mark_notified(("200",), account_id=p.account),
    },
    BatchRunLedger: {
        "begin_run": lambda c, p: c.batch.begin_run("authors", "manual", "both", (X,),
            account_id=p.account, resume_from=RunHandle(c.run, p.account)),
        "set_run_state": lambda c, p: c.batch.set_run_state(RunHandle(c.run, p.account), "failed"),
        "discard_run": lambda c, p: c.batch.discard_run(RunHandle(c.run, p.account)),
        "update_author": lambda c, p: c.batch.update_author(c.run, X, state="listing", account_id=p.account),
        "complete_author": lambda c, p: c.batch.complete_author(c.run, X, listing=p.listing, following=c.store),
        "complete_bookmark_row": lambda c, p: c.batch.complete_bookmark_row(c.run, X, account_id=p.account),
        "finish_author": lambda c, p: c.batch.finish_author(c.run, X, "failed", account_id=p.account),
        "record_item": lambda c, p: c.batch.record_item(c.run, X, "200", "illustration", "op", account_id=p.account),
        "record_items": lambda c, p: c.batch.record_items(c.run, X,
            (("200", "illustration", "op", None, "queued"),), account_id=p.account),
    },
    FavouriteBook: {"set": lambda c, p: c.book.set(X, False, account_id=p.account)},
    FollowingSyncStore: {
        "sync": lambda c, p: c.store.sync(p.sync_reader),
        "clear_account": lambda c, p: c.store.clear_account(A, scope=p.scope),
        "record_followed": lambda c, p: c.store.record_followed(A, X, scope=p.scope),
        "record_unfollowed": lambda c, p: c.store.record_unfollowed(A, X, scope=p.scope),
        "set_favorite": lambda c, p: c.store.set_favorite(p.account, X, False),
        "replace_favorites": lambda c, p: c.store.replace_favorites(p.account, ()),
        "record_downloaded": lambda c, p: c.store.record_downloaded(A, X, DATE, scope=p.scope),
    },
    BoundSchedulerLedger: {
        "defer_work": lambda c, p: BoundSchedulerLedger(c.ledger, p.scope, p.services).defer_work(
            author_id=X, work=WORK, now=c.now),
        "resolve_deferred": lambda c, p: BoundSchedulerLedger(c.ledger, p.scope, p.services).resolve_deferred(
            X, HELD["id"]),
    },
}


def public_methods(cls):
    return {name for name, value in getmembers(cls, isroutine) if not name.startswith("_")}


@pytest.mark.parametrize("cls", READ_ONLY, ids=lambda cls: cls.__name__)
def test_every_public_method_has_one_classification(cls):
    reads, writes = READ_ONLY[cls], OWNER_BOUND[cls]
    assert not set(reads).intersection(writes)
    assert all(reads.values())
    assert public_methods(cls) == set(reads).union(writes)


def reflected_write_cases():
    for cls, reads in READ_ONLY.items():
        for method in sorted(public_methods(cls) - set(reads)):
            yield pytest.param(cls, method, id=f"{cls.__name__}.{method}")


@pytest.mark.parametrize("cls,method", reflected_write_cases())
@pytest.mark.parametrize("evidence", ("absent", "unbound", "other_account"))
def test_account_sinks_reject_without_writing_any_table(tmp_path, cls, method, evidence):
    invoke = OWNER_BOUND[cls][method]
    with resources(tmp_path) as (_, store, ledger, reader, svc):
        sync_accounts(store, reader)
        now = datetime.now(timezone.utc)
        ledger.bind_account(A, scope=observed_scope(A))
        ledger.defer_work(author_id=X, work=HELD, now=now, account_id=A)
        ledger.begin_dispatch("existing", now, 1, account_id=A)
        batch = BatchRunLedger(tmp_path / "batch")
        try:
            kind = "bookmarks" if method == "complete_bookmark_row" else "authors"
            run = batch.begin_run(kind, "manual", "both", (X,), account_id=A).run_id
            if method in ("begin_run", "discard_run"):
                batch.set_run_state(batch.run(run).handle, "stopped")
            context = NS(book=svc.favourite_book(), ledger=ledger, batch=batch, run=run,
                         store=store, now=now, revision=ledger.snapshot().revision)
            scope = {"absent": None, "unbound": AccountScope(A), "other_account": observed_scope(B)}[evidence]
            identities = iter((A, B))
            other_sync = NS(require_identity=lambda: next(identities), get_following_users_detailed=lambda *args: ())
            proof = NS(account={"absent": None, "unbound": AccountScope(A), "other_account": B}[evidence],
                       scope=scope, listing=None if scope is None else OwnedListing(scope, X, "both", ()),
                       services=NS(live_client=scope.reader if scope is not None else None),
                       sync_reader={"absent": None, "unbound": object(), "other_account": other_sync}[evidence])
            before = dump(store, ledger, batch)
            with pytest.raises((AccountMismatch, ScheduleAccountError, FollowingSyncError)):
                invoke(context, proof)
            assert dump(store, ledger, batch) == before
        finally:
            batch.close()


def test_unbound_scheduler_and_bookmark_only_exit_cannot_write_author_state(tmp_path):
    with resources(tmp_path) as (_, store, ledger, reader, svc):
        sync_accounts(store, reader)
        batch = BatchRunLedger(tmp_path / "batch")
        try:
            run = batch.begin_run("authors", "manual", "both", (X,), account_id=A).run_id
            before = dump(store, ledger, batch)
            with pytest.raises(ScheduleAccountError):
                ledger.defer_work(author_id=X, work=HELD, now=datetime.now(timezone.utc), account_id=A)
            with pytest.raises(ScheduleAccountError):
                ledger.resolve_deferred(X, HELD["id"], account_id=A)
            with pytest.raises(BatchRunError):
                batch.complete_bookmark_row(run, X, account_id=batch.run(run).account_id)
            assert dump(store, ledger, batch) == before
        finally:
            batch.close()


def author_view(svc, targets):
    view = NS(mode="authors", targets=targets, services=svc, target_type="both",
              target_ids=lambda: [row["user_id"] for row in targets], _history_account=lambda: None,
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
    ledger.bind_account(A, scope=observed_scope(A))
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
            engine._shutdown(0)
            assert engine.join(5)
            batch.close()


def test_explicit_id_only_run_has_owner_and_no_follow_stamp(tmp_path):
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
            assert run.account_id == A and run.state == "done"
            (author,) = batch.authors(run.run_id)
            assert author.last_downloaded_utc is None and not author.stamp_written
            assert (store.snapshot(A), store.snapshot(B)) == before
        finally:
            engine._shutdown(0)
            assert engine.join(5)
            batch.close()
