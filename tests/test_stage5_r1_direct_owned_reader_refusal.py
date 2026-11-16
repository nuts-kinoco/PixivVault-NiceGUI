"""No direct product LivePixivReadClient may bypass the shared STOP owner.

Uses an isolated synthetic Cookie and a strictly fake owned transport.
"""

import time

import pytest

from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkGateStorageError
from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopGate
from pixivvault_nicegui.managed_network_stop_reader import ManagedLivePixivReadClient
from pixivvault_nicegui.services import ApplicationServices


def test_direct_owned_live_client_without_shared_stop_is_rejected(tmp_path, monkeypatch):
    credential = tmp_path / "synthetic.txt"
    credential.write_text(
        "# Netscape HTTP Cookie File\n"
        f".pixiv.net\tTRUE\t/\tTRUE\t{int(time.time())+86400}"
        "\tPHPSESSID\t55_generatedonly\n", encoding="utf-8",
    )
    calls = []

    class SyntheticOwnedTransport:
        revocation_unverified = False

        def __call__(self, *_):
            calls.append("unsafe synthetic GET")
            return b"invalid"

        def close(self):
            pass

    import pixivvault_nicegui.pixiv_read_client as client_module
    monkeypatch.setattr(client_module, "_OwnedReadTransport", SyntheticOwnedTransport)
    client = LivePixivReadClient(credential)
    try:
        with pytest.raises(ManagedNetworkGateStorageError):
            ApplicationServices(live_client=client)
        assert calls == []
    finally:
        client.close()


def test_managed_live_reader_subclass_cannot_bypass_shared_owner(tmp_path):
    credential = tmp_path / "generated-cookie.txt"
    credential.write_text(
        "# Netscape HTTP Cookie File\n"
        f".pixiv.net\tTRUE\t/\tTRUE\t{int(time.time())+86400}"
        "\tPHPSESSID\t55_generatedonly\n", encoding="utf-8",
    )
    calls = []
    local = ManagedNetworkStopGate(tmp_path / "generated-only-local-stop.sqlite3")
    reader = ManagedLivePixivReadClient(
        credential, local, transport=lambda *_: calls.append("unsafe generated GET") or b"bad",
    )
    try:
        with pytest.raises(ManagedNetworkGateStorageError):
            ApplicationServices(live_client=reader)
        assert calls == []
    finally:
        reader.close()
