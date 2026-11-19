from types import SimpleNamespace

import pytest

from pixivvault_nicegui.account_scope import AccountMismatch, require_current_account


def test_identity_observation_is_required_even_when_reader_matches():
    reader = SimpleNamespace()
    with pytest.raises(AccountMismatch):
        require_current_account(SimpleNamespace(live_client=reader), reader, "55")
