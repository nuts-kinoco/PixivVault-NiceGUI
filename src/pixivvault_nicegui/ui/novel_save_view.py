"""Small M4 fixture UI using the existing download/queue/library navigation."""
from __future__ import annotations

import asyncio
import math
from uuid import uuid4

from nicegui import ui

from ..novel_save_presentation import FORMATS, NovelSavePresentation
from .job_views import _accepted_call, _browser_event


class NovelDownloadView:
    def __init__(self, services, *, navigate):
        self.services = services
        self.presentation = NovelSavePresentation(services)
        self.navigate = navigate
        self.client = ui.context.client
        self.active, self.closed, self.busy = True, False, False
        self.epoch = self.selection_revision = 0
        self.work_id = ""
        preferences = getattr(services, 'preferences', None)
        self.output_format = (preferences.get_setting('novel_save_format', 'epub').upper()
                              if preferences is not None else 'EPUB')
        self.selection = None
        self.operation_id = None
        self.submitted = False
        with ui.column().classes("pv-jobs-view pv-novel-download w-full").props(
                "data-testid=novel-download-view data-active=true data-epoch=0 data-selection-revision=0") as self.root:
            with ui.row().classes("pv-toolbar"):
                ui.label("小説を保存").classes("pv-library-heading")
                self.work_input = ui.input(placeholder="単一小説ID（数字）").props(
                    "outlined dense data-testid=novel-work-id aria-label=小説ID").classes("pv-search")
                self.work_input.on("update:model-value", self._input_event,
                    js_handler=self._js("value", "value"))
                self.lookup_button = ui.button("IDから作品を確認", color=None).props(
                    "flat no-caps data-testid=novel-preview-button")
                self.lookup_button.on("click", self._preview_event, js_handler=self._js())
            if self.presentation.practical:
                with ui.row().classes('pv-toolbar'):
                    self.author_input = ui.input('作者ID').props('outlined dense data-testid=novel-author-id')
                    self.chapters_input = ui.input('シリーズ章ID（入力順・カンマ区切り、省略可）').props('outlined dense data-testid=novel-chapter-ids')
                    self.series_input = ui.input('シリーズ題名').props('outlined dense data-testid=novel-series-title')
                with ui.row().classes('pv-toolbar'):
                    ui.button('作者の小説一覧', on_click=lambda: self.load_catalog('author')).props('flat data-testid=novel-author-list')
                    ui.button('公開ブックマーク', on_click=lambda: self.load_catalog('show')).props('flat data-testid=novel-bookmarks-show')
                    ui.button('非公開ブックマーク', on_click=lambda: self.load_catalog('hide')).props('flat data-testid=novel-bookmarks-hide')
                self.catalog_rows = ui.column().classes('w-full pv-novel-catalog').style('max-height:160px;overflow:auto')
                for control in (self.author_input, self.chapters_input, self.series_input):
                    control.on_value_change(lambda _: self._practical_changed())
                ui.label('作品・作者IDを確認してください。シリーズは明示した章順で保存します。全件取得を推測しません。').classes('pv-subtitle')
            else:
                ui.label('合成データ：小説ID 123 / 124。確認だけでは保存しません。').classes('pv-subtitle')
            self.notice = ui.label().props("role=status data-testid=novel-notice")
            with ui.row().classes("pv-toolbar"):
                self.format_select = ui.select(FORMATS, value=self.output_format, label="小説の保存形式").props(
                    "outlined dense data-testid=novel-format").classes("pv-novel-format")
                self.format_select.on("update:model-value", self._format_event,
                    js_handler=self._js("value", "value"))
                self.submit_button = ui.button("キューへ追加", color=None).props(
                    "flat no-caps data-testid=novel-submit")
                self.submit_button.on("click", self._submit_event, js_handler=self._js())
                self.queue_button = ui.button("キューを開く", color=None).props(
                    "flat no-caps data-testid=novel-open-queue")
                self.queue_button.on("click", lambda e: self._navigate_event(e, "queue"), js_handler=self._js())
            with ui.column().classes("pv-novel-preview w-full").props("data-testid=novel-preview") as self.preview_root:
                self.preview_title = ui.label().classes("pv-library-heading")
                self.preview_author = ui.label().classes("pv-subtitle")
                self.preview_body = ui.label().classes("pv-novel-text")
                self.saved_label = ui.label().props("data-testid=novel-saved-status")
            self.preview_root.set_visibility(False)
        self.client.on_delete(self.close)
        self._sync()

    async def load_catalog(self, mode):
        if not self._valid() or self.busy:
            return
        self.busy = True
        epoch, revision = self.epoch, self.selection_revision
        self._sync()
        try:
            source = self.services.novel_fixture_source
            rows = await asyncio.to_thread(self.services.novel_catalog,
                str(self.author_input.value or '').strip() if mode == 'author' else None,
                mode if mode != 'author' else 'show')
            if not self._valid(epoch) or revision != self.selection_revision:
                return
            self.catalog_rows.clear()
            with self.catalog_rows:
                for wid, aid, title in rows:
                    ui.button(f'{title} · {wid}', on_click=lambda w=wid, a=aid, e=epoch, r=revision:
                        self.choose_catalog(w, a, e, r)).props(f'flat no-caps data-testid=novel-catalog-{wid}')
            self.notice.set_text(f'小説 {len(rows)} 件。作品を選んで確認してください。')
        except Exception:
            if self._valid(epoch):
                self.notice.set_text('一覧を取得できません。認証・作者・件数上限を確認してください。')
        finally:
            self.busy = False
            if not self.closed:
                self._sync()

    def choose_catalog(self, work_id, author_id, epoch, revision):
        if not self._valid(epoch) or revision != self.selection_revision:
            return
        self.author_input.set_value(author_id)
        self.chapters_input.set_value('')
        self.series_input.set_value('')
        self.work_input.set_value(work_id)
        self.change_work_id(work_id)
        self.catalog_rows.clear()

    def _practical_changed(self):
        if self._valid():
            self._invalidate()
            self._sync()

    def _js(self, argument="", value=None):
        extra = ",value" if value else ""
        return (f"({argument}) => {{const d=document.getElementById('{self.root.html_id}').dataset;"
                f"emit({{epoch:d.epoch,selectionRevision:d.selectionRevision{extra}}})}}")

    def _valid(self, epoch=None):
        return self.active and not self.closed and (epoch is None or str(epoch) == str(self.epoch))

    def _event_valid(self, event):
        return self._valid() and _browser_event(event, epoch=self.epoch,
                                                selection_revision=self.selection_revision)

    def _invalidate(self):
        self.selection_revision += 1
        self.selection = None
        self.operation_id = None
        self.submitted = False
        self.root.props["data-selection-revision"] = str(self.selection_revision)
        self.root.update()
        self.preview_root.set_visibility(False)

    def _input_event(self, event):
        if self._valid() and _browser_event(event, epoch=self.epoch):
            self.change_work_id(event.args.get("value"))

    def change_work_id(self, value):
        if not self._valid() or type(value) is not str:
            return
        self.work_id = value.strip()
        self._invalidate()
        self.notice.set_text("IDと保存形式を確認してプレビューしてください")
        self._sync()

    def _format_event(self, event):
        if self._valid() and _browser_event(event, epoch=self.epoch):
            # NiceGUI Select sends {value: option_index, label: ...}, not the
            # logical dict key. Resolve only a bounded integer index against
            # our fixed options; the browser's display label is not trusted.
            value = event.args.get("value")
            if type(value) is not dict:
                return
            index = value.get("value")
            if type(index) is not int or not 0 <= index < len(FORMATS):
                return
            self.change_format(tuple(FORMATS)[index])

    def change_format(self, value):
        if not self._valid() or type(value) is not str or value not in FORMATS:
            return
        self.output_format = value
        self._invalidate()
        self.notice.set_text("保存形式を変更しました。もう一度作品を確認してください")
        self._sync()

    async def _preview_event(self, event):
        if self._event_valid(event):
            await self.lookup(event.args.get("epoch"))

    async def lookup(self, epoch=None):
        if not self._valid(epoch) or self.busy or not self.work_id:
            return False
        self.busy = True
        captured = self.epoch, self.selection_revision
        work_id, output_format = self.work_id, self.output_format
        self._sync()
        try:
            if self.presentation.practical:
                author = str(self.author_input.value or '').strip()
                chapters = tuple(x.strip() for x in str(self.chapters_input.value or '').split(',') if x.strip())
                title = str(self.series_input.value or '').strip() or None
                self.presentation.select(work_id, author)
                selection = await asyncio.to_thread(self.presentation.preview, work_id, output_format, chapters, title)
            else:
                selection = await asyncio.to_thread(self.presentation.preview, work_id, output_format)
            if not self._valid(captured[0]) or captured[1] != self.selection_revision:
                return False
            self.selection = selection
            self.operation_id = uuid4().hex
            self.submitted = False
            self.preview_title.set_text(selection.title)
            self.preview_author.set_text(f"{selection.author} · 小説 {selection.intent.work_id} · {selection.intent.output_format}")
            self.preview_body.set_text(selection.excerpt)
            self.saved_label.set_text("この形式は保存済みです。ライブラリから確認できます。" if selection.saved else "未保存")
            self.preview_root.set_visibility(True)
            self.notice.set_text("保存済み" if selection.saved else "確認済み。キューへ追加すると保存します。")
            return True
        except Exception:
            if self._valid(captured[0]) and captured[1] == self.selection_revision:
                self.selection = None
                self.preview_root.set_visibility(False)
                self.notice.set_text("作品を確認できません。合成ID・形式・保存状態を確認して再度お試しください。")
            return False
        finally:
            self.busy = False
            if not self.closed:
                self._sync()

    async def _submit_event(self, event):
        if self._event_valid(event):
            await self.submit(event.args.get("epoch"))

    async def submit(self, epoch=None):
        if (not self._valid(epoch) or self.busy or self.selection is None
                or self.selection.saved is not None or self.submitted):
            return False
        self.busy = True
        captured = self.epoch, self.selection_revision
        selection, operation_id = self.selection, self.operation_id
        self._sync()
        try:
            await _accepted_call(self.presentation.enqueue, selection, operation_id)
            if self._valid(captured[0]) and captured[1] == self.selection_revision:
                self.submitted = True
                self.notice.set_text("キューに追加しました。保存結果はキュー・失敗リスト・ライブラリで確認できます。")
            return True
        except Exception:
            if self._valid(captured[0]) and captured[1] == self.selection_revision:
                # Keep the same operation ID across an uncertain response.
                self.notice.set_text("キューへ追加できません。キューの状態を確認し、必要なら再度作品を確認してください。")
            return False
        finally:
            self.busy = False
            if not self.closed:
                self._sync()

    def _navigate_event(self, event, target):
        if self._event_valid(event):
            self.navigate(target)

    def _sync(self):
        ready = self._valid() and not self.busy and not self.services.closing
        self.lookup_button.set_enabled(ready and bool(self.work_id))
        self.submit_button.set_enabled(ready and self.selection is not None
                                       and self.selection.saved is None and not self.submitted)
        self.queue_button.set_enabled(self._valid())

    def set_active(self, active):
        if self.closed or self.active == bool(active):
            return
        self.active = bool(active)
        self.epoch += 1
        self.root.props["data-epoch"] = str(self.epoch)
        self.root.props["data-active"] = str(self.active).lower()
        self._invalidate()
        self._sync()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.active = False
        self.epoch += 1
        self.selection = None


