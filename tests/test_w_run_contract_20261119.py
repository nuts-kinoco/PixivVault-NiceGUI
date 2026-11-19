"""Reflection and persistent-state contracts for execution and reconciliation."""
import asyncio
from inspect import getmembers, isroutine
from threading import Event
from types import SimpleNamespace as NS

import pytest

from account_test_support import observed_scope
from pixivvault_nicegui.account_scope import AccountMismatch, AccountScope
from pixivvault_nicegui.author_media import AuthorMediaService, canonical
from pixivvault_nicegui.batch_engine import BatchRunEngine, ServicesPort
from pixivvault_nicegui.batch_runs import BatchRunLedger
from pixivvault_nicegui.services import ApplicationServices
from test_w_account_separation_20261119 import A, B, X, Y, resources
from test_w_review_bookmark_resume_20261118 import GeneratedReader, services_with_runs


READ_ONLY = {
    BatchRunEngine: {"active_run", "status", "join", "resumable_run"},
    ApplicationServices: {"batch_run_active", "subscribe_batch_events"},
    AuthorMediaService: {"author_target", "resolve_author_target", "author_zip_setting", "owner_jobs",
                         "projection_unresolved_authors", "reconcile_status_messages", "close"},
}

EXECUTION = {
    (BatchRunEngine, "start"): ("start", lambda c: c.engine.start((X,), account_id=A)),
    (BatchRunEngine, "run"): ("start", lambda c: c.engine.run((X,))),
    (BatchRunEngine, "pause"): ("active", lambda c: c.engine.pause(c.handle)),
    (BatchRunEngine, "resume"): ("paused", lambda c: c.engine.resume(c.handle)),
    (BatchRunEngine, "stop"): ("active", lambda c: c.engine.stop(c.handle)),
    (BatchRunEngine, "start_unfinished"): ("offer", lambda c: c.engine.start_unfinished(c.handle)),
    (BatchRunEngine, "discard_resumable"): ("offer", lambda c: c.engine.discard_resumable(c.handle)),
    (ApplicationServices, "start_batch_run"): (
        "start", lambda c: ApplicationServices.start_batch_run(c.svc, (X,), account_id=A)),
    (ApplicationServices, "resume_batch_run"): (
        "offer", lambda c: asyncio.run(ApplicationServices.resume_batch_run(c.svc, c.handle))),
    (ApplicationServices, "discard_batch_run"): (
        "offer", lambda c: ApplicationServices.discard_batch_run(c.svc, c.handle)),
    (ApplicationServices, "dispatch_author_check"): (
        "start", lambda c: ApplicationServices.dispatch_author_check(
            c.svc, c.schedule, "contract-slot", Event(), authors=(X,))),
}

RECONCILE = {
    (AuthorMediaService, "preview"): lambda c, scope: c.media.preview(X, expected_account=scope.account_id),
    (AuthorMediaService, "preview_owned"): lambda c, scope: c.media.preview_owned(X, expected_account=scope.account_id),
    (AuthorMediaService, "reconcile"): lambda c, scope: c.media.reconcile(scope=scope),
    (ApplicationServices, "author_media_preview"): lambda c, scope: ApplicationServices.author_media_preview(
        c.svc, X, expected_account=scope.account_id),
    (ApplicationServices, "author_media_preview_owned"): lambda c, scope: ApplicationServices.author_media_preview_owned(
        c.svc, X, expected_account=scope.account_id),
}

PROJECTION = {
    "completed_projection_sources": lambda media: media.completed_projection_sources(),
    "completed_projection_sources_for_author": lambda media: media.completed_projection_sources_for_author(X),
}

OWNER_BOUND = {
    BatchRunEngine: {name for cls, name in EXECUTION if cls is BatchRunEngine},
    ApplicationServices: {name for cls, name in (*EXECUTION, *RECONCILE) if cls is ApplicationServices}
    | {"enable_batch_runs", "start_bookmark_run", "reconcile_managed_scheduler"},
    AuthorMediaService: {name for cls, name in RECONCILE if cls is AuthorMediaService} | set(PROJECTION)
    | {"configure_author_target", "enable_zip_settings", "configure_author_zip", "enqueue", "forget_admissions"},
}


