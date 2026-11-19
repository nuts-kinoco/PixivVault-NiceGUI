"""Synthetic Cookie adoption and cleanup race regressions."""
from contextlib import contextmanager
from email.utils import formatdate
import json

import pytest

from pixivvault_nicegui.cookie_expiry_refresh import CookieExpiryCoordinator
from pixivvault_nicegui.extension_cookie_store import CookieSyncConflict
from pixivvault_nicegui.pixiv_read_client import CookieSourceRejected, _ReadResponse
from test_ag_z04_20261119 import setup, reader, fetch


def _extend(client, expiry):
    header = 'PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Expires=' + formatdate(expiry, usegmt=True)
    client._transport = lambda *args: _ReadResponse(b'{"error":false,"body":{}}', (header,))
    fetch(client)


def test_candidate_metadata_changed_after_prepare_is_rejected(tmp_path):
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    try:
        candidate = store.prepare([{'domain': '.pixiv.net', 'path': '/', 'name': 'PHPSESSID',
            'value': '777_old', 'secure': True, 'expirationDate': expiry + 60}],
            expected_revision=profile.snapshot().revision)
        metadata = candidate.cookie_file.parent / 'generation.json'
        packet = json.loads(metadata.read_bytes())
        packet['previous'] = 'f' * 32
        metadata.write_text(json.dumps(packet, separators=(',', ':')) + '\n', encoding='ascii')
        with pytest.raises(CookieSyncConflict):
            store.commit(candidate)
        assert profile.snapshot().cookie_file == cookie
    finally:
        coordinator.close(); store.close(); profile.close()


def test_selected_metadata_changed_after_prepare_is_rejected(tmp_path):
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    try:
        _extend(client, expiry + 3600)
        selected = profile.snapshot().cookie_file
        candidate = store.prepare([{'domain': '.pixiv.net', 'path': '/', 'name': 'PHPSESSID',
            'value': '777_old', 'secure': True, 'expirationDate': expiry + 7200}],
            expected_revision=profile.snapshot().revision)
        metadata = selected.parent / 'generation.json'
        packet = json.loads(metadata.read_bytes())
        packet['previous'] = 'a' * 32
        metadata.write_text(json.dumps(packet, separators=(',', ':')) + '\n', encoding='ascii')
        with pytest.raises(CookieSyncConflict):
            store.commit(candidate)
        assert profile.snapshot().cookie_file == selected
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_candidate_swap_after_initial_check_is_rejected(tmp_path, monkeypatch):
    import hashlib
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    try:
        candidate = store.prepare([{'domain': '.pixiv.net', 'path': '/', 'name': 'PHPSESSID',
            'value': '777_old', 'secure': True, 'expirationDate': expiry + 60}],
            expected_revision=profile.snapshot().revision)
        original = store._pin_adoptions
        @contextmanager
        def swap_before_pin(candidate, pending):
            data = candidate.cookie_file.read_bytes().replace(b'777_old', b'777_new')
            candidate.cookie_file.write_bytes(data)
            metadata = candidate.cookie_file.parent / 'generation.json'
            packet = json.loads(metadata.read_bytes())
            packet['sha256'] = hashlib.sha256(data).hexdigest()
            metadata.write_text(json.dumps(packet, separators=(',', ':')) + '\n', encoding='ascii')
            with original(candidate, pending) as proof:
                yield proof
        monkeypatch.setattr(store, '_pin_adoptions', swap_before_pin)
        with pytest.raises(CookieSyncConflict):
            store.commit(candidate)
        assert profile.snapshot().cookie_file == cookie
    finally:
        coordinator.close(); store.close(); profile.close()


def test_candidate_swap_before_first_readback_is_rejected(tmp_path, monkeypatch):
    import hashlib
    import pixivvault_nicegui.extension_cookie_store as module
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    original = module._write_new
    def replace_after_write(path, data):
        original(path, data)
        if path.name == 'generation.json':
            cookie_path = path.parent / 'cookies.txt'
            content = cookie_path.read_bytes().replace(b'777_old', b'777_new')
            cookie_path.write_bytes(content)
            packet = json.loads(path.read_bytes())
            packet['sha256'] = hashlib.sha256(content).hexdigest()
            path.write_text(json.dumps(packet, separators=(',', ':')) + '\n', encoding='ascii')
    monkeypatch.setattr(module, '_write_new', replace_after_write)
    try:
        with pytest.raises(CookieSyncConflict):
            store.prepare([{'domain': '.pixiv.net', 'path': '/', 'name': 'PHPSESSID',
                'value': '777_old', 'secure': True, 'expirationDate': expiry + 60}],
                expected_revision=profile.snapshot().revision)
        assert profile.snapshot().cookie_file == cookie
    finally:
        coordinator.close(); store.close(); profile.close()


