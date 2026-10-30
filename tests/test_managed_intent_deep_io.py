"""Deep native IO and interrupted managed acceptance; synthetic payloads only."""
from dataclasses import replace
import errno
import json
import os
from pathlib import Path

import pytest

from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.fs_paths import logical_path, native_io_path
import pixivvault_nicegui.managed_download_requests as module
from pixivvault_nicegui.managed_downloads import IDENTITY, MARKER


def record():
    return RemoteBookmark('123', 'illustration', 'show', 'Synthetic', '456',
                          'Artist', ('tag',), 'https://i.pximg.net/not-persisted',
                          '2026-10-23T00:00:00Z', 2, 1, 0)


def owned_root(tmp_path):
    # Do not solve the production regression by shortening the test's root.
    # Both staging and published file paths must exceed legacy MAX_PATH.
    root = tmp_path / 'owned'
    while len(str(root)) < 240:
        root /= 'synthetic-owned-segment'
    root_io = native_io_path(root)
    root_io.mkdir(parents=True)
    (root_io / MARKER).write_text(json.dumps(IDENTITY), encoding='utf-8')
    return root


def intent(version):
    if version == 'v1':
        return record()
    if version == 'v2-new':
        return module.ManagedAuthorIntent(record(), 'id', '', None, 'new')
    return module.ManagedAuthorIntent(
        record(), 'follow', '2026-10-23T00:00:00Z', 'a' * 64, 'updated',
        'generations/work-123-' + 'b' * 32 + '/receipt.json')


@pytest.mark.parametrize('version', ['v1', 'v2-new', 'v2-updated'])
def test_deep_acceptance_publication_restart_and_collision(tmp_path, monkeypatch, version):
    root = owned_root(tmp_path)
    calls = []
    real_write, real_read = module._write, module._read_bounded
    real_fsync, real_rename = module._fsync_dir, Path.rename

    def observe(operation, path):
        calls.append((operation, logical_path(path)))
        if os.name == 'nt':
            assert str(path).startswith('\\\\?\\'), 'IO missed native Windows spelling'

    def write(path, data):
        observe('write', path)
        return real_write(path, data)

    def read(path, limit):
        observe('read', path)
        return real_read(path, limit)

    def fsync(path):
        observe('fsync', path)
        return real_fsync(path)

    def rename(path, target):
        observe('rename_source', path)
        observe('rename_target', target)
        return real_rename(path, target)

    monkeypatch.setattr(module, '_write', write)
    monkeypatch.setattr(module, '_read_bounded', read)
    monkeypatch.setattr(module, '_fsync_dir', fsync)
    monkeypatch.setattr(Path, 'rename', rename)
    store = module.ManagedDownloadRequests(root)
    assert store.root == root and store.folder == root / 'requests'
    target = native_io_path(store._path('opaque-operation'))
    assert len(str(logical_path(target))) > 260
    value = intent(version)
    store.store('opaque-operation', value)
    payload = target.read_bytes()
    assert b'pximg' not in payload and b'opaque-operation' not in payload
    assert not tuple(native_io_path(store.incomplete).iterdir())
    restarted = module.ManagedDownloadRequests(root)
    assert restarted.equivalent(restarted.load('opaque-operation'), value)
    restarted.store('opaque-operation', value)
    assert target.read_bytes() == payload
    other = replace(value, title='Changed') if version == 'v1' else replace(value, record=replace(value.record, title='Changed'))
    with pytest.raises(module.ManagedRequestError, match='collision'):
        restarted.store('opaque-operation', other)
    assert target.read_bytes() == payload
    assert {'write', 'read', 'fsync', 'rename_source', 'rename_target'} <= {name for name, _ in calls}
    assert any(name == 'write' and len(str(path)) > 260 for name, path in calls)
    assert any(name == 'rename_target' and path == store._path('opaque-operation') for name, path in calls)
    assert any(name == 'read' and path == store._path('opaque-operation') for name, path in calls)


