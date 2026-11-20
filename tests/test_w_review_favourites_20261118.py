import sqlite3
from datetime import datetime, timezone
import pytest
from account_test_support import schedule_owner
from pixivvault_nicegui.favourites import FavouriteBook
from pixivvault_nicegui.following_sync import FollowingSyncStore, FollowingSyncError
from pixivvault_nicegui.managed_scheduler import ManagedScheduler
from test_v_dl_priority_20261117 import ACCOUNT, sync


def create_stars(tmp_path):
    root = tmp_path / 'managed'
    root.mkdir()
    following = FollowingSyncStore(tmp_path / 'follow')
    ledger = ManagedScheduler(root)
    sync(following, 3)
    book = FavouriteBook(following, scheduler=ledger)
    ledger.attach_favourites(book)
    ledger.bind_account(ACCOUNT, scope=schedule_owner(ledger, ACCOUNT))
    return following, ledger, book


def test_form_save_error_rolls_back_all_stars(tmp_path):
    following, ledger, book = create_stars(tmp_path)
    try:
        book.set('1000', True, account_id=ACCOUNT)
        before = ledger.snapshot()
        following._connection.execute("""CREATE TRIGGER deny_second_star BEFORE UPDATE OF is_favorite ON following_authors
             WHEN OLD.user_id='1001' BEGIN SELECT RAISE(ABORT, 'synthetic write failure'); END""")
        with pytest.raises((sqlite3.IntegrityError, FollowingSyncError)):
            ledger.configure_all(author_ids=('1001',), interval_hours=6, startup_check=False,
                                 expected_revision=before.revision, now=datetime.now(timezone.utc), account_id=schedule_owner(ledger, ACCOUNT))
        after = ledger.snapshot()
        stars = ledger.favourites(ACCOUNT)
        print(f'form save failed: revision {before.revision} -> {after.revision}, stars={stars}')
        assert after == before and stars == ('1000',)
    finally:
        ledger.close()
        following.close()


