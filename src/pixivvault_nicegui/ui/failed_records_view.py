"""Legacy failed_queue inspection, separate from in-flight job errors.

Only explicit CSV export leaves this read-only screen. Retry/removal are kept
disabled until a reviewed mutation service can accept those intents.
"""
from __future__ import annotations

import asyncio
from uuid import uuid4

from nicegui import background_tasks, ui

from ..failed_records_fixtures import build_failed_records_fixture
from ..failed_records_state import CATEGORIES, MAX_RECORDS, PAGE_SIZE, FailedRecordsState, classify_reason, csv_bytes
from ..job_service import JobServiceError
from ..managed_download_requests import ManagedRequestError
from ..legacy_retry_errors import (DownloadQueueCapacityReached, LegacyRetryCancelled,
                                   LegacyRetryRowUnavailable)
from ..pixiv_read_client import HTTPStatusRejected, PixivReadError
from ..request_policy import request_scope
from ..legacy_retry_batch import retry_batch_scope, RETRY_CAPACITY_WAIT_SECONDS
from .job_views import _accepted_call


CSS = """
.pv-failure-records {flex:1 1 0;min-height:0;min-width:0;width:100%;overflow:auto;gap:12px;}
.pv-failure-heading {font-family:'Yu Mincho',serif;font-size:15px;font-weight:700;}
.pv-failure-toolbar {display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center;width:100%;}
.pv-failure-search {width:360px;max-width:100%;}
.pv-failure-chips {gap:8px;flex-wrap:wrap;}
.pv-failure-chip {border:1px solid #2a2b38;border-radius:8px;min-height:34px;font-size:12px;}
.pv-failure-chip[aria-pressed=true] {background:#0096fa !important;color:#fff !important;}
.pv-failure-rows {width:100%;gap:0;}
.pv-failure-row {width:100%;padding:4px 14px;border-bottom:1px solid #15161c;gap:2px;min-width:0;}
.pv-failure-row-top {width:100%;gap:8px;align-items:center;flex-wrap:wrap;}
.pv-failure-title {flex:1 1 240px;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12.5px;}
.pv-failure-reason {font-size:11px;color:#8a8a99;white-space:pre-wrap;overflow-wrap:anywhere;width:100%;}
.pv-failure-meta {font-size:11px;color:#e8e8ef;}
.pv-failure-empty {padding:24px 14px;white-space:pre-wrap;}
.pv-failure-pages {width:100%;justify-content:flex-end;align-items:center;gap:4px;}
"""