def test_post_commit_cookie_swap_is_not_adopted_by_reader(tmp_path, monkeypatch):
    import hashlib
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    original = store.commit
    def swap_after_commit(candidate):
        result = original(candidate)
        path = result.cookie_file
        content = path.read_bytes().replace(b'777_old', b'777_new')
        path.write_bytes(content)
        metadata = path.parent / 'generation.json'
        packet = json.loads(metadata.read_bytes())
        packet['sha256'] = hashlib.sha256(content).hexdigest()
        metadata.write_text(json.dumps(packet, separators=(',', ':')) + '\n', encoding='ascii')
        return result
    monkeypatch.setattr(store, 'commit', swap_after_commit)
    try:
        with pytest.raises(CookieSourceRejected):
            _extend(client, expiry + 3600)
        with pytest.raises(CookieSourceRejected):
            _extend(client, expiry + 3601)
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_source_pin_admission_failure_does_not_leave_store_busy(tmp_path, monkeypatch):
    import pixivvault_nicegui.frozen_source_pin as pin
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    rows = [{'domain': '.pixiv.net', 'path': '/', 'name': 'PHPSESSID',
             'value': '777_old', 'secure': True, 'expirationDate': expiry + 60}]
    try:
        candidate = store.prepare(rows, expected_revision=profile.snapshot().revision)
        with monkeypatch.context() as patch:
            def refuse(_path):
                raise PermissionError('synthetic sharing conflict')
            patch.setattr(pin, '_open_held', refuse)
            with pytest.raises(CookieSyncConflict):
                store.commit(candidate)
        retry = store.prepare(rows, expected_revision=profile.snapshot().revision)
        assert store.discard(retry)
    finally:
        coordinator.close(); store.close(); profile.close()


