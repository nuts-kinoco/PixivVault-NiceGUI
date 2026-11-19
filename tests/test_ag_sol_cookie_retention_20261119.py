"""Synthetic credential retention regression."""
from email.utils import formatdate
from pathlib import Path

import pytest

from test_ag_z04_20261119 import setup, reader, fetch
from pixivvault_nicegui.pixiv_read_client import _ReadResponse


def test_repeated_expiry_extensions_bound_and_prune_generations(tmp_path):
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    created = []
    try:
        for index in range(32):
            header = 'PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Expires=' + formatdate(expiry + 3600 + index, usegmt=True)
            client._transport = lambda *args: _ReadResponse(b'{"error":false,"body":{}}', (header,))
            fetch(client)
            created.append(profile.snapshot().cookie_file)
        generations = list((store.root / 'generations').iterdir())
        assert len(generations) <= 8
        assert sum(item.stat().st_size for folder in generations for item in folder.iterdir()) <= 16 * 1024 * 1024
        assert profile.snapshot().cookie_file.is_file()
        assert created[-2].is_file()
        assert any(not path.exists() for path in created[:-2])
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


@pytest.mark.parametrize('restart', [False, True])
def test_cleanup_start_failure_is_retried_and_renewals_resume(tmp_path, monkeypatch, restart):
    from pixivvault_nicegui.cookie_expiry_refresh import CookieExpiryCoordinator
    from pixivvault_nicegui.extension_cookie_store import ExtensionCookieStore
    import pixivvault_nicegui.extension_cookie_store as module
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    original = module._write_new
    failed = []
    def fail_retiring_once(path, data):
        if path.name == '.retiring' and not failed:
            failed.append(True)
            raise PermissionError('synthetic cleanup admission failure')
        return original(path, data)
    monkeypatch.setattr(module, '_write_new', fail_retiring_once)
    try:
        for index in range(9):
            header = 'PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Expires=' + formatdate(expiry + 3600 + index, usegmt=True)
            client._transport = lambda *args: _ReadResponse(b'{"error":false,"body":{}}', (header,))
            fetch(client)
        assert failed
        assert len(list((store.root / 'generations').iterdir())) == 9
        revision = profile.snapshot().revision
        if restart:
            selected = profile.snapshot().cookie_file
            client.close(); coordinator.close(); store.close()
            store = ExtensionCookieStore(tmp_path / 'generations', profile=profile)
            coordinator = CookieExpiryCoordinator(profile, lambda: store)
            client, _ = reader(selected, coordinator, [])
        for index in range(9, 12):
            header = 'PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Expires=' + formatdate(expiry + 3600 + index, usegmt=True)
            client._transport = lambda *args: _ReadResponse(b'{"error":false,"body":{}}', (header,))
            fetch(client)
        assert profile.snapshot().revision == revision + 3
        assert len(list((store.root / 'generations').iterdir())) <= 8
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_failed_partial_cleanup_does_not_fail_renewal_and_retries(tmp_path, monkeypatch):
    import pixivvault_nicegui.extension_cookie_store as module
    monkeypatch.setattr(module, 'MAX_RETAINED_COOKIE_GENERATIONS', 2)
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    original_unlink = store._delete_retired_file
    failed = []
    def fail_metadata_once(path, proof, limit):
        if path.name == 'generation.json' and not failed:
            failed.append(True)
            raise PermissionError('synthetic cleanup failure')
        return original_unlink(path, proof, limit)
    monkeypatch.setattr(store, '_delete_retired_file', fail_metadata_once)
    try:
        for index in range(4):
            header = 'PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Expires=' + formatdate(expiry + 3600 + index, usegmt=True)
            client._transport = lambda *args: _ReadResponse(b'{"error":false,"body":{}}', (header,))
            fetch(client)
            if index == 2:
                assert failed
                assert len(list((store.root / 'generations').iterdir())) == 3
        assert len(list((store.root / 'generations').iterdir())) == 2
        assert profile.snapshot().cookie_file.is_file()
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_failed_directory_removal_is_retried(tmp_path, monkeypatch):
    import pixivvault_nicegui.extension_cookie_store as module
    monkeypatch.setattr(module, 'MAX_RETAINED_COOKIE_GENERATIONS', 2)
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    original = Path.rmdir
    failed = []
    def fail_once(path, *args, **kwargs):
        if path.parent.name == 'generations' and not failed:
            failed.append(True)
            raise PermissionError('synthetic directory cleanup failure')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'rmdir', fail_once)
    try:
        for index in range(4):
            header = 'PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Expires=' + formatdate(expiry + 3600 + index, usegmt=True)
            client._transport = lambda *args: _ReadResponse(b'{"error":false,"body":{}}', (header,))
            fetch(client)
            if index == 2:
                assert failed
                assert any(p.name.startswith('.retired-') for p in (store.root / 'generations').iterdir())
        assert len(list((store.root / 'generations').iterdir())) == 2
        assert profile.snapshot().cookie_file.is_file()
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_incoming_bytes_do_not_delete_before_publication(tmp_path, monkeypatch):
    import pixivvault_nicegui.extension_cookie_store as module
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    saved = []
    try:
        for index in range(3):
            header = 'PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Expires=' + formatdate(expiry + 3600 + index, usegmt=True)
            client._transport = lambda *args: _ReadResponse(b'{"error":false,"body":{}}', (header,))
            fetch(client)
            saved.append(profile.snapshot().cookie_file)
        _, total, _ = store._retention_state()
        monkeypatch.setattr(module, 'MAX_RETAINED_COOKIE_BYTES', total + saved[-1].stat().st_size + 2047)
        header = 'PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Expires=' + formatdate(expiry + 3603, usegmt=True)
        client._transport = lambda *args: _ReadResponse(b'{"error":false,"body":{}}', (header,))
        fetch(client)
        assert saved[0].is_file()
        assert saved[-1].is_file()
        assert profile.snapshot().cookie_file == saved[-1]
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_partial_cleanup_retried_when_space_is_already_sufficient(tmp_path, monkeypatch):
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    saved = []
    try:
        for index in range(3):
            header = 'PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Expires=' + formatdate(expiry + 3600 + index, usegmt=True)
            client._transport = lambda *args: _ReadResponse(b'{"error":false,"body":{}}', (header,))
            fetch(client)
            saved.append(profile.snapshot().cookie_file)
        original = store._delete_retired_file
        failed = []
        def fail_once(path, proof, limit):
            if path.name == 'generation.json' and not failed:
                failed.append(True)
                raise PermissionError('synthetic cleanup failure')
            return original(path, proof, limit)
        monkeypatch.setattr(store, '_delete_retired_file', fail_once)
        with pytest.raises(PermissionError):
            store._remove_retired(saved[0].parent)
        assert failed
        store._cleanup_generations(profile.snapshot())
        assert not saved[0].parent.exists()
        assert profile.snapshot().cookie_file == saved[-1]
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_unadopted_generation_is_preserved(tmp_path):
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    candidate = store.prepare([{'domain': '.pixiv.net', 'path': '/', 'name': 'PHPSESSID',
        'value': '777_old', 'secure': True, 'expirationDate': expiry + 30}],
        expected_revision=profile.snapshot().revision)
    assert store.discard(candidate)
    unadopted = candidate.cookie_file
    client, _ = reader(cookie, coordinator, [])
    try:
        for index in range(10):
            header = 'PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Expires=' + formatdate(expiry + 3600 + index, usegmt=True)
            client._transport = lambda *args: _ReadResponse(b'{"error":false,"body":{}}', (header,))
            fetch(client)
        assert unadopted.is_file()
        assert profile.snapshot().cookie_file.is_file()
        assert len(list((store.root / 'generations').iterdir())) <= 8
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_cleanup_keeps_immediate_predecessor_after_restart(tmp_path):
    from pixivvault_nicegui.cookie_expiry_refresh import CookieExpiryCoordinator
    from pixivvault_nicegui.extension_cookie_store import ExtensionCookieStore
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    try:
        for index in range(8):
            header = 'PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Expires=' + formatdate(expiry + 3600 + index, usegmt=True)
            client._transport = lambda *args: _ReadResponse(b'{"error":false,"body":{}}', (header,))
            fetch(client)
        previous = profile.snapshot().cookie_file
        client.close(); coordinator.close(); store.close()
        store = ExtensionCookieStore(tmp_path / 'generations', profile=profile)
        coordinator = CookieExpiryCoordinator(profile, lambda: store)
        client, _ = reader(previous, coordinator, [])
        header = 'PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Expires=' + formatdate(expiry + 3610, usegmt=True)
        client._transport = lambda *args: _ReadResponse(b'{"error":false,"body":{}}', (header,))
        fetch(client)
        assert previous.is_file()
        assert profile.snapshot().cookie_file != previous
        assert len(list((store.root / 'generations').iterdir())) == 8
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_cleanup_inventory_failure_does_not_stop_reader(tmp_path, monkeypatch):
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    header = 'PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Expires=' + formatdate(expiry + 3600, usegmt=True)
    calls = []
    def transport(*args):
        calls.append(1)
        return _ReadResponse(b'{"error":false,"body":{}}', (header,))
    client._transport = transport
    try:
        with monkeypatch.context() as patch:
            def unavailable():
                raise OSError('synthetic inventory failure')
            patch.setattr(store, '_retention_state', unavailable)
            fetch(client)
            assert profile.snapshot().cookie_file == cookie
        fetch(client)
        assert profile.snapshot().cookie_file != cookie
        assert len(calls) == 2
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


@pytest.mark.skipif(__import__('os').name != 'nt', reason='Windows sharing-mode test')
def test_open_old_generation_is_kept_and_later_cleaned(tmp_path):
    import os
    from pixivvault_nicegui.frozen_source_pin import _open_held
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    held = None
    try:
        for index in range(8):
            header = 'PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Expires=' + formatdate(expiry + 3600 + index, usegmt=True)
            client._transport = lambda *args: _ReadResponse(b'{"error":false,"body":{}}', (header,))
            fetch(client)
            if index == 0:
                old = profile.snapshot().cookie_file
                held = _open_held(old)
        for index in (8, 9):
            header = 'PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Expires=' + formatdate(expiry + 3600 + index, usegmt=True)
            client._transport = lambda *args: _ReadResponse(b'{"error":false,"body":{}}', (header,))
            fetch(client)
            if index == 8:
                assert old.is_file()
                os.close(held)
                held = None
        assert not old.exists()
        assert profile.snapshot().cookie_file.is_file()
    finally:
        if held is not None:
            os.close(held)
        client.close(); coordinator.close(); store.close(); profile.close()
