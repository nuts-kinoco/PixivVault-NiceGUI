"""Explicit S03 synchronization; mounting and observation read only the local DB."""
from __future__ import annotations

import asyncio
from datetime import datetime
import math
import re
from threading import Event
import time

from nicegui import background_tasks, ui

from ..following_sync import (FollowingSyncCancelled, FollowingSyncConflict,
    FollowingSyncError, FollowingSyncTimedOut)
from ..pixiv_read_client import ReadDeadlineExceeded, read_deadline_scope
from ..request_policy import request_scope


class _PinnedReader:
    def __init__(self, reader, account_id, checkpoint):
        self.reader, self.account_id, self.checkpoint = reader, account_id, checkpoint

    def require_identity(self):
        self.checkpoint()
        if self.reader.require_identity() != self.account_id:
            raise FollowingSyncError()
        return self.account_id

    def get_following_users_detailed(self, account_id, rest_type='show'):
        self.checkpoint()
        if account_id != self.account_id:
            raise FollowingSyncError()
        return self.reader.get_following_users_detailed(account_id, rest_type)


class FollowingSyncView:
    """Borrow services.following_sync/live_client; never close either service.

    Optional allow_get() and cached_identity() are pure, thread-safe callbacks.
    cached_identity must only return an already verified ID, without I/O.
    """
    PAGE_SIZE = 50

    def __init__(self, services, *, allow_get=None, cached_identity=None, timeout=120):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('invalid following sync timeout')
        self.services = services
        self.allow_get, self.cached_identity, self.timeout = allow_get, cached_identity, timeout
        self.client = ui.context.client
        self.snapshot = None
        self.account_id = None
        self.status = self.summary = self.synced_at = None
        self.sync_button = self.cancel_button = self.more_button = self.rows = None
        self._store = self._reader = None
        self._active = self._closed = self._busy = False
        self._epoch = self._read_generation = self._reading = 0
        self._limit = self.PAGE_SIZE
        self._rows_signature = None
        self._message = ''
        self._cancel_event = self._worker = self._initial_task = None
        self._timer = ui.timer(1.0, self.refresh, active=False)
        self.client.on_delete(self.close)

    def _cached_account(self, reader):
        if reader is None or getattr(reader, '_closed', False):
            return None
        try:
            value = (self.cached_identity() if self.cached_identity is not None
                     else getattr(reader, '_verified_identity', None))
        except Exception:
            return None
        return value if type(value) is str and re.fullmatch(r'[1-9][0-9]{0,19}', value) else None

    def _can_get(self, store=None, reader=None):
        store = getattr(self.services, 'following_sync', None) if store is None else store
        reader = getattr(self.services, 'live_client', None) if reader is None else reader
        try:
            return (not self._closed and not getattr(self.services, 'closing', False)
                and store is not None and not getattr(store, '_closed', False)
                and reader is not None and not getattr(reader, '_closed', False)
                and callable(getattr(reader, 'require_identity', None))
                and callable(getattr(reader, 'get_following_users_detailed', None))
                and (self.allow_get is None or bool(self.allow_get())))
        except Exception:
            return False

    def _adopt_context(self):
        store = getattr(self.services, 'following_sync', None)
        reader = getattr(self.services, 'live_client', None)
        account = self._cached_account(reader)
        if store is not self._store or reader is not self._reader or account != self.account_id:
            changed = (store is not self._store or reader is not self._reader
                       or self.account_id is not None)
            if changed and self._cancel_event is not None:
                self._cancel_event.set()
            self._store, self._reader, self.account_id = store, reader, account
            self.snapshot = None
            self._limit = self.PAGE_SIZE
            self._read_generation += 1
            if not self._busy:
                self._message = ''
        return store, reader, account

    def _current(self, epoch, store, reader, account=None):
        return (self._active and not self._closed and self._epoch == epoch
            and getattr(self.services, 'following_sync', None) is store
            and getattr(self.services, 'live_client', None) is reader
            and (account is None or self._cached_account(reader) == account))

    def build(self):
        if self._closed:
            return
        self.deactivate()
        self._active = True
        epoch = self._epoch
        self._adopt_context()
        self._message = '前の同期の停止を待っています…' if self._busy else ''
        self._rows_signature = object()

        async def synchronize():
            if self._active and self._epoch == epoch:
                await self.sync_now()

        def cancel():
            if self._active and self._epoch == epoch:
                self.cancel_sync()

        def more():
            if self._active and self._epoch == epoch:
                self._limit += self.PAGE_SIZE
                self._render()

        with ui.column().classes('w-full gap-2').props('data-testid=following-sync-view') as self.root:
            ui.label('フォロー中リストの同期').classes('text-subtitle1')
            ui.label('ログイン中のアカウントの公開・非公開フォローを取得して保存します。').classes('text-caption')
            self.summary = ui.label('').props('data-testid=following-sync-summary')
            self.synced_at = ui.label('').props('data-testid=following-sync-time')
            self.status = ui.label('').props('role=status data-testid=following-sync-status')
            with ui.row():
                self.sync_button = ui.button('Pixivから同期', on_click=synchronize).props('data-testid=following-sync-button')
                self.cancel_button = ui.button('同期を取消', on_click=cancel).props('data-testid=following-sync-cancel')
            self.rows = ui.column().classes('w-full gap-1').props('data-testid=following-sync-rows')
            self.more_button = ui.button('もっと見る', on_click=more).props('data-testid=following-sync-more')
        self._render()
        self._timer.active = True
        self._initial_task = background_tasks.create(self.refresh(), name='following-local-snapshot')

    def _render(self):
        if not self._active or self._closed or self.status is None:
            return
        snapshot = self.snapshot
        authors = snapshot.following if snapshot is not None else ()
        if self._store is None:
            summary = '同期の保存先が未設定です。起動と保存先の設定を保存し、再起動してください。'
        elif self._reader is None or getattr(self._reader, '_closed', False):
            summary = 'Pixivへの接続が未設定です。Cookieの設定を保存し、再起動してください。'
        elif self.account_id is None:
            summary = 'まだ本人確認をしていません。「Pixivから同期」で確認して取得します。'
        elif snapshot is None:
            summary = '保存済みの一覧を確認しています…'
        elif snapshot.last_synced_at is None:
            summary = 'まだ同期していません。'
            if authors:
                summary += f' 保存済みの作者は{len(authors)}人です。'
        else:
            summary = f'同期済みの作者: {len(authors)}人'
        self.summary.set_text(summary)
        if snapshot is not None and snapshot.last_synced_at is not None:
            value = datetime.fromisoformat(snapshot.last_synced_at).astimezone().strftime('%Y/%m/%d %H:%M:%S')
            self.synced_at.set_text('最終同期: ' + value)
        else:
            self.synced_at.set_text('最終同期: 未同期' if snapshot is not None else '最終同期: 未確認')
        self.status.set_text(self._message)
        self.sync_button.set_enabled(not self._busy and self._can_get())
        self.cancel_button.set_enabled(self._busy and self._cancel_event is not None
                                       and not self._cancel_event.is_set())
        key = None if snapshot is None else (snapshot.account_id, snapshot.revision, self._limit)
        if key != self._rows_signature:
            self.rows.clear()
            with self.rows:
                for author in authors[:self._limit]:
                    with ui.row().classes('w-full items-center gap-2').props('data-testid=following-sync-row'):
                        ui.label(author.name or author.account or '名前未取得').classes('break-all')
                        ui.label('非公開' if author.restrict else '公開').classes('text-caption')
            self._rows_signature = key
        self.more_button.set_visibility(len(authors) > self._limit)

    async def refresh(self, *, _during_sync=False):
        """Observe only the dedicated local DB; never verify identity or issue GET."""
        if not self._active or self._closed:
            return
        store, reader, account = self._adopt_context()
        self._render()
        if ((self._busy or self._reading) and not _during_sync) or store is None or account is None:
            return
        epoch = self._epoch
        self._read_generation += 1
        generation = self._read_generation
        self._reading += 1
        try:
            result = await asyncio.to_thread(store.snapshot, account)
            if self._current(epoch, store, reader, account) and generation == self._read_generation:
                self.snapshot = result
                self._render()
        except Exception:
            if self._current(epoch, store, reader, account) and generation == self._read_generation:
                self._message = '保存済みの一覧を読み込めませんでした。'
                self._render()
        finally:
            self._reading -= 1

    def _run_sync(self, store, reader, account, revision, cancelled):
        deadline = time.monotonic() + self.timeout

        def checkpoint():
            if cancelled():
                raise FollowingSyncCancelled()
            if time.monotonic() >= deadline:
                raise FollowingSyncTimedOut()

        try:
            with request_scope(checkpoint=checkpoint, allow_retry=False), read_deadline_scope(deadline):
                checkpoint()
                actual = reader.require_identity()
                checkpoint()
                if type(actual) is not str or re.fullmatch(r'[1-9][0-9]{0,19}', actual) is None:
                    raise FollowingSyncError()
                if account is not None and actual != account:
                    raise FollowingSyncCancelled()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise FollowingSyncTimedOut()
                return store.sync(_PinnedReader(reader, actual, checkpoint),
                    expected_revision=revision if actual == account else None,
                    cancelled=cancelled, timeout=remaining)
        except ReadDeadlineExceeded:
            raise FollowingSyncTimedOut() from None

    def cancel_sync(self):
        if self._active and self._busy and self._cancel_event is not None:
            self._cancel_event.set()
            self._message = '同期を取り消しています…'
            self._render()

    def _finish_worker(self, task, abandoned=False):
        if not task.cancelled():
            task.exception()
        if self._worker is not task:
            return
        self._worker = self._cancel_event = None
        self._busy = False
        if self._active and not self._closed:
            with self.client:
                if self._message == '前の同期の停止を待っています…':
                    self._message = ''
                if abandoned:
                    self._message = '処理は終了しました。保存済みの一覧を確認しています…'
                    self._initial_task = background_tasks.create(self.refresh(), name='following-local-after-cancel')
                self._render()

    async def sync_now(self):
        if not self._active or self._closed or self._busy:
            return False
        store, reader, account = self._adopt_context()
        if not self._can_get(store, reader):
            self._message = 'この起動ではPixivから同期できません。接続と保存先の設定を確認してください。'
            self._render()
            return False
        epoch = self._epoch
        revision = (self.snapshot.revision if self.snapshot is not None
                    and self.snapshot.account_id == account else None)
        event = Event()
        self._cancel_event = event
        self._busy = True
        self._read_generation += 1
        self._message = 'Pixivから同期中…'
        self._render()

        def cancelled():
            return (event.is_set() or not self._current(epoch, store, reader)
                or not self._can_get(store, reader)
                or (account is not None and self._cached_account(reader) != account))

        task = asyncio.create_task(asyncio.to_thread(self._run_sync, store, reader, account, revision, cancelled))
        self._worker = task
        abandoned = False
        message = None
        try:
            result = await asyncio.shield(task)
            if not self._current(epoch, store, reader, result.account_id):
                return False
            self._adopt_context()
            self.snapshot = result
            self._read_generation += 1
            prefix = '取消前に同期が完了しました' if event.is_set() else '同期完了'
            self._message = f'{prefix}（{len(result.following)}人の作者）'
            self._render()
            return True
        except asyncio.CancelledError:
            abandoned = True
            event.set()
            raise
        except FollowingSyncConflict:
            message = '別の画面で一覧が更新されました。この同期は保存していません。'
        except FollowingSyncCancelled:
            message = '同期を取消しました。保存済みの一覧と最終同期日時は保持されています。'
        except FollowingSyncTimedOut:
            message = '同期が時間内に完了しませんでした。保存済みの一覧と最終同期日時は保持されています。'
        except Exception:
            message = '同期できませんでした。保存済みの一覧と最終同期日時は保持されています。'
        finally:
            try:
                if message is not None and self._current(epoch, store, reader):
                    await self.refresh(_during_sync=True)
                    if self._current(epoch, store, reader):
                        self._message = message
                        self._render()
            finally:
                if task.done():
                    self._finish_worker(task, abandoned)
                else:
                    task.add_done_callback(lambda done: self._finish_worker(done, abandoned))
        return False

    def deactivate(self):
        self._epoch += 1
        self._read_generation += 1
        self._active = False
        self._timer.active = False
        if self._cancel_event is not None:
            self._cancel_event.set()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.deactivate()
        self._timer.cancel()
