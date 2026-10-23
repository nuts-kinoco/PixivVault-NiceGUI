"""Candidate authorization for author selections is all-or-nothing."""

from collections import OrderedDict
from threading import RLock

import pytest

from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.services import ApplicationServices


def record(wid, *, visibility='show'):
    return RemoteBookmark(str(wid), 'illustration', visibility, f'Work {wid}', '91',
                          'Synthetic author', (), '', '', 1, 0, 0)


def service():
    app = object.__new__(ApplicationServices)
    app._managed_target_lock = RLock()
    app._direct_download_candidates = {}
    app._author_owned_pins = {}
    app._remote_records = ()
    app.closing = False
    app.managed_downloads = object()
    return app


def test_batch_rejects_over_capacity_without_eviction():
    app = service()
    owner = 'a' * 32
    previous = OrderedDict((str(i), record(i)) for i in range(1, 128))
    app._direct_download_candidates[owner] = previous
    with pytest.raises(ValueError, match='capacity'):
        app.remember_managed_author_batch(owner, (record(200), record(201)))
    assert app._direct_download_candidates[owner] is previous
    assert tuple(app._direct_download_candidates[owner]) == tuple(previous)


def test_batch_invalid_later_item_never_publishes_earlier_one():
    app = service()
    owner = 'a' * 32
    with pytest.raises(ValueError, match='rejected'):
        app.remember_managed_author_batch(owner, (record(123), object()))
    assert owner not in app._direct_download_candidates


def test_author_resolved_metadata_wins_over_stale_shared_bookmark():
    app = service()
    owner = 'a' * 32
    pinned_bookmark = record(123, visibility='hide')
    app._remote_records = (pinned_bookmark,)
    result = app.remember_managed_author_batch(owner, (record(123), record(124)))
    assert result[0] != pinned_bookmark
    assert result[0].visibility == 'show'
    assert result[1].remote_id == '124'
    assert list(app._direct_download_candidates[owner]) == ['123', '124']
    assert app.bookmark_download_candidates() == (pinned_bookmark,)
    # A later shared bookmark refresh must not switch the accepted candidate.
    app._remote_records = ()
    assert any(item is result[0] for item in app.download_candidates(owner=owner))
