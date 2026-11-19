import ctypes
from ctypes import wintypes
import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from w_round8_crossing_helper import digest, make_store, mg, open_downloads, PNG, state, wait_for
from w_round7_fixtures import (
    ManagedDownloadError,
    test_calls_cover_validation_on_reader_writer_fold_quarantine_and_capacity as exercise,
)


def test_changed_index_is_rechecked_after_recovery_state(tmp_path, monkeypatch):
    gen = make_store(tmp_path / 'root')._generations
    expected = state(open_downloads(gen.root))
    events = []
    real_assert = gen.assert_consistent
    real_load = gen._load_full
    real_recover = gen._recover_if_pending

    def cross(loaded, **kwargs):
        if not events:
            events.append('cross')
            replacement = gen.stage_dir / 'crossing-test.tmp'
            replacement.write_bytes(gen.index_path.read_bytes())
            os.replace(replacement, gen.index_path)
            raise ValueError('index changed while checking')
        return real_assert(loaded, **kwargs)

    def recover():
        events.append('recover')
        return real_recover()

    def load():
        assert events == ['cross', 'recover']
        return real_load()

    monkeypatch.setattr(gen, 'assert_consistent', cross)
    monkeypatch.setattr(gen, '_recover_if_pending', recover)
    monkeypatch.setattr(gen, '_load_full', load)
    assert set(gen._refresh().active) == set(expected)


@pytest.mark.parametrize('mode', ['cold', 'warm'])
@pytest.mark.parametrize('limit', ['deadline', 'attempts'])
def test_crossing_retry_has_a_deadline_and_attempt_limit(tmp_path, monkeypatch, mode, limit):
    gen = make_store(tmp_path / 'root')._generations
    if mode == 'cold':
        gen._loaded = None
    attempts = []
    ticks = iter(i * (0.6 if limit == 'deadline' else 0) for i in range(20))
    monkeypatch.setattr(mg, 'time', SimpleNamespace(monotonic=lambda: next(ticks), sleep=lambda _: None))
    monkeypatch.setattr(gen, '_recover_if_pending', lambda: None)

    def rejected(*args, **kwargs):
        attempts.append(1)
        raise ManagedDownloadError('active generation index rejected')

    monkeypatch.setattr(gen, '_load_full' if mode == 'cold' else 'assert_consistent', rejected)
    with pytest.raises(ManagedDownloadError, match='index rejected'):
        gen._refresh()
    if limit == 'deadline':
        assert 1 < len(attempts) < 8
    else:
        assert len(attempts) == 8


@pytest.mark.skipif(os.name != 'nt', reason='Windows sharing flags')
@pytest.mark.parametrize('mode', ['cold', 'warm'])
@pytest.mark.parametrize('target_name', [mg._V5_HEAD, 'active-generations.json'])
def test_crossing_with_native_windows_sharing(tmp_path, mode, target_name):
    root = tmp_path / 'root'
    writer = make_store(root)
    gen = writer._generations
    before = state(writer)
    old_index = gen.index_path.read_bytes()
    old_floor = gen._read_floor()
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    child = Path(__file__).with_name('w_round8_crossing_helper.py')
    with subprocess.Popen(
        [sys.executable, '-B', str(child), mode, str(root), str(tmp_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8',
    ) as reader, ThreadPoolExecutor(1) as pool:
        try:
            wait_for(lambda: (tmp_path / 'reader-ready').exists())
            handle = create(str(root / target_name), 0x80000000, 0x3, None, 3, 0x80, None)
            assert handle not in (None, wintypes.HANDLE(-1).value)
            try:
                fold = pool.submit(gen._compact, gen._loaded)
                wait_for(lambda: gen._read_floor() != old_floor)
                floor = gen._read_floor()
                assert floor['generation'] == old_floor['generation'] + 1
                assert gen.index_path.read_bytes() == old_index
                candidate = (gen.stage_dir / floor['candidate']).read_bytes()
                packet = json.loads(candidate)
                assert hashlib.sha256(candidate).hexdigest() == floor['digest']
                assert packet['entries'] == gen._loaded.indexed
                assert packet['exclusions'] == gen._loaded.exclusions
                assert packet['quarantine_operations'] == gen._loaded.operations
                (tmp_path / 'release-reader').touch()
                out, err = reader.communicate(timeout=5)
                assert not err, err
                assert not fold.done()
            finally:
                close(handle)
            fold.result(timeout=8)
        finally:
            (tmp_path / 'release-reader').touch()

    reopened = open_downloads(root)
    assert state(reopened) == before
    digest(root, before)
    for relative, sha in before.values():
        assert reopened.read_saved_page(relative, 0, sha) == PNG
    print(f'{mode}/{target_name}: during={reader.returncode}: {out.strip()}; after=both original receipts and pages')
    assert reader.returncode == 0, out
    assert json.loads(out) == {key: list(value) for key, value in before.items()}


def test_every_assert_consistent_callsite(tmp_path):
    import ast
    import inspect

    path = Path(inspect.getfile(mg))
    tree = ast.parse(path.read_text(encoding='utf-8'))
    expected = {call.lineno for call in ast.walk(tree) if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute) and call.func.attr == 'assert_consistent'}
    real = mg.ManagedGenerations.assert_consistent.__code__
    calls = {}

    def profile(frame, event, _):
        if event == 'call' and frame.f_code is real:
            caller = frame.f_back
            while caller is not None and caller.f_code.co_filename != real.co_filename:
                caller = caller.f_back
            if caller is not None:
                calls[caller.f_lineno] = caller.f_code.co_name

    original = sys.getprofile()
    sys.setprofile(profile)
    try:
        exercise(tmp_path)
    finally:
        sys.setprofile(original)
    print(f'assert_consistent callsites: {sorted(calls.items())}')
    assert set(calls) == expected