@pytest.mark.parametrize('failure', ['before_write', 'partial_write', 'stage_fsync', 'publish', 'requests_fsync'])
def test_deep_failure_boundary_restart_and_owned_temp_policy(tmp_path, monkeypatch, failure):
    root = owned_root(tmp_path)
    store = module.ManagedDownloadRequests(root)
    unknown = native_io_path(store.incomplete / 'unknown.bin')
    unknown.write_bytes(b'foreign data is not ours to clean')
    marker_before = (native_io_path(root) / MARKER).read_bytes()
    calls, fired = [], []
    real_write, real_fsync, real_rename = module._write, module._fsync_dir, Path.rename

    def fail(operation, path):
        fired.append((operation, logical_path(path)))
        raise FileNotFoundError(errno.ENOENT, 'synthetic private path must not leak', str(path))

    def write(path, data):
        calls.append(('write', logical_path(path)))
        if logical_path(path).parent == store.incomplete and failure in ('before_write', 'partial_write'):
            if failure == 'partial_write':
                real_write(path, data[:7])
            fail('stage_write', path)
        return real_write(path, data)

    def fsync(path):
        calls.append(('fsync', logical_path(path)))
        operation = 'stage_fsync' if logical_path(path) == store.incomplete else 'requests_fsync'
        if operation == failure:
            fail(operation, path)
        return real_fsync(path)

    def rename(path, target):
        calls.append(('rename', logical_path(path)))
        if logical_path(path).parent == store.incomplete and failure == 'publish':
            fail('publish', path)
        return real_rename(path, target)

    with monkeypatch.context() as patcher:
        patcher.setattr(module, '_write', write)
        patcher.setattr(module, '_fsync_dir', fsync)
        patcher.setattr(Path, 'rename', rename)
        with pytest.raises(module.ManagedRequestError, match='^managed intent save failed$') as caught:
            store.store('interrupted', record())
    assert calls and len(fired) == 1, 'target IO fault must actually fire exactly once'
    operation = 'stage_write' if failure in ('before_write', 'partial_write') else failure
    assert caught.value.io_failure == {'operation': operation,
        'error_type': 'FileNotFoundError', 'errno': errno.ENOENT, 'winerror': None}
    assert str(root) not in repr(caught.value.io_failure)
    assert 'private' not in str(caught.value)
    target = native_io_path(store._path('interrupted'))
    # Failure after publication is uncertain acknowledgement, not rollback.
    assert target.exists() == (failure == 'requests_fsync')
    remnants = {item.name: item.read_bytes() for item in native_io_path(store.incomplete).iterdir()}
    stages = [name for name in remnants if name.startswith('staging-')]
    assert len(stages) == (0 if failure in ('before_write', 'requests_fsync') else 1)
    if failure == 'partial_write':
        assert len(remnants[stages[0]]) == 7
    restarted = module.ManagedDownloadRequests(root)
    if failure != 'requests_fsync':
        with pytest.raises(module.ManagedRequestError, match='saved intent rejected'):
            restarted.load('interrupted')
    # Retry only publishes a fresh complete intent, never a retained partial.
    restarted.store('interrupted', record())
    assert restarted.equivalent(restarted.load('interrupted'), record())
    restarted.store('next', replace(record(), remote_id='124'))
    assert restarted.load('next').remote_id == '124'
    assert {item.name: item.read_bytes() for item in native_io_path(store.incomplete).iterdir()} == remnants
    assert unknown.read_bytes() == b'foreign data is not ours to clean'
    assert (native_io_path(root) / MARKER).read_bytes() == marker_before


def test_deep_read_failure_is_typed_and_does_not_modify_published_intent(tmp_path, monkeypatch):
    root = owned_root(tmp_path)
    store = module.ManagedDownloadRequests(root)
    store.store('accepted', record())
    target = store._path('accepted')
    before = native_io_path(target).read_bytes()
    calls, fired = [], []
    real_read = module._read_bounded

    def read(path, limit):
        calls.append(path)
        if logical_path(path) == target:
            fired.append(path)
            raise PermissionError(errno.EACCES, 'synthetic private path', str(path))
        return real_read(path, limit)

    with monkeypatch.context() as patcher:
        patcher.setattr(module, '_read_bounded', read)
        with pytest.raises(module.ManagedRequestError, match='^saved intent rejected$') as caught:
            store.load('accepted')
    assert calls and len(fired) == 1
    assert caught.value.io_failure == {'operation': 'read_intent', 'error_type': 'PermissionError',
                                      'errno': errno.EACCES, 'winerror': None}
    assert native_io_path(target).read_bytes() == before
    assert module.ManagedDownloadRequests(root).load('accepted').remote_id == '123'


def test_deep_unknown_request_file_is_rejected_and_preserved(tmp_path):
    root = owned_root(tmp_path)
    store = module.ManagedDownloadRequests(root)
    foreign = native_io_path(store.folder / 'unknown.bin')
    foreign.write_bytes(b'unchanged')
    with pytest.raises(module.ManagedRequestError, match='directory rejected'):
        store.store('new', record())
    assert foreign.read_bytes() == b'unchanged'
    assert not native_io_path(store._path('new')).exists()
    assert not tuple(native_io_path(store.incomplete).iterdir())


def test_deep_partial_namespace_initialization_recovers_without_touching_data(tmp_path, monkeypatch):
    root = owned_root(tmp_path)
    marker = native_io_path(root / MARKER)
    marker_before = marker.read_bytes()
    real_mkdir = Path.mkdir
    calls, fired = [], []

    def mkdir(path, *args, **kwargs):
        calls.append(path)
        if logical_path(path) == root / 'request-incomplete':
            fired.append(path)
            raise PermissionError(errno.EACCES, 'synthetic private path', str(path))
        return real_mkdir(path, *args, **kwargs)

    with monkeypatch.context() as patcher:
        patcher.setattr(Path, 'mkdir', mkdir)
        with pytest.raises(module.ManagedRequestError, match='^managed intent root rejected$') as caught:
            module.ManagedDownloadRequests(root)
    assert calls and len(fired) == 1
    assert caught.value.io_failure == {'operation': 'prepare_incomplete', 'error_type': 'PermissionError',
                                      'errno': errno.EACCES, 'winerror': None}
    assert native_io_path(root / 'requests').is_dir()
    unknown = native_io_path(root / 'keep.bin')
    unknown.write_bytes(b'leave unrelated root data alone')
    restarted = module.ManagedDownloadRequests(root)
    restarted.store('accepted', record())
    assert restarted.load('accepted').remote_id == '123'
    assert marker.read_bytes() == marker_before
    assert unknown.read_bytes() == b'leave unrelated root data alone'