def test_changed_adopted_metadata_is_not_deleted(tmp_path, monkeypatch):
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    try:
        for index in range(8):
            _extend(client, expiry + 3600 + index)
        original = store._remove_retired
        changed = []
        def change_before_delete(folder, expected_hashes=None):
            metadata = folder / 'generation.json'
            packet = json.loads(metadata.read_bytes())
            packet['previous'] = 'e' * 32
            metadata.write_text(json.dumps(packet, separators=(',', ':')) + '\n', encoding='ascii')
            changed.append(folder)
            return original(folder, expected_hashes)
        monkeypatch.setattr(store, '_remove_retired', change_before_delete)
        previous = profile.snapshot().revision
        _extend(client, expiry + 3608)
        assert profile.snapshot().revision == previous + 1
        assert len(changed) == 1 and (changed[0] / 'cookies.txt').is_file()
        assert not (changed[0] / '.retiring').exists()
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_unadopted_generation_linked_from_changed_ancestor_is_preserved(tmp_path, monkeypatch):
    import pixivvault_nicegui.extension_cookie_store as module
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    generations = []
    try:
        for index in range(3):
            _extend(client, expiry + 3600 + index)
            generations.append(profile.snapshot().cookie_file)
        candidate = store.prepare([{'domain': '.pixiv.net', 'path': '/', 'name': 'PHPSESSID',
            'value': '777_old', 'secure': True, 'expirationDate': expiry + 7200}],
            expected_revision=profile.snapshot().revision)
        assert store.discard(candidate)
        metadata = generations[1].parent / 'generation.json'
        packet = json.loads(metadata.read_bytes())
        packet['previous'] = candidate.generation
        metadata.write_text(json.dumps(packet, separators=(',', ':')) + '\n', encoding='ascii')
        monkeypatch.setattr(module, 'MAX_RETAINED_COOKIE_GENERATIONS', 2)
        store._cleanup_generations(profile.snapshot())
        assert candidate.cookie_file.is_file()
        assert generations[1].is_file()
        assert generations[2].is_file()
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_manual_external_selection_after_failed_cleanup_resumes(tmp_path, monkeypatch):
    import pixivvault_nicegui.extension_cookie_store as module
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    original = module._write_new
    failed = []
    def fail_once(path, data):
        if path.name == '.retiring' and not failed:
            failed.append(True)
            raise PermissionError('synthetic cleanup failure')
        return original(path, data)
    monkeypatch.setattr(module, '_write_new', fail_once)
    try:
        for index in range(9):
            _extend(client, expiry + 3600 + index)
        assert failed and len(list((store.root / 'generations').iterdir())) == 9
        client.close(); coordinator.close()
        manual_dir = tmp_path / 'manual'
        manual_dir.mkdir()
        manual = manual_dir / 'cookies.txt'
        manual.write_bytes(cookie.read_bytes())
        profile.update(cookie_file=manual, expected_revision=profile.snapshot().revision)
        coordinator = CookieExpiryCoordinator(profile, lambda: store)
        client, _ = reader(manual, coordinator, [])
        before = profile.snapshot().revision
        for index in range(3):
            _extend(client, expiry + 7200 + index)
        assert profile.snapshot().revision == before + 3
        assert len(list((store.root / 'generations').iterdir())) <= 8
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_failed_adoption_record_rolls_back_cookie_selection(tmp_path):
    import sqlite3
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    try:
        _extend(client, expiry + 3600)
        before = profile.snapshot()
        connection = sqlite3.connect(profile.path)
        try:
            count = connection.execute('SELECT COUNT(*) FROM native_cookie_adoptions').fetchone()[0]
            connection.execute('''CREATE TRIGGER synthetic_adoption_failure BEFORE INSERT ON native_cookie_adoptions
                BEGIN SELECT RAISE(ABORT, 'synthetic adoption failure'); END''')
            connection.commit()
        finally:
            connection.close()
        with pytest.raises(CookieSourceRejected):
            _extend(client, expiry + 3601)
        assert profile.snapshot() == before
        connection = sqlite3.connect(profile.path)
        try:
            assert connection.execute('SELECT COUNT(*) FROM native_cookie_adoptions').fetchone()[0] == count
        finally:
            connection.close()
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_recently_reselected_managed_generation_survives_external_switch(tmp_path):
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    saved = []
    try:
        for index in range(8):
            _extend(client, expiry + 3600 + index)
            saved.append(profile.snapshot().cookie_file)
        client.close(); coordinator.close()
        profile.update(cookie_file=saved[0], expected_revision=profile.snapshot().revision)
        manual_dir = tmp_path / 'manual'
        manual_dir.mkdir()
        manual = manual_dir / 'cookies.txt'
        manual.write_bytes(cookie.read_bytes())
        profile.update(cookie_file=manual, expected_revision=profile.snapshot().revision)
        coordinator = CookieExpiryCoordinator(profile, lambda: store)
        client, _ = reader(manual, coordinator, [])
        _extend(client, expiry + 7200)
        assert saved[0].is_file()
        assert profile.snapshot().cookie_file != manual
        assert len(list((store.root / 'generations').iterdir())) <= 8
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


@pytest.mark.skipif(__import__('os').name != 'nt', reason='Windows sharing-mode test')
def test_changed_metadata_after_reader_lock_does_not_delete_cookie(tmp_path):
    import os
    from pixivvault_nicegui.frozen_source_pin import _open_held
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    held = None
    try:
        for index in range(8):
            _extend(client, expiry + 3600 + index)
            if index == 0:
                old = profile.snapshot().cookie_file
                original = old.read_bytes()
                held = _open_held(old)
        _extend(client, expiry + 3608)
        assert (old.parent / '.retiring').is_file()
        os.close(held)
        held = None
        metadata = old.parent / 'generation.json'
        packet = json.loads(metadata.read_bytes())
        packet['previous'] = 'c' * 32
        metadata.write_text(json.dumps(packet, separators=(',', ':')) + '\n', encoding='ascii')
        _extend(client, expiry + 3609)
        assert old.read_bytes() == original
        assert metadata.is_file()
    finally:
        if held is not None:
            os.close(held)
        client.close(); coordinator.close(); store.close(); profile.close()
