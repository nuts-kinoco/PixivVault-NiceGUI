"""A published recommendation URL must honor a later managed tombstone."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from pixivvault_nicegui.managed_download_catalog import build_managed_library
from pixivvault_nicegui.recommendation_media import (
    RecommendationMediaSnapshot, RecommendationMediaUnavailable,
)
from test_managed_quarantine import owned, plan_for, proof_bytes
from test_recommendation_media import application, preview


def test_issued_remote_catalog_url_cannot_bypass_a_new_managed_tombstone(owned, application):
    writer, quarantine, original_client = owned
    services, reader = application
    before_sources = proof_bytes(writer)
    media = RecommendationMediaSnapshot(services, (preview('123'),))
    selection = None
    try:
        selection = media.select('123')
        token = selection.images.token
        issued = media.thumbnail_url('123')
        assert issued and f'catalog={token}' in issued
        assert reader.calls == []

        # Models another client's completed download/library publication and
        # X07 exclusion after this recommendation already owned a remote pin.
        quarantine.execute(plan_for(quarantine, '123:1', '123:2', '123:3'),
                           operation_id='8' * 32)
        _, catalog, _ = build_managed_library(writer)
        services.images.publish(catalog, None)
        assert services.images.library.source_pages['123'] == ()
        with pytest.raises(RecommendationMediaUnavailable):
            media.ensure_source('123')
        before_remote = tuple(reader.calls)
        before_original = tuple(original_client.images)

        # This is the same registry boundary used by /media?catalog=... after a
        # lazy thumbnail or an already-open viewer requests another page.
        # Checking only media.select/ensure_source cannot protect this request.
        with pytest.raises((ValueError, RuntimeError)):
            services.images.get_versioned(token, '123', 1, 'view')
        assert tuple(reader.calls) == before_remote
        assert tuple(original_client.images) == before_original
        assert proof_bytes(writer) == before_sources
    finally:
        if selection is not None:
            selection.close()
        media.close()


def _publish_tombstone(writer, quarantine, services):
    quarantine.execute(plan_for(quarantine, '123:1', '123:2', '123:3'),
                       operation_id='9' * 32)
    _, catalog, _ = build_managed_library(writer)
    services.images.publish(catalog, None)
    assert services.images.library.source_pages['123'] == ()


def test_remote_blob_finishing_after_tombstone_is_not_returned(owned, application, monkeypatch):
    writer, quarantine, original_client = owned
    services, reader = application
    before_sources = proof_bytes(writer)
    media = RecommendationMediaSnapshot(services, (preview('123'),))
    token = media.remote_pin.token
    entered, release = Event(), Event()
    original_get = reader.get_image_bytes

    def held_bytes(url):
        data = original_get(url)
        entered.set()
        assert release.wait(6), 'synthetic image response barrier was not released'
        return data

    monkeypatch.setattr(reader, 'get_image_bytes', held_bytes)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(services.images.get_versioned, token, '123', 1, 'view')
            try:
                assert entered.wait(3)
                _publish_tombstone(writer, quarantine, services)
                before_remote = tuple(reader.calls)
                before_original = tuple(original_client.images)
                release.set()
                with pytest.raises((ValueError, RuntimeError)):
                    future.result(timeout=3)
                assert tuple(reader.calls) == before_remote
                assert tuple(original_client.images) == before_original
                assert proof_bytes(writer) == before_sources
            finally:
                release.set()
    finally:
        media.close()


def test_cached_recommendation_get_and_head_cannot_return_after_tombstone(owned, application):
    writer, quarantine, original_client = owned
    services, reader = application
    before_sources = proof_bytes(writer)
    media = RecommendationMediaSnapshot(services, (preview('123'),))
    token = media.remote_pin.token
    try:
        blob = services.images.get_versioned(token, '123', 0, 'thumb')
        assert services.images.metadata_versioned(token, '123', 0, 'thumb') == blob
        _publish_tombstone(writer, quarantine, services)
        before_remote = tuple(reader.calls)
        before_original = tuple(original_client.images)
        returned = []
        for label, action in (
            ('GET', lambda: services.images.get_versioned(token, '123', 0, 'thumb')),
            ('HEAD', lambda: services.images.metadata_versioned(token, '123', 0, 'thumb')),
        ):
            try:
                action()
            except (ValueError, RuntimeError):
                pass
            else:
                returned.append(label)
        assert returned == [], 'cached bytes/HEAD metadata bypassed the new source exclusion'
        assert tuple(reader.calls) == before_remote
        assert tuple(original_client.images) == before_original
        assert proof_bytes(writer) == before_sources
    finally:
        media.close()

