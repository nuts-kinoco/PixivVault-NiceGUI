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


# ---- 4: 取得停止ゲートが閉じているときは何も削除しない -------------------------------------

async def test_confirm_with_a_closed_gate_deletes_nothing(tmp_path, monkeypatch):
    binding, following, events = F.make(tmp_path, monkeypatch, uid="888")
    F.seed_account(following, "777", hours_ago=40)
    await F.go(binding)
    assert binding.followup.pending is not None

    def stopped(cookie):
        raise ManagedNetworkStopped(429)
    binding.followup.reader_factory = stopped
    await binding.followup.confirm_account_switch()
    assert len(following.snapshot("777").authors) == 2
    assert following.last_synced_account() == "777"
    notices = F.texts(events)
    assert NOTICE_SYNC_FAILED not in notices
    assert NOTICE_SWITCH_CONFIRMED.format(uid="888") not in notices
    assert any("削除していません" in n for n in notices)
    assert binding.followup.pending is None and not binding.followup._running
    # 解除後の次の確認では、また尋ねる（辞退扱いにはしない）。
    binding.followup.reader_factory = lambda cookie: F.FakeFollowReader("888")
    events.clear()
    await F.go(binding)
    assert binding.followup.pending is not None
    binding.close()


async def test_failure_after_deletion_is_not_reported_as_data_kept(tmp_path, monkeypatch):
    binding, following, events = F.make(tmp_path, monkeypatch, uid="888")
    F.seed_account(following, "777", hours_ago=40)
    await F.go(binding)
    opened = []

    def second_open_fails(cookie):
        opened.append(1)
        if len(opened) == 1:
            return F.FakeFollowReader("888")
        raise ManagedNetworkStopped(429)
    binding.followup.reader_factory = second_open_fails
    await binding.followup.confirm_account_switch()
    notices = F.texts(events)
    assert following.snapshot("777").authors == ()
    assert NOTICE_SYNC_FAILED not in notices
    assert any("削除済み" in n for n in notices)
    binding.close()


async def test_confirm_with_an_open_gate_still_deletes_then_fetches(tmp_path, monkeypatch):
    binding, following, events = F.make(tmp_path, monkeypatch, uid="888")
    F.seed_account(following, "777", hours_ago=40)
    await F.go(binding)
    await binding.followup.confirm_account_switch()
    assert following.snapshot("777").authors == ()
    assert len(following.snapshot("888").following) == 3
    assert NOTICE_SWITCH_CONFIRMED.format(uid="888") in F.texts(events)
    binding.close()
