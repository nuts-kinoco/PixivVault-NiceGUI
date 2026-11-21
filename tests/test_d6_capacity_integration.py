import asyncio
from dataclasses import replace
from threading import Event

import pytest
from nicegui import background_tasks

from test_library_view import library  # noqa: F401
from pixivvault_nicegui.capacity_cache import CapacityCache
from pixivvault_nicegui.legacy_capacity import LegacyCapacityReader, LegacyCapacityService, MeasurementLease
from pixivvault_nicegui.library_models import LibrarySnapshot
from pixivvault_nicegui.library_state import LibraryState


@pytest.mark.asyncio
async def test_search_reloads_concurrent_capacity_write_after_auto_measurement(library, tmp_path, monkeypatch):
    view, services, client = library
    monkeypatch.setattr(background_tasks, 'create', asyncio.create_task)
    author_id = '321'
    original = view.state.snapshot.works[0]
    work = replace(original, author_id=author_id,
                   artwork=replace(original.artwork, title='D6 capacity search', author_id=author_id))
    view.state = LibraryState(LibrarySnapshot((work,)))
    media = tmp_path / 'library'
    media.mkdir()
    author = media / f'Author({author_id})'
    author.mkdir()
    (author / 'disk.bin').write_bytes(b'X' * 1024)
    prefs = tmp_path / 'preferences'
    prefs.mkdir()
    cache = CapacityCache.open_owned(prefs)
    reader = LegacyCapacityReader(media)
    service = LegacyCapacityService(reader, cache)
    monkeypatch.setattr(services, 'legacy_capacity', service)
    started = Event()
    release = Event()
    scans = []
    measure = reader.measure

    def blocked_scan(aid):
        scans.append(aid)
        started.set()
        assert release.wait(5)
        return measure(aid)

    monkeypatch.setattr(reader, 'measure', blocked_scan)
    try:
        with client:
            view.render_authors()
            view._sync_panels()
            view._legacy_restore_needed = False
            view._schedule_unmeasured_capacity()
        worker = view._legacy_auto_task
        initial_enabled = view.legacy_capacity_button.enabled
        assert await asyncio.to_thread(started.wait, 5)
        # No progress or disk result has been published when search invalidates
        # the worker. A separate screen can commit during the search debounce.
        assert view.legacy_auto_capacity_status.text == ''
        with client:
            view.search('D6 capacity search')
        after_search_enabled = view.legacy_capacity_button.enabled
        await view.measure_legacy_capacity()
        assert scans == [author_id]
        assert not initial_enabled
        assert not after_search_enabled
        assert MeasurementLease().publish(cache, {author_id: 2048}, {author_id: 0}) == {author_id: 2048}
        release.set()
        await worker
        assert service.cached(author_id) == (1, 2048)
        async with asyncio.timeout(3):
            while view._legacy_restore_needed:
                await asyncio.sleep(.01)
        assert view.author_capacity_labels[author_id].text == '2.0 KiB'
        assert scans == [author_id]
        assert view.legacy_capacity_button.enabled

        with client:
            view.search('D6 capacity')
            view.close()
        assert view._search_capacity_delay is None
        assert view._legacy_restore_request is None
    finally:
        release.set()
        if view._legacy_auto_task is not None:
            await view._legacy_auto_task
        if view._search_capacity_delay is not None:
            view._search_capacity_delay.cancel()
        cache.close()
