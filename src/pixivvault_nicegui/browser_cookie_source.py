"""Pixiv login Cookie extraction from installed browsers (replaceable source).

Restores the original PixivClient.auto_extract_browser_cookies /
_extract_cookies_native (pixiv_client.py): Chrome, Edge and Brave are decrypted
with the browser master key, Firefox is read from its plain cookies.sqlite.
Nothing here communicates, writes a credential file or logs a Cookie value.

The browser roots, the OS unprotect call and the scratch directory are all
injected. Nothing reads the environment except ``default_browser_cookie_source``,
which only the explicit native launch calls, so tests and a bare binding never
touch a real browser profile. No third-party package is required: AES-GCM comes
from ``cryptography`` (already a dependency) and Windows unprotect from ctypes.
"""
from __future__ import annotations

import base64
import glob
import json
import os
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

# Same search order as the original: Chromium family first, then Firefox.
CHROMIUM_BROWSERS = (
    ('Chrome', ('Google', 'Chrome', 'User Data')),
    ('Edge', ('Microsoft', 'Edge', 'User Data')),
    ('Brave', ('BraveSoftware', 'Brave-Browser', 'User Data')),
)
_PROFILES = ('Default',) + tuple(f'Profile {i}' for i in range(1, 10))
_CHROME_EPOCH_OFFSET = 11644473600
_MAX_DB_BYTES = 256 * 1024 * 1024
_SESSION_COOKIE_SECONDS = 86400 * 30  # 元: 期限の無い Cookie は今から 30 日後として扱う


@dataclass(frozen=True)
class ExtractedCookies:
    """Rows in the shape ExtensionCookieStore.prepare() accepts."""
    browser: str
    cookies: list = field(repr=False)

    def __repr__(self):  # never print values
        return f'ExtractedCookies(browser={self.browser!r}, count={len(self.cookies)})'


def _windows_unprotect(blob: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(blob, len(blob))
    source = _Blob(len(blob), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    target = _Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target)):
        raise OSError('unprotect failed')
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


def _pixiv_host(host):
    host = str(host).lower().lstrip('.')
    return host == 'pixiv.net' or host.endswith('.pixiv.net')


def _clean(rows):
    """Keep only valid, unique pixiv.net rows so one stray row cannot reject all."""
    result, seen = [], set()
    for row in rows:
        domain, path, name = row['domain'], row['path'] or '/', row['name']
        if not (_pixiv_host(domain) and name and isinstance(row['value'], str)):
            continue
        key = (domain.lower(), path, name)
        if key in seen:
            continue
        seen.add(key)
        result.append({'domain': domain.lower(), 'path': path, 'name': name,
                       'value': row['value'], 'secure': bool(row['secure']),
                       'expirationDate': row['expirationDate']})
    return result


class BrowserCookieSource:
    """Interface: ``extract()`` returns ExtractedCookies or None. Never raises."""

    def __init__(self, *, local_app_data=None, app_data=None, scratch_dir=None,
                 unprotect=None):
        self.local_app_data = None if local_app_data is None else Path(local_app_data)
        self.app_data = None if app_data is None else Path(app_data)
        self.scratch_dir = None if scratch_dir is None else Path(scratch_dir)
        self._unprotect = unprotect or _windows_unprotect

    def extract(self):
        try:
            for name, parts in CHROMIUM_BROWSERS:
                if self.local_app_data is None:
                    break
                rows = self._chromium(name, self.local_app_data.joinpath(*parts))
                if rows:
                    return ExtractedCookies(name, rows)
            rows = self._firefox()
            if rows:
                return ExtractedCookies('Firefox', rows)
        except Exception:
            return None
        return None

    # -- helpers ---------------------------------------------------------

    def _read_copy(self, database: Path, query: str):
        """Copy the live DB to a unique scratch folder, read it, always remove it."""
        if not database.is_file() or database.stat().st_size > _MAX_DB_BYTES:
            return []
        if self.scratch_dir is not None:
            self.scratch_dir.mkdir(parents=True, exist_ok=True)
        folder = tempfile.mkdtemp(prefix='pv-cookie-', dir=self.scratch_dir)
        try:
            copy = Path(folder) / 'cookies.sqlite'
            shutil.copy2(database, copy)
            connection = sqlite3.connect(str(copy))
            try:
                return connection.execute(query).fetchall()
            finally:
                connection.close()
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def _master_key(self, user_data: Path):
        state = user_data / 'Local State'
        if not state.is_file():
            return None
        encoded = (json.loads(state.read_text(encoding='utf-8'))
                   .get('os_crypt', {}).get('encrypted_key'))
        if not encoded:
            return None
        key = base64.b64decode(encoded)
        if key.startswith(b'DPAPI'):
            key = key[5:]
        return self._unprotect(key)

    def _chromium(self, browser, user_data: Path):
        if not user_data.is_dir():
            return []
        try:
            master = self._master_key(user_data)
        except Exception:
            return []
        if not master:
            return []
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        for profile in _PROFILES:
            database = user_data / profile / 'Network' / 'Cookies'
            if not database.is_file():
                database = user_data / profile / 'Cookies'
            if not database.is_file():
                continue
            try:
                rows = self._read_copy(database,
                    "SELECT host_key, name, value, encrypted_value, path, expires_utc, is_secure "
                    "FROM cookies WHERE host_key LIKE '%pixiv%'")
            except Exception:
                continue
            found = []
            for host, name, value, encrypted, path, expires_utc, secure in rows:
                if not value and encrypted:
                    encrypted = bytes(encrypted)
                    try:
                        if encrypted[:3] in (b'v10', b'v20'):
                            value = AESGCM(master).decrypt(
                                encrypted[3:15], encrypted[15:], None).decode('utf-8')
                        else:
                            value = self._unprotect(encrypted).decode('utf-8')
                    except Exception:
                        continue
                if not value:
                    continue
                expiry = (int(expires_utc / 1000000 - _CHROME_EPOCH_OFFSET)
                          if expires_utc and expires_utc > 0
                          else int(time.time() + _SESSION_COOKIE_SECONDS))
                found.append({'domain': host, 'name': name, 'value': value,
                              'path': path, 'expirationDate': expiry, 'secure': secure})
            found = _clean(found)
            # Like the original: the first profile that holds a login session wins.
            if any(row['name'] == 'PHPSESSID' for row in found):
                return found
        return []

    def _firefox(self):
        if self.app_data is None:
            return []
        pattern = str(self.app_data / 'Mozilla' / 'Firefox' / 'Profiles' / '*' / 'cookies.sqlite')
        for database in sorted(glob.glob(pattern)):
            try:
                rows = self._read_copy(Path(database),
                    "SELECT host, name, value, path, expiry, isSecure FROM moz_cookies "
                    "WHERE host LIKE '%pixiv%'")
            except Exception:
                continue
            found = []
            for host, name, value, path, expiry, secure in rows:
                if not value:
                    continue
                expiry = int(expiry) if expiry else int(time.time() + _SESSION_COOKIE_SECONDS)
                if expiry > 100_000_000_000:  # milliseconds (older builds)
                    expiry //= 1000
                found.append({'domain': host, 'name': name, 'value': value, 'path': path,
                              'expirationDate': expiry, 'secure': secure})
            found = _clean(found)
            if any(row['name'] == 'PHPSESSID' for row in found):
                return found
        return []


def default_browser_cookie_source(scratch_dir=None):
    """The real installed-browser source; only the explicit native launch calls this."""
    return BrowserCookieSource(local_app_data=os.environ.get('LOCALAPPDATA'),
                               app_data=os.environ.get('APPDATA'), scratch_dir=scratch_dir)
