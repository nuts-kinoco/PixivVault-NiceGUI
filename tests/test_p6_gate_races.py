"""Hold the production shared STOP with an isolated borrowed CDN request."""

from contextlib import ExitStack, contextmanager
from concurrent.futures import Future
from threading import Event, Thread

import pytest

from pixivvault_nicegui.external_vault import VaultError
from pixivvault_nicegui.external_vault_transport import ACCOUNT, INVALID, OAUTH, RANKING, VaultResponse
from pixivvault_nicegui.managed_network_stop_public_cdn import bind_public_cdn_shared_stop
from pixivvault_nicegui.managed_network_stop_shared_scope import SharedGetOutcomeUncertain
from pixivvault_nicegui.pixiv_read_client import PublicImageReadClient, _OwnedReadTransport
from test_oauth_send_boundary import make_vault, payout


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


@pytest.mark.parametrize('reply', [VaultError('transport-failed'), VaultResponse(103, {}), VaultResponse(600, {})])
def test_borrowed_pixiv_get_without_final_reply_preserves_shared_record(tmp_path, reply):
    vault, wire, scope = make_vault(tmp_path, [reply])
    try:
        with vault.transport.session_guard(lambda: True):
            with pytest.raises(VaultError, match='pixiv-outcome-uncertain'):
                vault.transport._request('GET', RANKING, {})
        assert wire.calls == [RANKING]
        with pytest.raises(SharedGetOutcomeUncertain):
            scope.require_allowed()
        plan = scope.preview_uncertain_get_recovery()
        assert plan is not None and scope.gate.inflight_path.is_file()
    finally:
        vault.close()


@pytest.mark.parametrize('retry', ('manual', 'keeper', 'stopped', 'unknown-after-send'))
def test_invalid_report_deferred_by_shared_gate_is_sent_at_most_once(tmp_path, monkeypatch, retry):
    vault, wire, scope = make_vault(tmp_path, [payout(), VaultResponse(400, {'error': 'invalid_grant'})])
    transport = vault.transport
    report = transport._report_invalid
    try:
        with ExitStack() as race:
            def report_while_cdn_holds_gate(*args):
                race.enter_context(held_cdn(scope, monkeypatch))
                return report(*args)

            monkeypatch.setattr(transport, '_report_invalid', report_while_cdn_holds_gate)
            with pytest.raises(VaultError, match='pixiv-network-busy'):
                vault.refresh(1)
            assert wire.calls == [ACCOUNT, OAUTH]
            assert vault._snapshot()[1]['slots']['1']['rejected_refresh_hashes']
        monkeypatch.setattr(transport, '_report_invalid', report)

        if retry == 'stopped':
            scope.gate.trip(429)
            transport.online_tick(1000)
            with pytest.raises(VaultError, match='pixiv-network-stopped'):
                vault.refresh(1)
            assert wire.calls == [ACCOUNT, OAUTH]
            return

        # Keep returning the same expired payout. The persisted rejection
        # suppresses another OAuth call but must not suppress the pending report.
        send = wire.fenced_request

        def replay_expired(method, url, **kwargs):
            if url == ACCOUNT:
                wire.responses.append(payout())
            elif retry == 'unknown-after-send':
                wire.responses.append(VaultError('transport-failed'))
            else:
                wire.responses.append(VaultResponse(200, {}))
            return send(method, url, **kwargs)

        monkeypatch.setattr(wire, 'fenced_request', replay_expired)
        if retry == 'keeper':
            transport.online_tick(1000)
            assert wire.calls == [ACCOUNT, OAUTH, INVALID]
        for _ in range(2):
            with pytest.raises(VaultError, match='expired-payout'):
                vault.refresh(1)
        transport.online_tick(1000)
        assert wire.calls == [ACCOUNT, OAUTH, INVALID, ACCOUNT, ACCOUNT, ACCOUNT, ACCOUNT]
    finally:
        vault.close()
