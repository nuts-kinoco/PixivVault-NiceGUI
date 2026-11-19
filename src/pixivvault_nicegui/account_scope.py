"""Fail closed when an account-scoped result belongs to another account."""


class AccountMismatch(RuntimeError):
    code = 'author_account_mismatch'

    def __init__(self):
        super().__init__('account changed')


def require_same_account(expected_owner, observed, *, error=None):
    if expected_owner != observed:
        raise AccountMismatch() if error is None else error


def require_current_account(services, reader, account, *, error=None):
    """Recheck the reader that produced the result before adopting it."""
    if reader is None or services.live_client is not reader:
        raise AccountMismatch() if error is None else error
    require_same_account(account, getattr(reader, '_verified_identity', account), error=error)
