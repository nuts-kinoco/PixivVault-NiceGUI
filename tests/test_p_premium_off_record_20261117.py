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


# ---- 5: OFF 記録だけで鍵素材が無いとき、保管庫の書き込みロックを取らない -------------------

def test_off_record_without_material_does_not_take_the_vault_write_lock(tmp_path, monkeypatch):
    now = [1000]
    prot = P.Protection()
    v, g, _ = P.create(tmp_path / "v", prot, now)
    P.enable(g, False)
    g.disable()
    v.close()
    calls = []
    orig = ExternalVaultService._ensure_writable

    def spy(self, expected):
        calls.append(expected)
        return orig(self, expected)
    monkeypatch.setattr(ExternalVaultService, "_ensure_writable", spy)
    v2, g2, _ = P.create(tmp_path / "v", prot, now)
    assert calls == []
    assert g2.status()["error"] is None
    v2.close()


def test_off_record_with_leftover_material_still_removes_it(tmp_path):
    now = [1000]
    prot = P.Protection()
    v, g, _ = P.create(tmp_path / "v", prot, now)
    P.enable(g, True)
    material = g._read_protected("material.dpapi")
    g.disable()
    g._write_protected("material.dpapi", material)
    v.close()
    v2, g2, _ = P.create(tmp_path / "v", prot, now)
    assert not g2._path("material.dpapi").exists() and g2.status()["error"] is None
    v2.close()


def test_second_instance_with_off_record_does_not_report_an_error(tmp_path):
    now = [1000]
    prot = P.Protection()
    v, g, _ = P.create(tmp_path / "v", prot, now)
    P.enable(g, False)
    g.disable()  # この時点で v が書き込みロックを持つ
    v2, g2, _ = P.create(tmp_path / "v", prot, now)
    assert g2.status()["error"] is None
    v2.close()
    v.close()
