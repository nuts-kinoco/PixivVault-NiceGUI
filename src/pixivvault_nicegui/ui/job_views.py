"""Per-client download/queue/failed views over application-owned jobs."""

from __future__ import annotations

import asyncio
import math
import re
from uuid import uuid4

from nicegui import ui

from ..job_service import UNFINISHED
from .download_status_details import DownloadStatusDetails


LABELS = {"queued": "待機中", "running": "実行中", "paused": "一時停止",
          "stopping": "停止中", "done": "完了", "error": "エラー", "stopped": "停止"}
ACTION_LABELS = {"pause": "一時停止", "resume": "再開", "stop": "停止",
                 "delete": "削除", "retry": "再試行"}
ACTION_STATES = {"pause": {"queued", "running"}, "resume": {"paused"},
                 "stop": {"queued", "running", "paused"}, "retry": {"error"},
                 "delete": set(LABELS)}


def _novel_identity(job):
    if job.kind.startswith("ugoira-"):
        return job.title or "うごイラ", "うごイラ保存ジョブ"
    if job.kind == "novel-export" and (match := re.fullmatch(r"([1-9][0-9]{0,19}):(TXT|EPUB|BOTH)(?::series:[a-f0-9]{16})?", job.target_id)):
        category = "小説シリーズ" if ":series:" in job.target_id else "小説"
        return f"{category} {match.group(1)}", f"{category} · {match.group(2)} · ID {match.group(1)}"
    return job.title or job.target_id, job.target_id

def _event_number(payload, key, expected):
    value = payload.get(key)
    return (type(value) is str and value.isascii() and value.isdecimal()
            and value == str(expected))


def _browser_event(event, *, epoch, selection_revision=None, view_generation=None):
    data = getattr(event, "args", None)
    if not isinstance(data, dict) or not _event_number(data, "epoch", epoch):
        return False
    if selection_revision is not None and not _event_number(data, "selectionRevision", selection_revision):
        return False
    if view_generation is not None and not _event_number(data, "viewGeneration", view_generation):
        return False
    return True


async def _accepted_call(function, *args, **kwargs):
    # Closing a client must not cancel a mutation that the app already accepted.
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
    return await asyncio.shield(task)


