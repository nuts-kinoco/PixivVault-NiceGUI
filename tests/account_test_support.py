"""Synthetic identity proof for fixtures that seed an owned database."""
from types import SimpleNamespace

from pixivvault_nicegui.account_scope import AccountScope


def observed_scope(account_id):
    return AccountScope(account_id, SimpleNamespace(_verified_identity=account_id))


def schedule_owner(ledger, account_id):
    services = SimpleNamespace(live_client=observed_scope(account_id).reader)
    return ledger.capture_owner(services, account_id=account_id)
