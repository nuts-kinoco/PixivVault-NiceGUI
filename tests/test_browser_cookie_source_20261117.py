"""Browser Cookie extraction (FL-A08) against fake browser stores; no real profile.

Every root is injected into tmp_path. Cookie values are dummy strings.
"""
import base64
import hashlib
import json
import os
import sqlite3

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from pixivvault_nicegui.browser_cookie_source import BrowserCookieSource

KEY = bytes(range(32))
CHROME_EPOCH = 11644473600


def _unprotect(blob):
    return blob  # fake OS unprotect: identity


def _chromium_cookie(db, host, name, value, *, expires_unix=None, secure=1, encrypt=True, path='/'):
    expires = int((expires_unix + CHROME_EPOCH) * 1000000) if expires_unix else 0
    if encrypt:
        nonce = os.urandom(12)
        blob = b'v10' + nonce + AESGCM(KEY).encrypt(nonce, value.encode(), None)
        plain, enc = '', blob
    else:
        plain, enc = value, b''
    db.execute('INSERT INTO cookies VALUES(?,?,?,?,?,?,?)',
               (host, name, plain, enc, path, expires, secure))


def make_chromium(root, vendor, rows, *, profile='Default', layout='Network'):
    user_data = root.joinpath(*vendor)
    user_data.mkdir(parents=True)
    key = base64.b64encode(b'DPAPI' + KEY).decode()
    (user_data / 'Local State').write_text(
        json.dumps({'os_crypt': {'encrypted_key': key}}), encoding='utf-8')
    folder = user_data / profile / layout if layout else user_data / profile
    folder.mkdir(parents=True)
    db = sqlite3.connect(folder / 'Cookies')
    db.execute('CREATE TABLE cookies(host_key TEXT,name TEXT,value TEXT,encrypted_value BLOB,'
               'path TEXT,expires_utc INTEGER,is_secure INTEGER)')
    for row in rows:
        _chromium_cookie(db, *row[:3], **row[3] if len(row) > 3 else {})
    db.commit()
    db.close()
    return user_data


def make_firefox(root, rows):
    profile = root / 'Mozilla' / 'Firefox' / 'Profiles' / 'abc.default'
    profile.mkdir(parents=True)
    db = sqlite3.connect(profile / 'cookies.sqlite')
    db.execute('CREATE TABLE moz_cookies(host TEXT,name TEXT,value TEXT,path TEXT,expiry INTEGER,isSecure INTEGER)')
    for host, name, value, expiry in rows:
        db.execute('INSERT INTO moz_cookies VALUES(?,?,?,?,?,?)', (host, name, value, '/', expiry, 1))
    db.commit()
    db.close()


def source(tmp_path, **kw):
    return BrowserCookieSource(local_app_data=tmp_path / 'local', app_data=tmp_path / 'roaming',
                               scratch_dir=tmp_path / 'scratch', unprotect=_unprotect, **kw)


def by_name(result):
    return {row['name']: row for row in result.cookies}


CHROME = ('Google', 'Chrome', 'User Data')
EDGE = ('Microsoft', 'Edge', 'User Data')


def test_chrome_values_are_decrypted_and_filtered_to_pixiv_net(tmp_path):
    make_chromium(tmp_path / 'local', CHROME, [
        ('.pixiv.net', 'PHPSESSID', 'dummy-session', {'expires_unix': 2000000000}),
        ('www.pixiv.net', 'device_token', 'dummy-device', {'expires_unix': 2000000000}),
        ('.pixiv.net', 'p_ab_id', 'plain-value', {'encrypt': False}),
        ('.notpixiv.net.example.com', 'PHPSESSID', 'stranger', {'expires_unix': 2000000000}),
        ('.pixiv.net', 'PHPSESSID', 'dummy-duplicate', {'expires_unix': 2000000000}),
    ])
    result = source(tmp_path).extract()
    assert result.browser == 'Chrome'
    rows = by_name(result)
    assert rows['PHPSESSID']['value'] == 'dummy-session'  # first one kept, duplicate dropped
    assert rows['PHPSESSID']['expirationDate'] == 2000000000
    assert rows['p_ab_id']['expirationDate'] is None  # session cookie: the store assigns expiry
    assert rows['p_ab_id']['value'] == 'plain-value'
    assert len(result.cookies) == 3
    assert all(row['domain'].lstrip('.').endswith('pixiv.net') for row in result.cookies)
    assert 'dummy-session' not in repr(result)  # values never reach logs


