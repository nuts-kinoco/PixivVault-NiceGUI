from datetime import datetime, timezone
import asyncio
import json
import sqlite3

import pytest

from pixivvault_nicegui import managed_generations as mg
from pixivvault_nicegui.bookmark_run import VISIBILITY_ROWS
from pixivvault_nicegui.managed_downloads import ManagedDownloadError
from pixivvault_nicegui.managed_root_writer import writer_fence
from test_v_dl_cap_20261117 import make_root, mark_of, publish, seed_index
from test_w_review_favourites_20261118 import create_stars
from test_w_review_bookmark_resume_20261118 import services_with_runs


def test_schedule_update_failure_keeps_old_stars(tmp_path):
    following, ledger, book = create_stars(tmp_path)
    try:
        book.set('1000', True)
        before = ledger.snapshot()
        ledger._db.execute("""CREATE TRIGGER reject_schedule_update BEFORE UPDATE OF interval_hours ON schedule
            BEGIN SELECT RAISE(ABORT, 'synthetic scheduler save failure'); END""")
        with pytest.raises(sqlite3.IntegrityError):
            ledger.configure_all(author_ids=('1001',), interval_hours=6, startup_check=False,
                                 expected_revision=before.revision, now=datetime.now(timezone.utc))
        assert ledger.snapshot() == before
        assert book.ids() == ('1000',)
    finally:
        ledger.close()
        following.close()


def test_inactive_follow_row_is_reported_as_ignored(tmp_path):
    following, ledger, book = create_stars(tmp_path)
    try:
        account = following.last_synced_account()
        following.record_unfollowed(account, '1001')
        assert '1001' not in {row.user_id for row in following.snapshot(account).following}
        before = ledger.snapshot()
        _, ignored = ledger.configure_all(author_ids=('1001',), interval_hours=6, startup_check=False,
                                          expected_revision=before.revision, now=datetime.now(timezone.utc),
                                          return_ignored=True)
        assert ignored == ('1001',)
    finally:
        ledger.close()
        following.close()