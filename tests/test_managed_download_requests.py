"""Offline managed intent durability and hostile-file checks."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json

import pytest

from pixivvault_nicegui.fs_paths import logical_path

from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads, MARKER
from pixivvault_nicegui.managed_download_requests import ManagedDownloadRequests, ManagedRequestError


def record():
    return RemoteBookmark('123', 'illustration', 'show', 'Synthetic', '456', 'Artist',
                          ('tag',), 'https://i.pximg.net/secret', '2026-10-23T00:00:00Z', 2, 1, 0)


def setup(tmp_path):
    root = tmp_path / 'managed'
    ManagedPixivDownloads(root, object(), resolve_record=lambda _: record()).close()
    return root, ManagedDownloadRequests(root)


def test_save_load_no_url_and_idempotent(tmp_path):
    root, store = setup(tmp_path)
    original = record()
    store.store('op-01', original)
    file = root / 'requests' / (hashlib.sha256(b'op-01').hexdigest() + '.json')
    previous = file.read_bytes()
    assert b'pximg' not in previous and b'op-01' not in previous
    assert store.equivalent(original, store.load('op-01'))
    assert store.load('op-01').thumbnail == ''
    store.store('op-01', replace(original, thumbnail='https://i.pximg.net/other'))
    assert file.read_bytes() == previous
    with pytest.raises(ManagedRequestError, match='collision'):
        store.store('op-01', replace(original, title='Different'))
    assert file.read_bytes() == previous
    reopened = ManagedDownloadRequests(root)
    assert reopened.equivalent(reopened.load('op-01'), original)


def test_bad_marker_foreign_root_and_identity(tmp_path):
    with pytest.raises(ManagedRequestError):
        ManagedDownloadRequests(tmp_path / 'missing')
    root, _ = setup(tmp_path)
    marker = root / MARKER
    marker.write_bytes(b'{' + b' ' * 5000)
    with pytest.raises(ManagedRequestError):
        ManagedDownloadRequests(root)


def test_tamper_duplicate_depth_and_size(tmp_path):
    root, store = setup(tmp_path)
    store.store('op', record())
    file = next((root / 'requests').iterdir())
    for raw in (b'X' * (128 * 1024 + 1), b'{bad', b'{"title":"A","title":"B"}',
                b'[' * 1500 + b'0' + b']' * 1500):
        file.write_bytes(raw)
        with pytest.raises(ManagedRequestError):
            store.load('op')
    with pytest.raises(ManagedRequestError):
        store.store('op', record())


def test_invalid_metadata_and_no_foreign_overwrite(tmp_path):
    root, store = setup(tmp_path)
    for invalid in (replace(record(), remote_id='000123'),
                    replace(record(), illustration_type=True),
                    replace(record(), page_count_known=False),
                    replace(record(), title='control\nchar')):
        with pytest.raises(ManagedRequestError):
            store.store('op', invalid)
    foreign = root / 'requests' / 'unknown.bin'
    foreign.write_bytes(b'original')
    with pytest.raises(ManagedRequestError):
        store.store('new', record())
    assert foreign.read_bytes() == b'original'


def test_directory_capacity_and_nonlink_checks(tmp_path, monkeypatch):
    import pixivvault_nicegui.managed_download_requests as module
    root, store = setup(tmp_path)
    store.store('op', record())
    target = next((root / 'requests').iterdir())
    monkeypatch.setattr(module, 'MAX_INTENTS', 1)
    with pytest.raises(ManagedRequestError, match='directory rejected'):
        store.store('another', record())
    assert store.load('op').remote_id == '123'
    original_lstat = module.Path.lstat

    class FakeStat:
        def __init__(self, source, **changes):
            self.source, self.changes = source, changes
        def __getattr__(self, key):
            return self.changes.get(key, getattr(self.source, key))

    hits = []

    def spoof(path):
        info = original_lstat(path)
        if logical_path(path) == target:
            hits.append(path)
            return FakeStat(info, st_nlink=2)
        return info

    monkeypatch.setattr(module.Path, 'lstat', spoof)
    with pytest.raises(ManagedRequestError):
        store.load('op')
    assert hits, 'hardlink guard injection was not reached'
    hits.clear()

    def reparse(path):
        info = original_lstat(path)
        if logical_path(path) == target:
            hits.append(path)
            return FakeStat(info, st_file_attributes=0x400)
        return info

    monkeypatch.setattr(module.Path, 'lstat', reparse)
    with pytest.raises(ManagedRequestError):
        store.load('op')
    assert hits, 'reparse guard injection was not reached'


@pytest.mark.parametrize('failure', ['fsync', 'rename'])
def test_staging_failure_restart_allows_next_intent(tmp_path, monkeypatch, failure):
    import pixivvault_nicegui.managed_download_requests as module
    root, store = setup(tmp_path)
    original_fsync = module._fsync_dir
    original_rename = module.Path.rename

    calls = []
    fired = []

    def broken_fsync(path):
        calls.append(path)
        if logical_path(path) == store.incomplete:
            fired.append(path)
            raise OSError('synthetic filesystem failure')
        return original_fsync(path)

    def broken_rename(path, target):
        calls.append(path)
        if logical_path(path.parent) == store.incomplete:
            fired.append(path)
            raise OSError('synthetic filesystem failure')
        return original_rename(path, target)

    with monkeypatch.context() as patcher:
        if failure == 'fsync':
            patcher.setattr(module, '_fsync_dir', broken_fsync)
        else:
            patcher.setattr(module.Path, 'rename', broken_rename)
        with pytest.raises(ManagedRequestError, match='save failed'):
            store.store('interrupted', record())
    assert calls and len(fired) == 1, 'intended filesystem fault did not fire'
    assert not tuple((root / 'requests').iterdir())
    remnants = tuple((root / 'request-incomplete').iterdir())
    assert len(remnants) == 1 and remnants[0].name.startswith('staging-')
    assert b'pximg' not in remnants[0].read_bytes()
    restarted = ManagedDownloadRequests(root)
    restarted.store('accepted', replace(record(), title='After restart'))
    assert restarted.load('accepted').title == 'After restart'
    with pytest.raises(ManagedRequestError):
        restarted.load('interrupted')
    assert remnants[0].exists()
