"""Legacy failed_queue inspection, separate from in-flight job errors.

Only explicit CSV export leaves this read-only screen. Retry/removal are kept
disabled until a reviewed mutation service can accept those intents.
"""
from __future__ import annotations

from uuid import uuid4

from nicegui import background_tasks, ui

from ..failed_records_fixtures import build_failed_records_fixture
from ..failed_records_state import CATEGORIES, PAGE_SIZE, FailedRecordsState, classify_reason, csv_bytes


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
        self._loaded = services.data_mode == "synthetic"
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
            ui.label("合成データ · 保存先・実アカウントへの変更なし" if self._loaded else
                     "読取専用 · 再試行・記録削除は未接続です").classes("pv-subtitle")
            with ui.element("div").classes("pv-failure-toolbar"):
                self.search_input = ui.input(placeholder="タイトル・IDで検索").props(
                    "outlined dense clearable dark maxlength=4096 data-testid=failed-search"
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
                with ui.button(icon="more_vert", color=None).props(
                        "flat dense aria-label=その他の操作 data-testid=failed-menu"):
                    with ui.menu():
                        self.export_item = ui.menu_item(auto_close=False).props(
                            "role=menuitem data-testid=failed-export")
                        with self.export_item:
                            self.export_label = ui.item_section("表示中の件数をCSV出力")
                        clear = ui.menu_item("失敗リストを全件クリア").props("disable role=menuitem data-testid=failed-clear-all")
                        clear.tooltip("記録削除は未接続です。作品やDBを変更しません")
            self.counter = ui.label().classes("pv-counter").props("data-testid=failed-count")
            self.status = ui.label().classes("pv-subtitle").props("role=status data-testid=failed-status")
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
        self._wire(self.previous, "click", lambda: self.change_page(-1))
        self._wire(self.next, "click", lambda: self.change_page(1))
        self._wire(self.clear_search, "click", lambda: self.search(""))
        self._wire(self.clear_category, "click", lambda: self.select_category("all"))
        self._wire(self.export_item, "click", self.export)
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
                        retry.disable()
                        remove = ui.button(icon="close", color=None).props("flat dense aria-label=キューから削除 data-testid=failed-row-delete")
                        remove.disable()
                    ui.label(record.error_reason or "不明エラー").classes("pv-failure-reason").props("data-testid=failed-reason")

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
            records = await self.services.read_failed_records()
            accepted = self.state.complete_refresh(token, records, ok=True)
        except Exception:
            accepted = self.state.complete_refresh(token, (), ok=False)
        else:
            if accepted:
                self._loaded = True
                self._read_revision += 1
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
