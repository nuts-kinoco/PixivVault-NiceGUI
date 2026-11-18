"""One home for the star (☆) authors: `following_authors.is_favorite` in the following ledger.

The original kept the star in a single column (`following_users.is_favorite`). The port had
three copies: the frozen legacy DB (read only), the following ledger (no writer) and the
scheduler's own `favourite_authors` list (capped at 128). From now on the following ledger is the
only truth. The scheduler's periodic check, the download screen and the author list all read it.

`seed` imports the two older copies once (union), after the ledger has a completed sync, and
records what it did in the scheduler database. It never talks to the network and never writes a
legacy DB.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3

from .fs_paths import native_io_path, sqlite_readonly_uri


class FavouriteBook:
    def __init__(self, following, *, scheduler=None, legacy_db=None):
        self.following = following
        self.scheduler = scheduler            # ManagedScheduler: legacy list + the seeded marker
        self.legacy_db = None if legacy_db is None else Path(legacy_db)

    # -- reading ------------------------------------------------------------------------
    def account(self):
        """The account of the newest completed sync (no network)."""
        if self.following is None:
            return None
        return self.following.last_synced_account()

    def ids(self):
        """Every ☆ author of the synced account, sorted. () until the first sync."""
        account = self.account()
        if account is None:
            return ()
        rows = self.following.snapshot(account).authors
        return tuple(sorted((row.user_id for row in rows if row.is_favorite), key=int))

    def readable(self):
        """True only while there is a following ledger with a completed sync to read the stars from.

        Without one an empty answer means "unknown", never "no stars": nothing may be pruned on it."""
        if self.following is None:
            return False
        try:
            return self.account() is not None
        except Exception:
            return False

    def seeded(self):
        return self.scheduler is not None and self.scheduler.favourites_seeded()

    # -- writing ------------------------------------------------------------------------
    def set(self, user_id, favourite):
        """Flip one star. Only a followed (synced) author can carry one, as in the original."""
        account = self.account()
        if account is None:
            return False
        user_id, favourite = str(user_id), bool(favourite)
        row = next((r for r in self.following.snapshot(account).authors if r.user_id == user_id), None)
        if row is None or row.is_favorite == favourite:
            return False                      # not followed, or nothing to change
        self.following.set_favorite(account, user_id, favourite)
        if self.scheduler is not None:
            # A settings form opened before this change holds the old star list; its save must fail the
            # revision check instead of replacing the new stars.
            self.scheduler.touch_revision()
        return True

    def replace(self, author_ids):
        """Make exactly `author_ids` the ☆ set. Returns (applied, ignored): authors the ledger has no row for."""
        account = self.account()
        if account is None:
            return (), tuple(author_ids)
        rows = {row.user_id: row for row in self.following.snapshot(account).authors}
        wanted = set(author_ids)
        for user_id, row in rows.items():
            if row.is_favorite != (user_id in wanted):
                self.following.set_favorite(account, user_id, user_id in wanted)
        applied = tuple(sorted((u for u in wanted if u in rows), key=int))
        return applied, tuple(sorted((u for u in wanted if u not in rows), key=int))

    # -- the one-time import ------------------------------------------------------------------
    def _legacy_ids(self):
        """☆ authors of the frozen legacy copy; read only (`immutable=1`).

        () when there is genuinely nothing to import (no legacy DB, no file, no star column or table).
        None when it could not be read (locked, damaged, a transient error): the import must not
        be recorded as done on that, it is retried at the next read."""
        if self.legacy_db is None:
            return ()
        try:
            path = native_io_path(self.legacy_db)
            if not path.is_file():
                return ()
            with closing(sqlite3.connect(sqlite_readonly_uri(self.legacy_db), uri=True, timeout=0.2)) as db:
                rows = db.execute('SELECT user_id FROM following_users WHERE is_favorite=1').fetchall()
            return tuple(str(r[0]) for r in rows if str(r[0]).isdecimal())
        except sqlite3.OperationalError as error:
            if 'no such table' in str(error) or 'no such column' in str(error):
                return ()
            return None
        except Exception:
            return None

    def seed(self):
        """Import the legacy ☆ and the scheduler's list into the ledger, once.

        Returns None when it cannot run yet (no completed sync) or already ran; otherwise
        {'imported': n, 'dropped': n}. A ☆ for an author the account does not follow cannot be
        kept (the original stored the star on the follow row), so it is counted as dropped.
        """
        if self.scheduler is None or self.seeded():
            return None
        account = self.account()
        if account is None:
            return None
        legacy = self._legacy_ids()
        if legacy is None:
            return None  # unreadable now: not seeded, tried again next time
        sources = set(legacy) | set(self.scheduler.legacy_favourites())
        rows = {row.user_id: row for row in self.following.snapshot(account).authors}
        imported = dropped = 0
        for user_id in sorted(sources, key=int):
            if user_id not in rows:
                dropped += 1
            else:
                if not rows[user_id].is_favorite:
                    self.following.set_favorite(account, user_id, True)
                imported += 1
        self.scheduler.mark_favourites_seeded(imported=imported, dropped=dropped)
        return {'imported': imported, 'dropped': dropped}
