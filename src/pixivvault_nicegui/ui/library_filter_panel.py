"""Client-local, push-aside library tag filters following the legacy panel layout."""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4
from nicegui import ui

from ..library_filters import LibraryFilters, normalize_tag


class LibraryFilterPanel:
    """One panel and one condition set shared by the two library subviews."""

    def __init__(self, *, get_filters: Callable[[], LibraryFilters],
                 on_change: Callable[[LibraryFilters], bool],
                 can_edit: Callable[[], bool], body, now_parent) -> None:
        self.get_filters = get_filters
        self.on_change = on_change
        self.can_edit = can_edit
        self.now_parent = now_parent
        self.opened = False
        self.subview = "downloaded"
        self._session = object()
        self._epoch = uuid4().hex
        self._toggles = []
        with now_parent:
            self.now = ui.row().classes("pv-library-filter-now w-full").props(
                "data-testid=library-filter-now")
        with body:
            with ui.column().classes("pv-library-filter-panel").props(
                "data-testid=library-filter-panel aria-label=ライブラリの絞り込み") as self.root:
                with ui.row().classes("items-center justify-between w-full"):
                    ui.label("絞り込み").classes("pv-library-heading")
                    self.clear_button = ui.button("タグ条件をクリア").props(
                        "flat dense no-caps data-testid=library-filter-clear")
                seats = (
                    ("sort", "01", "並び順", "ここには無い →", "一覧上部の並び替えボタンで指定します"),
                    ("date", "02", "期間・日付", "未実装", "投稿日による絞り込みは移植準備中です"),
                    ("bookmark", "03", "ブクマ数", "未実装", "ブクマ数による絞り込みは移植準備中です"),
                    ("tags_include", "04", "タグ含む", "ここには無い →", "下の「タグ」欄で＋／−を切り替えます"),
                    ("tags_exclude", "05", "タグ除く", "ここには無い →", "下の「タグ」欄で＋／−を切り替えます"),
                    ("per_author", "06", "作者あたり上限", "仕様上不要", "1作者あたりの表示件数を絞る機能はありません"),
                    ("exclude", "07", "除外", "未実装", "AI作品の除外は移植準備中です"),
                    ("type_format", "08", "種別・形式", "仕様上不要", "原版には作品の種別で絞り込む機能がありません"),
                )
                self._sort_labels = None
                for key, number, title, state, reason in seats:
                    with ui.column().classes("pv-library-filter-seat").props(
                            f"data-testid=library-filter-seat-{key}"):
                        ui.label(f"{number}  {title}").classes("pv-subtitle")
                        badge = ui.label(state).classes("pv-library-filter-state")
                        explanation = ui.label(reason).classes("pv-library-filter-reason")
                        if key == "sort":
                            self._sort_labels = badge, explanation
                ui.separator()
                with ui.column().classes("pv-library-tag-controls w-full").props(
                        "data-testid=library-tag-controls"):
                    with ui.row().classes("items-center justify-between w-full"):
                        ui.label("タグ").classes("pv-library-heading")
                        self.mode_button = ui.button("すべて含む").props(
                            "flat dense no-caps data-testid=library-filter-mode")
                    self.tag_input = ui.input(placeholder="タグを追加").props(
                        "outlined dense dark data-testid=library-filter-tag-input maxlength=256")
                    self.add_button = ui.button("追加").props(
                        "flat dense no-caps data-testid=library-filter-tag-add")
                    self.tags = ui.row().classes("pv-library-tag-chips w-full").props(
                        "data-testid=library-filter-tags")
                    ui.label("＋／−で含む・除外を切替、×で削除").classes("pv-library-filter-reason")
        self.root.set_visibility(False)
        self.root.props["data-filter-epoch"] = self._epoch
        self._wire(self.clear_button, "click", self.clear, needs_panel=True)
        self._wire(self.mode_button, "click", self.toggle_mode, needs_panel=True)
        self._wire(self.add_button, "click", self.add, needs_panel=True, read_value=True)
        self._wire(self.tag_input, "keydown.enter", self.add, needs_panel=True, read_value=True)
        self.refresh()

    def register_toggle(self, button) -> None:
        self._toggles.append(button)
        self._wire(button, "click", self.toggle)
        self._sync_toggles()

    def _wire(self, element, event, callback, *, needs_panel=False, read_value=False) -> None:
        """Capture the browser's epoch when clicked, not the server's later epoch."""
        def handle(message):
            args = message.args
            if (not isinstance(args, dict) or args.get("epoch") != self._epoch or
                    not self._allowed(needs_panel=needs_panel)):
                return
            if read_value:
                value = args.get("value")
                if isinstance(value, str) and len(value) <= 256:
                    callback(value=value)
            else:
                callback()
        value_js = (f"const field=document.getElementById('{self.tag_input.html_id}');"
                    "const input=field?.matches('input')?field:field?.querySelector('input');"
                    "message.value=input?.value ?? '';" if read_value else "")
        element.on(event, handle, js_handler=f"""() => {{
            const panel=document.getElementById('{self.root.html_id}');
            if (!panel) return;
            const message={{epoch:panel.dataset.filterEpoch}};
            {value_js}
            emit(message);
        }}""")

    def _allowed(self, session=None, *, needs_panel: bool = False) -> bool:
        return (self.can_edit() and (not needs_panel or self.opened) and
                (session is None or session is self._session))

    def _sync_toggles(self) -> None:
        filters = self.get_filters()
        count = len(filters.include) + len(filters.exclude)
        label = "絞り込み ‹ 閉じる" if self.opened else "絞り込み ›"
        if count:
            label += f" ({count})"
        for button in self._toggles:
            button.set_text(label)
            button.props["aria-expanded"] = str(self.opened).lower()

    def toggle(self) -> None:
        if not self._allowed():
            return
        self.opened = not self.opened
        self.root.set_visibility(self.opened)
        self.invalidate()

    def set_subview(self, subview: str) -> None:
        if subview == self.subview:
            return
        self.subview = subview
        badge, reason = self._sort_labels
        badge.set_text("ここには無い →" if subview == "downloaded" else "仕様上不要")
        reason.set_text("一覧上部の並び替えボタンで指定します" if subview == "downloaded"
                        else "このサブビューでは並び替えできません")
        self.invalidate()

    def invalidate(self) -> None:
        self._session = object()
        self._epoch = uuid4().hex
        self.root.props["data-filter-epoch"] = self._epoch
        self.refresh()

    def retire(self) -> None:
        """Reject old controls without creating new UI during client deletion."""
        self._session = object()
        self._epoch = uuid4().hex

    def add(self, _event=None, *, value: str | None = None) -> None:
        if not self._allowed(needs_panel=True):
            return
        tag = normalize_tag((self.tag_input.value or "") if value is None else value)
        if not tag:
            return
        current = self.get_filters()
        candidate = LibraryFilters(current.include | {tag}, current.exclude - {tag}, current.mode)
        self.on_change(candidate)
        self.tag_input.set_value("")
        self.invalidate()

    def flip(self, tag: str, session) -> None:
        if not self._allowed(session, needs_panel=True):
            return
        current = self.get_filters()
        if tag in current.include:
            candidate = LibraryFilters(current.include - {tag}, current.exclude | {tag}, current.mode)
        elif tag in current.exclude:
            candidate = LibraryFilters(current.include | {tag}, current.exclude - {tag}, current.mode)
        else:
            return
        self.on_change(candidate)
        self.invalidate()

    def remove(self, tag: str, session) -> None:
        if not self._allowed(session):
            return
        current = self.get_filters()
        self.on_change(LibraryFilters(current.include - {tag}, current.exclude - {tag}, current.mode))
        self.invalidate()

    def clear(self, _event=None, *, session=None) -> None:
        if not self._allowed(session):
            return
        self.on_change(LibraryFilters())
        self.tag_input.set_value("")
        self.invalidate()

    def toggle_mode(self) -> None:
        if not self._allowed(needs_panel=True):
            return
        current = self.get_filters()
        self.on_change(LibraryFilters(current.include, current.exclude,
                                     "or" if current.mode == "and" else "and"))
        self.invalidate()

    def refresh(self) -> None:
        filters, session = self.get_filters(), self._session
        self.tags.clear()
        self.now.clear()
        for excluded, values in ((False, filters.include), (True, filters.exclude)):
            for tag in sorted(values):
                label = ("−" if excluded else "+") + tag
                with self.tags:
                    with ui.row().classes("pv-library-tag-chip" + (" pv-tag-excluded" if excluded else "")) as chip:
                        chip.props["data-tag"] = tag
                        chip.props["data-excluded"] = str(excluded).lower()
                        ui.button("−" if excluded else "+", on_click=lambda _, t=tag, s=session: self.flip(t, s)).props(
                            "flat dense round data-testid=library-filter-tag-flip")
                        ui.label(tag)
                        remove = ui.button("×", on_click=lambda _, t=tag, s=session: self.remove(t, s)).props(
                            "flat dense round data-testid=library-filter-tag-remove")
                        remove.props["aria-label"] = f"タグ {tag} を解除"
                with self.now:
                    button = ui.button(label + " ×", on_click=lambda _, t=tag, s=session: self.remove(t, s)).props(
                        "flat dense no-caps data-testid=library-filter-now-chip")
                    button.props["data-tag"] = tag
        if filters.include or filters.exclude:
            with self.now:
                ui.button("タグをすべて解除", on_click=lambda _, s=session: self.clear(session=s)).props(
                    "flat dense no-caps data-testid=library-filter-now-clear")
        self.now.set_visibility(bool(filters.include or filters.exclude))
        self.now_parent.set_visibility(bool(filters.include or filters.exclude))
        self.mode_button.set_text("すべて含む" if filters.mode == "and" else "どれか")
        self.mode_button.props["data-mode"] = filters.mode
        self._sync_toggles()