class NovelLibraryView:
    PAGE_CHARS = 8000

    def __init__(self, services, **_):
        self.services = services
        self.presentation = NovelSavePresentation(services)
        self.client = ui.context.client
        self.active, self.closed, self.busy = False, False, False
        self.epoch = 0
        self.entries = ()
        self.generation = 0
        self.reader_text = ""
        self.reader_page = 0
        self.reader_entry = None
        self.read_buttons = {}
        self._loading_task = None
        with ui.column().classes("pv-jobs-view pv-novel-library w-full").props(
                "data-testid=novel-library-view data-epoch=0 data-active=false") as self.root:
            with ui.row().classes("pv-toolbar"):
                ui.label("小説ライブラリ").classes("pv-library-heading")
                self.refresh_button = ui.button("保存結果を確認", color=None).props(
                    "flat data-testid=novel-library-refresh")
                self.refresh_button.on("click", self._refresh_event, js_handler=self._js())
            self.notice = ui.label().props("role=status data-testid=novel-library-notice")
            self.list = ui.column().classes("pv-jobs-scroll w-full").props("data-testid=novel-library-list")
        with ui.dialog().props("data-testid=novel-reader") as self.dialog, ui.card().classes("pv-novel-reader"):
            self.reader_title = ui.label().classes("pv-library-heading")
            self.reader_body = ui.label().classes("pv-novel-text pv-novel-reader-text").props("data-testid=novel-reader-text")
            self.reader_images = ui.row().classes("w-full pv-novel-reader-images")
            with ui.row().classes("w-full items-center justify-end"):
                self.reader_previous = ui.button("前へ", color=None, on_click=lambda: self.move_reader(-1)).props("flat data-testid=novel-reader-previous")
                self.reader_count = ui.label().props("data-testid=novel-reader-page")
                self.reader_next = ui.button("次へ", color=None, on_click=lambda: self.move_reader(1)).props("flat data-testid=novel-reader-next")
                ui.button("閉じる", color=None, on_click=self.dialog.close).props("flat data-testid=novel-reader-close")
        # Restore keyboard focus only to a still-current row in the active tab.
        # QDialog's default refocus can target the previously focused input,
        # because opening the reader happens after an asynchronous disk read.
        self.dialog.on("hide", js_handler=f"""() => {{
            const root = document.getElementById('{self.root.html_id}');
            if (!root || root.dataset.active !== 'true' ||
                root.dataset.readerGeneration !== root.dataset.generation) return;
            const button = document.getElementById(root.dataset.readerTrigger);
            if (button && root.contains(button) && !button.disabled && button.getClientRects().length)
                button.focus({{preventScroll: true}});
        }}""")
        self.client.on_delete(self.close)

    def _js(self):
        return (f"() => {{const d=document.getElementById('{self.root.html_id}').dataset;"
                "emit({epoch:d.epoch,generation:d.generation})}")

    def _valid(self, epoch=None):
        return self.active and not self.closed and (epoch is None or str(epoch) == str(self.epoch))

    async def _refresh_event(self, event):
        if self._valid() and _browser_event(event, epoch=self.epoch):
            await self.refresh()

    async def refresh(self):
        if not self._valid() or self.busy:
            return False
        self.busy = True
        self.list.set_visibility(False)
        epoch = self.epoch
        self.refresh_button.set_enabled(False)
        try:
            entries = await asyncio.to_thread(self.presentation.library)
            if not self._valid(epoch):
                return False
            self.entries = entries
            self.generation += 1
            self.root.props["data-generation"] = str(self.generation)
            self.root.update()
            self.list.clear()
            self.read_buttons = {}
            with self.list:
                for entry in entries:
                    with ui.row().classes("pv-job-row w-full items-center"):
                        with ui.column().classes("pv-job-description gap-1"):
                            ui.label(entry.title).classes("pv-job-title")
                            ui.label(f"{entry.author} · 小説 {entry.work_id} · {entry.output_format}").classes("pv-subtitle")
                            ui.label("整合性確認済み · " + " / ".join(entry.saved.output_names)).classes("pv-subtitle")
                        suffix = ("-series-" + entry.saved.receipt_sha256[:8]) if len((entry.saved.metadata or {}).get("chapters", [])) > 1 else ""
                        button = ui.button("読む", color=None).props(f"flat data-testid=novel-read-{entry.work_id}-{entry.output_format}{suffix}")
                        self.read_buttons[(entry.work_id, entry.output_format, entry.saved.relative_receipt)] = button
                        button.on("click", lambda e, accepted=entry, generation=self.generation:
                                  self._read_event(e, accepted, generation), js_handler=self._js())
                if not entries:
                    ui.label("保存済みの小説はありません").classes("pv-empty")
            self.notice.set_text(f"保存済み {len(entries)} 件")
            return True
        except Exception:
            if self._valid(epoch):
                self.entries = ()
                self.list.clear()
                self.notice.set_text("保存結果を確認できません。破損や状態変更の可能性があります。再確認してください。")
            return False
        finally:
            self.busy = False
            if not self.closed:
                self.refresh_button.set_enabled(self._valid())
                self.list.set_visibility(self._valid(epoch))
                if self.active and epoch != self.epoch:
                    self._loading_task = asyncio.create_task(self.refresh())

    async def _read_event(self, event, entry, generation):
        if (self._valid() and _browser_event(event, epoch=self.epoch)
                and event.args.get("generation") == str(generation) and generation == self.generation):
            await self.read(entry)

    async def read(self, entry):
        if not self._valid() or self.busy or entry not in self.entries:
            return False
        epoch, generation = self.epoch, self.generation
        self.busy = True
        self.refresh_button.set_enabled(False)
        try:
            text = await asyncio.to_thread(self.presentation.read_saved, entry)
            images = await asyncio.to_thread(self.presentation.read_images, entry)
            if not self._valid(epoch) or generation != self.generation:
                return False
            self.reader_images.clear()
            with self.reader_images:
                for source in images:
                    ui.image(source).classes("w-48").props("fit=contain")
            self.reader_text, self.reader_page, self.reader_entry = text, 0, entry
            self.reader_title.set_text(entry.title + " · " + entry.output_format)
            self._render_reader()
            self.root.props["data-reader-trigger"] = self.read_buttons[(entry.work_id, entry.output_format, entry.saved.relative_receipt)].html_id
            self.root.props["data-reader-generation"] = str(generation)
            self.root.update()
            self.dialog.open()
            return True
        except Exception:
            if self._valid(epoch):
                self.notice.set_text("保存済み本文を確認できません。保存結果を再確認してください。")
            return False
        finally:
            self.busy = False
            if not self.closed:
                self.refresh_button.set_enabled(self._valid())
                if self.active and epoch != self.epoch:
                    self._loading_task = asyncio.create_task(self.refresh())

    def _render_reader(self):
        pages = max(1, math.ceil(len(self.reader_text) / self.PAGE_CHARS))
        self.reader_page = max(0, min(self.reader_page, pages - 1))
        start = self.reader_page * self.PAGE_CHARS
        self.reader_body.set_text(self.reader_text[start:start + self.PAGE_CHARS])
        self.reader_count.set_text(f"{self.reader_page + 1} / {pages}")
        self.reader_previous.set_enabled(self.reader_page > 0)
        self.reader_next.set_enabled(self.reader_page + 1 < pages)

    def move_reader(self, delta):
        if self._valid() and self.reader_entry is not None and delta in (-1, 1):
            self.reader_page += delta
            self._render_reader()

    def set_active(self, active):
        if self.closed or self.active == bool(active):
            return
        self.active = bool(active)
        self.epoch += 1
        self.root.props["data-epoch"] = str(self.epoch)
        self.root.props["data-active"] = str(self.active).lower()
        self.root.update()
        self.dialog.close()
        self.reader_text = ""
        self.reader_entry = None
        self.list.set_visibility(False)
        if self.active and not self.busy:
            self._loading_task = asyncio.create_task(self.refresh())
        self.refresh_button.set_enabled(self.active and not self.busy)

    def apply_card_size(self, _):
        pass

    def close(self):
        if self.closed:
            return
        self.closed, self.active = True, False
        self.epoch += 1
        self.reader_text = ""
        self.entries = ()
        self.dialog.close()
