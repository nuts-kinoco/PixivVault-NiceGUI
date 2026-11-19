"""Account-scoped stars in the following ledger."""
from __future__ import annotations

from .account_scope import require_same_account
from .managed_scheduler import ScheduleAccountError, ScheduleError


class FavouriteBook:
    def __init__(self, following, *, scheduler=None):
        self.following = following
        self.scheduler = scheduler

    def account(self):
        """The newest completed follow sync, without network access."""
        return None if self.following is None else self.following.last_synced_account()

    def ids(self, account_id):
        """Read only the requested account's stars."""
        if self.following is None or account_id is None:
            raise ScheduleError('favourite authors unavailable')
        snapshot = self.following.snapshot(account_id)
        require_same_account(account_id, snapshot.account_id)
        if snapshot.last_synced_at is None:
            raise ScheduleError('favourite authors unavailable')
        return tuple(sorted((row.user_id for row in snapshot.authors if row.is_favorite), key=int))

    def set(self, user_id, favourite, *, account_id, expected=None):
        """Change a star only while the displayed account is still current."""
        if account_id is None or self.following is None:
            return False
        if self.scheduler is not None:
            return self.scheduler.set_star(self, user_id, favourite,
                                           account_id=account_id, expected=expected)
        with self.following._lock:
            require_same_account(account_id, self.account(), error=ScheduleAccountError('mismatch'))
            before = self.following.snapshot(account_id)
            require_same_account(account_id, before.account_id)
            if expected is not None:
                row = next((r for r in before.authors if r.user_id == user_id), None)
                if row is None or row.is_favorite != expected:
                    return False
            after = self.following.set_favorite(account_id, user_id, favourite,
                                               expected_revision=before.revision)
            return before.revision != after.revision