@pytest.mark.parametrize("cls", READ_ONLY, ids=lambda cls: cls.__name__)
def test_execution_and_reconciliation_entries_are_classified(cls):
    public = {name for name, member in getmembers(cls, isroutine) if not name.startswith("_")}
    if cls is ApplicationServices:
        public = {name for name in public if "batch_run" in name or name.startswith("author_media_preview")
                  or name in {"start_bookmark_run", "dispatch_author_check", "subscribe_batch_events",
                              "reconcile_managed_scheduler"}}
    assert not READ_ONLY[cls] & OWNER_BOUND[cls]
    assert public == READ_ONLY[cls] | OWNER_BOUND[cls]


def journal(media):
    with media._db() as db:
        return tuple(db.iterdump())


def durable(c):
    return (tuple(c.store._connection.iterdump()), tuple(c.schedule._db.iterdump()),
            tuple(c.engine.ledger._connection.iterdump()), journal(c.media))


def seed(svc, root, store, schedule, reader):
    for account, author in ((B, Y), (A, X)):
        reader._verified_identity = account
        store.sync(reader)
        store.set_favorite(account, author, True)
    schedule.bind_account(A, scope=observed_scope(A))
    reader.get_author_artworks_complete = lambda _: ()
    reader.get_author_novel_ids = lambda _: ()
    svc.managed_downloads = NS(root=root)
    media = svc.author_media = AuthorMediaService(svc)
    svc.author_media_preview_owned = ApplicationServices.author_media_preview_owned.__get__(svc)
    svc.start_batch_run = ApplicationServices.start_batch_run.__get__(svc)
    svc.preferences = None
    batch = BatchRunLedger(root.parent / "batch")
    svc.batch_runs = engine = BatchRunEngine(ServicesPort(svc), batch, following=store, account_id=A)
    other = batch.begin_run("authors", "manual", "both", (Y,), account_id=B)
    batch.set_run_state(other, "stopped")
    return NS(svc=svc, store=store, schedule=schedule, engine=engine, media=media, reader=reader)


def cases(catalog):
    return [pytest.param(case, id=f"{case[0].__name__}.{case[1]}") for case in catalog]


@pytest.mark.parametrize("case", cases(EXECUTION))
@pytest.mark.parametrize("current", (A, B), ids=("owner", "other"))
def test_owned_operations_pair_rejection_with_owner_success(tmp_path, case, current):
    phase, invoke = EXECUTION[case]
    with resources(tmp_path) as (root, store, schedule, reader, svc):
        c = seed(svc, root, store, schedule, reader)
        entered, release = Event(), Event()
        try:
            if phase in ("active", "paused"):
                def blocked(_):
                    entered.set()
                    assert release.wait(5)
                    return ()

                reader.get_author_artworks_complete = blocked
                c.handle = c.engine.start((X,), account_id=A)
                assert entered.wait(5)
                if phase == "paused":
                    assert c.engine.pause(c.handle)
            elif phase == "offer":
                c.handle = c.engine.ledger.begin_run("authors", "manual", "both", (X,), account_id=A)
                c.engine.ledger.set_run_state(c.handle, "stopped")
            reader._verified_identity = current
            before = durable(c)
            if current == B:
                with pytest.raises(AccountMismatch):
                    invoke(c)
                assert durable(c) == before
            else:
                result = invoke(c)
                assert result is not None and result is not False
                if phase in ("start", "offer"):
                    assert c.engine.join(5)
                assert durable(c) != before
        finally:
            release.set()
            assert c.engine._shutdown(5)
            c.engine.ledger.close()


