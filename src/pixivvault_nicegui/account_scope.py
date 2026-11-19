"""Fail closed when an account-scoped result belongs to another account."""


from dataclasses import dataclass
import re


class AccountMismatch(RuntimeError):
    code = 'author_account_mismatch'

    def __init__(self):
        super().__init__('account changed')


def require_same_account(expected_owner, observed, *, error=None):
    if expected_owner != observed:
        raise AccountMismatch() if error is None else error


def require_current_account(services, reader, account, *, error=None):
    """Recheck the reader that produced the result before adopting it."""
    observed = getattr(reader, '_verified_identity', None)
    if reader is None or services.live_client is not reader or observed is None:
        raise AccountMismatch() if error is None else error
    require_same_account(account, observed, error=error)


@dataclass(frozen=True, slots=True)
class AccountScope:
    account_id: str
    reader: object | None = None

    def __post_init__(self):
        if type(self.account_id) is not str or re.fullmatch(r'[1-9][0-9]{0,19}', self.account_id) is None:
            raise AccountMismatch()

    def verify_observed(self) -> None:
        require_same_account(self.account_id, getattr(self.reader, '_verified_identity', None))

    def verify_current(self, services, *, error=None) -> str:
        require_current_account(services, self.reader, self.account_id, error=error)
        return self.account_id


@dataclass(frozen=True, slots=True)
class ConfirmedFollowingSwitch:
    """Proof for the explicit delete-and-resync confirmation."""

    old_account: str
    new_scope: AccountScope

    def __post_init__(self):
        AccountScope(self.old_account)
        if type(self.new_scope) is not AccountScope or self.old_account == self.new_scope.account_id:
            raise AccountMismatch()

    def verify(self, account_id, last_synced_account):
        require_same_account(self.old_account, account_id)
        require_same_account(self.old_account, last_synced_account)
        self.new_scope.verify_observed()


@dataclass(frozen=True, slots=True)
class OwnedListing:
    scope: AccountScope
    author_id: str
    target_type: str
    rows: tuple[object, ...]

    def __post_init__(self):
        if (type(self.scope) is not AccountScope or type(self.rows) is not tuple
                or type(self.author_id) is not str or re.fullmatch(r'[1-9][0-9]{0,19}', self.author_id) is None
                or self.target_type not in ('both', 'illust', 'novel')):
            raise AccountMismatch()

    @property
    def account_id(self) -> str:
        return self.scope.account_id


class BoundSchedulerLedger:
    """Deferred-work writes require the account scope of the current dispatch."""

    def __init__(self, ledger, scope: AccountScope, services):
        if type(scope) is not AccountScope:
            raise AccountMismatch()
        self._ledger = ledger
        self._scope = scope
        self._services = services

    def defer_work(self, *, author_id, work, now):
        account = self._scope.verify_current(self._services)
        self._ledger.defer_work(author_id=author_id, work=work, now=now, account_id=account)

    def resolve_deferred(self, author_id, work_id):
        account = self._scope.verify_current(self._services)
        self._ledger.resolve_deferred(author_id, work_id, account_id=account)
