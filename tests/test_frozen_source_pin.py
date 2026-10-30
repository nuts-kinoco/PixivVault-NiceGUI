"""Generated temp SQLite files only; no original DB, Cookie, media or network."""
import hashlib
import os
from pathlib import Path
import sqlite3
import threading

import pytest

from pixivvault_nicegui import frozen_source_pin as module
from pixivvault_nicegui.frozen_source_pin import (
    FrozenSourceCancelled, FrozenSourceError, FrozenSourcePin,
)
from pixivvault_nicegui.fs_paths import sqlite_readonly_uri

WINDOWS = os.name == 'nt'
windows_only = pytest.mark.skipif(not WINDOWS, reason='kernel deny-write pin is Windows-only')


def database(tmp_path, name='frozen.db'):
    path = tmp_path / name
    connection = sqlite3.connect(path)
    connection.execute('CREATE TABLE t(v TEXT)')
    connection.execute("INSERT INTO t VALUES('alpha')")
    connection.commit()
    connection.close()
    assert not any(Path(str(path) + suffix).exists() for suffix in ('-wal', '-shm', '-journal'))
    return path


def signature(path):
    info = path.stat()
    return info.st_ino, info.st_size, info.st_mtime_ns, hashlib.sha256(path.read_bytes()).digest()


def assert_released(path):
    # A leaked FILE_SHARE_READ-only handle would refuse this writer and rename.
    with path.open('r+b'):
        pass
    moved = path.with_name(path.name + '.moved')
    path.rename(moved)
    moved.rename(path)


def test_exact_signature_pins_and_allows_separate_readonly_sqlite(tmp_path):
    path = database(tmp_path)
    expected = signature(path)
    with FrozenSourcePin(path, expected) as pin:
        assert pin.secure is WINDOWS
        pin.check()
        for immutable in (True, False):
            connection = sqlite3.connect(sqlite_readonly_uri(path, immutable=immutable), uri=True)
            try:
                assert connection.execute('SELECT v FROM t').fetchall() == [('alpha',)]
            finally:
                connection.close()
        pin.check()
    assert not pin.secure
    assert signature(path) == expected
    assert_released(path)


@windows_only
def test_windows_pin_denies_mutation_until_close(tmp_path):
    path = database(tmp_path)
    expected = signature(path)
    other = tmp_path / 'other.db'
    other.write_bytes(b'replacement')
    moved = tmp_path / 'moved.db'
    with FrozenSourcePin(path, expected) as pin:
        with pytest.raises(PermissionError):
            path.open('r+b')
        with pytest.raises(PermissionError):
            path.unlink()
        with pytest.raises(PermissionError):
            path.rename(moved)
        with pytest.raises(PermissionError):
            os.replace(other, path)
        pin.check()
        assert pin.secure
    assert signature(path) == expected and other.exists() and not moved.exists()
    with path.open('r+b'):
        pass
    path.rename(moved)
    moved.rename(path)
    os.replace(other, path)
    assert path.read_bytes() == b'replacement'
    path.unlink()


@windows_only
def test_existing_writer_blocks_admission_without_leak(tmp_path):
    path = database(tmp_path)
    expected = signature(path)
    pin = FrozenSourcePin(path, expected)
    with path.open('r+b'):
        with pytest.raises(FrozenSourceError):
            pin.__enter__()
    assert not pin.secure
    assert_released(path)
    with pytest.raises(FrozenSourceError):
        pin.__enter__()  # single use, even after a failed admission
    with FrozenSourcePin(path, expected) as later:
        assert later.secure
        later.check()


def test_same_size_restored_mtime_mutation_before_admission_rejected_by_digest(tmp_path):
    path = database(tmp_path)
    expected = signature(path)
    info = path.stat()
    with path.open('r+b') as stream:
        stream.seek(info.st_size - 1)
        last = stream.read(1)
        stream.seek(info.st_size - 1)
        stream.write(bytes([last[0] ^ 0xFF]))
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    assert signature(path)[:3] == expected[:3] and signature(path)[3] != expected[3]
    with pytest.raises(FrozenSourceError) as caught:
        with FrozenSourcePin(path, expected):
            pass
    assert type(caught.value) is FrozenSourceError
    assert_released(path)


@pytest.mark.parametrize('index', range(4))
def test_wrong_expected_signature_component_rejected(tmp_path, index):
    path = database(tmp_path)
    wrong = list(signature(path))
    wrong[index] = bytes(32) if index == 3 else wrong[index] + 1
    pin = FrozenSourcePin(path, tuple(wrong))
    with pytest.raises(FrozenSourceError):
        pin.__enter__()
    assert not pin.secure
    assert_released(path)


