"""Synthetic product scheduler regression: fatal GET must stop the next author.

Both the legacy complete-inventory scheduler and the author-media scheduler
must notice the *durable* 401/403/429 latch even if an upstream read adapter
swallows the first error and returns an empty inventory. This is deliberately
not an actual Pixiv GET: only generated managed storage and injected methods
are touched. The second author's method must never be called.
"""

from threading import Event
from types import SimpleNamespace

import pytest

from pixivvault_nicegui.account_scope import AccountScope, OwnedListing
from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopped
from pixivvault_nicegui.managed_network_stop_integration import ManagedNetworkStopIntegration
from pixivvault_nicegui.managed_scheduler import submit_author_batch
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected


@pytest.mark.parametrize("status", (401, 403, 429))
@pytest.mark.parametrize("route", ("legacy", "author_media"))
def test_fatal_swallowed_in_first_author_stops_second_before_remote_inventory(
    tmp_path, status, route,
):
    root = tmp_path / "generated-managed-root"
    writer = ManagedPixivDownloads(root, object(), resolve_record=lambda _wid: None)
    try:
        # Writer creates the ownership marker first; the integration then
        # establishes the root-local durable gate, never the other way round.
        integration = ManagedNetworkStopIntegration(root)
        calls = []

        def generated_remote_inventory(author_id):
            calls.append(author_id)
            if author_id == "11":
                # Reproduce a pre-existing adapter that catches the first
                # typed HTTP exception. Without per-author scheduler admission
                # the loop would contact author 12 after this silent failure.
                try:
                    with integration.gate.request():
                        raise HTTPStatusRejected(status)
                except HTTPStatusRejected:
                    return ()
            return ()

        live_client = SimpleNamespace(
            _verified_identity="42", verify_identity=lambda: "42",
            get_author_artworks_complete=generated_remote_inventory,
        )
        services = SimpleNamespace(
            closing=False,
            managed_network_stop=integration,
            managed_downloads=writer,
            jobs=SimpleNamespace(snapshot=lambda: ()),
            live_client=live_client,
            author_media=(object() if route == "author_media" else None),
            author_media_preview_owned=lambda aid, **kw: OwnedListing(
                AccountScope("42", live_client), aid, "both", generated_remote_inventory(aid)),
            release_managed_owner=lambda _owner: None,
        )

        with pytest.raises(ManagedNetworkStopped) as stopped:
            submit_author_batch(services, ("11", "12"), cancellation=Event())
        assert stopped.value.status_code == status
        assert calls == ["11"], "a fatal first author must forbid the next remote inventory"
        persisted = ManagedNetworkStopIntegration(root).gate.snapshot()
        assert persisted.blocked is True and persisted.status_code == status
    finally:
        writer.close()
