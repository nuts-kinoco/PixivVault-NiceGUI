"""Hold the production shared STOP with an isolated borrowed CDN request."""

from contextlib import contextmanager
from concurrent.futures import Future
from threading import Event, Thread

import pytest

from pixivvault_nicegui.external_vault import VaultError
from pixivvault_nicegui.external_vault_transport import ACCOUNT, VaultResponse
from pixivvault_nicegui.managed_network_stop_public_cdn import bind_public_cdn_shared_stop
from pixivvault_nicegui.pixiv_read_client import PublicImageReadClient, _OwnedReadTransport
from test_oauth_send_boundary import make_vault


@contextmanager
def held_cdn(scope, monkeypatch):
    holding, release = Event(), Event()
    outcome = Future()

    def fake_image(self, *_):
        holding.set()
        assert release.wait(8)
        return b'generated-image'

    with monkeypatch.context() as patch:
        patch.setattr(_OwnedReadTransport, '__call__', fake_image)
        client = PublicImageReadClient()
        bind_public_cdn_shared_stop(client, scope)

        def read():
            try:
                outcome.set_result(client.get_image_bytes('https://i.pximg.net/generated/fixture.png'))
            except Exception as exc:
                outcome.set_exception(exc)

        worker = Thread(target=read)
        worker.start()
        try:
            assert holding.wait(5)
            yield
        finally:
            release.set()
            worker.join(5)
            client.close()
            assert not worker.is_alive()
            assert outcome.result(timeout=1) == b'generated-image'


@pytest.mark.parametrize('proactive', (True, False))
def test_first_account_gate_busy_consumes_no_failure_or_wait(tmp_path, monkeypatch, proactive):
    response = VaultResponse(200, {'account': {'access_token': 'a'}, 'expiresAt': 9000000})
    vault, wire, scope = make_vault(tmp_path, [response])
    charges = []
    vault.transport.bind_request_budget(lambda *args: charges.append(args[0]))
    try:
        prior = vault._snapshot()[1]['slots']['1'].copy()
        update = vault.keeper_tick if proactive else lambda: vault.refresh(1)
        with held_cdn(scope, monkeypatch):
            for _ in range(1 if proactive else 3):
                with pytest.raises(VaultError, match='pixiv-network-busy'):
                    update()
            assert wire.calls == charges == []
            assert vault._snapshot()[1]['slots']['1'] == prior
            assert vault.status()['slots'][0]['failures'] == 0
            assert vault.status()['slots'][0]['retry_after'] == 0
        assert vault._keeper_next_wait() == 1
        update()
        assert wire.calls == [ACCOUNT] and charges == ['payout']
        assert vault.status()['slots'][0]['failures'] == 0
    finally:
        vault.close()