class DownloadView:
    def __init__(self, services):
        self.services = services
        self.client = ui.context.client
        self.active, self.closed, self.busy = True, False, False
        self.epoch = 0
        self.selection_revision = 0
        self._render_session = object()
        self.query = ""
        self.shown = 50
        self.selected = set()
        self._operations = {}
        self.artworks = {item.work_id: item for item in services.artworks}
        self._choices = {}
        with ui.column().classes("pv-jobs-view w-full").props("data-testid=download-view data-active=true") as self.root:
            with ui.row().classes("pv-toolbar"):
                ui.label("ダウンロード").classes("pv-library-heading")
                self.search_input = ui.input(placeholder="作品名・作者・IDで絞り込む").props(
                    "outlined dense clearable debounce=200 data-testid=download-search").classes("pv-search")
                self.search_input.on("update:model-value", self._search_event,
                    js_handler=f"value => emit({{value, epoch:document.getElementById('{self.root.html_id}').dataset.epoch}})")
                self.counter = ui.label().classes("pv-counter").props("data-testid=download-count")
            ui.label("移植検証用：架空画像の全ページを専用フォルダーへ保存します").classes("pv-subtitle")
            self.notice = ui.label().props("data-testid=download-notice role=status")
            self.details = DownloadStatusDetails(self, managed=False)
            with ui.row().classes("pv-download-body w-full"):
                self.list = ui.column().classes("pv-jobs-scroll pv-download-candidates").props("data-testid=download-candidates")
                with ui.column().classes("pv-download-targets"):
                    self.selection_count = ui.label().props("data-testid=download-selection")
                    self.targets = ui.column().classes("w-full gap-1")
                    self.submit_button = ui.button("キューへ追加", color=None).props(
                        "flat no-caps data-testid=download-submit")
                    self.submit_button.on("click", self._submit_event,
                        js_handler=f"() => {{const d=document.getElementById('{self.root.html_id}').dataset;emit({{epoch:d.epoch,selectionRevision:d.selectionRevision}})}}")
                    ui.label("保存先のファイルはキューの削除では消えません").classes("pv-subtitle")
        self.root.props["data-epoch"] = str(self.epoch)
        self.root.props["data-selection-revision"] = str(self.selection_revision)
        self.client.on_delete(self.close)
        self.render()

    def _valid(self, epoch=None):
        return (self.active and not self.closed and (epoch is None or str(epoch) == str(self.epoch)))

    def _search_event(self, event):
        if self._valid() and _browser_event(event, epoch=self.epoch):
            self.search(event.args.get("value"))

    def search(self, text):
        if not self._valid():
            return
        self.query = str(text or "").strip().casefold()
        self.shown = 50
        self.render()

    def select(self, work_id, selected, epoch=None):
        if not self._valid(epoch) or self.busy or work_id not in self.artworks:
            return
        if (work_id in self.selected) == bool(selected):
            return
        if selected:
            self.selected.add(work_id)
        else:
            self.selected.discard(work_id)
        self._bump_selection()
        self._render_targets()

    def _bump_selection(self):
        self.selection_revision += 1
        if not self.closed:
            self.root.props["data-selection-revision"] = str(self.selection_revision)
            self.root.update()

    def _select_event(self, event, work_id, session=None):
        if session is not None and session is not self._render_session:
            return
        data = event.args
        if self._valid() and _browser_event(event, epoch=self.epoch) and type(data.get("value")) is bool:
            self.select(work_id, data.get("value") is True, data.get("epoch"))

    def _more_event(self, event, session):
        if (session is self._render_session and self._valid()
                and _browser_event(event, epoch=self.epoch)):
            self._more(event.args["epoch"])

    def _more(self, epoch):
        if self._valid(epoch):
            self.shown += 50
            self.render()

    def render(self):
        if self.closed:
            return
        self._render_session = object()
        matching = [item for item in self.artworks.values() if self.query in
                    f"{item.work_id} {item.title} {item.author}".casefold()]
        self.list.clear()
        self._choices = {}
        with self.list:
            for art in matching[:self.shown]:
                with ui.row().classes("pv-download-item w-full items-center").props(f"data-work-id={art.work_id}"):
                    choice = ui.checkbox(value=art.work_id in self.selected).props(
                        f"dense data-testid=download-select aria-label={art.work_id}")
                    choice.on("update:model-value", lambda e, wid=art.work_id, session=self._render_session:
                              self._select_event(e, wid, session),
                        js_handler=f"value => emit({{value,epoch:document.getElementById('{self.root.html_id}').dataset.epoch}})")
                    choice.set_enabled(not self.busy and self.active)
                    self._choices[art.work_id] = choice
                    with ui.column().classes("gap-0 flex-1 min-w-0"):
                        ui.label(art.title).classes("pv-job-title")
                        ui.label(f"{art.author} · {art.work_id}").classes("pv-subtitle")
                    ui.label(f"{art.page_count}頁").classes("pv-counter")
            if len(matching) > self.shown:
                more = ui.button("もっと見る", color=None).props("flat data-testid=download-more")
                more.on("click", lambda event, session=self._render_session: self._more_event(event, session),
                        js_handler=f"() => emit({{epoch:document.getElementById('{self.root.html_id}').dataset.epoch}})")
            if not matching:
                ui.label("条件に合う作品がありません").classes("pv-empty")
        self.counter.set_text(f"{min(self.shown, len(matching))} / {len(matching)} 件表示")
        if self.services.jobs is None:
            self.notice.set_text("保存先未指定です。専用フォルダーを指定して起動すると保存試験を利用できます。")
        self._render_targets()

    def _render_targets(self):
        self.selection_count.set_text(f"対象 {len(self.selected)} 件")
        self.targets.clear()
        with self.targets:
            for item in self.artworks.values():
                if item.work_id in self.selected:
                    ui.label(item.title).classes("pv-job-title")
        self.submit_button.set_enabled(bool(self.selected) and self.services.jobs is not None
                                       and not self.busy and self.active and not self.closed)
        self.details.refresh()

    async def _submit_event(self, event):
        if self._valid() and _browser_event(event, epoch=self.epoch,
                selection_revision=self.selection_revision):
            await self.submit(event.args.get("epoch"))

    async def submit(self, epoch=None):
        if not self._valid(epoch) or self.busy or self.services.jobs is None or not self.selected:
            return False
        self.busy = True
        self._render_targets()
        accepted = []
        failed = False
        try:
            targets = tuple(item for item in self.artworks.values() if item.work_id in self.selected)
            for item in targets:
                operation = self._operations.setdefault(item.work_id, uuid4().hex)
                await _accepted_call(self.services.jobs.enqueue, "demo", item.work_id,
                                     title=item.title, operation_id=operation)
                accepted.append(item.work_id)
                self._operations.pop(item.work_id, None)
        except Exception:
            failed = True
        finally:
            self.selected.difference_update(accepted)
            if accepted:
                self._bump_selection()
            self.busy = False
            if not self.closed:
                self.notice.set_text(f"{len(accepted)}件をキューへ追加しました" +
                                     ("。残りは追加できませんでした" if failed else ""))
                self.render()
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
        self.details.set_active(self.active)
        # A tab roundtrip only changes activation identity. Preserve the list
        # elements and their browser scroll position.
        self._render_targets()
        for checkbox in self._choices.values():
            checkbox.set_enabled(self.active and not self.busy)

    def close(self):
        if not self.closed:
            self.closed = True
            self.active = False
            self.epoch += 1


