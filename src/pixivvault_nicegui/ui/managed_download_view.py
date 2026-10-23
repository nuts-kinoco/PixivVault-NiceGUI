"""Per-client managed Pixiv bookmark download selection."""

from __future__ import annotations

import asyncio
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
        self._render_pending = False
        self.epoch = 0
        self.selection_revision = 0
        self._render_session = object()
        self.query = ""
        self.shown = 50
        self.selected: dict[str, RemoteBookmark] = {}
        self._operations: dict[str, str] = {}
        self.records: dict[str, RemoteBookmark] = {}
        self._choices = {}
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
            candidates = self.services.download_candidates()
        except Exception:
            return False
        self.records = {r.work_id: r for r in candidates if _supported(r)}
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

    def select(self, work_id, checked, epoch=None):
        if not self._valid(epoch) or self.busy:
            return
        record = self.records.get(work_id)
        if record is None or not _supported(record):
            return
        if checked:
            if work_id in self.selected:
                return
            self.selected[work_id] = record
        else:
            if work_id not in self.selected:
                return
            del self.selected[work_id]
            self._operations.pop(work_id, None)
        self._bump_selection()
        self._render_targets()

    def _select_event(self, event, work_id, session):
        if (session is self._render_session and self._valid()
                and isinstance(event.args, dict)
                and _browser_event(event, epoch=self.epoch,
                                   selection_revision=self.selection_revision)
                and type(event.args.get("value")) is bool):
            self.select(work_id, event.args["value"], event.args["epoch"])

    def _more_event(self, event, session):
        if (session is self._render_session and self._valid()
                and _browser_event(event, epoch=self.epoch)):
            self.shown += 50
            self.render()

    def render(self):
        if self.closed:
            return
        self._render_session = object()
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
                    choice.on("update:model-value",
                        lambda e, wid=record.work_id, session=self._render_session:
                            self._select_event(e, wid, session),
                        js_handler=f"""value => {{const d=document.getElementById('{self.root.html_id}').dataset;
                            emit({{value,epoch:d.epoch,selectionRevision:d.selectionRevision}})}}""")
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
                ui.label(record.title).classes("pv-job-title")
        self.submit_button.set_enabled(bool(self.selected)
            and self.services.managed_downloads is not None
            and self.services.jobs is not None and self.active
            and not self.closed and not self.busy)

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

    async def _submit_event(self, event):
        if (self._valid() and _browser_event(event, epoch=self.epoch,
                                           selection_revision=self.selection_revision)):
            await self.submit(event.args["epoch"])

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
                    await _accepted_call(self.services.enqueue_managed_download, record, operation)
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
        for choice in self._choices.values():
            choice.set_enabled(self.active and not self.busy)

    def close(self):
        if not self.closed:
            self.closed = True
            self.active = False
            self.epoch += 1
