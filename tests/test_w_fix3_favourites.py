from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
from threading import Event

import pytest

from pixivvault_nicegui.managed_scheduler import ScheduleError
from test_w_review_favourites_20261118 import create_stars


def test_form_save_blocks_then_rejects_stale_revision(tmp_path, monkeypatch):
    following, ledger, book = create_stars(tmp_path)
    star_written, form_attempting, allow_commit = Event(), Event(), Event()
    original = ledger.touch_revision

    def hold_revision():
        star_written.set()
        assert allow_commit.wait(8)
        original()

    def stale_save():
        form_attempting.set()
        with pytest.raises(ScheduleError, match='changed in another client'):
            ledger.configure_all(author_ids=('1000',), interval_hours=6, startup_check=False,
                                 expected_revision=before.revision, now=datetime.now(timezone.utc))

    try:
        assert book.set('1000', True)
        before = ledger.snapshot()
        prior_follow = following.snapshot(book.account()).revision
        monkeypatch.setattr(ledger, 'touch_revision', hold_revision)
        with ThreadPoolExecutor(max_workers=2) as pool:
            click = pool.submit(book.set, '1001', True)
            try:
                assert star_written.wait(8)
                form = pool.submit(stale_save)
                assert form_attempting.wait(8)
                assert not wait((form,), timeout=0.25).done
            finally:
                allow_commit.set()
            assert click.result(timeout=8)
            form.result(timeout=8)
        assert ledger.snapshot().revision == before.revision + 1
        assert following.snapshot(book.account()).revision == prior_follow + 1
        assert book.ids() == ('1000', '1001')
    finally:
        allow_commit.set()
        ledger.close()
        following.close()


def test_star_and_revision_rollback_together(tmp_path):
    import sqlite3
    following, ledger, book = create_stars(tmp_path)
    try:
        before = ledger.snapshot()
        ledger._db.execute("CREATE TRIGGER reject_revision BEFORE UPDATE OF revision ON schedule "
                           "BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
        with pytest.raises(sqlite3.IntegrityError):
            book.set('1001', True)
        assert ledger.snapshot() == before
        assert book.ids() == ()
    finally:
        ledger.close()
        following.close()
