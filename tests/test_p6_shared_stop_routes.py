"""Generated-only proofs of the production dispatch boundaries."""

import pytest

from pixivvault_nicegui.external_vault import VaultError
from pixivvault_nicegui.external_vault_transport import (
    ACCOUNT, INVALID, ONLINE, ShaftTransport, VaultResponse,
)
from pixivvault_nicegui.managed_network_stop_gate import (
    ManagedNetworkGateStorageError, ManagedNetworkStopped,
)
from pixivvault_nicegui.pixiv_read_client import _OwnedReadTransport
from pixivvault_nicegui.services import ApplicationServices
from stage5_generated_shared_owner import generated_shared_scope
from test_external_vault import FixtureCrypto, profile
from test_oauth_send_boundary import OfflineWire


@pytest.mark.parametrize('route', (ACCOUNT, INVALID, ONLINE))
def test_signed_shaft_route_honors_existing_stop(tmp_path, route):
    scope = generated_shared_scope(tmp_path)
    scope.gate.trip(429)
    wire = OfflineWire([VaultResponse(200, {})])
    transport = ShaftTransport(wire, oauth_profiles=[profile()])
    transport.bind_shared_pixiv_stop(scope)
    with transport.session_guard(lambda: True), pytest.raises(VaultError, match='pixiv-network-stopped'):
        transport._signed(route, {}, 'generated-signature-input')
    assert wire.calls == []


def test_shaft_payout_rejected_before_budget_when_stopped(tmp_path):
    scope = generated_shared_scope(tmp_path)
    scope.gate.trip(429)
    wire = OfflineWire([VaultResponse(200, {})])
    budget = []
    transport = ShaftTransport(wire, oauth_profiles=[profile()])
    transport.bind_shared_pixiv_stop(scope)
    transport.bind_request_budget(lambda *args: budget.append(args))
    with transport.session_guard(lambda: True), pytest.raises(VaultError, match='pixiv-network-stopped'):
        transport.fetch_account(1, 'generated-signature-input', {}, 1000)
    assert budget == wire.calls == []


def test_shaft_server_quota_retains_response_without_tripping_pixiv_stop(tmp_path):
    scope = generated_shared_scope(tmp_path)
    wire = OfflineWire([VaultResponse(429, {})])
    transport = ShaftTransport(wire, oauth_profiles=[profile()])
    transport.bind_shared_pixiv_stop(scope)
    during = []
    wire.before_send = lambda url: during.append(scope.gate.inflight_path.is_file())
    with transport.session_guard(lambda: True):
        assert transport._signed(ACCOUNT, {}, 'generated-signature-input').status == 429
    assert during == [True]
    assert not scope.gate.snapshot().blocked


def test_keeper_online_report_stays_pending_when_gate_rejects_dispatch(tmp_path):
    scope = generated_shared_scope(tmp_path)
    wire = OfflineWire([VaultResponse(200, {})])
    transport = ShaftTransport(wire, oauth_profiles=[profile()])
    transport.bind_shared_pixiv_stop(scope)
    transport.queue_online('generated-signature-input', 42, {
        'access_token': 'a', 'refresh_token': 'r', 'user': {'id': 42},
    }, 1000)
    scope.gate.trip(429)

    assert transport.online_tick(1000) == 900
    assert wire.calls == []
    assert len(transport._online) == 1
    assert transport._online[0]['attempt'] == 0


@pytest.mark.parametrize('binding', ('missing', 'clear', 'stopped'))
def test_product_borrowed_cdn_is_bound_before_publication(tmp_path, monkeypatch, binding):
    calls = []

    def fake_http(self, request, *_):
        calls.append(request.has_header('Cookie'))
        return b'generated-image'

    monkeypatch.setattr(_OwnedReadTransport, '__call__', fake_http)
    scope = None if binding == 'missing' else generated_shared_scope(tmp_path)
    if binding == 'stopped':
        scope.gate.trip(429)
    services = ApplicationServices(external_vault_root=tmp_path / 'vault',
                                   external_vault_crypto=FixtureCrypto(),
                                   shared_network_stop_scope=scope)
    try:
        url = 'https://i.pximg.net/img-original/img/2020/01/01/00/00/00/123_p0.png'
        if binding == 'clear':
            assert services._vault_images.get_image_bytes(url) == b'generated-image'
            assert calls == [False]
        else:
            with pytest.raises((ManagedNetworkGateStorageError, ManagedNetworkStopped)):
                services._vault_images.get_image_bytes(url)
            assert calls == []
    finally:
        services.close()
