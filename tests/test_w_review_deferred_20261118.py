import sqlite3
from datetime import datetime, timezone
import pytest
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
    ledger.bind_account(ACCOUNT)
    return following, ledger, book


def test_star_lookup_error_preserves_new_deferred(tmp_path):
    following, ledger, book = create_stars(tmp_path)
    try:
        book.set('1000', True, account_id=ACCOUNT)
        work = dict(id='5', update_date='d', page_count=1, type=0)
        # The sync metadata remains readable. SQLite fails only on the author list.
        following._connection.execute('ALTER TABLE following_authors RENAME TO temporarily_unreadable')
        assert book.account() == ACCOUNT
        with pytest.raises((sqlite3.OperationalError, FollowingSyncError)):
            ledger.favourites(ACCOUNT)
        ledger.defer_work(author_id='1000', work=work, now=datetime.now(timezone.utc), account_id=ACCOUNT)
        following._connection.execute('ALTER TABLE temporarily_unreadable RENAME TO following_authors')
        assert ledger.favourites(ACCOUNT) == ('1000',)
        ids = ledger.deferred_ids('1000')
        print(f'known star after repair; deferred IDs={sorted(ids)}')
        assert ids == {'5'}
    finally:
        ledger.close()
        following.close()