class QueueView:
    PAGE_SIZE = 8

    def __init__(self, services, *, failed_only=False):
        self.services = services
        self.client = ui.context.client
        self.failed_only = failed_only
        self.prefix = "failed" if failed_only else "queue"
        self.active, self.closed = True, False
        self.epoch = 0
        self.query = ""
        self.filter = "error" if failed_only else "all"
        self.page = 0
        self.selected = set()
        self.selection_revision = 0
        self.view_generation = 0
        self.selection_mode = False
        self.bulk_busy = False
        self.busy = set()
        self.rows = {}
        self._rows_signature = None
        self._latest = ()
        with ui.column().classes("pv-jobs-view w-full").props(
            f"data-testid={self.prefix}-view data-active=true data-epoch=0 data-selection-revision=0 data-view-generation=0") as self.root:
            with ui.row().classes("pv-toolbar"):
                ui.label("失敗リスト" if failed_only else "キュー").classes("pv-library-heading")
                self.search_input = ui.input(placeholder="タイトル・IDで検索" if failed_only else "作者名・作品IDで絞り込む").props(
                    f"outlined dense clearable debounce=200 data-testid={self.prefix}-search").classes("pv-search")
                self.search_input.on("update:model-value", self._search_event,
                    js_handler=f"value => emit({{value,epoch:document.getElementById('{self.root.html_id}').dataset.epoch}})")
                self.counter = ui.label().classes("pv-counter").props(f"data-testid={self.prefix}-count")
            self.chips = {}
            if not failed_only:
                with ui.row().classes("pv-job-chips"):
                    for state, label in {"all": "すべて", **LABELS}.items():
                        button = ui.button(label, color=None).props(f"flat dense no-caps data-testid=queue-filter-{state}")
                        button.on("click", lambda e, state=state: self._filter_event(e, state),
                            js_handler=f"() => emit({{epoch:document.getElementById('{self.root.html_id}').dataset.epoch}})")
                        self.chips[state] = button
                with ui.row().classes("w-full items-center gap-2") as self.selection_toolbar:
                    self.select_mode_button = ui.button("選択", color=None).props("flat data-testid=queue-select-mode")
                    self.select_all_button = ui.button("この絞り込みの全件を選ぶ", color=None).props("flat data-testid=queue-select-all")
                    self.select_clear_button = ui.button("選択を終了", color=None).props("flat data-testid=queue-select-clear")
                    self.bulk_delete_button = ui.button("まとめて削除", color=None).props("flat data-testid=queue-bulk-delete")
                    self.bulk_delete_button.on("click", self._bulk_delete_event,
                        js_handler=f"() => {{const d=document.getElementById('{self.root.html_id}').dataset;emit({{epoch:d.epoch,selectionRevision:d.selectionRevision,viewGeneration:d.viewGeneration}})}}")
                    self.selection_count = ui.label().props("data-testid=queue-selection-count")
                    self.bulk_buttons = {}
                    for action in ("pause", "resume", "stop"):
                        button = ui.button(ACTION_LABELS[action], color=None).props(
                            f"flat data-testid=queue-bulk-{action}")
                        button.on("click", lambda e, action=action: self._bulk_event(e, action),
                            js_handler=f"() => {{const d=document.getElementById('{self.root.html_id}').dataset;emit({{epoch:d.epoch,selectionRevision:d.selectionRevision,viewGeneration:d.viewGeneration}})}}")
                        self.bulk_buttons[action] = button
                    for button, callback in ((self.select_mode_button, self.enter_selection),
                                             (self.select_all_button, self.select_all_filtered),
                                             (self.select_clear_button, self.exit_selection)):
                        button.on("click", lambda e, callback=callback: self._selection_event(e, callback),
                            js_handler=f"() => {{const d=document.getElementById('{self.root.html_id}').dataset;emit({{epoch:d.epoch,selectionRevision:d.selectionRevision,viewGeneration:d.viewGeneration}})}}")
            self.notice = ui.label().classes("pv-subtitle").props(f"data-testid={self.prefix}-notice role=status")
            self.export_notice = ui.label().classes("pv-subtitle").props(f"data-testid={self.prefix}-export-status role=status")
            self.retained_list=ui.column().props(f"data-testid={self.prefix}-retained-exports")
            self._retained_signature=None
            self.list = ui.column().classes("pv-jobs-scroll w-full gap-0").props(f"data-testid={self.prefix}-list")
            with ui.row().classes("w-full items-center justify-end"):
                self.previous = ui.button("前へ", color=None).props(f"flat dense data-testid={self.prefix}-previous")
                self.previous.on("click", lambda e: self._page_event(e, -1),
                    js_handler=f"() => emit({{epoch:document.getElementById('{self.root.html_id}').dataset.epoch}})")
                self.page_label = ui.label().props(f"data-testid={self.prefix}-page")
                self.next = ui.button("次へ", color=None).props(f"flat dense data-testid={self.prefix}-next")
                self.next.on("click", lambda e: self._page_event(e, 1),
                    js_handler=f"() => emit({{epoch:document.getElementById('{self.root.html_id}').dataset.epoch}})")
        self.timer = ui.timer(0.25, self.refresh)
        self.client.on_delete(self.close)
        self.refresh()

    def _valid(self, epoch=None):
        return self.active and not self.closed and (epoch is None or str(epoch) == str(self.epoch))

    def _search_event(self, event):
        if self._valid() and _browser_event(event, epoch=self.epoch):
            self.search(event.args.get("value"))

    def search(self, text):
        if self._valid():
            self._bump_selection()
            self._bump_view()
            self.query = str(text or "").strip().casefold()
            self.page = 0
            self.refresh()

    def _filter_event(self, event, state):
        if self._valid() and _browser_event(event, epoch=self.epoch):
            self.set_filter(state)

    def set_filter(self, state):
        if not self._valid() or state not in {"all", *LABELS} or self.failed_only:
            return
        self._bump_selection()
        self._bump_view()
        self.filter = state
        self.page = 0
        self.refresh()

    def _page_event(self, event, delta):
        if self._valid() and _browser_event(event, epoch=self.epoch):
            self.move_page(delta)

    def move_page(self, delta):
        if self._valid() and delta in {-1, 1}:
            self._bump_selection()
            self._bump_view()
            self.page = max(0, self.page + delta)
            self.refresh()

    def _bump_selection(self):
        self.selection_revision += 1
        if not self.closed:
            self.root.props["data-selection-revision"] = str(self.selection_revision)
            self.root.update()

    def _bump_view(self):
        self.view_generation += 1
        if not self.closed:
            self.root.props["data-view-generation"] = str(self.view_generation)
            self.root.update()

    def _selection_valid(self, epoch, revision):
        return self._valid(epoch) and str(revision) == str(self.selection_revision)

    def _selection_event(self, event, callback):
        if self._valid() and _browser_event(event, epoch=self.epoch,
                selection_revision=self.selection_revision, view_generation=self.view_generation):
            callback()

    def enter_selection(self):
        if self._valid() and not self.failed_only and not self.bulk_busy:
            self._bump_selection()
            self.selection_mode = True
            self.selected.clear()
            self.refresh()

    def exit_selection(self):
        if self._valid() and not self.bulk_busy:
            self._bump_selection()
            self.selection_mode = False
            self.selected.clear()
            self.refresh()

    def select_job(self, job_id, selected, epoch=None, selection_revision=None, view_generation=None):
        if (not self._valid(epoch) or not self.selection_mode or self.bulk_busy
                or (selection_revision is not None and not self._selection_valid(epoch, selection_revision))
                or (view_generation is not None and str(view_generation) != str(self.view_generation))
                or job_id not in {job.job_id for job in self._matching()}):
            return False
        self._bump_selection()
        if selected:
            self.selected.add(job_id)
        else:
            self.selected.discard(job_id)
        self.refresh()
        return True

    def _select_job_event(self, event, job_id):
        if (self._valid() and _browser_event(event, epoch=self.epoch,
                selection_revision=self.selection_revision, view_generation=self.view_generation)
                and type(event.args.get("value")) is bool):
            self.select_job(job_id, event.args.get("value") is True, event.args.get("epoch"),
                            event.args.get("selectionRevision"), event.args.get("viewGeneration"))

    def select_all_filtered(self):
        if not self._valid() or not self.selection_mode or self.bulk_busy:
            return
        self._bump_selection()
        self.selected.update(job.job_id for job in self._matching())
        self.refresh()

    def _matching(self):
        authors = {item.work_id: item.author for item in self.services.artworks}
        names = getattr(self.services, 'managed_job_author_names', None)
        managed_authors = names(self._latest) if callable(names) else {}
        jobs = [job for job in self._latest if (self.filter == "all" or job.state == self.filter)
                and self.query in f"{job.title} {job.target_id} {managed_authors.get(job.job_id, authors.get(job.target_id, ''))}".casefold()]
        if not self.failed_only:
            jobs.sort(key=lambda job: job.state not in UNFINISHED)
        return jobs

    async def _bulk_event(self, event, action):
        if self._valid() and _browser_event(event, epoch=self.epoch,
                selection_revision=self.selection_revision, view_generation=self.view_generation):
            await self.bulk(action, event.args.get("epoch"), event.args.get("selectionRevision"))

    async def _bulk_delete_event(self, event):
        if (not self._valid() or not _browser_event(event, epoch=self.epoch,
                selection_revision=self.selection_revision, view_generation=self.view_generation)):
            return
        service = self.services.jobs
        if service is None or service.faulted or self.bulk_busy or not self.selection_mode:
            return
        frozen = tuple((job.job_id, job.control_revision) for job in service.snapshot()
                       if job.job_id in self.selected)
        selection_revision = self.selection_revision
        if not frozen:
            return
        with ui.dialog() as dialog, ui.card():
            ui.label(f"選択した {len(frozen)} 件のジョブ履歴を削除しますか？")
            ui.label("この履歴削除は取り消せません。実行中のジョブは停止します。")
            ui.label("保存済みファイルは削除しません")
            with ui.row():
                ui.button("キャンセル", on_click=lambda: dialog.submit(False)).props("flat")
                ui.button("削除する", on_click=lambda: dialog.submit(True)).props(
                    "flat data-testid=queue-bulk-delete-confirm")
        confirmed = await dialog
        dialog.delete()
        if confirmed and self._selection_valid(event.args.get("epoch"), selection_revision):
            await self.bulk("delete", event.args.get("epoch"), selection_revision, frozen)

    async def bulk(self, action, epoch=None, selection_revision=None, frozen=None):
        service = self.services.jobs
        if (not self._valid(epoch) or self.failed_only or not self.selection_mode or self.bulk_busy
                or (selection_revision is not None and not self._selection_valid(epoch, selection_revision))
                or service is None or service.faulted or action not in ("pause", "resume", "stop", "delete")):
            return False
        snapshot = {job.job_id: job for job in service.snapshot()}
        candidates = frozen if frozen is not None else tuple(
            (job.job_id, job.control_revision) for job in snapshot.values() if job.job_id in self.selected)
        targets = [(job_id, revision) for job_id, revision in candidates
                   if job_id in snapshot and snapshot[job_id].state in ACTION_STATES[action]
                   and snapshot[job_id].control_revision == revision]
        if not targets:
            self.notice.set_text("操作可能な選択ジョブがありません")
            return False
        self.bulk_busy = True
        self.busy.update(job_id for job_id, _ in targets)
        self.refresh()
        accepted = rejected = failed = 0
        try:
            for job_id, revision in targets:
                try:
                    if await _accepted_call(service.control, job_id, action,
                                            expected_control_revision=revision):
                        accepted += 1
                    else:
                        rejected += 1
                except Exception:
                    failed += 1
            return rejected == failed == 0
        finally:
            self.selected.difference_update(job_id for job_id, _ in candidates)
            self._bump_selection()
            self.busy.difference_update(job_id for job_id, _ in targets)
            self.bulk_busy = False
            if not self.closed:
                self.notice.set_text(f"一括{ACTION_LABELS[action]}: 受理{accepted}件・状態変更/拒否{rejected}件・失敗{failed}件")
                self.refresh()

    def _make_row(self, job):
        with self.list:
            with ui.row().classes("pv-job-row w-full items-center").props(
                f"data-testid=job-row data-job-id={job.job_id} data-state={job.state}") as root:
                with ui.column().classes("pv-job-description gap-1"):
                    display_title, display_target = _novel_identity(job)
                    title = ui.label(display_title).classes("pv-job-title")
                    target = ui.label("うごイラ保存ジョブ" if job.kind.startswith("ugoira-") else display_target).classes("pv-subtitle")
                    result = ui.label().classes("pv-subtitle").props("data-testid=job-save-result")
                    progress = ui.linear_progress(value=0).props("size=3px")
                state = ui.label().classes("pv-job-state").props("data-testid=job-state")
                with state:
                    tooltip = ui.tooltip()
                count = ui.label().classes("pv-counter").props("data-testid=job-progress")
                buttons = {}
                with ui.row().classes("pv-job-actions"):
                    if not self.failed_only:
                        checkbox = ui.checkbox(value=job.job_id in self.selected).props(
                            f"data-testid=job-select data-job-id={job.job_id} data-selection-revision={self.selection_revision}")
                        checkbox.on("update:model-value", lambda e, jid=job.job_id: self._select_job_event(e, jid),
                            js_handler=f"""value => {{const d=document.getElementById('{self.root.html_id}').dataset;
                                emit({{value,epoch:d.epoch,selectionRevision:d.selectionRevision,
                                       viewGeneration:'{self.view_generation}'}})}}""")
                        checkbox.set_visibility(self.selection_mode)
                    for action in (("retry", "delete") if self.failed_only else ("pause", "resume", "stop", "delete")):
                        button = ui.button(ACTION_LABELS[action], color=None).props(
                            f"flat dense no-caps data-testid=job-{action} data-action={action} data-job-id={job.job_id}")
                        button.on("click", self._action_event, js_handler=f"""event => {{
                            const button = event.currentTarget;
                            emit({{id:button.dataset.jobId, action:button.dataset.action,
                                   revision:button.dataset.revision,
                                   controlRevision:button.dataset.controlRevision,
                                   epoch:document.getElementById('{self.root.html_id}').dataset.epoch,
                                   viewGeneration:'{self.view_generation}'}});
                        }}""")
                        buttons[action] = button
        return {"root": root, "title": title, "target": target, "progress": progress,
                "state": state, "tooltip": tooltip, "count": count, "buttons": buttons,
                "checkbox": checkbox if not self.failed_only else None, "result": result, "rendered": None}

    def refresh(self):
        if not self._valid():
            return
        service = self.services.jobs
        jobs = () if service is None else service.snapshot()
        self._latest = jobs
        if service is None:
            self.notice.set_text("専用保存先を指定するとキューと保存試験を利用できます")
        elif service.faulted:
            self.notice.set_text("保存失敗により処理を停止しています。表示は最後に保存できた状態です")
        exports=getattr(self.services,'save_exports',None)
        messages=exports.status_messages() if exports is not None else ()
        ugoira=getattr(self.services,'ugoira',None)
        if ugoira is not None:messages+=getattr(ugoira,'status_messages',lambda:())()
        authors=getattr(self.services,'author_media',None)
        if authors is not None:messages+=getattr(authors,'reconcile_status_messages',lambda:())()
        self.export_notice.set_text('\n'.join(messages))
        self.export_notice.set_visibility(bool(messages))
        offers=exports.retained_requests() if exports is not None and self.failed_only else ()
        signature=tuple(entry.job_id for entry in offers)
        if signature!=self._retained_signature:
            self._retained_signature=signature;self.retained_list.clear()
            with self.retained_list:
                for entry in offers:
                    with ui.row().classes('items-center'):
                        ui.label(entry.title+' · '+entry.job_id).classes('pv-subtitle')
                        button=ui.button('退避履歴を再試行',color=None).props(
                            f'flat data-testid=retained-export-retry data-job-id={entry.job_id}')
                        button.on('click',lambda event,jid=entry.job_id:self._retained_event(event,jid),
                            js_handler=f"() => emit({{epoch:document.getElementById('{self.root.html_id}').dataset.epoch}})")
        self.retained_list.set_visibility(bool(offers))
        before = len(self.selected)
        self.selected.intersection_update(job.job_id for job in jobs)
        if len(self.selected) != before:
            self._bump_selection()
        matching = self._matching()
        page_count = max(1, math.ceil(len(matching) / self.PAGE_SIZE))
        self.page = min(self.page, page_count - 1)
        visible = matching[self.page * self.PAGE_SIZE:(self.page + 1) * self.PAGE_SIZE]
        # A new filter/page can produce the same IDs. Its handlers must still
        # belong to the new rendered generation, while progress keeps rows intact.
        signature = (self.view_generation, tuple(job.job_id for job in visible))
        if signature != self._rows_signature:
            self.list.clear()
            self.rows = {}
            self._rows_signature = signature
            for job in visible:
                self.rows[job.job_id] = self._make_row(job)
            if not visible:
                with self.list:
                    ui.label("失敗したジョブはありません" if self.failed_only else "該当するジョブはありません").classes("pv-empty")
        for job in visible:
            row = self.rows[job.job_id]
            rendering = (job.revision, job.job_id in self.busy, service.faulted,
                         self.selection_mode, job.job_id in self.selected)
            if row["rendered"] == rendering:
                continue
            row["rendered"] = rendering
            row["root"].props["data-state"] = job.state
            row["root"].update()
            row["state"].set_text("状態未確定" if service.faulted and job.state in UNFINISHED else LABELS[job.state])
            row["tooltip"].set_text("状態保存失敗" if job.error_code == "storage_unavailable" else
                                     "処理に失敗しました" if job.error_code else LABELS[job.state])
            row["result"].set_text(("保存済み · ライブラリで確認できます" if job.state == "done" else
                "保存失敗。再試行または形式を選び直してください。MP4は既存OpenCV環境が必要です。" if job.state == "error" and job.kind.startswith("ugoira-") else "")
                if job.kind == "novel-export" or job.kind.startswith("ugoira-") else "")
            row["count"].set_text(f"{job.done}/{job.total}" if job.total else "—")
            row["progress"].set_value(job.done / job.total if job.total else 0)
            if not self.failed_only:
                checkbox = row["checkbox"]
                checkbox.set_visibility(self.selection_mode)
                checkbox.set_value(job.job_id in self.selected)
                checkbox.set_enabled(not self.bulk_busy)
                checkbox.props["data-selection-revision"] = str(self.selection_revision)
                checkbox.update()
            for action, button in row["buttons"].items():
                button.props["data-revision"] = str(job.revision)
                button.props["data-control-revision"] = str(job.control_revision)
                button.set_visibility(not self.selection_mode)
                button.set_enabled(job.state in ACTION_STATES[action] and job.job_id not in self.busy
                                   and not service.faulted)
                button.update()
        for state, button in self.chips.items():
            count = len(jobs) if state == "all" else sum(job.state == state for job in jobs)
            button.set_text(f"{'すべて' if state == 'all' else LABELS[state]} {count}")
            button.props["aria-pressed"] = str(state == self.filter).lower()
        self.counter.set_text(f"{len(matching)}件")
        self.page_label.set_text(f"{self.page + 1} / {page_count}")
        self.previous.set_enabled(self.page > 0)
        self.next.set_enabled(self.page + 1 < page_count)
        if not self.failed_only:
            self.select_mode_button.set_visibility(not self.selection_mode)
            self.select_all_button.set_visibility(self.selection_mode)
            self.select_clear_button.set_visibility(self.selection_mode)
            self.bulk_delete_button.set_visibility(self.selection_mode)
            self.bulk_delete_button.set_enabled(self.selection_mode and bool(self.selected) and not self.bulk_busy
                                                and service is not None and not service.faulted)
            self.selection_count.set_text(f"{len(self.selected)}件を選択中")
            self.selection_count.set_visibility(self.selection_mode)
            for action, button in self.bulk_buttons.items():
                button.set_visibility(self.selection_mode)
                button.set_enabled(not self.bulk_busy and service is not None and not service.faulted and any(
                    job.job_id in self.selected and job.state in ACTION_STATES[action] for job in jobs))

    async def _retained_event(self,event,job_id):
        if not self._valid() or not _browser_event(event,epoch=self.epoch) or job_id in self.busy:return
        self.busy.add(job_id)
        try:
            exports=getattr(self.services,'save_exports',None)
            changed=await _accepted_call(exports.retry_retained,job_id) if exports is not None else False
            if not self.closed:self.notice.set_text('再試行を受け付けました' if changed else '履歴の状態を確認してください')
        except Exception:
            if not self.closed:self.notice.set_text('履歴を復帰できません。状態を確認してください')
        finally:
            self.busy.discard(job_id);self.refresh()

    async def _action_event(self, event):
        if not self._valid() or not _browser_event(event, epoch=self.epoch,
                view_generation=self.view_generation):
            return
        data = event.args
        try:
            if not (_event_number(data, "revision", data.get("revision"))
                    and _event_number(data, "controlRevision", data.get("controlRevision"))):
                return
            revision = int(data["revision"])
            control_revision = int(data["controlRevision"])
        except (ValueError, TypeError):
            return
        action, job_id = data.get("action"), data.get("id")
        if action == "delete":
            with ui.dialog() as dialog, ui.card():
                ui.label("ジョブを削除して未完了の処理を停止しますか？")
                ui.label("保存済みファイルは削除しません").classes("pv-subtitle")
                with ui.row():
                    ui.button("キャンセル", color=None, on_click=lambda: dialog.submit(False)).props("flat")
                    ui.button("削除する", color=None, on_click=lambda: dialog.submit(True)).props(
                        "flat data-testid=job-delete-confirm")
            confirmed = await dialog
            dialog.delete()
            if not confirmed or not self._valid(data.get("epoch")):
                return
        await self.command(job_id, action, revision, data.get("epoch"), control_revision)

    async def command(self, job_id, action, revision=None, epoch=None, control_revision=None):
        service = self.services.jobs
        if not self._valid(epoch) or service is None or job_id in self.busy or action not in ACTION_STATES:
            return False
        self.busy.add(job_id)
        self.refresh()
        try:
            changed = await _accepted_call(service.control, job_id, action,
                expected_revision=revision if control_revision is None else None,
                expected_control_revision=control_revision)
            if not self.closed:
                self.notice.set_text("操作を受け付けました" if changed else "状態が変わったため操作しませんでした")
            return changed
        except Exception:
            if not self.closed:
                self.notice.set_text("操作結果を保存できませんでした")
            return False
        finally:
            self.busy.discard(job_id)
            self.refresh()

    def set_active(self, active):
        if self.closed:
            return
        if self.active != bool(active):
            self.epoch += 1
        self.active = bool(active)
        self.root.props["data-active"] = str(self.active).lower()
        self.root.props["data-epoch"] = str(self.epoch)
        self.root.update()
        self.timer.active = self.active
        if self.active:
            self.refresh()

    def close(self):
        if not self.closed:
            self.closed = True
            self.active = False
            self.epoch += 1
            self.timer.deactivate()
