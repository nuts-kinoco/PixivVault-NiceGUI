"""Legacy-generation migration uses isolated synthetic profile data only."""
from email.utils import formatdate
import sqlite3
import json

import pytest

from pixivvault_nicegui.cookie_expiry_refresh import CookieExpiryCoordinator
from pixivvault_nicegui.extension_cookie_store import ExtensionCookieStore
from pixivvault_nicegui.pixiv_read_client import _ReadResponse
from test_ag_z04_20261119 import setup, reader, fetch


@pytest.mark.parametrize('manual_switch', [False, True])
def test_legacy_eight_generation_profile_continues_expiry_updates(tmp_path, manual_switch):
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    try:
        for index in range(8):
            header = 'PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Expires=' + formatdate(expiry + 3600 + index, usegmt=True)
            client._transport = lambda *args: _ReadResponse(b'{"error":false,"body":{}}', (header,))
            fetch(client)
        client.close(); coordinator.close(); store.close()
        connection = sqlite3.connect(profile.path)
        try:
            connection.execute('DROP TABLE native_cookie_adoptions')
            connection.commit()
        finally:
            connection.close()
        selected = profile.snapshot().cookie_file
        if manual_switch:
            manual = tmp_path / 'manual' / 'cookie.txt'
            manual.parent.mkdir()
            manual.write_bytes(cookie.read_bytes())
            profile.update(cookie_file=manual, expected_revision=profile.snapshot().revision)
            selected = manual
        old_files = {p: p.read_bytes() for p in (tmp_path / 'generations' / 'generations').rglob('*') if p.is_file()}
        store = ExtensionCookieStore(tmp_path / 'generations', profile=profile)
        if manual_switch:
            assert store.take_legacy_migration_notice() is True
            assert store.take_legacy_migration_notice() is False
        coordinator = CookieExpiryCoordinator(profile, lambda: store)
        client, _ = reader(selected, coordinator, [])
        start = profile.snapshot().revision
        for index in range(3):
            header = 'PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Expires=' + formatdate(expiry + 7200 + index, usegmt=True)
            client._transport = lambda *args: _ReadResponse(b'{"error":false,"body":{}}', (header,))
            fetch(client)
        if manual_switch:
            assert all(p.exists() and p.read_bytes() == content for p, content in old_files.items())
        else:
            assert profile.snapshot().revision == start + 3
            assert len(list((store.root / 'generations').iterdir())) <= 8
            with sqlite3.connect(profile.path) as db:
                before = db.execute('SELECT COUNT(*) FROM native_cookie_adoptions').fetchone()
            store.close()
            store = ExtensionCookieStore(tmp_path / 'generations', profile=profile)
            with sqlite3.connect(profile.path) as db:
                assert db.execute('SELECT COUNT(*) FROM native_cookie_adoptions').fetchone() == before
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_invalid_ancestor_is_retained_and_notice_is_claimed_once(tmp_path):
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    try:
        for index in range(3):
            header = 'PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Expires=' + formatdate(expiry + 3600 + index, usegmt=True)
            client._transport = lambda *args: _ReadResponse(b'{"error":false,"body":{}}', (header,))
            fetch(client)
        selected = profile.snapshot().cookie_file
        metadata = json.loads((selected.parent / 'generation.json').read_text(encoding='utf-8'))
        ancestor = store.root / 'generations' / metadata['previous']
        client.close(); coordinator.close(); store.close()
        meta_file = ancestor / 'generation.json'
        invalid = json.loads(meta_file.read_text(encoding='utf-8'))
        invalid['previous'] = '../bad'
        meta_file.write_text(json.dumps(invalid), encoding='utf-8')
        original = {p: p.read_bytes() for p in ancestor.iterdir() if p.is_file()}
        with sqlite3.connect(profile.path) as db:
            db.execute('DROP TABLE native_cookie_adoptions')
        store = ExtensionCookieStore(tmp_path / 'generations', profile=profile)
        assert store.take_legacy_migration_notice() is True
        with sqlite3.connect(profile.path) as db:
            adopted = {row[0] for row in db.execute('SELECT cookie_path FROM native_cookie_adoptions')}
        assert str(selected) in adopted
        assert str(ancestor / 'cookies.txt') not in adopted
        store.close()
        store = ExtensionCookieStore(tmp_path / 'generations', profile=profile)
        assert store.take_legacy_migration_notice() is False
        assert all(p.exists() and p.read_bytes() == content for p, content in original.items())
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_selected_older_generation_does_not_adopt_newer_branch(tmp_path):
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    try:
        for index in range(4):
            header = 'PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Expires=' + formatdate(expiry + 3600 + index, usegmt=True)
            client._transport = lambda *args: _ReadResponse(b'{"error":false,"body":{}}', (header,))
            fetch(client)
        newer = profile.snapshot().cookie_file
        previous = json.loads((newer.parent / 'generation.json').read_text(encoding='utf-8'))['previous']
        older = newer.parent.parent / previous / 'cookies.txt'
        client.close(); coordinator.close(); store.close()
        profile.update(cookie_file=older, expected_revision=profile.snapshot().revision)
        with sqlite3.connect(profile.path) as db:
            db.execute('DROP TABLE native_cookie_adoptions')
        before = {p: p.read_bytes() for p in newer.parent.iterdir() if p.is_file()}
        store = ExtensionCookieStore(tmp_path / 'generations', profile=profile)
        with sqlite3.connect(profile.path) as db:
            adopted = {row[0] for row in db.execute('SELECT cookie_path FROM native_cookie_adoptions')}
        assert str(older) in adopted
        assert str(newer) not in adopted
        assert store.take_legacy_migration_notice() is True
        assert all(p.exists() and p.read_bytes() == content for p, content in before.items())
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_cyclic_legacy_links_are_not_adopted(tmp_path):
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    try:
        for index in range(4):
            header = 'PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Expires=' + formatdate(expiry + 3600 + index, usegmt=True)
            client._transport = lambda *args: _ReadResponse(b'{"error":false,"body":{}}', (header,))
            fetch(client)
        cursor = profile.snapshot().cookie_file
        paths = []
        while cursor is not None:
            paths.append(cursor)
            previous = json.loads((cursor.parent / 'generation.json').read_text(encoding='utf-8'))['previous']
            cursor = cursor.parent.parent / previous / 'cookies.txt' if previous else None
        client.close(); coordinator.close(); store.close()
        meta_file = paths[-1].parent / 'generation.json'
        packet = json.loads(meta_file.read_text(encoding='utf-8'))
        packet['previous'] = paths[-2].parent.name
        meta_file.write_text(json.dumps(packet), encoding='utf-8')
        with sqlite3.connect(profile.path) as db:
            db.execute('DROP TABLE native_cookie_adoptions')
        store = ExtensionCookieStore(tmp_path / 'generations', profile=profile)
        with sqlite3.connect(profile.path) as db:
            adopted = {row[0] for row in db.execute('SELECT cookie_path FROM native_cookie_adoptions')}
        assert str(paths[0]) in adopted
        assert all(str(path) not in adopted and path.exists() for path in paths[-2:])
        assert store.take_legacy_migration_notice() is True
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_interrupted_migration_rolls_back_and_can_retry(tmp_path):
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, _ = reader(cookie, coordinator, [])
    try:
        for index in range(3):
            header = 'PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Expires=' + formatdate(expiry + 3600 + index, usegmt=True)
            client._transport = lambda *args: _ReadResponse(b'{"error":false,"body":{}}', (header,))
            fetch(client)
        selected = profile.snapshot()
        original = {p: p.read_bytes() for p in (store.root / 'generations').rglob('*') if p.is_file()}
        client.close(); coordinator.close(); store.close()
        with sqlite3.connect(profile.path) as db:
            db.execute('DELETE FROM native_cookie_adoptions')
            db.execute("CREATE TRIGGER fail_migration BEFORE INSERT ON native_cookie_adoptions BEGIN SELECT RAISE(ABORT, 'test interruption'); END")
        with pytest.raises(Exception):
            ExtensionCookieStore(tmp_path / 'generations', profile=profile)
        assert profile.snapshot() == selected
        assert all(p.exists() and p.read_bytes() == content for p, content in original.items())
        with sqlite3.connect(profile.path) as db:
            assert db.execute('SELECT COUNT(*) FROM native_cookie_adoptions').fetchone()[0] == 0
            db.execute('DROP TRIGGER fail_migration')
        store = ExtensionCookieStore(tmp_path / 'generations', profile=profile)
        with sqlite3.connect(profile.path) as db:
            assert db.execute('SELECT COUNT(*) FROM native_cookie_adoptions').fetchone()[0] == 3
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()
