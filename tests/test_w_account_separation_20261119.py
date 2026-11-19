"""Account separation regressions reproduced on the integrated candidate."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from threading import RLock
from types import SimpleNamespace as NS

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.favourites import FavouriteBook
from pixivvault_nicegui.following_sync import FollowingSyncStore
from pixivvault_nicegui.managed_scheduler import ManagedScheduler, ManagedScheduleRunner, ScheduleAccountError
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.scheduler_view import SchedulerView
from pixivvault_nicegui.ui.unified_download_view import UnifiedDownloadView

A, B = "55", "99"
X, Y = "1001", "1002"


class Reader:
    def __init__(self, account):
        self._verified_identity = account
        self.fail = False

    def verify_identity(self):
        if self.fail:
            raise RuntimeError("synthetic transient identity error")
        return self._verified_identity

    require_identity = verify_identity

    def get_following_users_detailed(self, account, visibility):
        assert account == self._verified_identity
        if visibility == "hide":
            return ()
        return tuple(dict(user_id=uid, name="synthetic", account="", profile_img="") for uid in (X, Y))


def stars(store, account):
    return tuple(sorted(row.user_id for row in store.snapshot(account).authors if row.is_favorite))


@contextmanager
def resources(tmp_path, *, legacy_db=None):
    root = tmp_path / "managed"
    root.mkdir()
    with_store = FollowingSyncStore(tmp_path / "following")
    ledger = ManagedScheduler(root)
    reader = Reader(A)
    svc = NS(closing=False, following_sync=with_store, live_client=reader, managed_network_stop=None,
             managed_scheduler=ledger, managed_schedule_runner=NS(settings_changed=lambda: None),
             library_readonly_db=legacy_db, _favourite_book=None)
    svc.scheduler_favourite_authors = lambda **kw: ApplicationServices.scheduler_favourite_authors(svc, **kw)
    svc.favourite_book = lambda **kw: ApplicationServices.favourite_book(svc, **kw)
    ledger.attach_favourites(svc.favourite_book(scheduler=ledger))
    svc.set_favourite = lambda *args, **kw: ApplicationServices.set_favourite(svc, *args, **kw)
    try:
        yield root, with_store, ledger, reader, svc
    finally:
        ledger.close()
        with_store.close()


def switch(store, reader):
    reader._verified_identity = B
    store.sync(reader)
    assert store.last_synced_account() == B


@pytest.mark.asyncio
@pytest.mark.parametrize("edit_stars", (True, False))
async def test_account_a_scheduler_form_cannot_save_after_syncing_b(tmp_path, edit_stars):
    with resources(tmp_path) as (_, store, ledger, reader, svc):
        store.sync(reader)
        store.set_favorite(A, X, True)
        client = Client(ui.page("/independent-merge-account"))
        try:
            with client:
                view = SchedulerView(svc)
            view._load(ledger)
            assert view.authors.value == X and ledger._bound_account() is None
            if edit_stars:
                view.authors.set_value(f"{X},{Y}")
            view.interval.set_value("6")
            switch(store, reader)
            store.set_favorite(B, Y, True)
            before = ledger.snapshot()
            await view.save()
            assert '再読込' in view.action_notice.text
            assert ledger.snapshot() == before and ledger._bound_account() is None
            assert stars(store, A) == (X,)
            assert stars(store, B) == (Y,)
        finally:
            client.delete()


def test_account_a_download_table_cannot_clear_account_b_star(tmp_path):
    with resources(tmp_path) as (_, store, ledger, reader, svc):
        store.sync(reader)
        store.set_favorite(A, X, True)
        rows = ApplicationServices.unified_download_authors(svc)
        notices = []
        view = NS(authors=[dict(r) for r in rows], services=svc, favorite_only=False,
                  set_notice=notices.append, _update_row=lambda uid: None, _render_favorite_card=lambda: None)
        displayed = next(row for row in view.authors if row["user_id"] == X)
        switch(store, reader)
        store.set_favorite(B, X, True)
        assert UnifiedDownloadView.toggle_favorite(view, X) is False
        assert "再読込" in notices[-1] and displayed["is_favorite"]
        assert stars(store, A) == stars(store, B) == (X,)
        view.authors = list(ApplicationServices.unified_download_authors(svc))
        assert UnifiedDownloadView.toggle_favorite(view, X, displayed_author=displayed) is False
        assert stars(store, A) == stars(store, B) == (X,)


@pytest.mark.parametrize("failure", ("unavailable", "false"))
def test_failed_star_write_is_visible_and_keeps_download_star(tmp_path, monkeypatch, failure):
    with resources(tmp_path) as (_, store, ledger, reader, svc):
        store.sync(reader)
        store.set_favorite(A, X, True)
        rows = ApplicationServices.unified_download_authors(svc)
        notices = []
        view = NS(authors=[dict(r) for r in rows], services=svc, favorite_only=False,
                  set_notice=notices.append, _update_row=lambda uid: None, _render_favorite_card=lambda: None)
        if failure == "unavailable":
            monkeypatch.setattr(store, "last_synced_account", lambda: None)
        else:
            svc.set_favourite = lambda *args, **kwargs: False
        result = UnifiedDownloadView.toggle_favorite(view, X)
        assert stars(store, A) == (X,)
        assert result is False and view.authors[0]["is_favorite"] is True and notices


def test_default_schedule_recovers_from_first_due_identity_failure_after_reopen(tmp_path):
    with resources(tmp_path) as (root, store, ledger, reader, svc):
        store.sync(reader)
        store.set_favorite(A, X, True)
        reader.fail = True
        assert ledger.snapshot().revision == 1
        runner = ManagedScheduleRunner(ledger, lambda slot, stop: svc.scheduler_favourite_authors(ledger=ledger))
        slot = runner._claim_reserved(datetime.now(timezone.utc) + timedelta(hours=25))
        assert slot and runner._dispatch_slot(slot) is False
        assert ledger.snapshot().last_status == "error" and ledger._bound_account() is None
        ledger.close()
        reader.fail = False
        reopened = ManagedScheduler(root)
        try:
            reopened.attach_favourites(FavouriteBook(store, scheduler=reopened))
            try:
                selected = svc.scheduler_favourite_authors(ledger=reopened)
            except ScheduleAccountError as ex:
                pytest.fail(f"restored identity but scheduler refuses to bind: {ex.reason}")
            assert selected == (X,)
        finally:
            reopened.close()


@pytest.mark.parametrize("legacy_owner", (None, A))
def test_unknown_legacy_stars_are_not_imported_into_verified_b(tmp_path, legacy_owner):
    path = tmp_path / "legacy-copy.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE following_users(user_id INTEGER PRIMARY KEY, is_favorite INTEGER)")
        db.execute("INSERT INTO following_users VALUES (?,1)", (X,))
        if legacy_owner is not None:
            db.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT)")
            db.execute("INSERT INTO settings VALUES (?,?)", ("my_user_id", legacy_owner))
    before = path.read_bytes()
    with resources(tmp_path, legacy_db=path) as (_, store, ledger, reader, svc):
        switch(store, reader)
        store.set_favorite(B, Y, True)
        selected = svc.scheduler_favourite_authors(ledger=ledger)
        assert path.read_bytes() == before
        assert stars(store, B) == selected == (Y,)


def test_unknown_old_schedule_and_backlog_are_kept_without_running(tmp_path):
    with resources(tmp_path) as (root, store, ledger, reader, svc):
        ledger.configure(interval_hours=6, startup_check=False, expected_revision=1,
                         now=datetime.now(timezone.utc) - timedelta(hours=7))
        ledger._db.execute('ALTER TABLE schedule DROP COLUMN settings_origin')
        ledger._db.execute("INSERT INTO deferred_work VALUES ('1001','100','2020',1,0,'2020')")
        ledger.close()
        store.sync(reader)
        store.set_favorite(A, X, True)
        reopened = ManagedScheduler(root)
        try:
            reopened.attach_favourites(FavouriteBook(store, scheduler=reopened))
            prior = reopened.snapshot()
            runner = ManagedScheduleRunner(reopened, lambda slot, stop: svc.scheduler_favourite_authors(ledger=reopened))
            slot = runner._claim_reserved(datetime.now(timezone.utc))
            if slot:
                assert runner._dispatch_slot(slot) is False
            assert reopened._bound_account() is None and reopened.deferred_count() == 1
            current = reopened.snapshot()
            assert (current.interval_hours, current.startup_check) == (prior.interval_hours, prior.startup_check)
            assert stars(store, A) == (X,)
        finally:
            reopened.close()


def test_none_and_invalid_id_preserve_account_and_notify(tmp_path):
    with resources(tmp_path) as (_, store, ledger, reader, svc):
        store.sync(reader)
        store.set_favorite(A, X, True)
        assert svc.scheduler_favourite_authors(ledger=ledger) == (X,)
        now = datetime.now(timezone.utc)
        ledger.defer_work(author_id=X, work=dict(id="200", update_date="2020", page_count=1, type=0), now=now)
        ledger.configure_all(author_ids=None, interval_hours=6, startup_check=False,
                             expected_revision=ledger.snapshot().revision, now=now, account_id=A)
        assert stars(store, A) == (X,) and ledger.deferred_count() == 1
        _, ignored = ledger.configure_all(author_ids=(X, "9999"), interval_hours=6, startup_check=False,
                                          expected_revision=ledger.snapshot().revision, now=now, account_id=A, return_ignored=True)
        assert ignored == ("9999",) and stars(store, A) == (X,) and ledger.deferred_count() == 1

def test_account_b_can_edit_its_own_star_with_account_a_scheduler(tmp_path):
    with resources(tmp_path) as (_, store, ledger, reader, svc):
        store.sync(reader)
        store.set_favorite(A, X, True)
        assert svc.scheduler_favourite_authors(ledger=ledger) == (X,)
        switch(store, reader)
        rows = ApplicationServices.unified_download_authors(svc)
        notices = []
        view = NS(authors=[dict(r) for r in rows], services=svc, favorite_only=False,
                  set_notice=notices.append, _update_row=lambda uid: None, _render_favorite_card=lambda: None)
        result = UnifiedDownloadView.toggle_favorite(view, Y)
        assert ledger._bound_account() == A and stars(store, A) == (X,)
        assert result is True and stars(store, B) == (Y,)


def test_first_download_star_before_due_does_not_lock_scheduler_after_reopen(tmp_path):
    with resources(tmp_path) as (root, store, ledger, reader, svc):
        store.sync(reader)
        assert svc.set_favourite(X, True, account_id=A)
        assert ledger._bound_account() is None
        ledger.close()
        reopened = ManagedScheduler(root)
        try:
            reopened.attach_favourites(FavouriteBook(store, scheduler=reopened))
            try:
                selected = svc.scheduler_favourite_authors(ledger=reopened)
            except ScheduleAccountError as ex:
                pytest.fail(f"empty scheduler refuses to bind after its own star write: {ex.reason}")
            assert selected == (X,)
        finally:
            reopened.close()


def test_stop_gate_precedes_identity_and_preserves_state(tmp_path):
    from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopped
    with resources(tmp_path) as (_, store, ledger, reader, svc):
        store.sync(reader)
        store.set_favorite(A, X, True)
        events = []
        def gate():
            events.append("gate")
            raise ManagedNetworkStopped(429)
        def verify():
            events.append("identity")
            return A
        svc.managed_network_stop = NS(require_allowed=gate)
        reader.verify_identity = verify
        with pytest.raises(ManagedNetworkStopped):
            svc.scheduler_favourite_authors(ledger=ledger)
        assert events == ["gate"] and stars(store, A) == (X,) and ledger._bound_account() is None

@pytest.mark.parametrize("switch_during_inventory", (False, True))
def test_queue_fallback_preserves_a_backlog_when_account_changes(tmp_path, switch_during_inventory):
    from threading import Event
    with resources(tmp_path) as (_, store, ledger, reader, svc):
        store.sync(reader)
        store.set_favorite(A, X, True)
        assert svc.scheduler_favourite_authors(ledger=ledger) == (X,)
        ledger.defer_work(author_id=X, work=dict(id="200", update_date="2020", page_count=1, type=0),
                          now=datetime.now(timezone.utc))
        svc.batch_runs = None
        svc.author_media = None
        svc.jobs = NS(snapshot=lambda: ())
        svc.release_managed_owner = lambda owner: None
        calls = []
        def saved_snapshot():
            if not switch_during_inventory:
                reader._verified_identity = B
            return ()

        def inventory(author_id):
            calls.append((reader._verified_identity, author_id))
            reader._verified_identity = B
            return ()

        svc.managed_downloads = NS(saved_receipts_with_dates=saved_snapshot)
        reader.get_author_artworks_complete = inventory
        with pytest.raises(ScheduleAccountError) as error:
            ApplicationServices.dispatch_author_check(svc, ledger, "manual-check", Event())
        assert error.value.reason == "mismatch"
        assert calls == ([(A, X)] if switch_during_inventory else [])
        assert ledger.deferred_ids(X) == {"200"}



@pytest.mark.asyncio
async def test_account_b_form_cannot_prune_account_a_backlog(tmp_path):
    with resources(tmp_path) as (_, store, ledger, reader, svc):
        store.sync(reader)
        store.set_favorite(A, X, True)
        assert svc.scheduler_favourite_authors(ledger=ledger) == (X,)
        ledger.defer_work(author_id=X, work=dict(id="200", update_date="2020", page_count=1, type=0),
                          now=datetime.now(timezone.utc))
        switch(store, reader)
        client = Client(ui.page("/independent-current-b"))
        try:
            with client:
                view = SchedulerView(svc)
            view._load(ledger)
            assert view.authors.value == ""
            view.authors.set_value(Y)
            await view.save()
            assert stars(store, A) == (X,) and stars(store, B) == (Y,)
            assert ledger.deferred_ids(X) == {"200"} and ledger._bound_account() == A
        finally:
            client.delete()
