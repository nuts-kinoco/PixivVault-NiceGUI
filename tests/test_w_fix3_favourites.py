from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
from threading import Event

import pytest

from pixivvault_nicegui.managed_scheduler import ScheduleError
from test_w_review_favourites_20261118 import create_stars


def test_stale_settings_do_not_erase_concurrent_star(tmp_path, monkeypatch):
    following, ledger, book = create_stars(tmp_path)
    ready, finish = Event(), Event()
    original_touch = ledger.touch_revision

    def paused_touch():
        ready.set()
        assert finish.wait(8)
        original_touch()

    try:
        book.set('1000', True)
        stale = ledger.snapshot()
        monkeypatch.setattr(ledger, 'touch_revision', paused_touch)
        with ThreadPoolExecutor(max_workers=2) as pool:
            click = pool.submit(book.set, '1001', True)
            assert ready.wait(8)
            def save():
                try:
                    ledger.configure_all(author_ids=('1000',), interval_hours=6, startup_check=False,
                                         expected_revision=stale.revision, now=datetime.now(timezone.utc))
                except ScheduleError:
                    return 'conflict'
                return 'saved'
            form = pool.submit(save)
            try:
                # The form may race for the lock; either ordering must preserve the star.
                pass
            finally:
                finish.set()
            assert click.result(timeout=8)
            result = form.result(timeout=8)
        assert result == 'conflict'
        assert book.ids() == ('1000', '1001')
    finally:
        finish.set()
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
