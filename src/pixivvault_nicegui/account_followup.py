"""After a confirmed Cookie: account-switch confirmation and the 24 hour auto sync.

Restores the original gui.py check_login_status tail (6274-6330) and
trigger_auto_sync / run_auto_sync_thread (6187-6209):

* A different account than the one last synced asks first. Nothing is deleted
  or fetched until the user confirms; Cancel only remembers the declined ID in
  memory (like the original ``_account_switch_declined_uid``).
* The same (or first) account syncs public and private following when more than
  24 hours have passed since the last sync (the sync timestamp is stored with
  the account's following data; an absent date means "sync").

Every request goes through a reader that the binding builds on the same
stop-gated path as the header authentication check, so the stop fence applies
exactly as for the manual "取得・更新". A bare binding has no such path and sends
nothing. Cookie values are never read here.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event

from .account_scope import AccountScope, ConfirmedFollowingSwitch, require_same_account
from .following_sync import FollowingSyncCancelled, FollowingSyncError

NOTICE_SWITCH_CONFIRMED = '別アカウント(ID: {uid})のCookieを読み込みました。旧フォロー中データを削除・再取得します。'
NOTICE_SWITCH_CANCELLED = '別アカウントのCookieを検出しましたが、フォロー一覧の削除をキャンセルしました。'
NOTICE_SYNC_DONE = '起動時自動同期完了 ({count}人の作者)'
NOTICE_SYNC_FAILED = '自動同期失敗: 保存済みの一覧は保持されています。'
NOTICE_SWITCH_NOT_STARTED = '別アカウントへの切り替えを始められませんでした（取得停止中など）。旧フォロー中データは削除していません。'
NOTICE_SWITCH_FETCH_FAILED = '自動同期失敗: 旧フォロー中データは削除済みです。取得できるようになってから、もう一度同期してください。'
NOTICE_SYNC_RUNNING = '同期中です。完了してからもう一度お試しください。'
SYNC_INTERVAL_SECONDS = 24 * 3600


@dataclass(frozen=True)
class AccountSwitchRequest:
    new_account: str
    old_account: str


class _PinnedReader:
    """Abort the run if the signed-in account changes while it is running."""

    def __init__(self, reader, account):
        self.reader, self.account = reader, account

    def require_identity(self):
        value = self.reader.require_identity()
        if str(value) != self.account:
            raise FollowingSyncCancelled()
        return self.account

    def get_following_users_detailed(self, account_id, rest_type='show'):
        return self.reader.get_following_users_detailed(account_id, rest_type)


def sync_is_due(last_synced_at, *, now=None):
    """Original rule: strictly more than 24 h; an absent/unreadable date syncs."""
    if not last_synced_at:
        return True
    try:
        stamp = datetime.fromisoformat(last_synced_at)
        current = datetime.now(stamp.tzinfo) if now is None else now
        return (current - stamp).total_seconds() > SYNC_INTERVAL_SECONDS
    except Exception:
        return True


class AccountFollowup:
    def __init__(self, binding):
        self.binding = binding
        self.store_provider = None       # callable -> FollowingSyncStore | None
        self.reader_factory = None       # test/override: callable(cookie_file) -> reader
        self.admission_provider = None   # callable -> shared request pacing (RequestAdmission)
        self.on_following_ready = None   # (account, store) -> whether the timer was released
        # The shared GET fence admits one request at a time. Another request in
        # flight (for instance the first ranking fetch) surfaces as a failed
        # attempt; the transactional sync is simply tried again a little later.
        self.busy_retry_delay = 10.0
        self.busy_retries = 2
        self.pending = None
        self._declined = None
        self._listeners = []
        self._running = False

    # -- events (shell subscribes; payloads never carry credentials) --------

    def subscribe(self, listener):
        self._listeners.append(listener)

        def unsubscribe():
            if listener in self._listeners:
                self._listeners.remove(listener)
        return unsubscribe

    def _emit(self, kind, payload=None):
        for listener in tuple(self._listeners):
            try:
                listener(kind, payload or {})
            except Exception:
                if listener in self._listeners:
                    self._listeners.remove(listener)

    def _notice(self, text, level='info'):
        self._emit('notice', {'text': text, 'type': level})

    def _store(self):
        provider = self.store_provider
        try:
            return provider() if provider is not None else None
        except Exception:
            return None

    # -- entry point ---------------------------------------------------------

    async def after_confirmed(self, account, *, sync=True, reader_ready=False):
        """Called with the verified account ID after a startup/saved/received check."""
        if self.binding._closed:
            return
        store = self._store()
        if store is None:
            return
        try:
            old = await asyncio.to_thread(store.last_synced_account)
            if old is not None and old != account:
                if self._declined == account:
                    return
                if self.pending is not None and self.pending.new_account == account:
                    return
                self._close_dialog()
                self.pending = AccountSwitchRequest(account, old)
                self._emit('account_switch', {'new_account': account, 'old_account': old})
                return
            if not sync:
                if reader_ready and old == account:
                    await self._signal_following_ready(account, store)
                return
            snapshot = await asyncio.to_thread(store.snapshot, account)
        except Exception:
            return
        # Inline: the startup check task (which the first ranking fetch waits
        # for) finishes after this, so the two requests are not contending.
        if self._running:
            return
        if sync_is_due(snapshot.last_synced_at):
            self._running = True
            from .shared_events import emit_shared
            emit_shared(getattr(self, '_shared_event_services', None), '起動時フォローリスト自動同期を開始します...', level='info')
            await self._sync(account, store)
        await self._signal_following_ready(account, store)

    async def _signal_following_ready(self, account, store):
        callback = self.on_following_ready
        if callback is None or self.binding._closed:
            return
        if await asyncio.to_thread(callback, account, store):
            self.on_following_ready = None

    def _close_dialog(self):
        if self.pending is not None:
            self.pending = None
            self._emit('account_switch_closed')

    def cancel_account_switch(self):
        if self.pending is None:
            return
        self._declined = self.pending.new_account
        self._close_dialog()
        self._notice(NOTICE_SWITCH_CANCELLED)

    async def confirm_account_switch(self):
        request = self.pending
        store = self._store()
        if request is None or store is None or self.binding._closed:
            return
        if self._running:
            self._notice(NOTICE_SYNC_RUNNING, 'warning')   # the dialog stays open
            return
        self._running = True
        self._close_dialog()
        # 元と同じく「削除してから取得」。ただし取得停止ゲートが閉じているときは、
        # 先に読み取り手段を開いて確かめ、何も削除せずに終える（新旧とも 0 件にしない）。
        reader = await self._open_gated_reader()
        if reader is None:
            self._running = False
            self._notice(NOTICE_SWITCH_NOT_STARTED, 'warning')
            return
        def clear_confirmed_account():
            require_same_account(request.new_account, reader.require_identity())
            scope = ConfirmedFollowingSwitch(request.old_account, AccountScope(request.new_account, reader))
            store.clear_account(request.old_account, scope=scope)

        try:
            await asyncio.to_thread(clear_confirmed_account)
        except Exception:
            self._running = False
            self._close_reader(reader)
            self._notice(NOTICE_SWITCH_NOT_STARTED, 'warning')
            return
        self._notice(NOTICE_SWITCH_CONFIRMED.format(uid=request.new_account), 'warning')
        await self._sync(request.new_account, store, failed_notice=NOTICE_SWITCH_FETCH_FAILED,
                         first_reader=reader)
        await self._signal_following_ready(request.new_account, store)

    # -- sync ------------------------------------------------------------------

    async def _open_gated_reader(self):
        """The stop-gated reader, or None when the gate is closed. No request is sent."""
        cookie = self.binding._configured_cookie()
        if cookie is None or self.binding._closed:
            return None
        try:
            return await asyncio.to_thread(self._open_reader, cookie)
        except Exception:
            return None

    @staticmethod
    def _close_reader(reader):
        try:
            reader.close()
        except Exception:
            pass

    async def _sync(self, account, store, failed_notice=NOTICE_SYNC_FAILED, first_reader=None):
        """``_running`` is already set by the caller; always cleared here."""
        from .managed_network_stop_gate import ManagedNetworkGateBusy
        try:
            for attempt in range(self.busy_retries + 1):
                try:
                    reader, first_reader = first_reader, None
                    count = await self._attempt(account, store, reader)
                except (ManagedNetworkGateBusy, FollowingSyncError) as error:
                    transient = (isinstance(error, ManagedNetworkGateBusy)
                                 or type(error) is FollowingSyncError)
                    if transient and attempt < self.busy_retries and not self.binding._closed:
                        await asyncio.sleep(self.busy_retry_delay)
                        continue
                    raise
                if count is not None:
                    from .shared_events import emit_shared
                    emit_shared(getattr(self, '_shared_event_services', None), NOTICE_SYNC_DONE.format(count=count), level='success')
                    self._notice(NOTICE_SYNC_DONE.format(count=count), 'positive')
                elif not self.binding._closed:
                    from .shared_events import emit_shared
                    emit_shared(getattr(self, '_shared_event_services', None), 'フォローリスト自動同期を開始できませんでした。')
                return
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if not self.binding._closed:
                from .managed_network_stop_gate import ManagedNetworkStopped, ManagedNetworkGateStorageError
                from .shared_events import emit_shared
                if isinstance(error, (ManagedNetworkStopped, ManagedNetworkGateStorageError, FollowingSyncCancelled)):
                    emit_shared(getattr(self, '_shared_event_services', None), 'フォローリスト自動同期は停止しました。')
                else:
                    emit_shared(getattr(self, '_shared_event_services', None), 'フォローリスト自動同期に失敗しました。', level='error')
                self._notice(failed_notice, 'negative')
        finally:
            self._running = False

    async def _attempt(self, account, store, reader=None):
        """One gated run; the saved list changes only when the whole fetch succeeded."""
        cookie = self.binding._configured_cookie()
        if cookie is None or self.binding._closed:
            if reader is not None:
                self._close_reader(reader)
            return None
        key = Event()
        try:
            if reader is None:
                reader = await asyncio.to_thread(self._open_reader, cookie)
            if reader is None:
                return None  # no stop-gated path here: no request is made
            with self.binding._reader_lock:
                self.binding._requests[key] = reader
            result = await asyncio.to_thread(
                store.sync, _PinnedReader(reader, account),
                cancelled=lambda: self.binding._closed, timeout=120)
            return len(result.following)
        finally:
            with self.binding._reader_lock:
                self.binding._requests.pop(key, None)
            if reader is not None:
                try:
                    reader.close()
                except Exception:
                    pass

    def _open_reader(self, cookie_file):
        """A per-run reader on the same stop-gated path as the header check."""
        binding = self.binding
        if self.reader_factory is not None:
            return self.reader_factory(cookie_file)
        if not binding.auth_stop_required:
            return None
        scope = binding.managed_network_stop_scope
        if scope is None:
            return None
        from .managed_network_stop_profile_scope import _require_native_stop_owner
        from .request_policy import request_gate_preflight

        shared = binding.shared_network_stop_scope
        with request_gate_preflight(binding.auth_retry_policy, (scope.gate, shared.gate if shared else None),
                                    checks_selection=binding.auth_shared_stop_required):
            _require_native_stop_owner(scope.root, scope.profile_identity)
            scope.require_allowed()
            if binding.auth_shared_stop_required:
                if shared is None:
                    return None
                shared.require_allowed()
                from .managed_network_stop_shared_transport import create_shared_profile_reader
                reader = create_shared_profile_reader(
                    scope, cookie_file, shared.gate, shared_owner_check=shared._check_owner)
            else:
                from .managed_network_stop_reader import create_exact_managed_read_client
                reader = create_exact_managed_read_client(cookie_file, scope.gate)
        reader.set_retry_policy(binding.auth_retry_policy)
        self._pace(reader)
        return reader

    def _pace(self, reader):
        """Same request pacing as the application's other readers (avoids a 429 stop)."""
        try:
            admission = self.admission_provider() if self.admission_provider is not None else None
        except Exception:
            admission = None
        configure = getattr(reader, 'set_request_admission', None)
        if admission is not None and callable(configure):
            configure(admission)

    def close(self):
        self.pending = None
