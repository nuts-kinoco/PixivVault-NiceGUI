"""Client-local, push-aside library tag filters following the legacy panel layout."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date
import calendar
import json
from uuid import uuid4
from nicegui import ui

from ..library_filters import LibraryFilters, normalize_tag


class _FilterModel:
    """One epoch-checked model event, with primitive values echoed by the server.

    An envelope emitted by a loopback=False model-value listener becomes the
    browser model itself. Use the component's own VALUE_PROP event and never
    add a second raw/guarded listener or preserve a client-side envelope.
    """
    LOOPBACK = True

    def __init__(self, panel, setting, *args, **kwargs):
        self._filter_panel = panel
        self._filter_setting = setting
        super().__init__(*args, **kwargs)

    def on(self, type, handler=None, args=None, **kwargs):
        # ValueElement calls this once during construction. Register the
        # guarded route directly; do not alter an existing listener table.
        if type == f'update:{self.VALUE_PROP}':
            handler = self._filter_model_changed
            kwargs['js_handler'] = self._filter_panel._setting_event_js()
        return super().on(type, handler, args, **kwargs)

    def _filter_model_changed(self, message):
        if self.is_deleted:
            return
        panel, args = self._filter_panel, message.args
        if (isinstance(args, dict) and 'value' in args
                and args.get('epoch') == panel._epoch
                and panel._allowed(needs_panel=True)):
            panel.change_setting(self._filter_setting, args['value'])
        # Reconcile even rejected/unchanged packets. An input may have local
        # typed text, but it must not retain a value rejected by this panel.
        self._sync_filter_model()

    def _sync_filter_model(self):
        self.set_value(getattr(self._filter_panel.get_filters(), self._filter_setting))
        self.update()


class _FilterInput(_FilterModel, ui.input):
    def _sync_filter_model(self):
        super()._sync_filter_model()
        # Input's Vue component owns inputValue separately from its value prop.
        # A rejected packet can leave the prop unchanged, so its watch would
        # not run. This is the same public component method used by Input.
        self.run_method('updateValue')


class _FilterNumber(_FilterModel, ui.number):
    pass


class _FilterCheckbox(_FilterModel, ui.checkbox):
    pass


class _BookmarkSlider(ui.slider):
    """Keep throttled preview updates inside this panel's visible generation."""

    def __init__(self, panel):
        self._filter_panel = panel
        super().__init__(min=0, max=1000, step=50, value=0)

    def on(self, type, handler=None, args=None, **kwargs):
        # ValueElement registers this one built-in handler during construction.
        # Supply its browser token before normalization/registration, so there
        # is no second unguarded model handler and no listener-table mutation.
        if type == f'update:{self.VALUE_PROP}':
            kwargs['js_handler'] = self._filter_panel._bookmark_event_js(self.html_id)
        return super().on(type, handler, args, **kwargs)

    def _event_args_to_value(self, message):
        panel, args = self._filter_panel, message.args
        if panel._bookmark_event_current(args):
            value = args.get('value')
            if type(value) in (int,float) and 0 <= value <= 1000 and not value % 50:
                return int(value)
        # refresh already synchronized a clear/preset/numeric edit. A stale
        # packet must also leave any newer, valid in-progress preview alone.
        return self.value