def seed_admission(c, *, operation_matches):
    op, job_id = "1" * 32, "2" * 32
    raw = canonical(dict(author_id=X, kind="illustration", work_id="200", output_format="", update_date="2026-01-01"))
    c.svc.static_jobs = NS(snapshot=lambda: (NS(job_id=job_id, state="done", result="verified-receipt"),),
                           operation_ids=lambda _: (op,) if operation_matches else ())
    c.svc.managed_downloads.verified_saved_receipt = lambda *_a, **_kw: (
        "verified-receipt", {}, "digest", "2026-01-01", "fingerprint")
    with c.media._db() as db:
        db.execute("INSERT INTO admissions(operation_id,media_key,spec) VALUES(?,?,?)", (op, "200", raw))


@pytest.mark.parametrize("case", cases(RECONCILE))
@pytest.mark.parametrize("current", (A, B), ids=("owner", "other"))
def test_reader_owned_reconcile_validates_before_journal_write(tmp_path, case, current):
    with resources(tmp_path) as (root, store, schedule, reader, svc):
        c = seed(svc, root, store, schedule, reader)
        try:
            seed_admission(c, operation_matches=True)
            scope = AccountScope(A, reader)
            reader._verified_identity = current
            before = durable(c)
            if current == B:
                with pytest.raises(AccountMismatch):
                    RECONCILE[case](c, scope)
                assert durable(c) == before
            else:
                RECONCILE[case](c, scope)
                after = durable(c)
                assert after[:3] == before[:3] and after[3] != before[3]
        finally:
            c.engine.ledger.close()


@pytest.mark.parametrize("method", PROJECTION)
@pytest.mark.parametrize("operation_matches", (False, True), ids=("unowned-job", "durable-owner"))
def test_local_projection_reconciles_only_the_durable_operation_owner(tmp_path, method, operation_matches):
    with resources(tmp_path) as (root, store, schedule, reader, svc):
        c = seed(svc, root, store, schedule, reader)
        try:
            seed_admission(c, operation_matches=operation_matches)
            reader._verified_identity = B
            reader.fail = True  # Local recovery has no reader-based authority.
            before = durable(c)
            sources = PROJECTION[method](c.media)
            after = durable(c)
            assert after[:3] == before[:3]
            if operation_matches:
                assert len(sources) == 1 and sources[0]["author_id"] == X
                assert after[3] != before[3]
            else:
                assert sources == () and after == before
        finally:
            c.engine.ledger.close()


@pytest.mark.parametrize("current", (A, B), ids=("owner", "other"))
def test_scheduler_reconcile_checks_owner_before_shared_journal_repair(tmp_path, current):
    with resources(tmp_path) as (root, store, schedule, reader, svc):
        c = seed(svc, root, store, schedule, reader)
        try:
            seed_admission(c, operation_matches=True)
            svc.jobs = NS(snapshot=lambda: ())
            svc._scheduler_unfinished_count = None
            reader._verified_identity = current
            before = durable(c)
            assert ApplicationServices.reconcile_managed_scheduler(svc) is (current == A)
            after = durable(c)
            assert after[:3] == before[:3]
            assert (after[3] != before[3]) is (current == A)
        finally:
            c.engine.ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("current", (A, B), ids=("owner", "other"))
async def test_bookmark_start_checks_the_expected_reader_before_writing(tmp_path, current):
    class Reader(GeneratedReader):
        def verify_identity(self):
            self._verified_identity = current
            return current

        def get_bookmarked_works(self, uid, *, rest_type):
            self.calls.append((uid, rest_type))
            return ()

    reader = Reader()
    svc = services_with_runs(tmp_path, reader)
    try:
        before = tuple(svc.batch_run_ledger._connection.iterdump())
        if current == B:
            with pytest.raises(AccountMismatch):
                await svc.start_bookmark_run(("show",), expected_account=A)
            assert tuple(svc.batch_run_ledger._connection.iterdump()) == before
            assert not reader.calls
        else:
            handle = await svc.start_bookmark_run(("show",), expected_account=A)
            assert await asyncio.to_thread(svc.batch_runs.join, 5, handle=handle)
            assert handle.account_id == A and svc.batch_run_ledger.run(handle.run_id).state == "done"
    finally:
        await asyncio.to_thread(svc.close)