def test_profile_without_login_session_is_skipped(tmp_path):
    user_data = make_chromium(tmp_path / 'local', CHROME,
                              [('.pixiv.net', 'p_ab_id', 'x', {'expires_unix': 2000000000})])
    second = user_data / 'Profile 1' / 'Network'
    second.mkdir(parents=True)
    db = sqlite3.connect(second / 'Cookies')
    db.execute('CREATE TABLE cookies(host_key TEXT,name TEXT,value TEXT,encrypted_value BLOB,'
               'path TEXT,expires_utc INTEGER,is_secure INTEGER)')
    _chromium_cookie(db, '.pixiv.net', 'PHPSESSID', 'second-profile', expires_unix=2000000000)
    db.commit()
    db.close()
    assert by_name(source(tmp_path).extract())['PHPSESSID']['value'] == 'second-profile'


def test_search_order_is_chrome_edge_then_firefox(tmp_path):
    make_chromium(tmp_path / 'local', EDGE, [('.pixiv.net', 'PHPSESSID', 'edge-session', {'expires_unix': 2000000000})])
    make_firefox(tmp_path / 'roaming', [('.pixiv.net', 'PHPSESSID', 'fox-session', 2000000000)])
    assert source(tmp_path).extract().browser == 'Edge'
    make_chromium(tmp_path / 'local', CHROME, [('.pixiv.net', 'PHPSESSID', 'chrome-session', {'expires_unix': 2000000000})])
    assert source(tmp_path).extract().browser == 'Chrome'


def test_firefox_plain_store(tmp_path):
    make_firefox(tmp_path / 'roaming', [('.pixiv.net', 'PHPSESSID', 'fox-session', 2000000000),
                                        ('www.pixiv.net', 'x', 'y', 0)])
    result = source(tmp_path).extract()
    assert result.browser == 'Firefox'
    assert by_name(result)['PHPSESSID']['value'] == 'fox-session'
    assert by_name(result)['x']['expirationDate'] is None


def test_nothing_found_or_unreadable_returns_none(tmp_path):
    assert source(tmp_path).extract() is None  # no browser installed
    make_chromium(tmp_path / 'local', CHROME, [('.pixiv.net', 'PHPSESSID', 'v', {'expires_unix': 2000000000})])

    def broken(blob):
        raise OSError('cannot unprotect')
    assert BrowserCookieSource(local_app_data=tmp_path / 'local', app_data=tmp_path / 'roaming',
                               scratch_dir=tmp_path / 'scratch', unprotect=broken).extract() is None


def test_browser_files_are_not_modified_and_scratch_is_cleaned(tmp_path):
    user_data = make_chromium(tmp_path / 'local', CHROME,
                              [('.pixiv.net', 'PHPSESSID', 'v', {'expires_unix': 2000000000})])
    original = user_data / 'Default' / 'Network' / 'Cookies'
    before = hashlib.sha256(original.read_bytes()).hexdigest()
    assert source(tmp_path).extract() is not None
    assert hashlib.sha256(original.read_bytes()).hexdigest() == before
    assert list((tmp_path / 'scratch').iterdir()) == []


def test_source_never_reads_the_environment_by_itself(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'env-local'))
    monkeypatch.setenv('APPDATA', str(tmp_path / 'env-roaming'))
    make_chromium(tmp_path / 'env-local', CHROME, [('.pixiv.net', 'PHPSESSID', 'v', {'expires_unix': 2000000000})])
    assert BrowserCookieSource(unprotect=_unprotect).extract() is None