class LibraryFilterPanel:
    """One panel and one condition set shared by the two library subviews."""

    def __init__(self, *, get_filters: Callable[[], LibraryFilters],
                 on_change: Callable[[LibraryFilters], bool],
                 can_edit: Callable[[], bool], body, now_parent, get_authors=lambda: {}) -> None:
        self.get_filters = get_filters
        self.on_change = on_change
        self.can_edit = can_edit
        self.now_parent = now_parent
        self.get_authors = get_authors
        self.opened = False
        self.subview = "downloaded"
        self._session = object()
        self._epoch = uuid4().hex
        self._bookmark_revision = uuid4().hex
        self._toggles = []
        self._date_preset = None
        self._calendar_token = None
        self.calendar_dialog = None
        self.calendar_picker = None
        self.calendar_cancel = None
        with now_parent:
            self.now = ui.row().classes("pv-library-filter-now w-full").props(
                "data-testid=library-filter-now")
        with body:
            with ui.column().classes("pv-library-filter-panel").props(
                "data-testid=library-filter-panel aria-label=ライブラリの絞り込み") as self.root:
                with ui.row().classes("items-center justify-between w-full"):
                    ui.label("絞り込み").classes("pv-library-heading")
                    self.clear_button = ui.button("条件をクリア").props(
                        "flat dense no-caps data-testid=library-filter-clear")
                seats = (
                    ("sort", "01", "並び順", "ここには無い →", "一覧上部の並び替えボタンで指定します"),
                    ("date", "02", "期間・日付", "利用可能", "投稿日の日付部分で絞り込みます"),
                    ("bookmark", "03", "ブクマ数", "利用可能", "取得済みのブクマ数が下限以上の作品"),
                    ("tags_include", "04", "タグ含む", "ここには無い →", "下の「タグ」欄で＋／−を切り替えます"),
                    ("tags_exclude", "05", "タグ除く", "ここには無い →", "下の「タグ」欄で＋／−を切り替えます"),
                    ("per_author", "06", "作者あたり上限", "仕様上不要", "1作者あたりの表示件数を絞る機能はありません"),
                    ("exclude", "07", "除外", "利用可能", "AI判定が取得できた作品を除外します"),
                    ("type_format", "08", "種別・形式", "仕様上不要", "原版には作品の種別で絞り込む機能がありません"),
                )
                self._sort_labels = None
                for key, number, title, state, reason in seats:
                    with ui.column().classes("pv-library-filter-seat").style("flex:0 0 auto").props(
                            f"data-testid=library-filter-seat-{key}"):
                        ui.label(f"{number}  {title}").classes("pv-subtitle")
                        badge = ui.label(state).classes("pv-library-filter-state")
                        explanation = ui.label(reason).classes("pv-library-filter-reason")
                        if key == "sort":
                            self._sort_labels = badge, explanation
                        if key == "date":
                            with ui.row():
                                self.date_buttons=[]
                                for years,label in [(1,'1年'),(3,'3年'),('custom','期間指定'),(None,'すべて')]:
                                    button=ui.button(label).props(f'flat dense data-testid=library-date-preset-{years or "all"}')
                                    self.date_buttons.append((button,years))
                            with ui.row().classes('items-end w-full no-wrap'):
                                self.date_from=_FilterInput(self,'date_from','開始日',placeholder='YYYY-MM-DD').classes('flex-1 min-w-0').props('outlined dense data-testid=library-date-from')
                                self.date_from_button=ui.button(icon='calendar_month').props(
                                    'flat round dense aria-label=開始日のカレンダーを開く data-testid=library-date-from-calendar')
                            with ui.row().classes('items-end w-full no-wrap'):
                                self.date_to=_FilterInput(self,'date_to','終了日',placeholder='YYYY-MM-DD').classes('flex-1 min-w-0').props('outlined dense data-testid=library-date-to')
                                self.date_to_button=ui.button(icon='calendar_month').props(
                                    'flat round dense aria-label=終了日のカレンダーを開く data-testid=library-date-to-calendar')
                        elif key == "bookmark":
                            self.bookmark_min=_FilterNumber(self,'bookmark_min','ブクマ数の下限',value=0,min=0,precision=0).props('outlined dense data-testid=library-bookmark-min')
                            self.bookmark_slider=_BookmarkSlider(self).classes('w-full').props(
                                'label aria-label=ブクマ数の下限スライダー data-testid=library-bookmark-slider')
                            self.bookmark_value=ui.label('指定なし').classes('pv-subtitle').props(
                                'role=status data-testid=library-bookmark-value')
                            ui.label('スライダーは0〜1,000（50刻み）。数値欄では任意の下限を指定できます。').classes('pv-library-filter-reason')
                            self.bookmark_buttons=[]
                            with ui.row():
                                for value in (0,100,500,1000):
                                    button=ui.button('指定なし' if value==0 else f'{value:,}+').props(f'flat dense data-testid=library-bookmark-preset-{value}')
                                    self.bookmark_buttons.append((button,value))
                        elif key == "exclude":
                            self.exclude_ai=_FilterCheckbox(self,'exclude_ai','AI作品を除外').props('data-testid=library-exclude-ai')
                ui.separator()
                ui.label('作者').classes('pv-library-heading')
                self.author_input=ui.input(placeholder='作者名の一部を入力').props('outlined dense data-testid=library-author-filter-input maxlength=256')
                self.author_add=ui.button('作者を追加').props('flat dense data-testid=library-author-filter-add')
                self.author_chips=ui.row().classes('w-full')
                self.author_notice=ui.label().props('role=status data-testid=library-author-filter-notice').classes('pv-subtitle')
                self.include_r18=_FilterCheckbox(self,'include_r18','R-18 を含める',value=True).props('data-testid=library-include-r18')
                self.unread_only=_FilterCheckbox(self,'unread_only','未読のみ').props('data-testid=library-unread-only')
                self.unfetched_only=_FilterCheckbox(self,'unfetched_only','未取得のみ（マイブックマーク）').props('data-testid=library-unfetched-only')
                ui.label('情報が未取得の作品は、ブクマ数下限・開始日を指定すると対象外です。未読・R-18・AIは原版と同じ未知値の扱いです。').classes('pv-library-filter-reason')
                with ui.column().classes("pv-library-tag-controls w-full").props(
                        "data-testid=library-tag-controls"):
                    with ui.row().classes("items-center justify-between w-full"):
                        ui.label("タグ").classes("pv-library-heading")
                        self.mode_button = ui.button("すべて含む").props(
                            "flat dense no-caps data-testid=library-filter-mode")
                    self.tag_input = ui.input(placeholder="タグを追加").props(
                        "outlined dense data-testid=library-filter-tag-input maxlength=256")
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
        self._wire(self.author_add,'click',self.add_author,needs_panel=True,read_value=True,field=self.author_input)
        self._wire(self.author_input,'keydown.enter',self.add_author,needs_panel=True,read_value=True,field=self.author_input)
        for name in ('date_from','date_to'):
            self._wire(getattr(self,name+'_button'),'click',
                       lambda field=name:self.open_calendar(field),needs_panel=True)
        self._wire_bookmark_slider()
        for button,years in self.date_buttons:
            self._wire(button,'click',lambda years=years:self.date_preset(years),needs_panel=True)
        for button,value in self.bookmark_buttons:
            self._wire(button,'click',lambda value=value:self.change_setting('bookmark_min',value),needs_panel=True)
        self.refresh()

    @staticmethod
    def _calendar_date(value):
        """Calendar-only validation; legacy manual text comparisons stay intact."""
        if type(value) is not str or len(value) != 10:
            return None
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            return None
        if parsed.isoformat() != value or not date(2007,1,1) <= parsed <= date.today():
            return None
        return parsed

    def _close_calendar(self):
        dialog = self.calendar_dialog
        self._calendar_token = None
        self.calendar_dialog = self.calendar_picker = self.calendar_cancel = None
        if dialog is not None and not dialog.is_deleted:
            dialog.close()
            dialog.delete()

    def _dismiss_calendar(self, token):
        if self._calendar_token == token:
            self._close_calendar()

    def open_calendar(self, field):
        if field not in ('date_from','date_to') or not self._allowed(needs_panel=True):
            return
        self._close_calendar()
        token = (field, self._epoch, uuid4().hex)
        self._calendar_token = token
        initial = self._calendar_date(getattr(self.get_filters(),field))
        today = date.today()
        with self.root:
            dialog = ui.dialog().props('data-testid=library-date-dialog')
        self.calendar_dialog = dialog
        with dialog, ui.card():
            ui.label('開始日を選択' if field == 'date_from' else '終了日を選択').classes('text-subtitle1')
            picker = ui.date(initial.isoformat() if initial is not None else None,
                             mask='YYYY-MM-DD').props('data-testid=library-date-picker')
            self.calendar_picker = picker
            picker.props['navigation-min-year-month'] = '2007/01'
            picker.props['navigation-max-year-month'] = today.strftime('%Y/%m')
            picker.props['default-year-month'] = (initial or today).strftime('%Y/%m')
            picker.props[':options'] = (
                "value => { const day = String(value).replaceAll('/', '-'); "
                f"return day >= '2007-01-01' && day <= '{today.isoformat()}'; }}")
            ui.label('2007-01-01 から今日まで選択できます。').classes('pv-library-filter-reason')
            self.calendar_cancel = ui.button('キャンセル').props('flat data-testid=library-date-cancel')
        pinned = json.dumps(dict(zip(('field','epoch','nonce'),token)),separators=(',',':'))
        picker.on('update:model-value',
                  lambda message, captured=token:self._calendar_pick(captured,message.args),
                  js_handler=f"value => {{ const opened = {pinned}; emit({{...opened,value}}); }}")
        self.calendar_cancel.on('click',lambda _event,captured=token:self._dismiss_calendar(captured))
        # A late hide from an older, deleted dialog cannot close a reopened one.
        dialog.on('hide',lambda _event,captured=token:self._dismiss_calendar(captured))
        dialog.open()

    def _calendar_pick(self, token, args):
        if (not isinstance(args,dict) or
                tuple(args.get(key) for key in ('field','epoch','nonce')) != token or
                self._calendar_token != token or token[1] != self._epoch or
                not self._allowed(needs_panel=True) or self.calendar_dialog is None or
                self.calendar_dialog.is_deleted or not self.calendar_dialog.value):
            return
        selected = self._calendar_date(args.get('value'))
        if selected is None:
            return
        field = token[0]
        self._close_calendar()
        self.change_setting(field,selected.isoformat())

    def _bookmark_event_js(self, slider_id):
        return f"""value => {{
            const panel=document.getElementById('{self.root.html_id}');
            const slider=document.getElementById('{slider_id}');
            if(panel && slider) emit({{epoch:panel.dataset.filterEpoch,
                revision:slider.dataset.bookmarkRevision,value}});
        }}"""

    def _bookmark_event_current(self, args):
        return (isinstance(args,dict) and args.get('epoch') == self._epoch and
                args.get('revision') == self._bookmark_revision and
                self._allowed(needs_panel=True))

    def _wire_bookmark_slider(self):
        def release(message):
            args = message.args
            if not self._bookmark_event_current(args):
                return
            value = args.get('value')
            if type(value) not in (int,float) or not 0 <= value <= 1000 or value % 50:
                return
            self.change_setting('bookmark_min',int(value))
        # Guarded model updates move the thumb during dragging.
        # Only QSlider change (release) may refilter the library.
        self.bookmark_slider.on('change',release,
            js_handler=self._bookmark_event_js(self.bookmark_slider.html_id))

    def _setting_event_js(self):
        return f"""value => {{
            const panel=document.getElementById('{self.root.html_id}');
            if(panel) emit({{epoch:panel.dataset.filterEpoch,value}});
        }}"""

    def change_setting(self,name,value):
        if not self._allowed(needs_panel=True):return
        if name in ('date_from','date_to'):
            if not isinstance(value,str) or len(value)>10:return
            self._close_calendar()
            self._date_preset = 'custom'
        elif name=='bookmark_min':
            if value in ('',None):value=0
            try:
                number=float(value)
                if not number.is_integer() or number<0:return
                value=int(number)
            except (TypeError,ValueError,OverflowError):return
        elif name in ('include_r18','exclude_ai','unread_only','unfetched_only'):
            if type(value) is not bool:return
            if name=='unfetched_only' and self.subview!='bookmarks':return
        else:return
        self.on_change(replace(self.get_filters(),**{name:value}))
        self.refresh()

    def date_preset(self,years):
        if not self._allowed(needs_panel=True) or years not in (1,3,'custom',None):return
        self._date_preset = years
        if years == 'custom':
            self.invalidate()
            return
        today=date.today()
        start='' if years is None else today.replace(year=today.year-years,
            day=min(today.day,calendar.monthrange(today.year-years,today.month)[1])).isoformat()
        self.on_change(replace(self.get_filters(),date_from=start,date_to=''))
        self.invalidate()

    def add_author(self,*,value=None):
        if not self._allowed(needs_panel=True):return
        query=(self.author_input.value or '') if value is None else value
        query=query.strip()
        if not query:return
        author=next((key for key,name in self.get_authors().items() if query in name),None)
        if author is None:
            self.author_notice.set_text('一致する作者が見つかりません')
            return
        self.on_change(replace(self.get_filters(),authors=self.get_filters().authors | {author}))
        self.author_input.set_value('');self.author_notice.set_text('');self.invalidate()

    def remove_author(self,author,session):
        if not self._allowed(session):return
        self.on_change(replace(self.get_filters(),authors=self.get_filters().authors-{author}))
        self.invalidate()

    def register_toggle(self, button) -> None:
        self._toggles.append(button)
        self._wire(button, "click", self.toggle)
        self._sync_toggles()

    def _wire(self, element, event, callback, *, needs_panel=False, read_value=False, field=None) -> None:
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
        value_js = (f"const field=document.getElementById('{(field or self.tag_input).html_id}');"
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
        count = len(filters.include) + len(filters.exclude) + len(filters.authors) + sum(bool(v) for v in (filters.date_from,filters.date_to,filters.bookmark_min,not filters.include_r18,filters.exclude_ai,filters.unread_only,filters.unfetched_only))
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
        self._close_calendar()
        self._session = object()
        self._epoch = uuid4().hex
        self.root.props["data-filter-epoch"] = self._epoch
        self.refresh()

    def retire(self) -> None:
        """Reject old controls without creating new UI during client deletion."""
        self._close_calendar()
        self._session = object()
        self._epoch = uuid4().hex

    def add(self, _event=None, *, value: str | None = None) -> None:
        if not self._allowed(needs_panel=True):
            return
        tag = normalize_tag((self.tag_input.value or "") if value is None else value)
        if not tag:
            return
        current = self.get_filters()
        candidate = replace(current,include=current.include | {tag},exclude=current.exclude - {tag})
        self.on_change(candidate)
        self.tag_input.set_value("")
        self.invalidate()

    def flip(self, tag: str, session) -> None:
        if not self._allowed(session, needs_panel=True):
            return
        current = self.get_filters()
        if tag in current.include:
            candidate = replace(current,include=current.include - {tag},exclude=current.exclude | {tag})
        elif tag in current.exclude:
            candidate = replace(current,include=current.include | {tag},exclude=current.exclude - {tag})
        else:
            return
        self.on_change(candidate)
        self.invalidate()

    def remove(self, tag: str, session) -> None:
        if not self._allowed(session):
            return
        current = self.get_filters()
        self.on_change(replace(current,include=current.include - {tag},exclude=current.exclude - {tag}))
        self.invalidate()

    def clear(self, _event=None, *, session=None) -> None:
        if not self._allowed(session):
            return
        self._date_preset = None
        self.on_change(LibraryFilters())
        self.tag_input.set_value("")
        self.author_input.set_value("")
        self.author_notice.set_text("")
        self.invalidate()

    def toggle_mode(self) -> None:
        if not self._allowed(needs_panel=True):
            return
        current = self.get_filters()
        self.on_change(replace(current,mode="or" if current.mode == "and" else "and"))
        self.invalidate()

    def refresh(self) -> None:
        filters, session = self.get_filters(), self._session
        self.tags.clear()
        self.now.clear()
        self.author_chips.clear()
        for name in ('date_from','date_to','bookmark_min','include_r18','exclude_ai','unread_only','unfetched_only'):
            getattr(self,name).set_value(getattr(filters,name))
        # Preserve the exact numeric threshold, including manual values outside
        # the original slider's range; only the visible thumb is clamped.
        # A numeric edit/preset can keep the panel epoch. Retire only slider
        # packets here, without rejecting later keystrokes in the number field.
        self._bookmark_revision = uuid4().hex
        self.bookmark_slider.props['data-bookmark-revision'] = self._bookmark_revision
        self.bookmark_slider.set_value(min(1000,filters.bookmark_min))
        self.bookmark_value.set_text('指定なし' if filters.bookmark_min == 0 else f'{filters.bookmark_min:,}+')
        for button,value in self.bookmark_buttons:
            selected = filters.bookmark_min == value
            button.props['aria-pressed'] = str(selected).lower()
            button.props['flat'] = not selected
        if self._date_preset is None and (filters.date_from or filters.date_to):
            self._date_preset = 'custom'
        for button,years in self.date_buttons:
            selected = self._date_preset == years
            button.props['aria-pressed'] = str(selected).lower()
            button.props['flat'] = not selected
        self.unfetched_only.set_visibility(self.subview=='bookmarks')
        names=self.get_authors()
        for author in sorted(filters.authors):
            with self.author_chips:
                ui.button(names.get(author,author)+' ×',on_click=lambda _,a=author,s=session:self.remove_author(a,s)).props('flat dense data-testid=library-author-filter-remove')
            with self.now:
                ui.button('作者: '+names.get(author,author)+' ×',on_click=lambda _,a=author,s=session:self.remove_author(a,s)).props('flat dense data-testid=library-author-now-chip')
        with self.now:
            for active,label in [(bool(filters.date_from or filters.date_to),f'投稿日: {filters.date_from or "…"}〜{filters.date_to or "…"}'),
                (bool(filters.bookmark_min),f'ブクマ: {filters.bookmark_min}+'),(not filters.include_r18,'R-18除外'),
                (filters.exclude_ai,'AI除外'),(filters.unread_only,'未読のみ'),(filters.unfetched_only and self.subview=='bookmarks','未取得のみ')]:
                if active:ui.label(label).classes('pv-subtitle')
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
        if filters != LibraryFilters():
            with self.now:
                ui.button("すべて解除", on_click=lambda _, s=session: self.clear(session=s)).props(
                    "flat dense no-caps data-testid=library-filter-now-clear")
        self.now.set_visibility(filters != LibraryFilters())
        self.now_parent.set_visibility(filters != LibraryFilters())
        self.mode_button.set_text("すべて含む" if filters.mode == "and" else "どれか")
        self.mode_button.props["data-mode"] = filters.mode
        self._sync_toggles()
