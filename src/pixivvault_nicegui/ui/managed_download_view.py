"""Per-client managed Pixiv bookmark download selection."""

from __future__ import annotations

import asyncio
import re
from uuid import uuid4

from nicegui import ui

from ..bookmark_remote_adapter import RemoteBookmark
from .job_views import _accepted_call, _browser_event


def _supported(record: object) -> bool:
    return (isinstance(record, RemoteBookmark)
            and record.kind == "illustration"
            and type(record.illustration_type) is int
            and record.illustration_type in (0, 1))


class ManagedDownloadView:
    """Bookmark cache is read-only; only explicit selections enter the managed queue."""

    def __init__(self, services):
        self.services = services
        self.client = ui.context.client
        self.active = True
        self.closed = False
        self.busy = False
        self.refreshing = False
        self.looking_up = False
        self._render_pending = False
        self.epoch = 0
        self._owner = uuid4().hex  # Server-owned, never accepted from a browser payload.
        self.selection_revision = 0
        self._render_session = object()
        self._pending_enqueues: set[asyncio.Task] = set()
        self._owner_released = False
        self.query = ""
        self.shown = 50
        self.selected: dict[str, RemoteBookmark] = {}
        # Explicit-ID candidates belong to this client, not the shared
        # bookmark listing seen by another browser tab or client.
        self._direct_records: dict[str, RemoteBookmark] = {}
        self._operations: dict[str, str] = {}
        self.records: dict[str, RemoteBookmark] = {}
        self._choices = {}
        self._choice_serials = {}
        self._choice_generations = {}
        self._load_cached()
        with ui.column().classes("pv-jobs-view w-full").props(
            "data-testid=managed-download-view data-active=true data-epoch=0 data-selection-revision=0"
        ) as self.root:
            with ui.row().classes("pv-toolbar"):
                ui.label("Pixivブックマークからダウンロード").classes("pv-library-heading")
                self.search_input = ui.input(placeholder="作品名・作者・IDで検索").props(
                    "outlined dense clearable dark debounce=200 data-testid=managed-download-search"
                ).classes("pv-search")
                self.search_input.on("update:model-value", self._search_event,
                    js_handler=f"value => emit({{value,epoch:document.getElementById('{self.root.html_id}').dataset.epoch}})")
                self.counter = ui.label().classes("pv-counter").props("data-testid=managed-download-count")
            self.refresh_button = ui.button("ブックマーク更新", color=None).props(
                "flat no-caps data-testid=managed-download-refresh")
            self.refresh_button.on("click", self._refresh_event,
                js_handler=f"() => emit({{epoch:document.getElementById('{self.root.html_id}').dataset.epoch}})")
            with ui.row().classes("w-full items-center"):
                self.direct_input = ui.input(placeholder="単一作品ID（数字）").props(
                    "outlined dense dark data-testid=managed-download-direct-id").classes("pv-search")
                self.direct_button = ui.button("IDから作品を確認", color=None).props(
                    "flat no-caps data-testid=managed-download-direct-lookup")
                self.direct_button.on("click", self._lookup_event,
                    js_handler=f"() => emit({{epoch:document.getElementById('{self.root.html_id}').dataset.epoch}})")
            ui.label("ブックマーク以外の作品もIDで確認できます。確認だけでは保存・登録しません。").classes("pv-subtitle")
            ui.label("ブックマーク取得はGETのみ。元DBは変更せず、保存先は設定済みの専用フォルダーです。").classes("pv-subtitle")
            self.notice = ui.label().props("data-testid=managed-download-notice role=status")
            with ui.row().classes("pv-download-body w-full"):
                self.list = ui.column().classes("pv-jobs-scroll pv-download-candidates")
                with ui.column().classes("pv-download-targets"):
                    self.selection_count = ui.label().props("data-testid=managed-download-selection")
                    self.targets = ui.column().classes("w-full gap-1")
                    self.submit_button = ui.button("キューへ追加", color=None).props(
                        "flat no-caps data-testid=managed-download-submit")
                    self.submit_button.on("click", self._submit_event,
                        js_handler=f"() => {{const d=document.getElementById('{self.root.html_id}').dataset;emit({{epoch:d.epoch,selectionRevision:d.selectionRevision}})}}")
        self.client.on_delete(self.close)
        self.render()

    def _load_cached(self):
        try:
            bookmark_candidates = getattr(self.services, "bookmark_download_candidates", None)
            candidates = (bookmark_candidates() if callable(bookmark_candidates)
                          else self.services.download_candidates())
        except Exception:
            return False
        self.records = {r.work_id: r for r in candidates if _supported(r)}
        for wid, record in self._direct_records.items():
            # An existing bookmark keeps its own visibility/metadata rather
            # than being silently rewritten as a synthetic direct "show" DTO.
            self.records.setdefault(wid, record)
        return True

    def _valid(self, epoch=None):
        return (self.active and not self.closed
                and (epoch is None or str(epoch) == str(self.epoch)))

    def _bump_selection(self):
        self.selection_revision += 1
        if not self.closed:
            self.root.props["data-selection-revision"] = str(self.selection_revision)
            self.root.update()

    def _search_event(self, event):
        if self._valid() and _browser_event(event, epoch=self.epoch):
            self.search(event.args.get("value"))

    def search(self, value):
        if not self._valid():
            return
        self.query = str(value or "").strip().casefold()
        self.shown = 50
        self.render()

    def _invalidate_choice(self, work_id):
        generation = self._choice_generations.get(work_id, 0) + 1
        self._choice_generations[work_id] = generation
        choice = self._choices.get(work_id)
        if choice is not None:
            choice.props["data-row-generation"] = str(generation)
            choice.update()

    def select(self, work_id, checked, epoch=None, *, from_checkbox=False):
        if not self._valid(epoch) or self.busy:
            return
        if checked:
            record = self.records.get(work_id)
            if record is None or not _supported(record):
                return
            if work_id in self.selected:
                return
            self.selected[work_id] = record
        else:
            if work_id not in self.selected:
                return
            del self.selected[work_id]
            self._operations.pop(work_id, None)
        if not from_checkbox:
            # Target-chip removal and other server-side updates invalidate
            # already emitted checkbox events for this same row.
            self._invalidate_choice(work_id)
        self._bump_selection()
        self._render_targets()
        # An old optimistic checkbox update can arrive after a newer selection
        # event, so always converge visible inputs to the accepted server state.
        self._sync_choices()

    def _sync_choices(self):
        for work_id, choice in self._choices.items():
            choice.set_value(work_id in self.selected)

    def _select_event(self, event, work_id, session):
        if (session is self._render_session and self._valid()
                and isinstance(event.args, dict)
                and _browser_event(event, epoch=self.epoch)
                and type(event.args.get("value")) is bool):
            # The browser owns the monotonic sequence per rendered checkbox.
            # A global selection revision would drop a second fast click on a
            # *different* row. The session protects old renders and the
            # sequence rejects older events from the same checkbox.
            serial = event.args.get("eventSerial")
            if (type(serial) is str and serial.isascii() and serial.isdecimal()
                    and 0 < int(serial) <= 1000000000
                    and int(serial) > self._choice_serials.get(work_id, 0)
                    and event.args.get("rowGeneration") == str(
                        self._choice_generations.get(work_id, 0))
                    and work_id in self._choices):
                self._choice_serials[work_id] = int(serial)
                self.select(work_id, event.args["value"], event.args["epoch"],
                            from_checkbox=True)
            else:
                self._sync_choices()
        elif session is self._render_session and self._valid():
            self._sync_choices()

    def _more_event(self, event, session):
        if (session is self._render_session and self._valid()
                and _browser_event(event, epoch=self.epoch)):
            self.shown += 50
            self.render()

    def render(self):
        if self.closed:
            return
        self._render_session = object()
        self._choice_serials = {}
        self._choice_generations = {}
        matches = [r for r in self.records.values()
                   if self.query in f"{r.remote_id} {r.title} {r.author_name}".casefold()]
        self.list.clear()
        self._choices = {}
        with self.list:
            for record in matches[:self.shown]:
                with ui.row().classes("pv-download-item w-full items-center").props(
                    f"data-work-id={record.work_id} data-page-count={record.page_count} "
                    f"data-x-restrict={record.x_restrict} data-illustration-type={record.illustration_type}"):
                    choice = ui.checkbox(value=record.work_id in self.selected).props(
                        f"dense data-testid=managed-download-select aria-label={record.work_id}")
                    choice.props["data-event-serial"] = "0"
                    choice.props["data-row-generation"] = "0"
                    choice.on("update:model-value",
                        lambda e, wid=record.work_id, session=self._render_session:
                            self._select_event(e, wid, session),
                        js_handler=f"""value => {{const d=document.getElementById('{self.root.html_id}').dataset;
                            const c=document.getElementById('{choice.html_id}');if(!c)return;
                            const next=String(Number(c.dataset.eventSerial||'0')+1);
                            c.dataset.eventSerial=next;
                            emit({{value,epoch:d.epoch,eventSerial:next,
                                rowGeneration:c.dataset.rowGeneration,
                                selectionRevision:d.selectionRevision}})}}""")
                    choice.set_enabled(self.active and not self.busy)
                    self._choices[record.work_id] = choice
                    with ui.column().classes("gap-0 flex-1 min-w-0"):
                        ui.label(record.title).classes("pv-job-title")
                        ui.label(f"{record.author_name} · {record.remote_id}").classes("pv-subtitle")
                    ui.label(f"{record.page_count}頁").classes("pv-counter")
            if len(matches) > self.shown:
                button = ui.button("もっと見る", color=None).props(
                    "flat data-testid=managed-download-more")
                button.on("click", lambda e, session=self._render_session:
                    self._more_event(e, session),
                    js_handler=f"() => emit({{epoch:document.getElementById('{self.root.html_id}').dataset.epoch}})")
            if not matches:
                ui.label("条件に合う対応作品がありません").classes("pv-empty")
        self.counter.set_text(f"{min(len(matches), self.shown)} / {len(matches)} 件表示")
        if self.services.managed_downloads is None:
            self.notice.set_text("管理ダウンロードは未設定です。専用保存先を指定してください。")
        self._render_targets()

    def _render_targets(self):
        self.selection_count.set_text(f"対象 {len(self.selected)} 件")
        self.targets.clear()
        with self.targets:
            for record in self.selected.values():
                with ui.row().classes("w-full items-center"):
                    ui.label(record.title).classes("pv-job-title")
                    button = ui.button("解除", color=None).props("flat dense no-caps")
                    button.on("click", lambda e, wid=record.work_id: self._remove_target_event(e, wid),
                        js_handler=f"""() => {{const d=document.getElementById('{self.root.html_id}').dataset;
                            emit({{epoch:d.epoch,selectionRevision:d.selectionRevision}})}}""")
                    button.set_enabled(self.active and not self.busy)
        self.submit_button.set_enabled(bool(self.selected)
            and self.services.managed_downloads is not None
            and self.services.jobs is not None and self.active
            and not self.closed and not self.busy)

    def _remove_target_event(self, event, work_id):
        if (self._valid() and _browser_event(event, epoch=self.epoch,
                selection_revision=self.selection_revision)):
            self.select(work_id, False, event.args["epoch"])

    async def _refresh_event(self, event):
        if self._valid() and _browser_event(event, epoch=self.epoch):
            await self.refresh_bookmarks(event.args["epoch"])

    async def refresh_bookmarks(self, epoch=None):
        if not self._valid(epoch) or self.refreshing or self.busy:
            return False
        self.refreshing = True
        self.refresh_button.set_enabled(False)
        issued_epoch = self.epoch
        try:
            await self.services.refresh_bookmark_cache()
            if not self._valid(issued_epoch):
                return False
            if not self._load_cached():
                raise RuntimeError("cache unavailable")
            self.render()
            self.notice.set_text("ブックマークを更新しました")
            return True
        except Exception:
            if self._valid(issued_epoch):
                self.notice.set_text("更新に失敗しました。以前の一覧を保持しています")
            return False
        finally:
            self.refreshing = False
            if not self.closed:
                self.refresh_button.set_enabled(self.active)

    async def _lookup_event(self, event):
        if self._valid() and _browser_event(event, epoch=self.epoch):
            await self.lookup_work_id(event.args["epoch"])

    async def lookup_work_id(self, epoch=None):
        """GET metadata off-loop; never let late responses replace another view."""
        if (not self._valid(epoch) or self.looking_up or self.refreshing or self.busy
                or self.services.managed_downloads is None):
            return False
        raw = self.direct_input.value
        work_id = str(raw) if type(raw) is str else ""
        if not re.fullmatch(r"[1-9][0-9]{0,19}", work_id):
            self.notice.set_text("作品IDは先頭0のない数字（最大20桁）で指定してください")
            return False
        self.looking_up = True
        issued_epoch, original_query = self.epoch, self.query
        self.direct_button.set_enabled(False)
        try:
            record = await self.services.lookup_managed_work_id(
                work_id, owner=self._owner, publish=False)
            if (not self._valid(issued_epoch) or self.query != original_query
                    or str(self.direct_input.value) != work_id):
                return False
            # Publish authorization only after this view accepts the async
            # result. Invalidated lookups must not leak into another client.
            self.services.remember_managed_work_id(self._owner, record)
            self._direct_records[work_id] = record
            if not self._load_cached() or work_id not in self.records:
                raise RuntimeError("lookup record unavailable")
            self.query = work_id
            self.shown = 50
            self.search_input.set_value(work_id)
            self.render()
            self.notice.set_text("作品を確認しました。チェックを付けてキューへ追加してください")
            return True
        except Exception:
            if (self._valid(issued_epoch) and self.query == original_query
                    and str(self.direct_input.value) == work_id):
                self.notice.set_text("作品情報を取得できませんでした。ID・対応形式・認証を確認してください")
            return False
        finally:
            self.looking_up = False
            if not self.closed:
                self.direct_button.set_enabled(self.active)

    async def _submit_event(self, event):
        if (self._valid() and _browser_event(event, epoch=self.epoch,
                                           selection_revision=self.selection_revision)):
            await self.submit(event.args["epoch"])

    def _enqueue_owned(self, record, operation):
        # The owner capability is captured by the server-side view, never
        # supplied by the browser event or inferred from the artwork ID.
        return self.services.enqueue_managed_download(record, operation, owner=self._owner)

    def _release_owner_if_done(self):
        if (self.closed and not self.busy and not self._pending_enqueues
                and not self._owner_released):
            self._owner_released = True
            if hasattr(self.services, "release_managed_owner"):
                self.services.release_managed_owner(self._owner)

    def _accepted_finished(self, task):
        self._pending_enqueues.discard(task)
        if not task.cancelled():
            task.exception()  # Observe a rejected operation after client disconnect.
        self._release_owner_if_done()

    async def _enqueue_accepted(self, record, operation):
        # The view may be deleted while a queued executor task has not yet
        # acquired the service lock. Keep its owner capability until that task
        # finishes rather than turning an accepted click into a false reject.
        task = asyncio.create_task(_accepted_call(self._enqueue_owned, record, operation))
        self._pending_enqueues.add(task)
        task.add_done_callback(self._accepted_finished)
        return await asyncio.shield(task)

    async def submit(self, epoch=None):
        if (not self._valid(epoch) or self.busy or not self.selected
                or self.services.managed_downloads is None or self.services.jobs is None):
            return False
        self.busy = True
        issued_epoch = self.epoch
        self._render_targets()
        accepted = []
        failed = False
        try:
            for work_id, record in tuple(self.selected.items()):
                operation = self._operations.setdefault(work_id, uuid4().hex)
                try:
                    await self._enqueue_accepted(record, operation)
                except Exception:
                    failed = True
                    break
                accepted.append(work_id)
                self._operations.pop(work_id, None)
        finally:
            for work_id in accepted:
                self.selected.pop(work_id, None)
            if accepted:
                self._bump_selection()
            self.busy = False
            self._release_owner_if_done()
            if not self.closed:
                if self.active:
                    self.render()
                else:
                    self._render_pending = True
                if self._valid(issued_epoch):
                    self.notice.set_text(f"{len(accepted)}件をキューへ追加しました"
                        + ("。残りは追加できませんでした" if failed else ""))
        return not failed

    def set_active(self, active):
        if self.closed:
            return
        if self.active != bool(active):
            self.epoch += 1
        self.active = bool(active)
        self.root.props["data-active"] = str(self.active).lower()
        self.root.props["data-epoch"] = str(self.epoch)
        self.root.update()
        if self.active and self._render_pending:
            self._render_pending = False
            self.render()
        self._render_targets()
        self.refresh_button.set_enabled(self.active and not self.refreshing)
        self.direct_button.set_enabled(self.active and not self.looking_up)
        for choice in self._choices.values():
            choice.set_enabled(self.active and not self.busy)
        self._sync_choices()

    def close(self):
        if not self.closed:
            self.closed = True
            self.active = False
            self.epoch += 1
            self._release_owner_if_done()
