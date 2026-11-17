"""checker 指摘 4〜6: 別アカウント確認の削除順、プレミアム OFF 記録の起動、セッション Cookie の期限。"""
import base64
import json
import sqlite3
import time

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import test_cookie_account_followup_20261117 as F
import test_premium_authorization as P
from pixivvault_nicegui.account_followup import (NOTICE_SWITCH_CONFIRMED, NOTICE_SYNC_FAILED)
from pixivvault_nicegui.browser_cookie_source import BrowserCookieSource
from pixivvault_nicegui.external_vault import ExternalVaultService
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopped


# ---- 6: 期限なし（セッション）Cookie は元と同じく 30 日後 ----------------------------------

KEY = bytes(range(32))


def fake_unprotect(blob):
    if blob == b"WRAPPED" + KEY:
        return KEY
    raise OSError("bad blob")


def enc(value):
    nonce = b"n" * 12
    return b"v10" + nonce + AESGCM(KEY).encrypt(nonce, value.encode(), None)


def test_chromium_session_cookie_expires_in_thirty_days(tmp_path):
    ud = tmp_path / "Local" / "Google" / "Chrome" / "User Data"
    (ud / "Default" / "Network").mkdir(parents=True)
    (ud / "Local State").write_text(json.dumps({"os_crypt": {"encrypted_key": base64.b64encode(
        b"DPAPI" + b"WRAPPED" + KEY).decode()}}), encoding="utf-8")
    db = sqlite3.connect(ud / "Default" / "Network" / "Cookies")
    db.execute("CREATE TABLE cookies (host_key, name, value, encrypted_value, path, expires_utc, is_secure)")
    db.execute("INSERT INTO cookies VALUES (?,?,?,?,?,?,?)",
               (".pixiv.net", "PHPSESSID", "", enc("abc_def"), "/", 0, 1))
    db.commit()
    db.close()
    src = BrowserCookieSource(local_app_data=tmp_path / "Local", app_data=tmp_path / "Roaming",
                              scratch_dir=tmp_path / "scratch", unprotect=fake_unprotect)
    found = src.extract()
    row = [c for c in found.cookies if c["name"] == "PHPSESSID"][0]
    assert abs(row["expirationDate"] - (time.time() + 30 * 86400)) < 60


def test_firefox_session_cookie_expires_in_thirty_days(tmp_path):
    ff = tmp_path / "Roaming" / "Mozilla" / "Firefox" / "Profiles" / "p1"
    ff.mkdir(parents=True)
    db = sqlite3.connect(ff / "cookies.sqlite")
    db.execute("CREATE TABLE moz_cookies (host, name, value, path, expiry, isSecure)")
    db.execute("INSERT INTO moz_cookies VALUES (?,?,?,?,?,?)",
               (".pixiv.net", "PHPSESSID", "abc_def", "/", 0, 1))
    db.commit()
    db.close()
    src = BrowserCookieSource(local_app_data=tmp_path / "Local", app_data=tmp_path / "Roaming",
                              scratch_dir=tmp_path / "scratch", unprotect=fake_unprotect)
    found = src.extract()
    row = [c for c in found.cookies if c["name"] == "PHPSESSID"][0]
    assert abs(row["expirationDate"] - (time.time() + 30 * 86400)) < 60
