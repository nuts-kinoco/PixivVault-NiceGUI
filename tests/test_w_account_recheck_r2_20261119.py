"""Account changes between selection, inventory, and adoption."""
from datetime import datetime, timezone
from threading import Event, Lock
from types import SimpleNamespace as NS

import pytest
from nicegui import Client, ui

from account_test_support import observed_scope
from pixivvault_nicegui.account_scope import AccountScope, OwnedListing
from pixivvault_nicegui.managed_scheduler import ScheduleAccountError
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.scheduler_view import SchedulerView

from test_w_account_separation_20261119 import A, B, X, Y, resources, stars


def use_media_dispatch(root, reader, svc, *, switch_at="verify"):
    from pixivvault_nicegui.author_media import AuthorMediaService

    svc.managed_downloads = NS(root=root)
    svc.preferences = None
    svc.jobs = NS(snapshot=lambda: ())
    svc.author_media = AuthorMediaService(svc)
    svc.author_media_preview_owned = ApplicationServices.author_media_preview_owned.__get__(svc)
    svc.author_media_preview = ApplicationServices.author_media_preview.__get__(svc)
    svc.start_batch_run = ApplicationServices.start_batch_run.__get__(svc)
    calls = []

    def illustrations(aid):
        calls.append(("illustration", reader._verified_identity, aid))
        if switch_at == "illustration":
            reader._verified_identity = B
        return ()

    def novels(aid):
        calls.append(("novel", reader._verified_identity, aid))
        if switch_at == "novel":
            reader._verified_identity = B
        return ()

    def change_on_second_verification():
        nonlocal count
        count += 1
        if count == 2:
            if switch_at == "verify":
                reader._verified_identity = B
            elif switch_at == "return":
                return B  # cached identity still claims A
        return reader._verified_identity

    count = 0
    reader.get_author_artworks_complete = illustrations
    reader.get_author_novel_ids = novels
    reader.verify_identity = change_on_second_verification
    return calls


@pytest.mark.parametrize("switch_at", ("verify", "return", "illustration", "novel"))
def test_all_media_queue_does_not_adopt_b_inventory(tmp_path, switch_at):
    with resources(tmp_path) as (root, store, ledger, reader, svc):
        store.sync(reader)
        store.set_favorite(A, X, True)
        assert svc.scheduler_favourite_authors(ledger=ledger) == (X,)
        key = "illustration:200"
        ledger.defer_work(author_id=X, work=dict(id=key, update_date="2020", page_count=1, type=0),
                          now=datetime.now(timezone.utc), account_id=A)
        assert ledger.deferred_ids(X) == {key}
        svc.batch_runs = None
        calls = use_media_dispatch(root, reader, svc, switch_at=switch_at)
        with pytest.raises(ScheduleAccountError) as error:
            ApplicationServices.dispatch_author_check(svc, ledger, "manual-check", Event())
        assert error.value.reason == "mismatch"
        expected = {"verify": [], "return": [], "illustration": ["illustration"],
                    "novel": ["illustration", "novel"]}
        assert [kind for kind, account, aid in calls] == expected[switch_at]
        assert all(account == A and aid == X for _, account, aid in calls)
        assert ledger.deferred_ids(X) == {key} and stars(store, A) == (X,)
        assert ledger.history()[0]["status"] == "failed"
        assert ledger._db.execute("SELECT finished_utc FROM dispatch_history").fetchone() == (None,)


