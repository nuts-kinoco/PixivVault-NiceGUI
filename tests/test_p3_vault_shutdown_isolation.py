"""Failed Vault socket revocation must not strand independent application owners."""
from types import SimpleNamespace

import pytest

from pixivvault_nicegui.external_vault import VaultError
from pixivvault_nicegui.services import ApplicationServices


@pytest.mark.parametrize('fail_vault', (False, True))
def test_shutdown_preserves_owner_close_order_after_unverified_vault_lock(fail_vault):
    events = []
    services = ApplicationServices(1)

    def close_vault():
        events.append('vault')
        if fail_vault:
            raise VaultError('socket-shutdown-unverified')

    services.external_vault = SimpleNamespace(close=close_vault)
    services.static_jobs = SimpleNamespace(close=lambda *, timeout: events.append('jobs'))
    services.managed_downloads = SimpleNamespace(close=lambda: events.append('downloads'))
    services.images = SimpleNamespace(close=lambda: events.append('images'))
    services.live_client = SimpleNamespace(close=lambda: events.append('reader'))
    if fail_vault:
        with pytest.raises(VaultError, match='socket-shutdown-unverified'):
            services.close()
    else:
        services.close()
    assert events == ['vault', 'jobs', 'downloads', 'images', 'reader']
    assert services.closing and services.closed
    services.close()
    assert events == ['vault', 'jobs', 'downloads', 'images', 'reader']