class FailedRecordsView:
    def __init__(self, services) -> None:
        self.services = services
        self.client = ui.context.client
        self.active = True
        self.closed = False
        self._epoch = uuid4().hex
        self._search_epoch = uuid4().hex
        self._read_revision = 0
        self._retry_busy = False
        self._retry_generation = 0
        self._retry_owner = uuid4().hex
        self._retry_ops = {}
        self._pending_accepts = set()
        self._retry_owner_released = False
        self._failure_snapshot = None
        self._remove_busy = False
        self._remove_token = None
        self._remove_dialog = None
        self._pending_removals = set()
        self._loaded = (services.data_mode == "synthetic"
                        and not getattr(services, 'failed_record_removal_available', False))
        initial = build_failed_records_fixture() if self._loaded else ()
        self.state = FailedRecordsState(initial)
        self._category_labels = {key: label for key, label, _ in CATEGORIES}
        ui.add_css(CSS)
        with ui.column().classes("pv-failure-records").props(
                "data-testid=failed-records-view data-active=true") as self.root:
            with ui.row().classes("items-center w-full"):
                ui.label("ダウンロード失敗キュー・保存品質異常リスト").classes("pv-failure-heading")
                self.reload_button = ui.button(icon="refresh", color=None).props(
                    "flat dense aria-label=リスト更新 data-testid=failed-reload")
                self.reload_button.tooltip("失敗記録を読み直す（DBは変更しません）")
            if getattr(services, 'failed_record_removal_available', False):
                subtitle = ('一覧の削除は専用台帳に保存します。元DB・画像・ZIPは変更しません。'
                            + (' 再試行は対応作品だけ管理キューへ明示再投入します。'
                               if getattr(services, 'legacy_failed_retry_available', False) else ''))
            else:
                subtitle = ("合成データ · 保存先・実アカウントへの変更なし" if self._loaded else
                    ("静的DBコピーは読取専用。対応作品だけ管理キューへ明示再投入し、元の失敗記録は消しません"
                     if getattr(services, 'legacy_failed_retry_available', False) else
                     "読取専用 · 再試行・記録削除は未接続です"))
            ui.label(subtitle).classes('pv-subtitle')
            with ui.element("div").classes("pv-failure-toolbar"):
                self.search_input = ui.input(placeholder="タイトル・IDで検索").props(
                    "outlined dense clearable maxlength=4096 data-testid=failed-search"
                ).classes("pv-failure-search")
                with ui.row().classes("pv-failure-chips"):
                    self.category_buttons = {}
                    for key, label in [("all", "すべて"), *self._category_labels.items()]:
                        button = ui.button(label, color=None).props(
                            f"flat dense no-caps data-testid=failed-category-{key}").classes("pv-failure-chip")
                        self.category_buttons[key] = button
                        self._wire(button, "click", lambda value=key: self.select_category(value))
                self.retry_all = ui.button("再試行", color=None).props(
                    "flat dense data-testid=failed-retry-all")
                self.retry_all.disable()
                self.retry_all.tooltip("ダウンロード再試行の書込み接続は未受入です")
                self.cancel_retry_button = ui.button('再投入を中断', color=None).props(
                    'flat dense data-testid=failed-retry-cancel')
                self.cancel_retry_button.set_visibility(False)
                with ui.button(icon="more_vert", color=None).props(
                        "flat dense aria-label=その他の操作 data-testid=failed-menu"):
                    with ui.menu():
                        self.export_item = ui.menu_item(auto_close=False).props(
                            "role=menuitem data-testid=failed-export")
                        with self.export_item:
                            self.export_label = ui.item_section("表示中の件数をCSV出力")
                        self.clear_all_item = ui.menu_item("失敗リストを全件クリア", auto_close=False).props(
                            "disable role=menuitem data-testid=failed-clear-all")
                        self.clear_all_item.tooltip("一覧から除外します。元DB・作品ファイルは変更しません")
            self.counter = ui.label().classes("pv-counter").props("data-testid=failed-count")
            self.status = ui.label().classes("pv-subtitle").props("role=status data-testid=failed-status")
            self.retry_notice = ui.label().classes('pv-subtitle').props(
                'role=status data-testid=failed-managed-retry-status')
            self.remove_notice = ui.label().classes('pv-subtitle').props(
                'role=status data-testid=failed-remove-status')
            self.alert = ui.label("失敗記録を読み込めませんでした。最後に取得できた一覧を保持しています。リスト更新で再試行できます。"
                                  ).classes("pv-viewer-error").props("role=alert data-testid=failed-error")
            self.empty = ui.label().classes("pv-failure-empty").props("data-testid=failed-empty")
            with ui.row().classes("w-full") as self.recovery:
                self.clear_search = ui.button("検索を消す", color=None).props("flat dense data-testid=failed-clear-search")
                self.clear_category = ui.button("すべて", color=None).props("flat dense data-testid=failed-clear-category")
            self.rows = ui.column().classes("pv-failure-rows").props("data-testid=failed-rows")
            with ui.row().classes("pv-failure-pages"):
                self.page_label = ui.label().classes("pv-counter").props("data-testid=failed-page")
                self.previous = ui.button(icon="chevron_left", color=None).props(
                    "flat dense aria-label=前のページ data-testid=failed-previous")
                self.next = ui.button(icon="chevron_right", color=None).props(
                    "flat dense aria-label=次のページ data-testid=failed-next")
        self._wire(self.reload_button, "click", self.request_refresh)
        self._wire(self.retry_all, 'click', self.request_retry)
        self._wire(self.cancel_retry_button, 'click', self.cancel_retry)
        self._wire(self.previous, "click", lambda: self.change_page(-1))
        self._wire(self.next, "click", lambda: self.change_page(1))
        self._wire(self.clear_search, "click", lambda: self.search(""))
        self._wire(self.clear_category, "click", lambda: self.select_category("all"))
        self._wire(self.export_item, "click", self.export)
        self._wire(self.clear_all_item, 'click', self.request_remove)
        self._wire(self.search_input, "update:model-value", self.search, value=True)
        self._invalidate()
        self.render()
        self.client.on_delete(self.close)

    def _allowed(self) -> bool:
        return self.active and not self.closed

    def _wire(self, element, event, callback, *, value=False) -> None:
        def receive(message):
            payload = message.args
            epoch = self._search_epoch if value else self._epoch
            if not self._allowed() or not isinstance(payload, dict) or payload.get("epoch") != epoch:
                if value and not self.closed:
                    self.search_input.set_value(self.state.query)
                return
            if value:
                if "value" not in payload:
                    return
                text = payload.get("value")
                if text is None:
                    text = ""
                if isinstance(text, str) and len(text) <= 4096:
                    callback(text)
            else:
                callback()
        # During construction self.root is not assigned yet inside its context;
        # current client-local wrapper is found from the triggering control.
        root_id = getattr(self, "root", None)
        if root_id is None:
            # Defer wiring category controls until the wrapper exists.
            self._pending_wires = getattr(self, "_pending_wires", [])
            self._pending_wires.append((element, event, callback, value))
            return
        element.on(event, receive, js_handler=f"""(value) => {{
            const root=document.getElementById('{self.root.html_id}');
            if(!root) return;
            emit({{epoch:root.dataset.{"failedSearchEpoch" if value else "failedEpoch"}{", value: value ?? ''" if value else ""}}});
        }}""")

    def _invalidate(self, *, search=False) -> None:
        self._epoch = uuid4().hex
        if search:
            self._search_epoch = uuid4().hex
        self.root.props["data-failed-epoch"] = self._epoch
        self.root.props["data-failed-search-epoch"] = self._search_epoch
        self.root.props["data-read-revision"] = str(self._read_revision)
        self.root.update()
        pending, self._pending_wires = getattr(self, "_pending_wires", []), []
        for element, event, callback, value in pending:
            self._wire(element, event, callback, value=value)

    def render(self) -> None:
        if self.closed:
            return
        state = self.state
        available = self.services.failed_records_available
        matched, counts = state.matched_records, state.counts
        pages = max(1, (len(matched) + PAGE_SIZE - 1) // PAGE_SIZE)
        visible = matched[state.page_index * PAGE_SIZE:(state.page_index + 1) * PAGE_SIZE]
        self.counter.set_text(f"{len(matched)} / {len(state.records)} 件" if self._loaded else "未取得")
        self.page_label.set_text(f"{state.page_index + 1} / {pages}" if self._loaded else "— / —")
        self.previous.set_enabled(self._loaded and state.page_index > 0)
        self.next.set_enabled(self._loaded and state.page_index + 1 < pages)
        self.reload_button.set_enabled(available and not state.loading)
        self.search_input.set_enabled(self._loaded)
        self.search_input.set_value(state.query)
        for key, button in self.category_buttons.items():
            button.set_text(f"{'すべて' if key == 'all' else self._category_labels[key]} {counts[key]}")
            button.props["aria-pressed"] = str(state.category == key).lower()
            button.set_enabled(self._loaded)
        self.retry_all.set_text(f"{len(matched)}件を再試行" if matched
                                else "再試行できるものがありません")
        opted_in = getattr(self.services, 'legacy_failed_retry_available', False)
        can_retry = (opted_in and self._loaded and not state.loading
                     and not state.error and not self._retry_busy
                     and not self._remove_busy and self._allowed())
        can_remove = (getattr(self.services, 'failed_record_removal_available', False)
                      and self._failure_snapshot is not None and self._loaded
                      and not state.loading and not state.error and not self._retry_busy
                      and not self._remove_busy and self._allowed())
        self.clear_all_item.set_enabled(can_remove and bool(state.records))
        eligible = tuple(item for item in matched if self._retry_supported(item))
        self.retry_all.set_enabled(can_retry and bool(eligible))
        self.cancel_retry_button.set_visibility(self._retry_busy)
        self.cancel_retry_button.set_enabled(self._retry_busy and self._allowed())
        if opted_in:
            self.retry_all.set_text(f'対応作品 {len(eligible)} 件を管理キューへ再投入'
                                    if eligible else '再試行できる対応作品がありません')
        self.export_label.set_text(f"表示中の{len(matched)}件をCSV出力")
        self.export_item.set_enabled(self._loaded and not state.loading and not state.error)
        self.status.set_text("読み込み中です" if state.loading else "")
        self.status.set_visibility(state.loading)
        self.alert.set_visibility(state.error)
        if not available:
            empty_text = "失敗記録のDBが指定されていません。実アカウントの失敗が0件であることを意味しません。"
        elif not self._loaded:
            empty_text = "失敗記録を読み込んでいません"
        elif not state.records:
            empty_text = "失敗はありません\n失敗した取得は0件です"
        else:
            empty_text = "一致する失敗はありません"
        self.empty.set_text(empty_text)
        self.empty.set_visibility(not visible and not state.loading)
        self.recovery.set_visibility(self._loaded and bool(state.records) and not matched)
        self.clear_search.set_visibility(bool(state.query))
        self.clear_category.set_visibility(state.category != "all")
        self.rows.clear()
        with self.rows:
            for record in visible:
                with ui.column().classes("pv-failure-row").props("data-testid=failed-record") as row:
                    row.props["data-work-id"] = record.work_id
                    with ui.row().classes("pv-failure-row-top"):
                        ui.label(f"{record.title or '無題'}（ID: {record.work_id}）").classes("pv-failure-title")
                        category = self._category_labels[classify_reason(record.error_reason)]
                        ui.label(f"{category} ・ リトライ回数: {record.retry_count}").classes("pv-failure-meta")
                        retry = ui.button("再試行", icon="refresh", color=None).props("flat dense data-testid=failed-row-retry")
                        if can_retry and self._retry_supported(record):
                            self._wire(retry, 'click', lambda row=record: self.request_retry((row,)))
                        else:
                            retry.disable()
                        remove = ui.button(icon="close", color=None).props("flat dense aria-label=キューから削除 data-testid=failed-row-delete")
                        if can_remove:
                            self._wire(remove, 'click', lambda row=record: self.request_remove((row,)))
                        else:
                            remove.disable()
                    ui.label(record.error_reason or "不明エラー").classes("pv-failure-reason").props("data-testid=failed-reason")

    @staticmethod
    def _retry_supported(item) -> bool:
        from ..services import ApplicationServices
        return ApplicationServices._supported_failed_original(item)

    def request_retry(self, rows=None) -> None:
        """Start the explicit managed retry; never delete or update old rows."""
        if (not self._allowed() or self._retry_busy or self._remove_busy or
                not getattr(self.services, 'legacy_failed_retry_available', False)
                or not self._loaded or self.state.loading or self.state.error):
            return
        if rows is None:
            # The original command captures the entire filtered set, including
            # pages beyond the first 50. The state already bounds it to 10k.
            rows = tuple(item for item in self.state.matched_records
                         if self._retry_supported(item))
        else:
            rows = tuple(rows)
        known = set(self.state.records)
        if (not rows or len(rows) > MAX_RECORDS or
                any(not self._retry_supported(row) or row not in known for row in rows)):
            return
        self._invalidate()  # Consume this browser epoch before the first await.
        background_tasks.create(self._retry_records(rows))

    def request_remove(self, rows=None):
        """Capture the unfiltered loaded snapshot before a confirmation dialog."""
        if (not self._allowed() or self._remove_busy or self._retry_busy
                or self.state.loading or self.state.error or not self._loaded
                or self._failure_snapshot is None
                or not getattr(self.services, 'failed_record_removal_available', False)):
            return
        selected = tuple(self.state.records if rows is None else rows)
        known = frozenset(self.state.records)
        if not selected or any(row not in known for row in selected):
            return
        self._invalidate()
        epoch, revision = self._epoch, self._read_revision
        token = self._remove_token = uuid4().hex
        self._remove_busy = True
        self.clear_all_item.menu.close()
        self.render()
        background_tasks.create(self._remove_records(
            self._failure_snapshot, selected, epoch, revision, token))

    async def _confirm_remove(self, count):
        with self.client:
            with ui.dialog() as dialog, ui.card().props('data-testid=failed-remove-dialog'):
                ui.label(f'{count} 件の失敗記録を一覧から削除しますか？').props(
                    'data-testid=failed-remove-confirm-count')
                ui.label('この一覧での削除は取り消せません。元のDB・画像・ZIPと受理済みジョブは変更しません。')
                with ui.row():
                    ui.button('キャンセル', on_click=lambda: dialog.submit(False)).props(
                        'flat data-testid=failed-remove-cancel')
                    ui.button('一覧から削除', on_click=lambda: dialog.submit(True)).props(
                        'flat data-testid=failed-remove-confirm')
            self._remove_dialog = dialog
        try:
            return await dialog is True
        finally:
            if self._remove_dialog is dialog:
                self._remove_dialog = None
            if not dialog.is_deleted:
                dialog.delete()

    async def _remove_records(self, snapshot, selected, epoch, revision, token):
        def current():
            return (self._allowed() and self._remove_token == token
                    and self._epoch == epoch and self._read_revision == revision
                    and self._failure_snapshot is snapshot)
        try:
            confirmed = await self._confirm_remove(len(selected))
            if not confirmed:
                if current():
                    self.remove_notice.set_text('削除をキャンセルしました。記録は変更していません。')
                return False
            if not current():
                if not self.closed:
                    self.remove_notice.set_text('一覧が変わったため削除を中止しました。再読み込みしてください。')
                return False
            task = asyncio.create_task(_accepted_call(
                self.services.dismiss_failed_records, snapshot, selected))
            self._pending_removals.add(task)
            def finished(done):
                self._pending_removals.discard(done)
                if not done.cancelled():
                    done.exception()
            task.add_done_callback(finished)
            await asyncio.shield(task)
            if not self.closed:
                self.remove_notice.set_text(f'{len(selected)} 件を一覧から削除しました。元DB・作品は変更していません。')
                if self.active:
                    self.request_refresh()
                else:
                    self._loaded = False
            return True
        except Exception:
            if not self.closed:
                self.remove_notice.set_text('失敗記録を削除できませんでした。一覧を再読み込みして確認してください。')
            return False
        finally:
            if self._remove_token == token:
                self._remove_token = None
                self._remove_busy = False
            if not self.closed:
                self.render()

    def _release_retry_owner(self) -> None:
        if (self.closed and not self._pending_accepts and not self._retry_busy
                and not self._retry_owner_released):
            self._retry_owner_released = True
            release = getattr(self.services, 'release_managed_owner', None)
            if callable(release):
                release(self._retry_owner)

    def _accept_finished(self, task):
        self._pending_accepts.discard(task)
        if not task.cancelled():
            task.exception()
        self._release_retry_owner()

    def _retry_operation(self, key):
        """Keep a pending request's idempotency key, rotate after terminal.

        The old failure stays visible because its source is read-only. A second
        explicit click after a completed/error/stopped job must start a *new*
        attempt, not silently return the former terminal JobSnapshot.
        """
        previous = self._retry_ops.get(key)
        if previous is not None:
            operation, job_id = previous
            if job_id is None:
                return operation  # DB accept may have failed after intent store.
            if (self.services.jobs is not None and
                    any(job.job_id == job_id and job.state in
                        ('queued', 'running', 'paused', 'stopping')
                        for job in self.services.jobs.snapshot())):
                return operation
        operation = uuid4().hex
        # Never evict a possibly uncommitted operation merely because a single
        # explicit filtered command contains more than 256 rows. Such an
        # eviction can turn an uncertain accepted job into a duplicate retry.
        # Each snapshot is already capped by FailedRecordsState.MAX_RECORDS.
        self._retry_ops[key] = (operation, None)
        return operation

    def cancel_retry(self):
        if self._allowed() and self._retry_busy:
            self._retry_generation += 1
            self.cancel_retry_button.disable()
            self.retry_notice.set_text('再投入の中断を要求しました。受理済みのジョブは保持します。')

    async def _retry_records(self, rows) -> bool:
        if not self._allowed() or self._retry_busy:
            return False
        self._retry_busy = True
        self._retry_generation += 1
        generation = self._retry_generation
        start_epoch, start_revision = self._epoch, self._read_revision
        accepted, failed = 0, 0
        total = len(rows)
        interrupted = False
        bulk = total > 1
        def current():
            return (not self.closed and self._retry_generation == generation
                    and (bulk or (self.active and self._epoch == start_epoch
                                  and self._read_revision == start_revision))
                    and not getattr(self.services, 'closing', False))
        def checkpoint():
            if not current():
                raise LegacyRetryCancelled('legacy retry cancelled')
        wait_capacity = getattr(self.services, 'wait_legacy_retry_capacity', None)
        scope = retry_batch_scope(self.services, rows, self._retry_owner, is_current=current)
        scope_entered = False
        self.render()
        try:
            await scope.__aenter__()
            scope_entered = True
            for index, row in enumerate(rows, 1):
                if not current():
                    interrupted = True
                    return False
                key, operation = None, None
                capacity_deadline = asyncio.get_running_loop().time() + RETRY_CAPACITY_WAIT_SECONDS
                try:
                    if callable(wait_capacity):
                        await wait_capacity(row, is_current=current, deadline=capacity_deadline)
                    checkpoint()
                    with request_scope(checkpoint=checkpoint):
                        candidate = await self.services.lookup_legacy_failed_original(
                            row, owner=self._retry_owner)
                    if not current():
                        interrupted = True
                        return False
                    key = (row.work_id, row.user_id, row.work_type, row.failed_at,
                           row.retry_count, row.title, row.error_reason)
                    operation = self._retry_operation(key)
                    # A client's disconnect cannot cancel a committed intent.
                    while True:
                        checkpoint()
                        task = asyncio.create_task(_accepted_call(
                            self.services.enqueue_legacy_failed_original,
                            row, candidate, operation, owner=self._retry_owner))
                        self._pending_accepts.add(task)
                        task.add_done_callback(self._accept_finished)
                        try:
                            accepted_job = await asyncio.shield(task)
                            break
                        except DownloadQueueCapacityReached:
                            # No job/intention was accepted by this exception.
                            # Keep the exact DTO and operation ID; never issue
                            # another metadata GET while waiting for a slot.
                            if (not callable(wait_capacity) or
                                    asyncio.get_running_loop().time() >= capacity_deadline):
                                raise
                            if current():
                                self.retry_notice.set_text(
                                    f'管理キューの空きを待っています · 受理 {accepted}/{total} 件')
                            await asyncio.sleep(0.2)
                            await wait_capacity(row, is_current=current, deadline=capacity_deadline)
                    self._retry_ops[key] = (operation, accepted_job.job_id)
                    accepted += 1
                except LegacyRetryRowUnavailable:
                    failed += 1
                except HTTPStatusRejected as exc:
                    failed += 1
                    if exc.status_code not in (404, 410):
                        interrupted = True
                        break
                except PixivReadError:
                    failed += 1
                    interrupted = True
                    break
                except (ManagedRequestError, JobServiceError) as exc:
                    failed += 1
                    # Only a known ledger collision invalidates an unaccepted
                    # operation token. Uncertain I/O keeps its idempotency key.
                    if (str(exc) in ('operation intent collision', 'operation already used')
                            and key is not None
                            and self._retry_ops.get(key) == (operation, None)):
                        self._retry_ops.pop(key, None)
                    else:
                        interrupted = True
                        break
                except Exception:
                    # Unknown source/storage/closing errors are systemic until
                    # proven otherwise. Never multiply them across 10k GETs.
                    failed += 1
                    interrupted = True
                    break
                if index % 16 == 0 and current():
                    self.retry_notice.set_text(
                        f'再投入中 {index}/{total} 件 · 受理 {accepted} 件 · 未追加 {failed} 件')
        except Exception:
            # Source admission failed before the first GET or commit.
            interrupted = True
        finally:
            if scope_entered:
                await scope.__aexit__(None, None, None)
            self._retry_busy = False
            self._release_retry_owner()
            if not self.closed:
                self.render()
                if bulk or (self._allowed() and start_epoch == self._epoch):
                    self.retry_notice.set_text(
                        f'{accepted} 件を管理キューへ追加しました。元失敗DBは変更していません'
                        + (f'。未追加 {total-accepted} 件' if accepted != total else '')
                        + ('。中断しました。接続・保存先・キュー状態を確認してください' if interrupted else ''))
        return accepted == total

    def search(self, query: str) -> None:
        if self._allowed() and self.state.set_query(query):
            self._invalidate()
            self.render()

    def select_category(self, category: str) -> None:
        if self._allowed() and self.state.select_category(category):
            self._invalidate()
            self.render()

    def change_page(self, delta: int) -> None:
        if self._allowed() and self.state.set_page(self.state.page_index + delta):
            self._invalidate()
            self.render()

    def export(self) -> None:
        if not self._allowed() or not self._loaded or self.state.loading or self.state.error:
            return
        try:
            content = csv_bytes(self.state.matched_records)
            with self.client:
                ui.download.content(content, "failed_queue_export.csv", "text/csv;charset=utf-8")
            self.export_item.menu.close()
            self.status.set_text(
                f"{len(self.state.matched_records)} 件のCSVダウンロードを開始しました"
                "（failed_queue_export.csv）。保存先はブラウザーの設定を確認してください")
            self.status.set_visibility(True)
        except Exception:
            self.status.set_text("CSVを出力できませんでした。条件を確認して再試行してください")
            self.status.set_visibility(True)

    def request_refresh(self) -> None:
        if not self._allowed() or not self.services.failed_records_available:
            return
        token = self.state.begin_refresh()
        if token is None:
            return
        self._invalidate(search=True)
        self.render()
        background_tasks.create(self._refresh(token))

    async def _refresh(self, token) -> None:
        try:
            snapshot = None
            if getattr(self.services, 'failed_record_removal_available', False):
                snapshot = await self.services.read_failed_records_view()
                records = snapshot.records
            else:
                records = await self.services.read_failed_records()
            accepted = self.state.complete_refresh(token, records, ok=True)
        except Exception:
            accepted = self.state.complete_refresh(token, (), ok=False)
        else:
            if accepted:
                self._loaded = True
                self._read_revision += 1
                self._failure_snapshot = snapshot
        if accepted and not self.closed:
            with self.client:
                self._invalidate()
                self.render()

    def set_active(self, active: bool) -> None:
        if self.closed:
            return
        if self.active != bool(active):
            self.active = bool(active)
            self.state.set_active(self.active)
            self._invalidate(search=True)
            if not self.active and self._remove_dialog is not None:
                self._remove_dialog.submit(False)
            # A tab roundtrip must not replace rows or reset their scroll DOM.
            # Only the pending refresh is invalidated by state.set_active().
            self.status.set_visibility(False)
            self.reload_button.set_enabled(self.services.failed_records_available)
            self.export_item.set_enabled(self._loaded and not self.state.error)
        self.root.props["data-active"] = str(self.active).lower()
        self.root.update()
        if self.active and not self._loaded and not self.state.loading:
            self.request_refresh()

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.active = False
            self._epoch = uuid4().hex
            self._search_epoch = uuid4().hex
            self.state.close()
            self._remove_token = None
            if self._remove_dialog is not None:
                self._remove_dialog.submit(False)
            self._release_retry_owner()