@pytest.mark.parametrize("switch_at", ("verify", "return", "illustration", "novel", "after_preview", "after_download"))
def test_batch_engine_does_not_finish_author_after_account_change(tmp_path, switch_at):
    from pixivvault_nicegui.batch_engine import BatchRunEngine, ServicesPort
    from pixivvault_nicegui.batch_runs import BatchRunLedger

    with resources(tmp_path) as (root, store, ledger, reader, svc):
        store.sync(reader)
        store.set_favorite(A, X, True)
        assert svc.scheduler_favourite_authors(ledger=ledger) == (X,)
        calls = use_media_dispatch(root, reader, svc, switch_at=switch_at)
        if switch_at == "after_preview":
            preview = svc.author_media_preview_owned

            def switch_after_preview(*args, **kwargs):
                rows = preview(*args, **kwargs)
                reader._verified_identity = B
                return rows

            svc.author_media_preview_owned = switch_after_preview
        batch_ledger = BatchRunLedger(tmp_path / "batch")
        svc.batch_runs = BatchRunEngine(ServicesPort(svc), batch_ledger, following=store)
        engine = svc.batch_runs
        if switch_at == "after_download":
            download = engine._download

            def switch_after_download(*args, **kwargs):
                result = download(*args, **kwargs)
                reader._verified_identity = B
                return result

            engine._download = switch_after_download
        try:
            with pytest.raises(ScheduleAccountError) as error:
                ApplicationServices.dispatch_author_check(svc, ledger, "manual-check", Event())
            assert error.value.reason == "mismatch"
            stamp = next(row.last_downloaded for row in store.snapshot(A).authors if row.user_id == X)
            run = batch_ledger.run(engine.run_id)
            author = batch_ledger.authors(run.run_id)[0]
            assert stamp is None and author.finished_utc is None and author.last_downloaded_utc is None
            assert run.state == "failed" and run.error_code == "author_account_mismatch"
            assert run.finished_utc is None
            assert all(account == A for _, account, _ in calls)
        finally:
            svc.batch_runs.stop()
            assert svc.batch_runs.join(5)
            batch_ledger.close()


@pytest.mark.asyncio
async def test_existing_star_and_backlog_survive_concurrent_unfollow_when_star_is_kept_in_form(tmp_path):
    with resources(tmp_path) as (_, store, ledger, reader, svc):
        store.sync(reader)
        store.set_favorite(A, X, True)
        assert svc.scheduler_favourite_authors(ledger=ledger) == (X,)
        ledger.defer_work(author_id=X, work=dict(id="200", update_date="2020", page_count=1, type=0),
                          now=datetime.now(timezone.utc), account_id=A)
        client = Client(ui.page("/independent-unfollow-form"))
        with client:
            view = SchedulerView(svc)
        try:
            view._load(ledger)
            assert view.authors.value == X and ledger.deferred_ids(X) == {"200"}
            view.authors.set_value(f"{X},{Y}")
            # X remains selected. The app changes its follow status after the form loaded.
            store.record_unfollowed(A, X, scope=observed_scope(A))
            assert stars(store, A) == (X,) and ledger.deferred_ids(X) == {"200"}
            assert ledger.snapshot().revision == view.revision
            await view.save()
            assert stars(store, A) == (X, Y) and ledger.deferred_ids(X) == {"200"}
            assert X in view.action_notice.text and "保持" in view.action_notice.text
            view.authors.set_value(Y)  # explicit removal is allowed, even for an unfollowed author
            await view.save()
            assert stars(store, A) == (Y,) and ledger.deferred_ids(X) == set()
        finally:
            view.close()
            client.delete()


@pytest.mark.asyncio
@pytest.mark.parametrize("consumer", ("preview", "confirm"))
async def test_explicit_author_inventory_rejects_switch_before_observation(tmp_path, consumer):
    with resources(tmp_path) as (_, store, ledger, reader, svc):
        def switched_inventory(author_id):
            assert author_id == X
            reader._verified_identity = B
            return ()

        reader.get_author_artworks_complete = switched_inventory
        svc._direct_owner = lambda owner: owner
        svc._legacy_inventory_lock = Lock()
        svc.managed_downloads = NS()
        scans = []
        svc.legacy_author_bridge = NS(observe_author=lambda *args, **kwargs: scans.append(args))
        with pytest.raises(RuntimeError, match="account changed"):
            if consumer == "preview":
                await ApplicationServices.lookup_managed_author(svc, X, owner="test")
            else:
                await ApplicationServices.confirm_legacy_author_inventory(svc, X, owner="test", scan_id="1" * 32)
        assert scans == []


