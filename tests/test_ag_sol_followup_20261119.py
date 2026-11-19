"""Regression tests for independent Sol findings, using synthetic data."""
import sqlite3
import os
from email.utils import formatdate

from test_ag_z04_20261119 import setup, reader, fetch


def test_cookie_source_cannot_change_after_final_proof(tmp_path, monkeypatch):
    from pixivvault_nicegui.extension_cookie_store import CookieSyncError
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    header = 'PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Expires=' + formatdate(expiry + 3600, usegmt=True)
    client, _ = reader(cookie, coordinator, [header])
    original_update = profile.update
    replacement = cookie.read_bytes().replace(b'777_old', b'777_new')
    modified = []
    def concurrent_update(**kwargs):
        try:
            cookie.write_bytes(replacement)
            modified.append(True)
        except PermissionError:
            modified.append(False)
        return original_update(**kwargs)
    monkeypatch.setattr(profile, 'update', concurrent_update)
    try:
        try:
            fetch(client)
        except CookieSyncError:
            pass
        assert modified == [False] or profile.snapshot().cookie_file == cookie
        if modified == [True]:
            assert cookie.read_bytes() == replacement
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()


def test_cookie_renewal_ignores_later_legacy_wal(tmp_path):
    expiry, cookie, profile, store, old = setup(tmp_path)
    old.close()
    legacy = tmp_path / 'legacy'
    legacy.mkdir()
    db = legacy / 'legacy.sqlite'
    images = tmp_path / 'legacy-images'
    images.mkdir()
    with sqlite3.connect(db) as connection:
        connection.execute('CREATE TABLE sample (value INTEGER)')
    profile.update(expected_revision=profile.snapshot().revision,
                   library_readonly_db=db, library_image_root=images)
    from pixivvault_nicegui.cookie_expiry_refresh import CookieExpiryCoordinator
    coordinator = CookieExpiryCoordinator(profile, lambda: store)
    header = 'PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Expires=' + formatdate(expiry + 3600, usegmt=True)
    client, sent = reader(cookie, coordinator, [header])
    connection = sqlite3.connect(db)
    try:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('INSERT INTO sample VALUES (1)')
        connection.commit()
        fetch(client)
        fetch(client)
        assert len(sent) == 2
        assert profile.snapshot().cookie_file != cookie
    finally:
        connection.close()
        client.close()
        coordinator.close()
        store.close()
        profile.close()