def test_invalid_inputs_sidecars_directories_and_size_rejected(tmp_path, monkeypatch):
    path = database(tmp_path)
    expected = signature(path)
    for bad in (expected[:3], (True,) + expected[1:], expected[:3] + (b'short',),
                expected[:3] + (bytearray(expected[3]),)):
        with pytest.raises(FrozenSourceError):
            FrozenSourcePin(path, bad)
    for bad_path in ('frozen.db', tmp_path / 'x' / '..' / 'frozen.db', tmp_path / 'frozen.db-wal'):
        with pytest.raises(FrozenSourceError):
            FrozenSourcePin(bad_path, expected)
    with pytest.raises(FrozenSourceError):
        FrozenSourcePin(path, expected, cancelled=object())
    # The constructor is side-effect-free: a missing source fails only on entry.
    missing = FrozenSourcePin(tmp_path / 'missing.db', expected)
    with pytest.raises(FrozenSourceError):
        missing.__enter__()
    with pytest.raises(FrozenSourceError):
        FrozenSourcePin(tmp_path, expected).__enter__()
    for suffix in ('-wal', '-shm', '-journal'):
        sidecar = Path(str(path) + suffix)
        sidecar.write_bytes(b'')
        with pytest.raises(FrozenSourceError):
            FrozenSourcePin(path, expected).__enter__()
        sidecar.unlink()
    monkeypatch.setattr(module, 'MAX_SOURCE_BYTES', expected[1] - 1)
    with pytest.raises(FrozenSourceError):
        FrozenSourcePin(path, expected).__enter__()
    monkeypatch.undo()
    with FrozenSourcePin(path, expected) as pin:
        pin.check()
    assert_released(path)


def test_hard_link_and_symlinks_rejected(tmp_path):
    path = database(tmp_path)
    expected = signature(path)
    hard = tmp_path / 'hard.db'
    os.link(path, hard)
    with pytest.raises(FrozenSourceError):
        FrozenSourcePin(path, expected).__enter__()
    hard.unlink()
    link = tmp_path / 'link.db'
    folder = tmp_path / 'real'
    folder.mkdir()
    inner = database(folder, 'inner.db')
    linked_folder = tmp_path / 'linked'
    try:
        os.symlink(path, link)
        os.symlink(folder, linked_folder, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip('account cannot create symlinks')
    with pytest.raises(FrozenSourceError):
        FrozenSourcePin(link, expected).__enter__()
    with pytest.raises(FrozenSourceError):
        FrozenSourcePin(linked_folder / 'inner.db', signature(inner)).__enter__()
    assert_released(path)


def test_hash_cancellation_and_unknown_cancel_failure_close_handle(tmp_path):
    path = database(tmp_path)
    expected = signature(path)
    calls = []

    def cancel_on_second_poll():
        calls.append(None)
        return len(calls) >= 2

    with pytest.raises(FrozenSourceCancelled):
        FrozenSourcePin(path, expected, cancelled=cancel_on_second_poll).__enter__()
    assert len(calls) == 2
    assert_released(path)

    def broken():
        raise RuntimeError('private payload')

    with pytest.raises(FrozenSourceError) as caught:
        FrozenSourcePin(path, expected, cancelled=broken).__enter__()
    assert type(caught.value) is FrozenSourceError
    assert 'private payload' not in str(caught.value) and caught.value.__cause__ is None
    assert_released(path)
    assert signature(path) == expected
    path.unlink()


def test_pin_does_not_mutate_and_exception_branch_releases(tmp_path):
    path = database(tmp_path)
    expected = signature(path)
    content = path.read_bytes()
    with pytest.raises(RuntimeError):
        with FrozenSourcePin(path, expected) as pin:
            pin.check()
            raise RuntimeError('caller failure')
    assert not pin.secure
    with pytest.raises(FrozenSourceError):
        pin.check()
    pin.close()
    pin.close()
    with pytest.raises(FrozenSourceError):
        pin.__enter__()
    assert path.read_bytes() == content and signature(path) == expected
    assert_released(path)


def test_check_skips_rehash_only_while_windows_pin_holds(tmp_path, monkeypatch):
    path = database(tmp_path)
    expected = signature(path)
    hashes = []
    original = module._digest

    def counted(*args):
        hashes.append(None)
        return original(*args)

    monkeypatch.setattr(module, '_digest', counted)
    with FrozenSourcePin(path, expected) as pin:
        pin.check()
        pin.check()
        assert len(hashes) == (1 if WINDOWS else 3)
        sidecar = Path(str(path) + '-wal')
        sidecar.write_bytes(b'')
        with pytest.raises(FrozenSourceError):
            pin.check()
        assert not pin.secure
        with pytest.raises(FrozenSourceError):
            pin.check()
        sidecar.unlink()
        assert_released(path)
    with pytest.raises(FrozenSourceError):
        pin.check()


def test_check_and_close_from_another_thread(tmp_path):
    path = database(tmp_path)
    errors = []
    with FrozenSourcePin(path, signature(path)) as pin:
        def worker():
            try:
                pin.check()
                pin.close()
                pin.close()
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(10)
        assert not thread.is_alive() and not errors and not pin.secure
        with pytest.raises(FrozenSourceError):
            pin.check()
    assert_released(path)