def test_scheduler_reconcile_rechecks_before_ledger_adoption(tmp_path):
    with resources(tmp_path) as (_, store, ledger, reader, svc):
        store.sync(reader)
        store.set_favorite(A, X, True)
        assert svc.scheduler_favourite_authors(ledger=ledger) == (X,)
        ledger.defer_work(author_id=X, work=dict(id="200", update_date="2020", page_count=1, type=0),
                          now=datetime.now(timezone.utc), account_id=A)

        def snapshot_jobs():
            reader._verified_identity = B
            return ()

        svc.jobs = NS(snapshot=snapshot_jobs)
        svc.managed_downloads = NS()
        adopted = []
        ledger.reconcile_jobs = lambda *args: adopted.append(args) or ()
        svc.shared_log = NS(append=lambda *args, **kwargs: None)
        svc._scheduler_unfinished_count = 0
        assert ApplicationServices.reconcile_managed_scheduler(svc) is False
        assert adopted == []
        assert ledger.deferred_ids(X) == {"200"} and stars(store, A) == (X,)


def test_completion_ledger_rejects_account_other_than_run_owner(tmp_path):
    from pixivvault_nicegui.batch_runs import BatchRunLedger
    with resources(tmp_path) as (_, store, ledger, reader, svc):
        store.sync(reader)
        batch = BatchRunLedger(tmp_path / "batch")
        try:
            run_id = batch.begin_run("authors", "manual", "both", (X,), account_id=A)
            with pytest.raises(RuntimeError, match="account changed"):
                listing = OwnedListing(AccountScope(B, NS(_verified_identity=B)), X, "both", ())
                batch.complete_author(run_id, X, following=store, listing=listing)
            author = batch.authors(run_id)[0]
            assert author.finished_utc is None and author.last_downloaded_utc is None
        finally:
            batch.close()


def test_verified_empty_inventory_leaves_existing_backlog(tmp_path):
    with resources(tmp_path) as (root, store, ledger, reader, svc):
        store.sync(reader)
        store.set_favorite(A, X, True)
        assert svc.scheduler_favourite_authors(ledger=ledger) == (X,)
        ledger.defer_work(author_id=X, work=dict(id="200", update_date="2020", page_count=1, type=0),
                          now=datetime.now(timezone.utc), account_id=A)
        svc.batch_runs = None
        calls = use_media_dispatch(root, reader, svc, switch_at=None)
        ApplicationServices.dispatch_author_check(svc, ledger, "manual-check", Event())
        assert calls == [("illustration", A, X), ("novel", A, X)]
        assert ledger.deferred_ids(X) == {"200"}


def test_account_scoped_star_and_author_snapshots_reject_other_owner(tmp_path, monkeypatch):
    with resources(tmp_path) as (_, store, ledger, reader, svc):
        store.sync(reader)
        store.set_favorite(A, X, True)
        reader._verified_identity = B
        store.sync(reader)
        other = store.snapshot(B)
        reader._verified_identity = A
        store.sync(reader)
        monkeypatch.setattr(store, "snapshot", lambda _: other)
        for read in (
            lambda: svc.scheduler_favourite_authors(ledger=ledger),
            lambda: svc.favourite_book().ids(A),
            lambda: ApplicationServices.unified_download_authors(svc),
        ):
            with pytest.raises(RuntimeError):
                read()
        assert ledger._bound_account() is None


def test_follow_resync_drops_mismatched_result_before_publication(tmp_path):
    from pixivvault_nicegui.following_sync import FollowingSyncError
    with resources(tmp_path) as (_, store, ledger, reader, svc):
        store.sync(reader)
        store.set_favorite(A, X, True)
        prior = store.snapshot(A)
        accounts = iter((A, B))
        reader.require_identity = lambda: next(accounts)
        reader.get_following_users_detailed = lambda *args: ()
        with pytest.raises(FollowingSyncError):
            store.sync(reader)
        assert store.snapshot(A) == prior
        assert store.snapshot(B).last_synced_at is None


def test_media_admission_rejects_candidate_from_previous_account(tmp_path):
    with resources(tmp_path) as (root, store, ledger, reader, svc):
        use_media_dispatch(root, reader, svc, switch_at=None)
        svc.managed_downloads.verified_saved_receipt = lambda wid: None
        reader.get_author_artworks_complete = lambda aid: (
            dict(id="200", user_id=aid, type=0, page_count=1, title="work", update_date="2020"),)
        (candidate,) = svc.author_media_preview_owned(X, "illust", expected_account=A).rows
        reader._verified_identity = B
        with pytest.raises(RuntimeError, match="account changed"):
            svc.author_media.enqueue(candidate, "1" * 32)
        with svc.author_media._db() as db:
            assert db.execute("SELECT count(*) FROM admissions").fetchone() == (0,)
