"""Header login-status model restored from the original Flet application.

The original (gui.py check_login_status / PixivClient.check_cookie_status)
shows one always-visible status line, plus a "Cookie not linked" banner.
This module only maps already-obtained results to that presentation; it never
communicates. Cookie values are never read here: only name, domain and expiry.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from http.cookiejar import MozillaCookieJar
from pathlib import Path

# Original Flet colours (ft.Colors.*_400); CHECKING mirrors ft.Colors.PRIMARY.
GREEN = '#66BB6A'
YELLOW = '#FFEE58'
RED = '#EF5350'
BLUE = '#42A5F5'
PRIMARY = '#0096FA'

CHECKING_TEXT = 'ログインチェック中...'
CONFIRMING_TEXT = '⏳ Cookie を確認中...'
MISSING_MESSAGE = 'Cookieファイルが設定されていません。設定から選んでください。'
EXPIRED_MESSAGE = 'ログインセッションが無効化されています。またはCookieの有効期限が切れています。'
NETWORK_MESSAGE = ('pixiv と通信できませんでした。ネットワーク状態を確認してください。'
                   'Cookieの有効性は判定できていません。')
NOTICE_UPDATED = 'Cookie を確認・更新しました。'
_COOKIE_FILE_LIMIT = 1024 * 1024
_IGNORED_FOR_EXPIRY = {'_cfuvid', '_ga', 'g_state'}


@dataclass(frozen=True)
class AuthHeaderStatus:
    level: str
    text: str
    color: str
    banner: bool = False
    ok: bool = False
    user_id: str | None = None


CHECKING = AuthHeaderStatus('checking', CHECKING_TEXT, PRIMARY)
CONFIRMING = AuthHeaderStatus('checking', CONFIRMING_TEXT, BLUE)


def cookie_days_left(cookie_file, *, now: float | None = None) -> int:
    """Remaining days by the original rule; reads only name/domain/expires."""
    now = time.time() if now is None else now
    try:
        path = Path(cookie_file)
        if not path.is_file() or path.stat().st_size > _COOKIE_FILE_LIMIT:
            return 30
        jar = MozillaCookieJar(str(path))
        jar.load(ignore_discard=True, ignore_expires=True)
        cookies = [c for c in jar if 'pixiv.net' in c.domain]
        session = next((c for c in cookies if c.name == 'PHPSESSID'), None)
        target = None
        if session is not None and session.expires and session.expires > 0:
            target = session.expires
        else:
            others = [c.expires for c in cookies
                      if c.expires and c.expires > 0 and c.name not in _IGNORED_FOR_EXPIRY]
            if others:
                target = max(others)
        if target and target > now:
            return max(1, int(math.ceil((target - now) / 86400)))
    except Exception:
        pass
    # The API accepted the session but no usable expiry exists: original default.
    return 30


def missing_status() -> AuthHeaderStatus:
    return AuthHeaderStatus('expired', f'× [未認証/期限切れ] {MISSING_MESSAGE}', RED, banner=True)


def status_from_result(result, user_id=None, days_left=None) -> AuthHeaderStatus:
    """Map a NativeAuthResult to the original header states."""
    code = getattr(result, 'code', None)
    if getattr(result, 'ok', False):
        days = 30 if days_left is None else days_left
        uid = '' if user_id is None else str(user_id)
        if days > 7:
            return AuthHeaderStatus(
                'valid', f'● [有効] ログイン中 (有効期限 残り約{days}日) - ユーザーID: {uid}',
                GREEN, ok=True, user_id=user_id)
        if days > 3:
            return AuthHeaderStatus(
                'warning_yellow',
                f'▲ [注意/残り1週間以内] Cookie有効期限 残り約{days}日 (1週間以内/黄色アラート) - ID: {uid}',
                YELLOW, ok=True, user_id=user_id)
        return AuthHeaderStatus(
            'warning_red',
            f'▲ [警告/残り3日以内] Cookie有効期限 残り約{days}日 (3日以内/赤色アラート) - ID: {uid}',
            RED, ok=True, user_id=user_id)
    if code == 'communication_failed':
        return AuthHeaderStatus('network_error', f'▲ [通信エラー] {NETWORK_MESSAGE}', YELLOW)
    if code in ('cookie_unreadable', 'identity_unverified', 'remote_denied'):
        message = EXPIRED_MESSAGE if code == 'identity_unverified' else result.message
        return AuthHeaderStatus('expired', f'× [未認証/期限切れ] {message}', RED, banner=True)
    # STOP, busy, uncertain, rate limit, local failure, shutdown: no request was
    # made (or its outcome is unknown). Show the bounded reason; never claim the
    # Cookie is invalid. These states have no original equivalent.
    message = result.message if hasattr(result, 'message') else '認証確認の結果を取得できませんでした。'
    return AuthHeaderStatus('stopped', f'▲ [通信停止中] {message}', YELLOW)


class Recovery401Event:
    """A local, thread-safe signal emitted only after a 401 recovery commits."""

    def __init__(self):
        from threading import Lock
        self._lock = Lock()
        self._generation = 0
        self._revision = None
        self._listeners = []

    @property
    def generation(self):
        with self._lock:
            return self._generation

    @property
    def revision(self):
        with self._lock:
            return self._revision

    def subscribe(self, listener):
        with self._lock:
            self._listeners.append(listener)
        def unsubscribe():
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)
        return unsubscribe

    def publish(self, revision):
        with self._lock:
            self._generation += 1
            generation = self._generation
            self._revision = revision
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(generation, revision)
            except Exception:
                pass  # Notification failures cannot change recovery's outcome.


class AuthStatusHolder:
    """Process-wide current header status; shells subscribe and render from it."""

    def __init__(self):
        self.status: AuthHeaderStatus = CHECKING
        self._listeners = []

    def subscribe(self, listener):
        """listener(status, notice_or_None); returns an unsubscribe callable."""
        self._listeners.append(listener)

        def unsubscribe():
            if listener in self._listeners:
                self._listeners.remove(listener)
        return unsubscribe

    def publish(self, status: AuthHeaderStatus, notice: str | None = None):
        self.status = status
        for listener in tuple(self._listeners):
            try:
                listener(status, notice)
            except Exception:
                # A closed client must not break the check or other shells.
                if listener in self._listeners:
                    self._listeners.remove(listener)
