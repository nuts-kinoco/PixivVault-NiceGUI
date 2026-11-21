"""架空作品のみを使う学習デモ画面。ブラウザ送信時点の作品世代を検証する。"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections import OrderedDict
from concurrent.futures import Future
from datetime import datetime
from uuid import uuid4
from ..learning_review import ReviewFilter, PAGE_SIZE, requeue_outcome, review_trace
from ..learning_trend_layout import PreferenceCapacity, displayed_rows, layout_mode
from ..discovery_refill import DiscoveryRefill
from ..discovery_description import layout as summary_layout, is_blank as summary_is_blank, MAX_DESCRIPTION
from ..discovery_summary_fetch import fetch_summary, SummaryFetchFailed

from nicegui import background_tasks, ui

from ..image_events import source_matches
from ..learning import LearningState, MemoryJudgmentLedger, SaveCommand
from ..services import ApplicationServices
from ..viewer_metadata import pixiv_url
from .learning_toast import LearningToast
from .verified_image import VerifiedImage


# 元 discovery_tab.py:4358-4361
EMPTY_TITLE = "表示できる候補がありません"
EMPTY_BODY = ("作品を「見送る」「合わない」「いいね」「SuperLike」で判定していくと、\n"
              "好みの傾向を学習し、次第に精度の高いおすすめが表示されるようになります。")


class _ReviewSection:
    """Present review as a tab mode while retaining the existing close contract."""
    def __init__(self, view, element):
        self.view, self.element = view, element

    def open(self):
        self.view.swipe_area.set_visibility(False)
        self.element.set_visibility(True)
        self.view.root.props('data-review-open=true')
        self.view.swipe_button.props('aria-pressed=false')
        self.view.review_button.props('aria-pressed=true')

    def close(self):
        self.view._close_review()


class LearningView:
    CHOICES = (
        ("見送る", "skip", "arrowleft", "a", "1"),
        ("合わない", "hate", "arrowdown", "s", "2"),
        ("いいね", "like", "arrowright", "d", "3"),
        ("SuperLike", "superLike", "arrowup", "w", "4"),
    )

    def __init__(self, services: ApplicationServices, *, on_open_preferences=None) -> None:
        self.services = services
        self.ledger = MemoryJudgmentLedger()
        self.discovery = getattr(services, "discovery", None)
        self.storage = self.discovery or services.learning
        self._artworks = tuple(services.artworks) if self.discovery is None else ()
        self._images = services.images
        self._discovery_pin = None
        self._prefetch = None
        self._prefetch_pin = None
        self._prefetch_task = None
        self._refill = DiscoveryRefill()
        self._refill_epoch = 0
        self._refill_task = None
        self._refill_wake = asyncio.Event()
        self._refill_merge_revision = 0
        self._refill_retired_pins = []
        self._refill_clock = time.monotonic
        self._initial_epoch = 0
        self._initial_task = None
        self._initial_done = False
        self._managed_busy = False
        self._managed_operations = {}
        self._discovery_frames = {}
        self._discovery_image_ids = frozenset()
        self._trend_open = False
        self._trend_mode = 'rail'
        self._trend_capacity = PreferenceCapacity()
        self._trend_snapshot = None
        self._seed_watch_task = None
        self._tagger_rank_epoch = 0
        self._trend_visible_rows = 0
        self._summary_open = False
        self._summary_page = 0
        self._summary_pages = []
        self._summary_page_text = ""
        self._summary_layout_cache = OrderedDict()
        self._summary_blank_cache = OrderedDict()
        self._summary_viewport = (1280, 720)
        self._summary_mode = "rail"
        self._summary_cols = 1
        self._summary_rows = 1
        self._summary_identity = None
        self._summary_descriptions = {}
        # GET-derived text belongs to the exact (work,author), not to a batch
        # snapshot; a subsequent None snapshot must not erase it.
        self._summary_fetched = {}
        self._summary_pending = set()
        self._summary_failed = set()
        self._summary_retrying = False
        self._summary_epoch = 0
        self._managed_owner = uuid4().hex
        self._settings_epoch=0
        self._settings_busy=False
        self._settings_loading=False
        self._settings_open=False
        self._settings_snapshot=None
        self._settings_operations={}
        self.state = LearningState(services.artworks if self.storage is None else (), self.ledger)
        self._session_id: str | None = None
        self._storage_busy = False
        self._storage_ready = self.storage is None
        self._baseline_count = 0
        self._judged_before_session = 0
        self._failed_command: SaveCommand | None = None
        self._notice = ""
        self._save_task = None
        self._reason_tasks: set = set()
        self._refill_notice_shown = False
        self._superlike_used = 0
        self._review_session_id = None
        self._review_epoch = 0
        self._review_busy = False
        self._review_mutating = False
        self._review_open = False
        self._review_rows = ()
        self._review_details = {}
        self._review_records = {}
        self._review_image_pin = None
        self._review_detail_titles = {}
        self._review_detail_failed = set()
        self._review_detail_pending = set()
        self._review_detail_lock = asyncio.Lock()
        self._bookmark_busy = False
        self._bookmark_token = uuid4().hex
        self._bookmark_confirmed = set()
        self._bookmark_new = set()
        self._opened_work_ids: set[str] = set()
        self._bookmark_uncertain = {}
        self._review_undone = {}
        self._review_outcomes = {}
        self._review_queue_ids = set()
        self._review_operations = {}
        self._review_watched_jobs = {}
        self._review_seen_jobs = {}
        self._review_refresh_busy = False
        self._review_pages = {True: 0, False: 0}
        self._review_started = datetime.now()
        self.active = False
        self._image_token = None
        self._image_url = ""
        self.client = ui.context.client
        self.client.on_delete(self.close)

        with ui.column().classes("pv-learning w-full gap-3") as self.root:
            if self.discovery is None:
                ui.label("学習（デモ）").classes("pv-view-title")
            description = (
                "判定はデモ専用フォルダーに保存します。Undoはこの画面での直前の判定だけが対象です。"
                if self.storage is not None else
                "判定はこの画面内のデモ台帳だけに記録されます。実データへの保存はありません。"
            )
            if self.discovery is not None:
                description = "発掘の判定は専用保存先に記録します。設定された取得元から初回候補を自動で読み込み、この画面を開いている間に候補を補充します。作品保存は明示操作で実行します。"
            if self.discovery is None:
                ui.label(description).classes("pv-subtitle")
            stage_start = len(self.root.default_slot.children)
            with ui.element('div').classes('w-full relative') as self.candidate_header:
                with ui.row().classes('w-full items-center gap-2'):
                    self.title = ui.label().classes("pv-view-title")
                    self.summary_button = ui.button('概要 ▸').props(
                        'flat dense data-testid=learning-summary-toggle aria-expanded=false')
                    self.summary_button.on('click', self._on_summary_click,
                        js_handler="""(event) => {
                          const anchor=event.currentTarget.closest('.relative');
                          const top=Math.ceil((anchor?.getBoundingClientRect().top ?? 0)+34);
                          const button=event.currentTarget;
                          emit({width:window.innerWidth,height:window.innerHeight,top,
                            work_id:button.dataset.workId,author_id:button.dataset.authorId,
                            generation:Number(button.dataset.generation),
                            summary_epoch:Number(button.dataset.summaryEpoch)});
                        }""")
                    self.summary_status = ui.label('').classes('pv-subtitle').props(
                        'role=status data-testid=learning-summary-status')
                with ui.card().classes('pv-learning-summary-board').props(
                        'role=region aria-label=作品の概要 data-testid=learning-summary-board').style(
                        'position:absolute;right:0;top:34px;z-index:25;'
                        'width:min(276px,calc(100vw - 32px));max-width:calc(100vw - 32px);'
                        'background:var(--pv-raised);border:1px solid var(--pv-border);'
                        'overflow:hidden;box-sizing:border-box;padding:10px;gap:4px;') as self.summary_board:
                    with ui.row().classes('w-full items-center justify-between'):
                        ui.label('概要').classes('pv-card-title')
                        self.summary_close_button=ui.button('閉じる',on_click=self.close_summary).props(
                            'flat dense data-testid=learning-summary-close')
                    self.summary_body=ui.element('div').classes(
                        'w-full flex-1 min-h-0 overflow-hidden').props(
                        'data-testid=learning-summary-body')
                    with ui.row().classes('w-full items-center justify-between') as self.summary_pager:
                        self.summary_page_label=ui.label().classes('pv-subtitle').props(
                            'data-testid=learning-summary-pages')
                        with ui.row().classes('items-center gap-0'):
                            self.summary_prev=ui.button(icon='chevron_left',
                                on_click=lambda:self.step_summary(-1)).props(
                                'flat dense round aria-label=前の頁 data-testid=learning-summary-prev')
                            self.summary_next=ui.button(icon='chevron_right',
                                on_click=lambda:self.step_summary(1)).props(
                                'flat dense round aria-label=次の頁 data-testid=learning-summary-next')
                self.summary_board.set_visibility(False)
                self.summary_pager.set_visibility(False)
            # 探索枠から出た候補にだけ添える小さなピル（元 discovery_tab.py:2918-2926）。確実枠・枠なし・画像失敗では出さない。
            self.frame_label = ui.label().props("data-testid=learning-frame-badge").style(
                "background:var(--pv-surface);border:1px solid var(--pv-border);border-radius:999px;"
                "padding:3px 8px;font-size:10px;color:var(--pv-muted);align-self:flex-start;width:fit-content;")
            self.frame_label.set_visibility(False)
            self.progress = ui.label().classes("pv-counter")
            # 通知帯は画像の右下に重ねる（元 discovery_tab.py:241-244）。判定ボタンの位置は動かさない。
            with ui.element("div").classes("w-full relative").style("min-height:96px"):
                with ui.element('div').classes('w-full relative').style('max-width:560px'):
                    self.picture = VerifiedImage(alt="判定対象の作品画像").classes("pv-view-art")
                    if self.discovery is not None:
                        self.open_pixiv_button = ui.button(icon='open_in_new', color=None).props(
                            'flat dense round target=_blank rel="noopener noreferrer" '
                            'aria-label="pixivで作品ページを開く" data-testid=learning-open-pixiv'
                        ).style('position:absolute;top:4px;right:4px;min-width:21px;min-height:21px;'
                                'width:21px;height:21px;font-size:8px;'
                                'background:var(--pv-raised);color:var(--pv-muted)')
                        self.open_pixiv_button.tooltip('クリックでpixivの作品ページを開く')
                        open_js = self._click_js('open')
                        self.open_pixiv_button.on('click', self._open_learning_pixiv, js_handler=open_js)
                        self.open_pixiv_button.on('auxclick', js_handler=open_js)
                        self.open_pixiv_button.on('contextmenu', js_handler=open_js)
                        self.open_pixiv_button.set_visibility(False)
                if self.discovery is not None:
                    self._build_candidate_status_card()
                self.toast = LearningToast(on_expired_undo=self._on_expired_undo)
            self.picture.on('click', self._open_learning_picture, js_handler=self._click_js('open'))
            self.status = ui.label().classes("pv-subtitle pv-learning-status").props("role=status aria-live=polite")
            # 元 discovery_tab.py:4358-4365 の空状態の案内（候補が無いとき、何をすると何が起きるか）。
            with ui.column().classes("items-center gap-1").props("data-testid=learning-empty") as self.empty_card:
                ui.icon("explore").classes("text-4xl")
                ui.label(EMPTY_TITLE).classes("pv-view-title")
                ui.label(EMPTY_BODY).classes("pv-subtitle").style("white-space:pre-line;text-align:center")
                ui.separator().classes('w-full')
                with ui.row().classes('items-center justify-center flex-wrap'):
                    ui.label('画像からのタグ補完にはローカルモデルの導入（任意）が利用できます。').style('font-size:11px;color:var(--pv-muted)')
                    self.tagger_settings_button=ui.button('設定を開く', on_click=on_open_preferences).props('flat dense data-testid=learning-tagger-settings')
                    self.tagger_settings_button.set_enabled(on_open_preferences is not None)
            self.empty_card.set_visibility(False)
            with ui.row().classes("items-center gap-2") as self.judgment_controls:
                self.buttons = {}
                for label, choice, *_ in self.CHOICES:
                    button = ui.button(label, color=None).props(
                        "no-caps" if choice == "like" else "outline no-caps"
                    ).classes(f"pv-judgment pv-judgment-{choice}")
                    button.on("click", lambda event, c=choice: self._on_judgment(c, event),
                              js_handler=self._click_js('block-open'))
                    self.buttons[choice] = button
            # 元 discovery_tab.py:2672-2680: 判定ボタンの真下の情報行。高さを固定し、出し入れでボタンを動かさない。
            with ui.row().classes("w-full items-center").style("height:20px"):
                self.superlike_used_label = ui.label().classes("pv-subtitle").style("font-size:10px").props(
                    "data-testid=learning-superlike-used")
            self.superlike_used_label.set_visibility(False)
            with ui.row().classes("items-center gap-2"):
                self.undo_button = ui.button("ひとつ戻す（Undo）", color=None).props("flat no-caps").classes("pv-undo")
                self.undo_button.on("click", self._on_undo, js_handler=self._click_js('block-open'))
                self.retry_button = ui.button("画像を再読込", on_click=self.request_image).props("flat")
                self.save_retry_button = ui.button("保存を再試行", on_click=self.retry_save).props("flat")
                self.reload_button = ui.button("保存状態を再読込").props("flat")
                self.reload_button.on("click", self._on_reload_click, js_handler=self._click_js('block-open'))
                self.bookmark_button = ui.button('pixiv', icon='favorite_border').props(
                    'flat dense no-caps data-testid=learning-bookmark')
                self.bookmark_button.on('click', self._on_learning_bookmark, js_handler=self._bookmark_click_js())
                self.bookmark_tip = self.bookmark_button.tooltip('絵が表示されてから押せます')
            self.judgment_help = ui.label("矢印キー / WASD / 1〜4 で判定。画面を選択して操作してください。").classes("pv-subtitle")
            if self.discovery is not None:
                # 元 discovery_tab.py:3295-3302 の「探索の設定 ▸」引き出し。畳んだ行だけが流れに入り、開いた板は重ね置き。
                ui.label('判定は保存され、次に出す候補の並べ替えに反映されます。').classes('pv-subtitle')
                with ui.element('div').classes('relative w-full'):
                    with ui.card().classes('gap-2').props('role=region aria-label=探索の設定 data-testid=discovery-settings-panel').style(
                            'position:absolute;left:0;top:100%;z-index:25;width:min(320px,calc(100vw - 32px));'
                            'background:var(--pv-overlay);border:1px solid var(--pv-border);box-sizing:border-box;'
                            'padding:10px;') as self.settings_panel:
                        self.settings_subject,self.settings_subject_text=self._explore_slider('知らないジャンルを見る割合','discovery-settings-subject')
                        self.settings_style,self.settings_style_text=self._explore_slider('知らない絵柄を見る割合','discovery-settings-style')
                        self.settings_mode=ui.select({'all':'すべて','safe':'全年齢','r18':'R-18'},value='all',label='検索対象',
                            on_change=lambda _:self._settings_commit()).props('outlined dense data-testid=discovery-settings-mode').classes('w-full')
                        self.settings_status=ui.label().classes('pv-subtitle').props('role=status data-testid=discovery-settings-status')
                    self.settings_panel.set_visibility(False)
                    self.settings_button = ui.button(on_click=self.toggle_discovery_settings).props(
                        'flat dense no-caps align=between aria-expanded=false data-testid=discovery-settings-open')
                    with self.settings_button:
                        ui.label('探索の設定').classes('pv-subtitle').style('flex:1 1 auto;text-align:left')
                        self.settings_arrow=ui.icon('keyboard_arrow_right').style('font-size:15px;color:var(--pv-muted)')
                        ui.tooltip('どのくらい知らないものを見るかを変えます')
                self.fetch_button = ui.button('候補を取得', on_click=self.fetch_discovery).props('flat data-testid=discovery-fetch')
                self.refill_status = ui.label().classes('pv-subtitle').props('role=status data-testid=discovery-refill-status')
                self.managed_button = ui.button('作品を保存').props('flat data-testid=discovery-save')
                self.managed_button.on('click', self._on_managed_save, js_handler=self._click_js())
                with ui.row().classes('w-full items-center') as self.trend_header:
                    ui.label('好みの傾向').classes('pv-card-title')
                    self.trend_label = ui.label().classes('pv-subtitle').props('data-testid=discovery-trend')
                    self.trend_button = ui.button('傾向を見る',on_click=self.toggle_trend).props(
                        'flat dense aria-expanded=false data-testid=discovery-trend-toggle')
                    self.trend_button.tooltip('判定すると自動で閉じます')
                self.trend_content = ui.column().classes('w-full gap-1').props(
                    'data-testid=discovery-trend-panel')
                stage_children = tuple(self.root.default_slot.children[stage_start:])
                with ui.element('div').classes('pv-trend-layout w-full').props('data-mode=rail') as self.trend_layout:
                    self.trend_main = ui.column().classes('pv-trend-main w-full gap-3')
                    with ui.column().classes('pv-trend-side w-full gap-1') as self.trend_side:
                        self.trend_viewport = ui.element('div').classes('pv-trend-viewport w-full')
                for element in stage_children:
                    if element is self.trend_header:
                        element.move(self.trend_side, target_index=0)
                    elif element is self.trend_content:
                        element.move(self.trend_viewport)
                    else:
                        element.move(self.trend_main)
                self.trend_button.set_visibility(False)
            self.review_button = ui.button('振り返り', on_click=self.open_review).props('flat data-testid=learning-review-open')
            from .view_history_view import ViewHistoryView
            self.view_history = ViewHistoryView(self, self.storage)
            from .learning_reset import LearningResetView
            self.reset_view = LearningResetView(self, self.storage) if self.storage is not None else None

            self.review_button.set_visibility(self.storage is not None)
            self.swipe_area = ui.column().classes('w-full gap-3')
            for element in tuple(self.root.default_slot.children):
                if element is not self.swipe_area and element is not self.review_button:
                    element.move(self.swipe_area)
            with ui.row().classes('w-full items-center gap-1') as self.review_modes:
                self.swipe_button = ui.button('めくる', on_click=self.return_to_swipe).props(
                    'flat no-caps aria-pressed=true data-testid=learning-swipe-mode')
                self.review_button.props('aria-pressed=false')
                self.review_button.move(self.review_modes)
            self.review_modes.move(self.root, 0)
            self.swipe_button.set_visibility(self.storage is not None)
        with self.root, ui.column().classes('w-full gap-3').props('data-testid=learning-review-dialog') as self.review_panel:
            self.review_dialog = _ReviewSection(self, self.review_panel)
            with ui.card().classes('w-full').style('max-width:1000px'):
                ui.label('学習の振り返り').classes('pv-view-title')
                ui.label(('専用発掘保存先' if self.discovery is not None else 'デモ専用保存先') + 'の判定です。取消した作品は学習候補へ戻り、元に戻す操作はこの画面を閉じるまで有効です。').classes('pv-subtitle')
                with ui.row().classes('w-full'):
                    self.review_text = ui.input('タイトル・タグで検索', on_change=lambda _: self._review_filter_changed()).props('outlined dense data-testid=learning-review-query')
                    self.review_period = ui.select({'today':'今日','7d':'直近7日','30d':'直近30日','all':'すべて','session':'この学習セッション'},value='all',on_change=lambda _:self._review_filter_changed()).props('outlined dense data-testid=learning-review-period')
                self.review_status = ui.label().props('role=status data-testid=learning-review-status')
                self.review_columns, self.review_bodies = {}, {}
                with ui.row().classes('w-full items-start no-wrap') as self.review_content:
                    for positive, label in ((True, '好意側（いいね／SuperLike）'), (False, '否定側（見送る／合わない）')):
                        with ui.column().classes('flex-1 min-w-0').style('max-height:calc(100dvh - 240px);overflow-y:auto').props('data-testid=learning-review-positive' if positive else 'data-testid=learning-review-negative') as column:
                            self.review_columns[positive] = column
                            ui.label(label).classes('pv-card-title shrink-0')
                            with ui.row().classes('w-full items-center gap-1 shrink-0'):
                                if positive:
                                    self.review_unsaved = ui.checkbox('未保存だけ',value=True,on_change=lambda _:self._review_column_filter_changed(True))
                                    self.review_superlike = ui.checkbox('SuperLikeだけ',on_change=lambda _:self._review_column_filter_changed(True))
                                else:
                                    self.review_hate = ui.checkbox('合わないだけ',on_change=lambda _:self._review_column_filter_changed(False))
                            self.review_bodies[positive] = ui.column().classes('w-full gap-1 shrink-0')
        self.review_panel.set_visibility(False)
        self._review_timer = ui.timer(1.0, self._poll_review_jobs)
        self._local_trend_revision = None
        self._local_revision_task = None

        # NiceGUI 3.18: js_handler は emit(...) へ送る値を組み立てられる。
        # root 配下にフォーカスがある場合だけ拾い、入力中とIME中は除外する。
        self.root.props(f"tabindex=0 data-storage-mode={'sandbox' if self.storage is not None else 'memory'}")
        self.root.on("keydown", self._on_key, js_handler=self._key_js())
        self.root.on('summary-resize', self._on_summary_resize, js_handler="""(event) => {
            const el=event.currentTarget.querySelector('[data-testid="learning-summary-toggle"]');
            const anchor=el?.closest('.relative');
            emit({width:window.innerWidth,height:window.innerHeight,
                 top:Math.ceil((anchor?.getBoundingClientRect().top ?? 0)+34)});
        }""")
        if self.discovery is not None:
            self.root.on('trend-measure', self._on_trend_measure, js_handler='event => emit(event.detail)')
        self.picture.on("load", self._on_loaded)
        self.picture.on("error", self._on_failed, js_handler=self._image_error_js(self.picture.html_id))
        self.set_summary_viewport(1280, 720)
        self._summary_sync()
        self.set_active(False)
        if self.storage is None:
            self.refresh()
        else:
            self.picture.set_visibility(False)
            self.reload_storage()

    @staticmethod
    def _click_js(intent: str = '') -> str:
        return """(event) => {
            const el = event.currentTarget;
            const root = el.closest('.pv-learning');
            const payload = {work_id: el.dataset.workId ?? root?.dataset.workId,
                             generation: Number(el.dataset.generation ?? root?.dataset.generation),
                             operation_id: crypto.randomUUID()};
            """ + {
                'open': """
                    const pending = root?.__pvPendingLearningAction;
                    if (el.dataset.workOpen !== 'true' || root?.__pvFailedImage === payload.generation ||
                        (pending &&
                        pending.generation === payload.generation &&
                        pending.operation_id !== root.dataset.finishedOperation)) {
                        event.preventDefault();
                        return;
                    }
                    if (event.type !== 'click') return;
                """,
                'block-open': 'if (root) root.__pvPendingLearningAction = payload;',
            }.get(intent, '') + """
            emit(payload);
        }"""

    @staticmethod
    def _image_error_js(image_id: str) -> str:
        return f"""(source) => {{
            const image = document.getElementById('{image_id}');
            const root = image?.closest('.pv-learning');
            if (root) root.__pvFailedImage = Number(image.dataset.generation);
            emit(source);
        }}"""

    @staticmethod
    def _bookmark_click_js() -> str:
        return """(event) => {
            const el = event.currentTarget;
            emit({work_id: el.getAttribute('data-work-id'),
                  generation: Number(el.getAttribute('data-generation')),
                  bookmark_token: el.getAttribute('data-bookmark-token')});
        }"""

    @staticmethod
    def _key_js() -> str:
        return """(event) => {
            const tag = event.target?.tagName?.toLowerCase();
            if (event.repeat || event.isComposing || event.keyCode === 229 ||
                event.altKey || event.metaKey ||
                event.currentTarget.getAttribute('data-review-open') === 'true' ||
                ['input', 'textarea', 'select'].includes(tag) ||
                event.target?.isContentEditable ||
                event.target?.closest('[role=dialog]') ||
                event.target?.closest('[role=slider]') ||
                event.target?.closest('[data-testid=discovery-settings-panel]') ||
                !event.currentTarget.getAttribute('data-active') ||
                event.currentTarget.getAttribute('data-active') !== 'true') return;
            const key = event.key.toLowerCase();
            const root = event.currentTarget;
            const save = (input) => {
                const payload = {...input, work_id:root.dataset.workId || '',
                                 generation:Number(root.dataset.generation),
                                 operation_id:crypto.randomUUID()};
                root.__pvPendingLearningAction = payload;
                emit(payload);
            };
            if (event.ctrlKey) {
                if (key !== 'z' || event.shiftKey) return;
                event.preventDefault();
                event.stopPropagation();
                save({action:'undo'});
                return;
            }
            if (key === 'escape' || key === 'esc') {
                if (event.currentTarget.getAttribute('data-summary-open') === 'true') {
                    event.preventDefault();
                    event.stopPropagation();
                    emit({action:'summary-close'});
                }
                return;
            }
            const map = {arrowleft:'skip',a:'skip','1':'skip',
                         arrowdown:'hate',s:'hate','2':'hate',
                         arrowright:'like',d:'like','3':'like',
                         arrowup:'superLike',w:'superLike','4':'superLike'};
            const choice = map[key];
            if (!choice) return;
            event.preventDefault();
            save({choice});
        }"""

    @staticmethod
    def _payload(event) -> dict:
        return event.args if isinstance(event.args, dict) else {}

    def _on_judgment(self, choice: str, event) -> None:
        payload = self._payload(event)
        self.judge(choice, payload)
        self._finish_open_block(payload)

    def _finish_open_block(self, payload: dict) -> None:
        # A successful action changes generation. A rejected browser gesture
        # can stay on the same candidate and needs an explicit release.
        if (not self.state.saving and not self._storage_busy
                and isinstance(payload.get('operation_id'), str)):
            self.root.props['data-finished-operation'] = payload['operation_id']

    def _on_expired_undo(self, event) -> None:
        self._finish_open_block(self._payload(event))

    def _on_key(self, event) -> None:
        data = self._payload(event)
        if data.get('action') == 'undo':
            self._on_undo(event)
            return
        if data.get('action') == 'summary-close':
            if self.active:
                self.close_summary()
            return
        self._on_judgment(data.get("choice"), event)

    def _summary_source(self):
        item = self.state.current_artwork
        if item is None:
            return None, None, False
        if self.discovery is None:
            return item.work_id, item.description, True
        key = (item.work_id, item.author_id)
        if key in self._summary_fetched:
            return item.work_id, self._summary_fetched[key], True
        if item.work_id not in self._summary_descriptions:
            return item.work_id, None, False
        text = self._summary_descriptions[item.work_id]
        return item.work_id, text, text is not None

    def _summary_state(self):
        work_id, text, known = self._summary_source()
        if work_id is None:
            return 'none'
        if self._summary_retrying:
            return 'retrying'
        if work_id in self._summary_failed:
            return 'failed'
        if not known:
            return 'unavailable'
        try:
            key=(work_id,text)
            if key in self._summary_blank_cache:
                blank=self._summary_blank_cache[key]
                self._summary_blank_cache.move_to_end(key)
            else:
                blank=summary_is_blank(text)
                self._summary_blank_cache[key]=blank
                if len(self._summary_blank_cache)>64:
                    self._summary_blank_cache.popitem(last=False)
            return 'none' if blank else 'ready'
        except (ValueError, TypeError):
            return 'failed'

    def _summary_sync(self):
        wid, text, known = self._summary_source()
        item = self.state.current_artwork
        identity = (wid, item.author_id if item else None, text, known)
        if identity != self._summary_identity:
            self._summary_identity = identity
            self._summary_epoch += 1
            self._summary_open = False
            self._summary_page = 0
            self._summary_pages = []
            self._summary_page_text = ''
            self._summary_retrying = False
            self.summary_board.set_visibility(False)
            self.summary_pager.set_visibility(False)
            self.summary_prev.set_visibility(False)
            self.summary_next.set_visibility(False)
            self.root.props('data-summary-open=false')
        self._summary_buttons()

    def _summary_buttons(self):
        state = self._summary_state()
        reader = getattr(self.discovery, 'reader', None) if self.discovery is not None else None
        live = callable(getattr(reader, 'get_artwork_metadata_raw', None))
        opened = self._summary_open and state == 'ready'
        labels = {'ready': '概要 ▾' if opened else '概要 ▸',
                  'none': '概要 ▸', 'unavailable': '概要を取得' if live else '概要 未取得',
                  'failed': '概要 取り直す', 'retrying': '概要 取得中'}
        self.summary_button.set_text(labels[state])
        item = self.state.current_artwork
        self.summary_button.props(
            f'data-work-id={item.work_id if item else ""} '
            f'data-author-id={item.author_id if item else ""} '
            f'data-generation={self.state.generation} data-summary-epoch={self._summary_epoch}')
        self.summary_button.props(f"aria-expanded={'true' if opened else 'false'}")
        self.summary_button.set_enabled(
            self.active and not self.state.closed and not self.services.closing
            and self._storage_ready and not self._storage_busy and not self.state.saving
            and (state in ('ready', 'failed') or (state == 'unavailable' and live)))
        tooltips = {'ready':'概要を閉じる' if opened else '作品の概要を開く',
                    'none':'この作品には説明がありません',
                    'unavailable':'選択した作品の概要本文を明示的に取得します' if live else '概要本文はまだ取得されていません',
                    'failed':'説明文を確認できませんでした。押すと明示的に取り直します',
                    'retrying':'概要本文を再確認しています'}
        self.summary_button.props(f"aria-label={tooltips[state]}")
        self.summary_status.set_text(
            '説明文の表示に失敗しました。再試行できます。' if state == 'failed'
            else '選択した作品の概要を取得しています。' if state == 'retrying'
            else '')

    def _summary_click_token(self):
        item = self.state.current_artwork
        if item is None:
            return {}
        return {'work_id': item.work_id, 'author_id': item.author_id,
                'generation': self.state.generation, 'summary_epoch': self._summary_epoch}

    def _summary_click_current(self, payload):
        return (self.discovery is not None and self.active and not self.state.closed
                and not self.services.closing and not self.state.saving
                and not self._storage_busy and self._storage_ready
                and isinstance(payload, dict) and
                type(payload.get('generation')) is int and
                type(payload.get('summary_epoch')) is int and
                payload == self._summary_click_token())

    async def request_summary(self, payload):
        """Only an explicit, generation-fenced browser click may start a GET."""
        if not self._summary_click_current(payload):
            return False
        state = self._summary_state()
        if state == 'ready':
            return self.toggle_summary()
        if state not in ('unavailable', 'failed'):
            return False
        item = self.state.current_artwork
        key = (item.work_id, item.author_id)
        if key in self._summary_pending:
            return False
        reader = getattr(self.discovery, 'reader', None)
        if not callable(getattr(reader, 'get_artwork_metadata_raw', None)):
            return False
        self._summary_pending.add(key)
        self._summary_retrying = True
        self._summary_buttons()
        try:
            from ..request_policy import interactive
            raw = await asyncio.to_thread(interactive(fetch_summary), reader, *key)   # original discovery_tab.py:3928: interactive
            if not self._summary_click_current(payload):
                return False
            self._summary_fetched[key] = raw
            self._summary_failed.discard(item.work_id)
            self._summary_retrying = False
            self._summary_sync()
            if summary_is_blank(raw):
                return True
            return self.toggle_summary()
        except SummaryFetchFailed:
            if self._summary_click_current(payload):
                self._summary_retrying = False
                self.set_summary_failed()
            return False
        finally:
            self._summary_pending.discard(key)
            if self._summary_click_current(payload):
                self._summary_retrying = False
                self._summary_buttons()

    def set_summary_failed(self, reason=None):
        """Local failure evidence; never display exception text or assume absent."""
        item = self.state.current_artwork
        if item is None or self.state.closed:
            return
        self._summary_failed.add(item.work_id)
        self._summary_open = False
        self._summary_page = 0
        self.summary_board.set_visibility(False)
        self.summary_pager.set_visibility(False)
        self.summary_prev.set_visibility(False)
        self.summary_next.set_visibility(False)
        self.root.props('data-summary-open=false')
        self._summary_buttons()

    async def retry_summary(self):
        """Live reader: explicitly retry one GET; local-only fixture: recheck cache."""
        if (not self.active or self.state.closed or self.services.closing
                or self._summary_state() != 'failed'):
            return False
        reader = getattr(self.discovery, 'reader', None) if self.discovery is not None else None
        if callable(getattr(reader, 'get_artwork_metadata_raw', None)):
            return await self.request_summary(self._summary_click_token())
        item = self.state.current_artwork
        wid,epoch = item.work_id,self._summary_epoch
        self._summary_retrying = True
        self._summary_buttons()
        try:
            if self.discovery is not None:
                descriptions = await self.discovery.description_snapshot()
                if (self.state.closed or not self.active or epoch != self._summary_epoch
                        or self.state.current_artwork is None
                        or self.state.current_artwork.work_id != wid):
                    return False
                self._summary_descriptions.update(descriptions)
                recovered = descriptions.get(wid)
            else:
                await asyncio.sleep(0)
                recovered = item.description
            if recovered is None or not isinstance(recovered, str) or len(recovered)>MAX_DESCRIPTION:
                return False
            summary_layout(recovered, 24.0, 12)
            self._summary_failed.discard(wid)
            return True
        except (ValueError, RuntimeError, TypeError):
            return False
        finally:
            if (not self.state.closed and epoch == self._summary_epoch):
                self._summary_retrying = False
                self._summary_buttons()

    def _on_summary_click(self, event):
        payload = self._payload(event)
        # Guard before any state change; old DOM clicks cannot request another
        # artwork's metadata after a judgment, tab move or state replacement.
        item = self.state.current_artwork
        expected = self._summary_click_token()
        if (not self.active or self.state.closed or self.services.closing
                or item is None or (self.discovery is not None and
                   any(payload.get(k) != expected[k] or
                       type(payload.get(k)) is not type(expected[k]) for k in expected))):
            return
        if (type(payload.get('width')) is int and type(payload.get('height')) is int):
            self.set_summary_viewport(payload['width'],payload['height'],payload.get('top'))
        if self._summary_state() == 'failed' and self.discovery is None:
            background_tasks.create(self.retry_summary(),name='learning summary local retry')
        elif self._summary_state() == 'failed' and not callable(
                getattr(getattr(self.discovery,'reader',None), 'get_artwork_metadata_raw',None)):
            background_tasks.create(self.retry_summary(),name='learning summary cached retry')
        elif self._summary_state() in ('failed', 'unavailable'):
            background_tasks.create(self.request_summary(expected),name='learning explicit summary GET')
        else:
            self.toggle_summary()

    def _on_summary_resize(self,event):
        if not self._summary_open or not self.active or self.state.closed:
            return
        payload=self._payload(event)
        if type(payload.get('width')) is int and type(payload.get('height')) is int:
            self.set_summary_viewport(payload['width'],payload['height'],payload.get('top'))

    def set_summary_viewport(self, width, height, top=None):
        if (type(width) is not int or type(height) is not int or
                not 320 <= width <= 10000 or not 320 <= height <= 10000):
            return
        self._summary_viewport = (width, height)
        self._summary_mode = 'band' if width >= 1500 else 'rail'
        self._summary_cols = 3 if self._summary_mode == 'band' else 1
        board_width = min(900,width-32) if self._summary_cols == 3 else min(276,width-32)
        # The shell header/nav changes the actual vertical origin. Never
        # estimate it from viewport alone when a browser supplied a live
        # anchor position; clamp the overlay below the visible bottom edge.
        board_top = top if type(top) is int and 0 <= top < height else min(260,height//3)
        remaining = max(100,height-board_top-12)
        board_height = min(180,remaining) if self._summary_cols == 3 else min(642,remaining)
        self._summary_rows = max(1,int((board_height-34-34-20-12)//17))
        self._summary_width = board_width
        self._summary_height = board_height
        if self._summary_open:
            self._prepare_summary()
            self._render_summary()

    def _prepare_summary(self):
        _wid, raw, known = self._summary_source()
        if not known or not isinstance(raw, str):
            return False
        if not hasattr(self,'_summary_width'):
            self.set_summary_viewport(*self._summary_viewport)
        cols,rows=self._summary_cols,self._summary_rows
        width_per_column = max(1,(self._summary_width-20-12*(cols-1))/cols)
        key=(_wid,raw,round(width_per_column/11.5,1),rows,cols)
        try:
            if key in self._summary_layout_cache:
                self._summary_pages=self._summary_layout_cache[key]
                self._summary_layout_cache.move_to_end(key)
            else:
                self._summary_pages=summary_layout(raw,width_per_column/11.5,rows*cols)
                self._summary_layout_cache[key]=self._summary_pages
                if len(self._summary_layout_cache)>64:
                    self._summary_layout_cache.popitem(last=False)
        except (ValueError,TypeError):
            self.set_summary_failed()
            return False
        self._summary_page=min(self._summary_page,max(0,len(self._summary_pages)-1))
        return bool(self._summary_pages)

    def _render_summary(self):
        if not self._summary_open or not self._summary_pages:
            self.summary_board.set_visibility(False)
            self.root.props('data-summary-open=false')
            return
        self._summary_page=max(0,min(self._summary_page,len(self._summary_pages)-1))
        lines=self._summary_pages[self._summary_page]
        self._summary_page_text='\n'.join(''.join(run.text for run in line) for line in lines)
        self.summary_body.clear()
        with self.summary_body:
            with ui.row().classes('w-full items-start flex-nowrap gap-3'):
                for col in range(self._summary_cols):
                    with ui.column().classes('min-w-0 flex-1 gap-0'):
                        for line in lines[col*self._summary_rows:(col+1)*self._summary_rows]:
                            with ui.row().classes('w-full gap-0 items-start flex-nowrap').style(
                                    'height:17px;overflow:hidden;'):
                                if not line:
                                    ui.label(' ').style('font-size:11.5px;line-height:17px')
                                for run in line:
                                    if run.url:
                                        link = ui.link(run.text + (' ↗' if run.external else ''),
                                            run.url, new_tab=True).classes('min-w-0').style(
                                                'font-size:11.5px;line-height:17px;white-space:pre;')
                                        link.props('rel=noopener noreferrer')
                                    else:
                                        ui.label(run.text).classes('min-w-0').style(
                                            'font-size:11.5px;line-height:17px;white-space:pre;')
        total=len(self._summary_pages)
        self.summary_pager.set_visibility(total>1)
        self.summary_prev.set_visibility(total>1)
        self.summary_next.set_visibility(total>1)
        self.summary_prev.set_enabled(self._summary_page>0)
        self.summary_next.set_enabled(self._summary_page<total-1)
        self.summary_page_label.set_text(
            f'続きがあります ◂ {self._summary_page+1} / {total} ▸' if total>1 else '')
        height = self._summary_height
        if total==1:
            # One-page prose gets a content-sized board, no unused empty tray.
            height=min(height,34+20+8+max(1,min(len(lines),self._summary_rows))*17)
        self.summary_board.style(f'width:{self._summary_width}px;height:{height}px;')
        self.summary_board.set_visibility(True)
        self.root.props('data-summary-open=true')

    def toggle_summary(self):
        if (not self.active or self.state.closed or self.services.closing
                or self._summary_state() != 'ready'):
            return False
        if self._summary_open:
            self.close_summary()
            return True
        self._summary_open=True
        self._summary_page=0
        if not self._prepare_summary():
            self._summary_open=False
            self._summary_buttons()
            return False
        if self.discovery is not None:
            self._set_trend_open(False)
        self._render_summary()
        self._summary_buttons()
        return True

    def close_summary(self):
        if not self._summary_open:
            return False
        self._summary_open=False
        self._summary_page=0
        self.summary_board.set_visibility(False)
        self.summary_pager.set_visibility(False)
        self.summary_prev.set_visibility(False)
        self.summary_next.set_visibility(False)
        self.root.props('data-summary-open=false')
        self._summary_buttons()
        return True

    def step_summary(self,delta):
        if (not self._summary_open or type(delta) is not int
                or not self.active or self.state.closed or self.services.closing):
            return False
        next_page=max(0,min(self._summary_page+delta,len(self._summary_pages)-1))
        if next_page==self._summary_page:
            return False
        self._summary_page=next_page
        self._render_summary()
        return True

    def judge(self, choice: str, payload: dict) -> bool:
        if not self.active or self.state.closed or self.services.closing or not self._storage_ready or self._storage_busy or self._review_mutating or self._review_open:
            return False
        generation = payload.get("generation")
        if type(generation) is not int:
            return False
        options = dict(expected_work_id=payload.get("work_id"),
                       expected_generation=generation, operation_id=payload.get("operation_id"))
        if self.storage is not None:
            actions = getattr(self.services, 'pixiv_actions', None)
            account = getattr(actions, 'cached_verified_account_id', None) if actions is not None else None
            work_id = payload.get('work_id')
            if not isinstance(work_id, str):
                return False
            command = self.state.prepare_judgment(
                choice, bookmarked=(account, work_id) in self._bookmark_new,
                opened=work_id in self._opened_work_ids, **options,
            )
            if command is None:
                return False
            self.close_summary()
            return self._start_save(command)
        success = self.state.judge(choice, **options)
        if success:
            self.close_summary()
            self.refresh()
            self._count_superlike(choice, undo=False)
            self._show_judgment_toast(choice)
        elif self.state.last_error:
            self.status.set_text(self.state.last_error)
        return success

    def _on_undo(self, event) -> None:
        payload = self._payload(event)
        self._undo(payload)
        self._finish_open_block(payload)

    def _undo(self, payload: dict) -> None:
        item = self.state.current_artwork
        # Undo は直前の操作で画面が切り替わった後に届くイベントを拒否する。
        if (not self.active or self.state.closed or self.services.closing or not self._storage_ready or self._storage_busy or self._review_mutating or self._review_open or
                payload.get("work_id") != (item.work_id if item else "") or
                type(payload.get("generation")) is not int or
                payload["generation"] != self.state.generation):
            return
        if self.storage is not None:
            command = self.state.prepare_undo(operation_id=payload.get("operation_id"))
            if command is not None:
                self._start_save(command)
            return
        last = self.state._last_judgment
        if self.state.undo(operation_id=payload.get("operation_id")):
            if last is not None:
                self._count_superlike(last.choice, undo=True)
            self.toast.hide()
            self.refresh()
        elif self.state.last_error:
            self.status.set_text(self.state.last_error)

    def _start_save(self, command: SaveCommand) -> bool:
        # Enqueue before acknowledging the click: shutdown may cancel a new async
        # task before it ever starts. The application owns this accepted Future.
        try:
            if command.undo:
                pending = self.storage.submit_revert(self._session_id, command.judgment, command.operation_id)
            else:
                pending = self.storage.submit_record(self._session_id, command.judgment)
        except Exception as exc:
            self._save_failed(command, exc)
            return False
        self._failed_command = None
        self._notice = ""
        self._save_task = background_tasks.create(self._persist(command, pending), name="learning save")
        return True

    async def _persist(self, command: SaveCommand, pending: Future) -> None:
        def show_saving() -> None:
            if pending.done() or self.state.closed or self.services.closing:
                return
            with self.client:
                self.status.set_text("取消を保存しています…" if command.undo else "判定を保存しています…")
                self._update_buttons()

        saving_notice = asyncio.get_running_loop().call_later(0.09, show_saving)
        try:
            # Cancelling a UI waiter must not cancel an accepted executor job.
            wrapped = asyncio.wrap_future(pending)
            wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            try:
                await asyncio.shield(wrapped)
            finally:
                saving_notice.cancel()
        except Exception as exc:
            self._save_failed(command, exc)
        else:
            if self.services.closing:
                self.close()
            elif self.state.complete_save(command):
                saved_state = self.state
                saved_generation = self.state.generation
                self._count_superlike(command.judgment.choice, undo=command.undo)
                # Publish the committed next card before any background ranking.
                self.refresh()
                if command.undo:
                    self.toast.hide()
                else:
                    self._show_judgment_toast(command.judgment.choice, command)
                if self.discovery is not None:
                    if not command.undo:self._set_trend_open(False)
                    try:
                        # Keep the newly visible head and Undo prefix stable. Only
                        # rerank unseen tail against the committed preference counts.
                        rank_generation=self.state.generation
                        merge_revision=self._refill_merge_revision
                        prefix=self.state.artworks[:self.state.index+1]
                        tail=self.state.artworks[self.state.index+1:]
                        order,frames=await self.discovery.rank_pending(tuple(a.work_id for a in tail),tuple(a.author_id for a in prefix if a.author_id))
                        if self.state.closed:return
                        by_id={a.work_id:a for a in tail}
                        if self.state.generation == rank_generation and not self.state.saving and self._refill_merge_revision == merge_revision:
                            self.state.artworks=prefix+tuple(by_id[wid] for wid in order)
                            self._discovery_frames.update(frames)
                    except Exception:
                        self._notice='判定は保存済みです。候補の並び替えを更新できませんでした。'
                if self.state is not saved_state or self.state.generation != saved_generation or self._storage_busy:
                    return
                self.refresh()
                if self.discovery is not None:
                    try:
                        trend=await self.discovery.trend_snapshot()
                        if not self.state.closed:self._render_trend(trend)
                    except Exception:
                        if not self.state.closed:self.trend_label.set_text('判定は保存済みです。傾向を読み込めませんでした。')

    def reset_superlike_count(self) -> None:
        """学習状況のリセット後は回数も0に戻す（元 discovery_tab.py:5669 §3-3-7）。"""
        self._superlike_used = 0
        self.superlike_used_label.set_text("")
        self.superlike_used_label.set_visibility(False)

    def _count_superlike(self, choice, *, undo: bool) -> None:
        """この画面で使った SuperLike の回数（元 discovery_tab.py:1874-1877, 1996-1998）。再読込では戻さない。"""
        if choice != "superLike":
            return
        if undo:
            self._superlike_used = max(0, self._superlike_used - 1)
        else:
            self._superlike_used += 1
        n = self._superlike_used
        self.superlike_used_label.set_text(f"SuperLike使用: {n}回" if n else "")
        self.superlike_used_label.set_visibility(bool(n))

    def _show_judgment_toast(self, choice: str, command=None) -> None:
        """判定直後に「判定しました／取り消す」を出す（元 discovery_tab.py:1949-1952）。"""
        item = self.state.current_artwork
        props = f'data-work-id="{item.work_id if item else ""}" data-generation={self.state.generation}'
        judgment = command.judgment if command is not None else None
        if (choice == "hate" and judgment is not None and self.storage is not None
                and self._session_id is not None and hasattr(self.storage, "submit_hate_reason")):
            # 「合わない」だけ理由を後追いで聞く。生きている間は「判定しました」で上書きしない（元 1949-1952）。
            session_id = self._session_id
            if self.toast.show_reason(lambda reason, j=judgment, s=session_id: self._pick_reason(j, s, reason)):
                return
        self.toast.show_undo(self._click_js('block-open'), self._on_undo, props)

    def _pick_reason(self, judgment, session_id: str, reason: str) -> None:
        self.toast.hide()
        if (self.state.closed or self.services.closing or session_id != self._session_id
                or self.storage is None):
            return
        task = background_tasks.create(self._save_reason(judgment, session_id, reason), name="learning hate reason")
        self._reason_tasks.add(task)
        task.add_done_callback(self._reason_tasks.discard)

    async def _save_reason(self, judgment, session_id: str, reason: str) -> None:
        """元 1717-1739。付けられなければ判定は保存済みのまま「タグだけ下げる」に倒れる。"""
        try:
            pending = self.storage.submit_hate_reason(session_id, judgment, reason)
            wrapped = asyncio.wrap_future(pending)
            wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            saved = await asyncio.shield(wrapped)
        except Exception:
            if not self.state.closed and not self.services.closing:
                self.toast.show_gave_up(
                    "「合わない」の理由を保存できませんでした",
                    "判定は保存済みです。理由は付かず、タグだけ下げる扱いのままです。", heavy=False)
            return
        if not saved or self.discovery is None or self.state.closed:
            return
        try:
            # 理由の後追いでもカウントは動く。傾向パネルの材料を作り直す（元 1729-1730）。
            self._render_trend(await self.discovery.trend_snapshot())
        except Exception:
            if not self.state.closed:
                self.trend_label.set_text('判定は保存済みです。傾向を読み込めませんでした。')

    def _save_failed(self, command: SaveCommand, exc: Exception) -> None:
        from ..learning_repository import LearningConflict

        if self.services.closing:
            self.close()
        if self.state.closed or not self.state.saving:
            return
        self.state.complete_save(command, exc)
        if isinstance(exc, LearningConflict):
            self.reload_storage(
                notice="別の画面で判定が更新されたため、取消は行いませんでした。保存状態を読み直しました。"
            )
        else:
            self._failed_command = command
            self.status.set_text(self._resting_message())
            # 保存できなかった判定・取り消しは、画面に出ないまま学習が止まる重い失敗。
            # 数秒で消えない通知枠に出す（元 discovery_tab.py:1921-1926, 2030-2035）。
            if command.undo:
                self.toast.show_gave_up("取り消しを保存できませんでした（判定は残っています）",
                                        "「保存を再試行」でやり直せます。", heavy=True, key="save")
            else:
                self.toast.show_gave_up("判定を保存できませんでした",
                                        "画面は進めていません。「保存を再試行」でやり直せます。", heavy=True, key="save")
            self._update_buttons()

    def _resting_message(self) -> str:
        if self._failed_command is not None:
            if self._failed_command.undo:
                return "取消を保存できませんでした。判定はそのままです。「保存を再試行」でやり直せます。"
            return "判定を保存できませんでした。画面は進めていません。「保存を再試行」でやり直せます。"
        return self._notice

    def retry_save(self) -> None:
        failed = self._failed_command
        if (failed is None or not self.active or self.state.closed or self.services.closing or
                not self._storage_ready or self._storage_busy or self._review_mutating):
            return
        if failed.undo:
            command = self.state.prepare_undo(operation_id=failed.operation_id)
        else:
            command = self.state.prepare_judgment(
                failed.judgment.choice, expected_work_id=failed.judgment.work_id,
                expected_generation=self.state.generation, operation_id=failed.operation_id,
                bookmarked=failed.judgment.bookmarked,
                opened=failed.judgment.opened,
            )
        if command is not None:
            self._start_save(command)

    def _on_reload_click(self, event) -> None:
        if not self.reload_storage():
            self._finish_open_block(self._payload(event))

    def reload_storage(self, *, notice: str = "", review_queue=None) -> bool:
        if (self.storage is None or self.state.closed or self.services.closing or self.state.saving or self._storage_busy
                or (self._review_mutating and review_queue is None)):
            return False
        self._stop_candidate_refill()
        self._storage_busy = True
        self._render_review()
        self._storage_ready = False
        self.status.set_text("保存された判定を読み込んでいます…")
        self._update_buttons()
        background_tasks.create(self._reload_storage(notice, review_queue), name="learning restore")
        return True

    async def _reload_storage(self, notice: str, review_queue=None) -> None:
        try:
            snapshot = await self.storage.open_session()
            if self.discovery is not None:
                self._artworks = tuple(self.discovery.artworks)
                self._discovery_frames = dict(self.discovery.frames)
                self._summary_descriptions = await self.discovery.description_snapshot()
                records = await asyncio.to_thread(self.discovery.candidate_records)
                self._discovery_image_ids = frozenset(r.work_id for r in records)
                if records and not self.state.closed and not self.services.closing:
                    pin = self.services.acquire_ranking_images(records)
                    self._stop_prefetch(close=True)
                    old = self._discovery_pin
                    self._discovery_pin = self._images = pin
                    if old is not None: old.close()
                trend = await self.discovery.trend_snapshot()
                if not self.state.closed:
                    self._render_trend(trend)
                    self.watch_local_seeds()
        except Exception:
            if self.services.closing:
                self.close()
            elif not self.state.closed:
                self._storage_busy = False
                self.status.set_text("保存状態を読み込めませんでした。「保存状態を再読込」でやり直してください。")
                self._update_buttons()
            return
        if self.services.closing:
            self.close()
        if self.state.closed:
            self.storage.release_session(snapshot.session_id)
            return
        if self._session_id is not None:
            self.storage.release_session(self._session_id)
        self._session_id = snapshot.session_id
        generation = self.state.generation + 1
        previous_queue_ids = {item.work_id for item in self.state.artworks}
        self.state.close()
        remaining = tuple(item for item in self._artworks if item.work_id not in snapshot.judged_ids)
        if review_queue is None:
            self._review_queue_ids = {item.work_id for item in remaining}
        else:
            self._review_queue_ids.update(previous_queue_ids)
            if self.discovery is not None:
                remaining = tuple(item for item in remaining if item.work_id in self._review_queue_ids)
        if review_queue is not None:
            # Review cancellation returns a card immediately after the current
            # one, as in the original swipe queue. Keep pending order instead of
            # restarting at the first artwork in the database snapshot. A redo
            # is removed by judged_ids above and must not replace the current.
            pending_ids, requeue_id, outcome = review_queue
            if requeue_id and outcome == 'absent':
                remaining = tuple(item for item in remaining if item.work_id != requeue_id)
            by_id = {item.work_id: item for item in remaining}
            order = [wid for wid in pending_ids if wid in by_id and wid != requeue_id]
            if requeue_id in by_id:
                is_current = bool(pending_ids) and pending_ids[0] == requeue_id
                order.insert(0 if is_current or not order else 1, requeue_id)
            known = set(order)
            order.extend(item.work_id for item in remaining if item.work_id not in known)
            remaining = tuple(by_id[wid] for wid in order)
        self.toast.hide()
        self._baseline_count = (sum(item.work_id in snapshot.judged_ids for item in self._artworks)
                                if self.discovery is not None else len(self._artworks) - len(remaining))
        self._judged_before_session = len(snapshot.judged_ids)
        self.state = LearningState(remaining, self.ledger)
        # A late browser event from the previous snapshot must never match again.
        self.state.generation = generation
        self._storage_busy = False
        self._storage_ready = True
        self._refill.active = self.active
        self._failed_command = None
        self._notice = notice
        self.refresh()
        self._render_review()
        self._start_initial_load()

    def set_active(self, active: bool) -> None:
        was_active = self.active
        self.active = bool(active) and not self.state.closed
        self.root.props(f"data-active={'true' if self.active else 'false'}")
        self.root.set_visibility(self.active)
        if not self.active:
            self.toast.hide()
            self.view_history.hide()
            self.view_history.dialog.close()
            self.view_history.viewer.close_viewer(restore_focus=False)
            self.close_summary()
            self._summary_epoch += 1
            self._summary_retrying = False
            self._stop_prefetch()
            self._stop_candidate_refill()
            self._stop_initial_load()
        elif not was_active and self._refill.armed:
            self._refill.resume(self._refill_clock())
        if not self.active and self._review_open:
            self.review_dialog.close()
        if not self.active and self.discovery is not None and self._settings_open:
            self._close_settings()
        self._update_buttons()
        self._summary_buttons()
        if self.active and self.discovery is not None:
            self.request_image()
            self._start_initial_load()
            self._kick_candidate_refill()
        if self.active and self.client.has_socket_connection:
            self.client.run_javascript(
                f"""requestAnimationFrame(() => {{
                    const node = document.getElementById('{self.root.html_id}');
                    if (node?.isConnected && node.getAttribute('data-active') === 'true' &&
                        node.getClientRects().length &&
                        !node.closest('[inert], [aria-hidden="true"]'))
                        node.focus({{preventScroll:true}});
                }})"""
            )
            self.client.run_javascript(
                f"""requestAnimationFrame(() => {{
                  const root=document.getElementById('{self.root.html_id}');
                  if (!root || root.__pvSummaryResize) return;
                  const resize=() => {{
                    if (!root.isConnected) {{
                      window.removeEventListener('resize',resize);
                      return;
                    }}
                    if (root.getAttribute('data-summary-open')==='true')
                      root.dispatchEvent(new CustomEvent('summary-resize'));
                    const viewport=root.querySelector('.pv-trend-viewport');
                    if (viewport && root.getAttribute('data-active')==='true' && root.getAttribute('data-review-open')!=='true') {{
                      const rect=root.getBoundingClientRect();
                      const layout=root.querySelector('.pv-trend-layout');
                      const side=viewport.parentElement;
                      if (layout?.dataset.mode==='rail') {{
                        const available=Math.max(80,Math.floor(Math.min(rect.bottom,window.innerHeight)-side.getBoundingClientRect().top));
                        const next=available+'px';
                        if (side.style.height!==next) side.style.height=next;
                      }} else if (side.style.height) side.style.removeProperty('height');
                      if (layout?.dataset.mode==='fold') {{
                        const picture=document.getElementById('{self.picture.html_id}');
                        const imageRect=picture?.getBoundingClientRect();
                        if (imageRect) {{
                          viewport.style.left=imageRect.left+'px';
                          viewport.style.width=imageRect.width+'px';
                          viewport.style.bottom=Math.max(0,window.innerHeight-imageRect.bottom)+'px';
                          viewport.style.maxHeight=(imageRect.height/2)+'px';
                        }}
                      }} else {{
                        for (const key of ['left','width','bottom','max-height']) viewport.style.removeProperty(key);
                      }}
                      const image=root.querySelector('.pv-view-art img[data-pv-displayed="true"]');
                      const aspect=image?.naturalWidth && image?.naturalHeight ? image.naturalWidth/image.naturalHeight : 1;
                      root.dispatchEvent(new CustomEvent('trend-measure', {{detail: {{
                        width:rect.width,height:rect.height,viewport:viewport.getBoundingClientRect().height,aspect
                      }}}}));
                    }}
                  }};
                  root.__pvSummaryResize=resize;
                  window.addEventListener('resize',resize,{{passive:true}});
                  const observer=new ResizeObserver(resize);
                  observer.observe(root);
                  const viewport=root.querySelector('.pv-trend-viewport');
                  if (viewport) observer.observe(viewport);
                  resize();
                  root.addEventListener('summary-dispose',()=>{{
                    window.removeEventListener('resize',resize);
                    observer.disconnect();
                    delete root.__pvSummaryResize;
                  }},{{once:true}});
                }})"""
            )

    def _update_buttons(self) -> None:
        item = self.state.current_artwork
        work_id = item.work_id if item else ""
        props = f'data-work-id="{work_id}" data-generation={self.state.generation}'
        available = self.active and not self.services.closing and self._storage_ready and not self._storage_busy and not self.state.saving and not self._settings_busy and not self._review_mutating
        can_open = item is not None and self._can_open_learning_work()
        open_props = props + f" data-work-open={'true' if can_open else 'false'}"
        self.picture.props(open_props)
        if self.discovery is not None:
            self.open_pixiv_button.props(open_props)
            self.open_pixiv_button.set_enabled(can_open)
            self.open_pixiv_button.set_visibility(bool(self.open_pixiv_button.props.get('href'))
                                                  and self.state.last_error != 'image load failed')
        self.root.props(props + f" data-saving={'true' if self.state.saving else 'false'}")
        for button in self.buttons.values():
            button.props(props)
            button.set_enabled(available and self.state.ready)
        self.undo_button.props(props)
        self.bookmark_button.props(props + f' data-bookmark-token="{self._bookmark_token}"')
        actions = getattr(self.services, 'pixiv_actions', None)
        account = getattr(actions, 'cached_verified_account_id', None) if actions is not None else None
        marked = bool(item and account and (account, item.work_id) in self._bookmark_confirmed)
        self.bookmark_button.props('icon=favorite' if marked else 'icon=favorite_border')
        self.bookmark_button.set_enabled(bool(available and self.state.ready and item is not None
                                               and actions is not None and not self._bookmark_busy))
        self.bookmark_tip.set_text('pixivでブックマーク（pixiv側に書き込みます）' if self.state.ready
                                   else '絵が表示されてから押せます')
        self.toast.set_undo_props(props)
        self.undo_button.set_enabled(available and self.state.can_undo)
        self.retry_button.set_enabled(available and item is not None)
        self.save_retry_button.set_visibility(self._failed_command is not None)
        self.save_retry_button.set_enabled(available and (self.state.ready or self.state.can_undo))
        self.reload_button.set_visibility(self.storage is not None)
        self.reload_button.set_enabled(self.active and not self.state.closed and not self.services.closing and not self._storage_busy and not self.state.saving and not self._review_mutating)
        if self.discovery is not None:
            self.settings_button.set_enabled(available and not self._managed_busy)
            self.fetch_button.set_enabled(available and not self._managed_busy and (self._initial_task is None or self._initial_task.done()))
            self.candidate_status_retry.set_enabled(self.fetch_button.enabled and not self._refill.running)
            self.managed_button.props(props)
            self.managed_button.set_enabled(available and item is not None and not self._managed_busy and getattr(self.services, 'managed_downloads', None) is not None)
        self.review_button.set_enabled(available and self.storage is not None and not self._review_busy)

    # 元 discovery_tab.py:2819-2828 の文言表。確実枠は添えない。
    FRAME_NOTES = {"style": "新しい絵柄を試しています", "subject": "知らない題材を試しています"}

    def _update_frame_badge(self) -> None:
        item = self.state.current_artwork
        note = None
        if self.discovery is not None and item is not None and self.state.last_error != "image load failed":
            note = self.FRAME_NOTES.get(self._discovery_frames.get(item.work_id))
        self.frame_label.set_text(note or "")
        self.frame_label.set_visibility(bool(note))

    def _empty_state_shown(self) -> bool:
        return (self.discovery is not None and self.state.current_artwork is None
                and bool(self._refill.armed))

    def _sync_pixiv_link(self, item) -> None:
        target = pixiv_url(item.work_id) if item is not None else None
        self.open_pixiv_button.props['href'] = target or ''
        self.open_pixiv_button.set_visibility(target is not None)

    def _build_candidate_status_card(self) -> None:
        with ui.element('div').classes('w-full grid place-items-center').style(
                'min-height:min(54dvh,460px)') as self.candidate_status_stage:
            with ui.card().classes('items-center gap-2 text-center').props(
                    'data-testid=learning-candidate-status').style(
                    'width:460px;max-width:100%;background:var(--pv-raised);'
                    'border:1px solid var(--pv-border);padding:24px'):
                self.candidate_status_spinner = ui.spinner(size='22px', thickness=2).props(
                    'data-testid=learning-candidate-spinner')
                self.candidate_status_icon = ui.icon('search_off', size='22px').props(
                    'data-testid=learning-candidate-icon').style('color:var(--pv-muted)')
                self.candidate_status_title = ui.label().classes('pv-dialog-title').style(
                    'font-size:14px;font-weight:600')
                self.candidate_status_body = ui.label().classes('pv-subtitle').props('role=status').style('font-size:12px')
                self.candidate_status_judged = ui.label().style('font-size:11.5px')
                self.candidate_status_retry = ui.button('もう一度探す', on_click=self._retry_candidate_restock).props(
                    'flat no-caps data-testid=learning-candidate-retry')
        self.candidate_status_stage.set_visibility(False)

    def _render_candidate_status(self) -> None:
        policy = self._refill
        judged = self._judged_before_session + self.state.index
        failed = policy.last_failed
        no_candidate = self._empty_state_shown()
        show_status = no_candidate and (failed or judged > 0 or bool(self.state.artworks))
        self.empty_card.set_visibility(no_candidate and not show_status)
        self.candidate_status_stage.set_visibility(show_status)
        for element in (self.candidate_header, self.progress, self.judgment_controls, self.judgment_help,
                        self.retry_button, self.bookmark_button, self.fetch_button, self.managed_button,
                        self.refill_status):
            element.set_visibility(not show_status)
        if not show_status:
            self.candidate_status_retry.set_visibility(False)
            return

        # 元 discovery_tab.py:4381-4488 と discovery_display_state.py:71-88。
        stopped = policy.gave_up
        if failed:
            title = '候補を取りにいけませんでした'
            body = '通信に失敗しました。30秒ごとにもう一度試しています。'
        else:
            title = 'しばらく新しい候補が見つかりませんでした' if stopped else '次の候補を探しています'
            body = 'いま出せる候補はすべて判定しました。新しい候補が見つかると続きから始まります。'
        self.candidate_status_title.set_text(title)
        self.candidate_status_body.set_text('自動での再取得は止まっています。' if stopped else body)
        self.candidate_status_judged.set_text(f'これまでの判定: {judged}枚')
        self.candidate_status_spinner.set_visibility(not stopped)
        self.candidate_status_icon.props('name=cloud_off' if failed else 'name=search_off')
        self.candidate_status_icon.set_visibility(stopped)
        self.candidate_status_retry.set_visibility(failed or stopped)
        self.candidate_status_retry.set_enabled(self.fetch_button.enabled and not policy.running)

    def refresh(self) -> None:
        item = self.state.current_artwork
        self._summary_sync()
        if self.discovery is not None:
            self._update_frame_badge()
            self._sync_pixiv_link(item)
            self._render_candidate_status()
        self.title.set_text(item.title if item else ("現在の候補がありません" if self._refill.armed else "初回候補を読み込んでいます") if self.discovery is not None else "判定が完了しました")
        self.progress.set_text(f"{self._baseline_count + self.state.index} / {len(self._artworks)} 件判定済み")
        if item is None:
            self._image_token = None
            self._image_url = ""
            self.picture.set_visibility(False)
            message = (("現在の候補が尽きました。" if self._refill.armed else "設定された取得元から初回候補を読み込みます。") if self.discovery is not None else "すべての判定が完了しました。")
            if self.state.can_undo:
                message += "Undo で戻せます。"
            if self._empty_state_shown():
                self.status.set_text(self._resting_message())
            else:
                self.status.set_text(self._notice or message)
        else:
            self.picture.set_visibility(True)
            self.request_image()
        self._update_buttons()
        self._kick_candidate_refill()

    def _stop_prefetch(self,*,close=False):
        if self._prefetch is not None:
            if close:
                self._prefetch.close()
                self._prefetch=None;self._prefetch_pin=None
            else:self._prefetch.stop()

    def refresh_prefetch(self):
        self._start_prefetch()
        self._kick_candidate_refill()

    def _start_prefetch(self):
        preferences=getattr(self.services,'preferences',None)
        pin=self._discovery_pin
        if (self.discovery is None or preferences is None or pin is None
                or getattr(self.services.images,'shared_cache',None) is None
                or not self.active or self.state.closed or self.services.closing):return
        if self._prefetch is None or self._prefetch_pin is not pin:
            from ..learning_prefetch import LearningImagePrefetch
            self._stop_prefetch(close=True)
            on_image = self.discovery.shadow_image if getattr(self.discovery, 'shadow_observer', None) is not None else None
            self._prefetch=LearningImagePrefetch(preferences,pin,on_ready=lambda wid:self._prefetch_ready(wid,pin),on_image=on_image)
            self._prefetch_pin=pin
        try:self._prefetch.configure()
        except Exception:return
        self._prefetch.set_queue(item.work_id for item in self.state.artworks[self.state.index:])
        if self._prefetch_task is None or self._prefetch_task.done():
            worker=self._prefetch
            async def run_prefetch():
                try:await worker.run()
                except Exception:
                    # Prefetch failure never substitutes for the current image's
                    # own decode/retry state, and never starts an automatic retry.
                    return
                finally:
                    if self._prefetch is not None and self._prefetch is not worker:
                        self._prefetch_task=None
                        self._start_prefetch()
            self._prefetch_task=background_tasks.create(run_prefetch())

    def _prefetch_ready(self,work_id,pin):
        item=self.state.current_artwork
        if (self.active and not self.state.closed and not self.services.closing
                and self._discovery_pin is pin and item is not None and item.work_id==work_id
                and self.state.last_error and not self.state.saving):
            self.request_image()  # browser decode is still required before judging

    def request_image(self) -> None:
        if self.discovery is not None and not self.active: return
        item = self.state.current_artwork
        if item is None or self.state.closed or self.services.closing or self.state.saving or not self._storage_ready or self._storage_busy:
            return
        self._image_token = self.state.begin_load()
        self._shadow_image_source = self._discovery_pin
        self._release_refill_pins()
        self._image_url = (self._images.url(item.work_id, 0, "view")
                           + f"#learning-{self._image_token.generation}")
        self.picture.set_source(self._image_url)
        self.status.set_text("画像を読み込んでいます…")
        self._update_buttons()

    def _on_loaded(self, event) -> None:
        if not self._storage_ready or self._storage_busy or self.state.closed or self.services.closing:
            return
        if self._image_token is not None and source_matches(event.args, self._image_url):
            if self.state.finish_load(self._image_token):
                if getattr(self.discovery, 'shadow_observer', None) is not None:
                    self.discovery.shadow_exposure(self._session_id, self._image_token, self.state.artworks,
                        self.state.index, self._discovery_frames, self._shadow_image_source)
                if self.client.has_socket_connection:
                    self.client.run_javascript(f"requestAnimationFrame(() => document.getElementById('{self.root.html_id}')?.__pvSummaryResize?.())")
                self._update_frame_badge()
                self._start_prefetch()
                if not self.state.saving and not self._storage_busy:
                    self.status.set_text(self._resting_message())
                self._update_buttons()

    def _on_failed(self, event) -> None:
        if not self._storage_ready or self._storage_busy or self.state.closed or self.services.closing:
            return
        if self._image_token is not None and source_matches(event.args, self._image_url):
            self.state.finish_load(self._image_token, success=False)
            self._update_frame_badge()
            if self.state.last_error:
                self.status.set_text("画像を読み込めませんでした。再読込してください。")
                self._update_buttons()

    async def open_review(self):
        if (not self.active or self.storage is None or self.state.closed or self.services.closing
                or self.state.saving or self._storage_busy or self._review_busy or self._review_mutating):
            return
        if self._review_open:
            await self._refresh_review()
            return
        self._review_epoch += 1
        epoch = self._review_epoch
        self._review_open = True
        self._review_busy = True
        self.review_dialog.open()
        self.review_status.set_text('保存済み判定を読み込んでいます…')
        self._update_buttons()
        try:
            session = await self.storage.open_session()
            if not self._review_valid(epoch):
                self.storage.release_session(session.session_id)
                return
            self._review_session_id = session.session_id
            packet = await self.storage.review_snapshot(session.session_id)
            if self._review_valid(epoch):
                self._review_rows = tuple(packet['rows'])
                self._review_undone.clear(); self._review_outcomes.clear()
                self._review_operations.clear()
                self._review_filter_changed()
                self.review_status.set_text('')
        except Exception:
            if self._review_valid(epoch):self.review_status.set_text('保存済み判定を読み込めませんでした。閉じて再度開いてください。')
        finally:
            if self._review_valid(epoch):
                self._review_busy = False
                self._render_review()
                self._update_buttons()

    async def _refresh_review(self):
        epoch = self._review_epoch
        session = self._review_session_id
        if not self._review_valid(epoch) or session is None or self._review_refresh_busy:
            return
        self._review_busy = True
        self._render_review()
        try:
            packet = await self.storage.review_snapshot(session)
            if not self._review_valid(epoch) or self._review_session_id != session:
                return
            self._review_rows = tuple(packet['rows'])
            self._review_undone.clear()
            self._review_outcomes.clear()
            self._review_operations.clear()
            self._review_filter_changed()
            self.review_status.set_text('')
        except Exception:
            if self._review_valid(epoch):
                self.review_status.set_text('保存済み判定を読み込めませんでした。もう一度「振り返り」を押してください。')
        finally:
            if self._review_valid(epoch):
                self._review_busy = False
                self._render_review()
                self._update_buttons()

    async def _poll_review_jobs(self):
        # Job completion is only a trigger to re-read the authoritative receipt-backed
        # snapshot. A queued, failed, stopped, or unverified job is never a saved row.
        epoch = self._review_epoch
        session = self._review_session_id
        if (not self._review_valid(epoch) or session is None or self._review_busy
                or self._review_refresh_busy or not self._review_watched_jobs):
            return
        owners = (getattr(self.services, 'static_jobs', None),
                  getattr(getattr(self.services, 'ugoira', None), 'jobs', None))
        changed = {}
        for owner in owners:
            if owner is None:
                continue
            for job in owner.snapshot():
                signature = (job.state, job.revision)
                if (job.job_id in self._review_watched_jobs and job.state in ('done', 'error', 'stopped')
                        and self._review_seen_jobs.get(job.job_id) != signature):
                    changed[job.job_id] = signature
        if not changed:
            return
        self._review_refresh_busy = True
        self._render_review()
        try:
            packet = await self.storage.review_snapshot(session)
            if not self._review_valid(epoch) or self._review_session_id != session:
                return
            self._review_rows = tuple(packet['rows'])
            self._review_seen_jobs.update(changed)
            if any(state == 'error' for state, _ in changed.values()):
                self.review_status.set_text('保存に失敗しました。ダウンロード画面の失敗リストから再試行できます。')
            elif any(state == 'stopped' for state, _ in changed.values()):
                self.review_status.set_text('保存は停止しました。ダウンロード画面で状態を確認できます。')
            else:
                self.review_status.set_text('')
        except Exception:
            if self._review_valid(epoch) and self._review_session_id == session:
                self._review_seen_jobs.update(changed)
                self.review_status.set_text('保存状態を確認できません。閉じて再度開いてください。')
        finally:
            self._review_refresh_busy = False
            if self._review_valid(self._review_epoch):
                self._render_review()

    def _review_valid(self,epoch):
        return self._review_open and self.active and not self.state.closed and not self.services.closing and epoch==self._review_epoch

    def _close_review(self):
        self._review_epoch += 1
        self._review_open = False
        self._review_busy = False
        self._review_undone.clear(); self._review_outcomes.clear()
        self._review_operations.clear()
        if self._review_session_id is not None:
            self.storage.release_session(self._review_session_id)
            self._review_session_id = None
        self.review_panel.set_visibility(False)
        self.swipe_area.set_visibility(True)
        self.root.props('data-review-open=false')
        self.swipe_button.props('aria-pressed=true')
        self.review_button.props('aria-pressed=false')
        if self.active and self.client.has_socket_connection:
            self.client.run_javascript(f"requestAnimationFrame(() => document.getElementById('{self.root.html_id}')?.__pvSummaryResize?.())")
        if not self.state.closed:self._update_buttons()

    def return_to_swipe(self):
        self.review_dialog.close()

    def _review_filter_changed(self):
        self._review_pages = {True:0,False:0}
        self._render_review()

    def _review_column_filter_changed(self, positive):
        self._review_pages[positive] = 0
        self._render_review(positive)

    def _review_page(self,positive,delta):
        self._review_pages[positive] = max(0,self._review_pages[positive]+delta)
        self._render_review(positive)

    def _render_review(self, only=None):
        if not self._review_open:return
        filters = ReviewFilter(str(self.review_text.value or ''),self.review_period.value,
                               bool(self.review_unsaved.value),bool(self.review_superlike.value),bool(self.review_hate.value))
        for positive,label in ((True,'好意'),(False,'否定')):
            if only is not None and positive != only:
                continue
            visible_rows = tuple(row for row in self._review_rows if row['work_id'] not in self._review_undone) + tuple(row for row, _ in self._review_undone.values())
            visible_rows = tuple(dict(row, title=self._review_detail_titles[row['work_id']])
                                 if row['work_id'] in self._review_detail_titles and row.get('title') in (None, '', row['work_id'])
                                 else row for row in visible_rows)
            rows = filters.rows(visible_rows,positive,now=datetime.now(),session_started=self._review_started)
            page = min(self._review_pages[positive],max(0,(len(rows)-1)//PAGE_SIZE))
            self._review_pages[positive] = page
            self.review_bodies[positive].clear()
            with self.review_bodies[positive]:
                if not rows:
                    ui.label('今日は該当なし').classes('pv-subtitle')
                for row in rows[page*PAGE_SIZE:(page+1)*PAGE_SIZE]:
                    wid = row['work_id']
                    if wid in self._review_undone:
                        _, operation = self._review_undone[wid]
                        outcome = self._review_outcomes.get(wid, 'absent')
                        with ui.row().classes('w-full items-center'):
                            ui.label(review_trace(row.get('title') or wid, outcome)).classes('flex-1')
                            button=ui.button('元に戻す',on_click=lambda _,row=row,operation=operation:self._review_change(row,operation)).props(f'flat data-testid=learning-review-restore-{wid}')
                            button.set_enabled(not self._review_busy and not self._review_refresh_busy and not self._storage_busy and not self.state.saving)
                        continue
                    with ui.row().classes('w-full items-center').style('padding:6px;border:1px solid var(--pv-border);border-radius:8px;background:var(--pv-surface)'):
                        wid = row['work_id']
                        ui.label({'like':'→','superLike':'↑','skip':'↓','hate':'←'}.get(row['verdict'], '')).classes('pv-subtitle').style('width:16px')
                        thumb_size = 80 if positive else 60
                        detail = self._review_details.get(wid)
                        if self.discovery is None or wid in self._discovery_image_ids:
                            ui.image(self._images.url(wid,0,'thumb')).style(f'width:{thumb_size}px;height:{thumb_size}px')
                            if (self.discovery is not None and row.get('title') in (None, '', wid)
                                    and wid not in self._review_detail_titles):
                                self._review_schedule_detail(wid)
                        elif detail is not None:
                            ui.image(detail[1].url(wid,0,'thumb')).style(f'width:{thumb_size}px;height:{thumb_size}px').on(
                                'error', lambda _,w=wid,p=detail[1]:self._review_detail_image_failed(w,p))
                        elif wid in self._review_detail_failed:
                            ui.button('取得失敗（タップで再取得）',
                                      on_click=lambda _,w=wid:self._review_retry_detail(w)).props('flat no-caps')
                        else:
                            ui.label('読み込み中…').classes('pv-subtitle')
                            self._review_schedule_detail(wid)
                        with ui.column().classes('flex-1 gap-0').style('min-width:100px'):
                            ui.label(row.get('title') or wid).classes('truncate w-full')
                            ui.label(row.get('judged_at','')).classes('pv-subtitle').style('font-size:9.5px')
                            if wid in self._review_detail_failed and (detail is not None or wid in self._discovery_image_ids):
                                ui.button('取得失敗（タップで再取得）',
                                          on_click=lambda _,w=wid:self._review_retry_detail(w)).props('flat no-caps')
                        if self.discovery is not None and positive:
                            saved = bool(row.get('work_meta')) and not row.get('is_deleted')
                            if saved:
                                ui.label('保存済み').classes('pv-subtitle').props('data-testid=discovery-review-saved')
                            else:
                                save_button = ui.button('保存する',
                                    on_click=lambda _,wid=row['work_id']:self.save_managed(wid)).props('flat data-testid=discovery-review-save')
                                save_button.set_enabled(not self._review_busy and not self._review_refresh_busy)
                        button=ui.button('取り消す',on_click=lambda _,row=row:self._review_change(row)).props(f"flat data-testid=learning-review-undo-{row['work_id']}")
                        button.set_enabled(not self._review_busy and not self._review_refresh_busy and not self._storage_busy and not self.state.saving)
                with ui.row():
                    previous=ui.button('前',on_click=lambda _,positive=positive:self._review_page(positive,-1)).props('flat')
                    previous.set_enabled(page>0)
                    ui.label(f'{page+1} / {max(1,(len(rows)+PAGE_SIZE-1)//PAGE_SIZE)}')
                    following=ui.button('次',on_click=lambda _,positive=positive:self._review_page(positive,1)).props('flat')
                    following.set_enabled((page+1)*PAGE_SIZE<len(rows))

    async def _on_learning_bookmark(self, event):
        payload = self._payload(event)
        if payload.get('bookmark_token') != self._bookmark_token:
            return
        self._bookmark_token = uuid4().hex
        self._update_buttons()
        await self._bookmark_current(payload)

    async def _bookmark_current(self, payload):
        item = self.state.current_artwork
        actions = getattr(self.services, 'pixiv_actions', None)
        if (not self.active or self.state.closed or self.services.closing or self._storage_busy
                or self.state.saving or self._review_open or self._review_mutating
                or self._bookmark_busy or not self.state.ready or item is None
                or actions is None or payload.get('work_id') != item.work_id
                or type(payload.get('generation')) is not int or payload['generation'] != self.state.generation):
            return False
        generation = self.state.generation
        def cancelled():
            return (self.state.closed or self.services.closing or not self.active
                    or self.state.generation != generation or self.state.current_artwork is not item)
        self._bookmark_busy = True
        self._update_buttons()
        intent = None
        key = None
        try:
            account = actions.cached_verified_account_id
            if account is None:
                account = await actions.verify_account(cancelled=cancelled)
            if cancelled():
                return False
            key = (account, item.work_id)
            prior = self._bookmark_uncertain.get(key)
            state = getattr(actions, 'state', None)
            result = getattr(actions, 'result', None)
            if prior is None and callable(state) and callable(result):
                shared = state(account, 'illust', item.work_id)
                if shared.unknown:
                    saved = result(shared.operation_id)
                    prior = getattr(saved, 'intent', None)
                    from ..pixiv_write_client import WriteIntent
                    if (not isinstance(prior, WriteIntent) or prior.account_id != account
                            or prior.target_id != item.work_id or prior.work_kind != 'illust'
                            or prior.author_id != item.author_id
                            or prior.action not in ('bookmark_add', 'bookmark_delete')):
                        if not cancelled():
                            self.status.set_text('ブックマークの結果を確認できません。もう一度押すと状態を確認します。')
                        return False
                    self._bookmark_uncertain[key] = prior
            if prior is not None:
                reconcile = getattr(actions, 'reconcile', None)
                if not callable(reconcile):
                    return False
                outcome = await reconcile(prior, author_name=item.author, cancelled=cancelled)
                intent = prior
            else:
                from ..pixiv_write_client import WriteIntent
                action = 'bookmark_delete' if key in self._bookmark_confirmed else 'bookmark_add'
                intent = WriteIntent(uuid4().hex, account, item.work_id, action,
                                     'illust', item.author_id, 0)
                outcome = await actions.execute(intent, author_name=item.author, cancelled=cancelled)
                if action == 'bookmark_add' and outcome.status == 'already' and outcome.remote_state is True:
                    self._bookmark_confirmed.add(key)
                    if cancelled():
                        return False
                    intent = WriteIntent(uuid4().hex, account, item.work_id, 'bookmark_delete',
                                         'illust', item.author_id, 0)
                    outcome = await actions.execute(intent, author_name=item.author, cancelled=cancelled)
                if outcome.status == 'indeterminate':
                    self._bookmark_uncertain[key] = intent
                    if not cancelled():
                        self.status.set_text('ブックマークの結果を確認できません。もう一度押すと状態を確認します。')
                    return False
            desired = intent.action == 'bookmark_add'
            if outcome.status in ('confirmed', 'already', 'observed') and outcome.remote_state is desired:
                if desired:
                    self._bookmark_confirmed.add(key)
                    if outcome.status == 'confirmed':
                        self._bookmark_new.add(key)
                else:
                    self._bookmark_confirmed.discard(key)
                    self._bookmark_new.discard(key)
                self._bookmark_uncertain.pop(key, None)
                if not cancelled():
                    self.status.set_text('pixivへブックマークした' if desired else
                                         'pixivのブックマークは既に外れている' if outcome.status == 'already' else
                                         'pixivのブックマークを外した')
                return True
            if prior is not None and outcome.status == 'observed':
                self._bookmark_uncertain.pop(key, None)
                if outcome.remote_state is True:
                    self._bookmark_confirmed.add(key)
                elif outcome.remote_state is False:
                    self._bookmark_confirmed.discard(key)
                    self._bookmark_new.discard(key)
            if not cancelled():
                self.status.set_text('pixivのブックマーク切替に失敗しました')
                self.toast.show_gave_up('pixivのブックマーク切替に失敗しました', 'もう一度♥を押すとやり直せます', heavy=False)
            return False
        except asyncio.CancelledError:
            result = getattr(actions, 'result', None)
            outcome = result(intent.operation_id) if intent is not None and callable(result) else None
            if key is not None and outcome is not None:
                if outcome.status == 'indeterminate':
                    self._bookmark_uncertain[key] = intent
                elif outcome.status in ('confirmed', 'already'):
                    if outcome.remote_state is True:
                        self._bookmark_confirmed.add(key)
                        if intent.action == 'bookmark_add' and outcome.status == 'confirmed':
                            self._bookmark_new.add(key)
                    elif outcome.remote_state is False:
                        self._bookmark_confirmed.discard(key)
                        self._bookmark_new.discard(key)
            raise
        except Exception:
            if not cancelled():
                self.status.set_text('pixivのブックマーク切替に失敗しました')
                self.toast.show_gave_up('pixivのブックマーク切替に失敗しました', 'もう一度♥を押すとやり直せます', heavy=False)
            return False
        finally:
            self._bookmark_busy = False
            if not self.state.closed:
                self._update_buttons()

    def _review_schedule_detail(self, wid):
        if (wid not in self._review_detail_pending and wid not in self._review_detail_failed
                and wid not in self._review_details):
            self._review_detail_pending.add(wid)
            background_tasks.create(self._review_fetch_detail(wid), name='review detail')

    def _review_retry_detail(self, wid):
        if wid in self._review_detail_pending:
            return
        self._review_detail_failed.discard(wid)
        self._review_detail_pending.add(wid)
        background_tasks.create(self._review_fetch_detail(wid), name='review detail retry')
        self._render_review()

    def _review_detail_image_failed(self, wid, pin):
        detail = self._review_details.get(wid)
        if detail is None or detail[1] is not pin:
            return
        self._review_details.pop(wid, None)
        self._review_records.pop(wid, None)
        self._review_detail_failed.add(wid)
        self._render_review()

    async def _review_fetch_detail(self, wid, *, retry=False):
        if self.discovery is None or not self._review_valid(self._review_epoch):
            self._review_detail_pending.discard(wid)
            return
        epoch = self._review_epoch
        if retry:
            self._review_detail_failed.discard(wid)
            self._review_detail_pending.add(wid)
        pin = None
        try:
            row = next(r for r in self._review_rows if r['work_id'] == wid)
            meta = row.get('work_meta')
            if isinstance(meta, str):
                try: meta = json.loads(meta)
                except (ValueError, TypeError): meta = None
            if not isinstance(meta, dict): meta = {}
            urls = meta.get('urls') if isinstance(meta.get('urls'), dict) else {}
            known_thumb = urls.get('small') or urls.get('thumb') or meta.get('url') or row.get('thumbnail_url')
            title = row.get('title') if row.get('title') not in (None, '', wid) else None
            raw = {}
            if not title or not known_thumb:
                async with self._review_detail_lock:
                    if not self._review_valid(epoch):
                        return
                    raw = await asyncio.to_thread(self.discovery.reader.get_discovery_work, wid)   # original: get_work_info, default background lane
                if str(raw.get('id')) != wid:
                    raise ValueError('wrong work identity')
            title = title or raw.get('title')
            if type(title) is str and title and len(title) <= 512 and self._review_valid(epoch):
                self._review_detail_titles[wid] = title
            if wid in self._discovery_image_ids:
                if title:
                    self._review_detail_failed.discard(wid)
                else:
                    self._review_detail_failed.add(wid)
                return
            tags = row.get('tags') or []
            if isinstance(tags, str):
                try: tags = json.loads(tags)
                except (ValueError, TypeError): tags = []
            candidate = dict(work_id=wid, title=title, author_id=raw.get('user_id') or row.get('author_id'),
                             author_name=raw.get('user_name') or '', tags=raw.get('tags') or tags,
                             thumbnail_url=known_thumb or raw.get('thumbnail_url'), source='history')
            record = self.discovery._record_for(candidate)
            records = dict(self._review_records)
            records[wid] = record
            pin = self.services.acquire_ranking_images(tuple(records.values()))
            if not self._review_valid(epoch):
                pin.close()
                return
            old_pin = self._review_image_pin
            self._review_records = records
            self._review_image_pin = pin
            self._review_details = {key:(item.as_artwork(), pin) for key, item in records.items()}
            if old_pin is not None:
                old_pin.close()
            if record.title:
                self._review_detail_titles[wid] = record.title
            pin = None
            if title:
                self._review_detail_failed.discard(wid)
            else:
                self._review_detail_failed.add(wid)
        except Exception:
            if pin is not None:
                pin.close()
            if self._review_valid(epoch):
                self._review_detail_failed.add(wid)
        finally:
            self._review_detail_pending.discard(wid)
            if self._review_valid(self._review_epoch):
                self._render_review()

    async def _review_change(self,row,undo_operation=None):
        epoch=self._review_epoch
        if not self._review_valid(epoch) or self._review_busy or self._review_refresh_busy or self._storage_busy or self.state.saving or self._review_session_id is None:return
        if undo_operation is not None:
            current = self._review_undone.get(row['work_id'])
            if current is None or current[1] != undo_operation:
                return
        self._review_busy=True
        self._review_mutating=True
        action='restore' if undo_operation else 'undo'
        key=(row['work_id'],action,undo_operation or row['revision'])
        operation=self._review_operations.setdefault(key,uuid4().hex)
        self._render_review()
        self._update_buttons()
        try:
            # Acceptance happens before awaiting. Closing this view cannot cancel the queued write.
            pending=(self.storage.submit_review_restore(self._review_session_id,undo_operation,operation) if undo_operation else
                     self.storage.submit_review_undo(self._review_session_id,row['work_id'],row['revision'],operation))
            await self.storage._wait(pending)
            queue_ids = (self._review_queue_ids | {item.work_id for item in self.state.artworks}
                         if self.discovery is not None else {item.work_id for item in self._artworks})
            current = self.state.current_artwork
            outcome = requeue_outcome(queue_ids,
                                      current.work_id if current else None, row['work_id'])
            pending_ids = tuple(item.work_id for item in self.state.artworks[self.state.index:])
            self.reload_storage(review_queue=(pending_ids, None if undo_operation else row['work_id'], outcome))
            if not self._review_valid(epoch):return
            if undo_operation:
                self._review_undone.pop(row['work_id'],None)
                self._review_outcomes.pop(row['work_id'],None)
            else:
                self._review_undone[row['work_id']]=(row,operation)
                self._review_outcomes[row['work_id']]=outcome
            packet=await self.storage.review_snapshot(self._review_session_id)
            if not self._review_valid(epoch):return
            self._review_rows=tuple(packet['rows'])
            self.review_status.set_text('判定を元に戻しました。' if undo_operation else '判定を取り消しました。')
        except Exception as error:
            if self._review_valid(epoch):
                from ..learning_repository import LearningConflict
                self.review_status.set_text('別の判定に更新されています。閉じて一覧を読み直してください。' if isinstance(error,LearningConflict) else '保存できませんでした。同じ操作を再試行できます。')
        finally:
            self._review_mutating=False
            if self._review_valid(epoch):
                self._review_busy=False
                self._render_review()
            if not self.state.closed:self._update_buttons()

    def _explore_slider(self,label,testid):
        """元 discovery_tab.py:3199-3250 つまみ1本。現在値は%の実数のみ。ドラッグ中は右端の数字だけを書き換え、離したときに保存する。"""
        with ui.row().classes('w-full items-center justify-between no-wrap'):
            ui.label(label).style('font-size:12px;color:var(--pv-text)')
            value_text=ui.label('0%').style('font-size:11px;color:var(--pv-muted)')
        slider=ui.slider(min=0,max=100,step=1,value=0,on_change=lambda e,t=value_text:t.set_text(f"{int(round(e.value or 0))}%")).props(
            f'data-testid={testid}').classes('w-full')
        # 離したときの値は change イベントの値を使う（モデル更新は間引かれて最後の値が遅れることがある）。
        slider.on('change',lambda e,s=slider:self._settings_commit(s,e.args))
        return slider,value_text

    def _settings_controls(self):
        enabled=not self._settings_busy and self._settings_snapshot is not None
        for control in (self.settings_mode,self.settings_subject,self.settings_style):control.set_enabled(enabled)
        self._update_buttons()

    def _settings_commit(self,slider=None,released=None):
        """離したとき／検索対象を変えたときに保存する。読み込み中の値の反映では保存しない。"""
        if self._settings_loading or not self._settings_open or self._settings_busy or self._settings_snapshot is None:return
        if slider is not None and isinstance(released,(int,float)) and not isinstance(released,bool):
            slider.set_value(int(round(min(100,max(0,released)))))
        background_tasks.create(self.save_discovery_settings(),name='discovery settings save')

    def _set_settings_open(self,opened):
        self._settings_open=bool(opened)
        self.settings_panel.set_visibility(self._settings_open)
        self.settings_button.props(f"aria-expanded={'true' if self._settings_open else 'false'}")
        self.settings_arrow.props(f"name={'keyboard_arrow_down' if self._settings_open else 'keyboard_arrow_right'}")

    async def toggle_discovery_settings(self):
        if self._settings_open:
            self._close_settings()
        else:
            await self.open_discovery_settings()

    async def open_discovery_settings(self):
        if not self.active or self.state.closed or self._storage_busy or self.state.saving or self._settings_busy:return
        self.close_summary()
        self._settings_epoch+=1
        self._set_settings_open(True)
        await self.load_discovery_settings()

    def _close_settings(self):
        self._settings_epoch+=1
        self._set_settings_open(False)
        self._settings_busy=False
        if not self.state.closed:self._update_buttons()

    async def load_discovery_settings(self):
        if not self._settings_open or self._settings_busy:return
        epoch=self._settings_epoch
        self._settings_busy=True
        self._settings_controls()
        try:
            snapshot=await self.discovery.settings_snapshot()
            if self.state.closed or not self._settings_open or epoch!=self._settings_epoch:return
            self._settings_snapshot=snapshot
            self._settings_loading=True
            try:
                self.settings_mode.set_value(snapshot.values['discovery_search_mode'])
                self.settings_subject.set_value(int(round(float(snapshot.values['discovery_ratio_subject']))))
                self.settings_style.set_value(int(round(float(snapshot.values['discovery_ratio_style']))))
            finally:
                self._settings_loading=False
            self.settings_status.set_text('')
        except Exception:
            if not self.state.closed and epoch==self._settings_epoch:self.settings_status.set_text('設定を読み込めませんでした。開き直してください。')
        finally:
            if not self.state.closed and epoch==self._settings_epoch:
                self._settings_busy=False
                self._settings_controls()

    async def save_discovery_settings(self):
        if not self._settings_open or self._settings_busy or self._settings_snapshot is None:return
        epoch=self._settings_epoch
        values={'discovery_search_mode':self.settings_mode.value,'discovery_ratio_subject':int(round(self.settings_subject.value)),'discovery_ratio_style':int(round(self.settings_style.value))}
        signature=(self._settings_snapshot.revision,tuple(values.items()))
        operation=self._settings_operations.setdefault(signature,uuid4().hex)
        self._settings_busy=True
        self._settings_controls()
        try:
            accepted=self.discovery.submit_settings_update(self._settings_snapshot.revision,values,operation)
            snapshot=await self.discovery._wait(accepted)
            if self.state.closed or not self._settings_open or epoch!=self._settings_epoch:return
            self._settings_snapshot=snapshot
            ratios=snapshot.effective_ratios
            self.settings_status.set_text(f"保存しました。確実枠 {ratios['sure']:.0%} / 題材探索 {ratios['subject']:.0%} / 絵柄探索 {ratios['style']:.0%}")
            # 元は設定値を書くだけで画面は作り直さない。次に並べ替える候補から新しい割合を読む。
        except Exception as error:
            if not self.state.closed and epoch==self._settings_epoch:
                from ..learning_repository import LearningConflict
                self.settings_status.set_text('別の画面で設定が変わりました。開き直してから操作してください。' if isinstance(error,LearningConflict) else '設定を保存できませんでした。値を確認して再試行してください。')
        finally:
            if not self.state.closed and epoch==self._settings_epoch:
                self._settings_busy=False
                self._settings_controls()

    def _set_trend_open(self, opened: bool):
        if self.discovery is None or self.state.closed:return
        if opened:
            self.close_summary()
        self._trend_open=bool(opened)
        self.trend_content.set_visibility(self._trend_mode != 'fold' or self._trend_open)
        self.trend_button.set_text('傾向を閉じる' if self._trend_open else '傾向を見る')
        self.trend_button.props(f"aria-expanded={'true' if self._trend_open else 'false'}")

    def toggle_trend(self):
        if self.discovery is None or self.state.closed or not self.active or self.services.closing:return
        self._set_trend_open(not self._trend_open)
        if self._trend_open:
            self._revalidate_local_trend()

    def _on_trend_measure(self, event):
        if self.discovery is None or self.state.closed or self._review_open:
            return
        args = event.args if isinstance(event.args, dict) else {}
        try:
            width, height, viewport = (float(args[key]) for key in ('width', 'height', 'viewport'))
            item = self.state.current_artwork
            aspect = float(args.get('aspect') or (item.aspect_ratio if item else 1.0) or 1.0)
            if not all(math.isfinite(x) and x >= 0 for x in (width, height, viewport, aspect)):
                return
        except (ValueError, TypeError, KeyError):
            return
        mode = layout_mode(width, height, aspect, self._trend_mode)
        rows = self._trend_capacity.update(viewport) if viewport else (self._trend_capacity.rows or 1)
        changed = mode != self._trend_mode or rows != getattr(self, '_trend_rail_rows', None)
        if mode != self._trend_mode:
            if mode == 'fold':
                actions = self.buttons['like'].parent_slot.parent
                self.trend_header.move(self.trend_main, self.trend_main.default_slot.children.index(actions))
            else:
                self.trend_header.move(self.trend_side, 0)
        self._trend_mode, self._trend_rail_rows = mode, rows
        self.trend_layout.props(f'data-mode={mode}')
        self.settings_button.set_visibility(mode != 'fold')
        self.settings_panel.set_visibility(self._settings_open and mode != 'fold')
        self.trend_button.set_visibility(mode == 'fold')
        self._set_trend_open(self._trend_open)
        if changed and self._trend_snapshot is not None:
            if self._trend_snapshot.get('local_seed_rows'):
                self._revalidate_local_trend()
            else:
                self._render_trend(self._trend_snapshot)

    def _revalidate_local_trend(self):
        if self._trend_snapshot and self._trend_snapshot.get('local_seed_rows'):
            self._render_trend(dict(self._trend_snapshot, local_seed_rows=()))
            background_tasks.create(self._refresh_local_trend(), name='learning local trend recheck')

    def refresh_tagger_order(self):
        if self.discovery is None or self.state.closed or self.services.closing:
            return
        self._tagger_rank_epoch += 1
        background_tasks.create(self._rerank_tagger_order(self._tagger_rank_epoch), name='learning tagger rerank')

    async def _rerank_tagger_order(self, epoch):
        while not self.state.closed and not self.services.closing and epoch == self._tagger_rank_epoch:
            if self.state.saving or getattr(self, '_storage_busy', False):
                await asyncio.sleep(0.1)
                continue
            state = self.state
            generation = state.generation
            revision = self._refill_merge_revision
            prefix, pending = state.artworks[:state.index], state.artworks[state.index:]
            if not pending:
                return
            try:
                order, frames = await self.discovery.rank_pending(tuple(a.work_id for a in pending), tuple(a.author_id for a in prefix if a.author_id))
            except Exception:
                return
            if state.closed or self.services.closing or epoch != self._tagger_rank_epoch:
                return
            if (self.state is not state or state.saving or state.generation != generation
                    or self._refill_merge_revision != revision):
                continue
            by_id = {a.work_id:a for a in pending}
            if len(order) != len(pending) or set(order) != set(by_id):
                return
            self._discovery_frames.update(frames)
            if tuple(a.work_id for a in pending) != tuple(order):
                state._invalidate()
                state.artworks = prefix + tuple(by_id[wid] for wid in order)
                self.refresh()
            return

    def watch_local_seeds(self):
        if self.state.closed or getattr(self.discovery, '_seed_executor', None) is None:
            return
        if self._local_revision_task is None or self._local_revision_task.done():
            self._local_revision_task = background_tasks.create(self._watch_local_revision(), name='learning local revision watch')
        future = getattr(self.discovery, '_seed_future', None)
        if self._seed_watch_task is not None and not self._seed_watch_task.done():
            self._seed_watch_task.cancel()
        if future is None:
            self._seed_watch_task = background_tasks.create(self._refresh_local_trend(), name='learning local seed refresh')
        else:
            self._seed_watch_task = background_tasks.create(self._watch_local_seeds(future), name='learning local seeds')

    async def _watch_local_revision(self):
        local = getattr(getattr(self, 'discovery', None), 'local_tag_view', None)
        if local is None:
            return
        while not self.state.closed and not self.services.closing:
            revision = await asyncio.to_thread(local.revision)
            if self.active and revision != self._local_trend_revision:
                self._local_trend_revision = revision
                await self._refresh_local_trend()
                self.refresh_tagger_order()
            await asyncio.sleep(0.5)

    async def _refresh_local_trend(self):
        if self.state.closed or self.services.closing:
            return
        try:
            trend = await self.discovery.trend_snapshot()
            if not self.state.closed and not self.services.closing:
                self._render_trend(trend)
        except Exception:
            return

    async def _watch_local_seeds(self, future):
        wrapped = asyncio.wrap_future(future)
        while not self.state.closed and not self.services.closing:
            try:
                await asyncio.wait_for(asyncio.shield(wrapped), timeout=3.0)
            except asyncio.TimeoutError:
                pass
            except Exception:
                return
            if self.state.closed or self.services.closing:
                return
            try:
                self._render_trend(await self.discovery.trend_snapshot())
            except Exception:
                return
            if future.done():
                self.refresh_tagger_order()
                return

    def _render_trend(self,trend):
        revision = trend.get('local_revision')
        local = getattr(getattr(self, 'discovery', None), 'local_tag_view', None)
        if revision is not None and local is not None and local.revision() != revision:
            trend = dict(trend, rows=(), local_seed_rows=(), local_revision=None)
            background_tasks.create(self._refresh_local_trend(), name='learning stale trend recheck')
        self._trend_snapshot = trend
        self.trend_content.clear()
        self.trend_label.set_text(f"これまでの判定: {trend['judged']}枚")
        if not trend['ready']:
            with self.trend_content:
                ui.label('いまはあなたの保存から出しています').classes('pv-subtitle')
                for row in trend.get('local_seed_rows', ())[:6]:
                    with ui.row().classes('w-full items-center justify-between'):
                        ui.label(row['display']).classes('truncate')
                        ui.label(f'保存 {row["count"]} 件').classes('pv-subtitle')
            self._trend_visible_rows = len(trend.get('local_seed_rows', ())[:6])
            return
        shown = displayed_rows(trend['rows'], self._trend_mode, getattr(self, '_trend_rail_rows', 1))
        self._trend_visible_rows = len(shown)
        with self.trend_content:
            if not shown:
                ui.label('まだ傾向が出ていません').classes('pv-subtitle')
            with ui.element('div').classes('pv-trend-rows w-full').style(
                    f'grid-template-rows:repeat({max(1, (len(shown)+2)//3)},minmax(0,1fr));'):
                for row, ratio in shown:
                    with ui.row().classes('pv-trend-item w-full items-center no-wrap'):
                        ui.label(row['display']).classes('truncate').style('width:84px')
                        ui.label('MAX' if row.get('at_cap') else '').style('width:24px;font-size:8px')
                        ui.linear_progress(value=ratio,show_value=False).classes('flex-1')
                        ui.label(f"いいね{row['like_count']}・表示{row['shown_count']}").classes('pv-subtitle').style('width:96px;font-size:10.5px')

    def _initial_current(self,epoch):
        return (self.discovery is not None and self.active and not self.state.closed
                and not self.services.closing and epoch==self._initial_epoch)

    def _stop_initial_load(self):
        self._initial_epoch+=1
        task=self._initial_task
        if task is not None and not task.done():task.cancel()
        self._initial_task=None

    def _start_initial_load(self):
        if (self.discovery is None or self._initial_done or not self.active or self.state.closed
                or self.services.closing or self._storage_busy or not self._storage_ready):return
        if self._initial_task is not None and not self._initial_task.done():return
        epoch=self._initial_epoch
        self._initial_task=background_tasks.create(self._load_initial_candidates(epoch),name='discovery initial load')
        self._update_buttons()

    async def _load_initial_candidates(self,epoch):
        try:
            result=await self.discovery.initialize_once(getattr(self.services,'refresh_discovery_context',None),
                    is_current=lambda:self._initial_current(epoch))
            if not self._initial_current(epoch) or result is None:return
            self._initial_done=True
            self._refill.arm(self._refill_clock())
            self._refill.active=self.active
            self._refill.last_failed=bool(result.failed)
            batch,records=await self.discovery.refill_snapshot()
            if not self._initial_current(epoch):return
            self._append_refill(batch,records,self._refill_epoch)
            if self.state.current_artwork is None:
                self.refresh()
            else:self._kick_candidate_refill()
        except asyncio.CancelledError:
            raise
        except Exception:
            if self._initial_current(epoch):
                self._initial_done=True
                self._refill.arm(self._refill_clock())
                self._refill.last_failed=True
                if self.state.current_artwork is not None:
                    self._notice='初回候補を取得できませんでした。'
                    self.toast.show_gave_up('初回の候補を取得できませんでした',
                                            '「候補を取得」でもう一度探せます。', heavy=True, key='initial')
                self.refresh()
        finally:
            if epoch==self._initial_epoch:
                self._initial_task=None
                if not self.state.closed:
                    self._update_buttons()
                    self._render_refill_status()

    def _stop_candidate_refill(self):
        self._refill_epoch+=1
        self._refill.pause()
        self._refill_wake.set()
        task=self._refill_task
        if task is not None and not task.done():task.cancel()
        self._refill_task=None

    def _refill_current(self,epoch):
        return (self.discovery is not None and self._refill.armed and self.active
                and not self.state.closed and not self.services.closing and epoch==self._refill_epoch)

    def _remaining_candidates(self):
        return max(0,len(self.state.artworks)-self.state.index)

    def _topup_has_room(self):
        stats=getattr(self._discovery_pin,'prefetch_stats',None)
        return not callable(stats) or not stats()['full']

    def _kick_candidate_refill(self):
        self._refill_wake.set()
        if (self.discovery is None or not self._refill.armed or not self.active or self.state.closed
                or self.services.closing or self._storage_busy or not self._storage_ready):return
        self._render_refill_status()
        if self._remaining_candidates()>0 and not self._topup_has_room():return
        if self._refill_task is not None and not self._refill_task.done():return
        if self._refill.delay(self._remaining_candidates(),self._refill_clock()) is None:return
        epoch=self._refill_epoch
        self._refill_task=background_tasks.create(self._run_candidate_refill(epoch),name='discovery candidate refill')

    async def _wait_candidate_refill(self,delay):
        self._refill_wake.clear()
        try:await asyncio.wait_for(self._refill_wake.wait(),timeout=delay)
        except asyncio.TimeoutError:pass

    def _retry_candidate_restock(self) -> None:
        if (not self._refill_current(self._refill_epoch) or self.state.current_artwork is not None
                or not self.candidate_status_retry.visible or not self.candidate_status_retry.enabled):
            return
        self._refill.resume(self._refill_clock())
        self._kick_candidate_refill()

    def _render_refill_status(self):
        if self.discovery is None:return
        policy=self._refill
        if not policy.armed:text='設定された取得元から初回候補を読み込んでいます。'
        elif not self.active:text='この画面を離れている間は自動補充を停止しています。'
        elif policy.gave_up:
            text=('候補の取得に失敗しました。' if policy.failed_rounds else 'しばらく新しい候補が見つかりませんでした。')+' 自動補充は停止しています。「候補を取得」で再開できます。'
        elif policy.running:text='次の候補を取得しています。'
        elif self._remaining_candidates()==0:
            text=('候補を取得できませんでした。' if policy.last_failed else '現在は新しい候補がありません。')+' 30秒間隔で再取得します。'
        elif not self._topup_has_room():text='画像キャッシュが満杯のため補充を待っています。候補が尽きた場合は再取得します。'
        elif policy.empty_topups>=3:text='候補の補充をいったん止めています。候補が尽きると再取得します。'
        else:text=''
        self.refill_status.set_text(text)
        self._render_candidate_status()
        self._sync_refill_notice()

    def _sync_refill_notice(self):
        """仕入れが止まったことを、消えない通知枠で一度だけ知らせる（元 discovery_tab.py:896-901, 1137-1151）。"""
        policy=self._refill
        stopped=policy.armed and policy.gave_up and not self.candidate_status_stage.visible
        if stopped and not self._refill_notice_shown:
            self._refill_notice_shown=self.toast.show_gave_up(
                '候補を取りにいけませんでした' if policy.failed_rounds else 'しばらく新しい候補が見つかりませんでした',
                '通信を確かめてから、「候補を取得」で再開できます。' if policy.failed_rounds else '「候補を取得」で再開できます。',
                heavy=True,key='refill')
        elif not stopped and self._refill_notice_shown:
            self._refill_notice_shown=False

    async def _run_candidate_refill(self,epoch):
        try:
            while self._refill_current(epoch):
                if self._storage_busy or not self._storage_ready:
                    self._refill_wake.clear()
                    await self._refill_wake.wait()
                    continue
                if self._remaining_candidates()>0 and not self._topup_has_room():
                    self._render_refill_status()
                    return
                delay=self._refill.delay(self._remaining_candidates(),self._refill_clock())
                self._render_refill_status()
                if delay is None:return
                if delay>0:
                    await self._wait_candidate_refill(delay)
                    continue
                # Reconcile data accepted by another view or a request that
                # finished while this view was paused before issuing another GET.
                cached,records=await self.discovery.refill_snapshot()
                if not self._refill_current(epoch):return
                if self._storage_busy:continue
                added=self._append_refill(cached,records,epoch)
                if added:
                    self._refill.complete(added,False,self._refill_clock())
                    continue
                if not self._refill.begin(self._remaining_candidates(),self._refill_clock()):
                    self._render_refill_status()
                    return
                self._render_refill_status()
                failed=False
                try:
                    batch=await self.discovery.fetch_candidates(
                        exclude_ids=tuple(a.work_id for a in self.state.artworks),
                        recent_author_ids=tuple(a.author_id for a in self.state.artworks[:self.state.index] if a.author_id),
                        is_current=lambda:self._refill_current(epoch))
                    if not self._refill_current(epoch):return
                    if batch is None:return
                    failed=bool(batch.failed)
                    cached,records=await self.discovery.refill_snapshot()
                    if not self._refill_current(epoch):return
                    if self._storage_busy:continue
                    added=self._append_refill(cached,records,epoch)
                except Exception:
                    if not self._refill_current(epoch):return
                    added=0;failed=True
                self._refill.complete(added,failed,self._refill_clock())
                self._render_refill_status()
        except asyncio.CancelledError:
            raise
        except Exception:
            if self._refill_current(epoch):
                self._refill.gave_up=True
                self._refill.failed_rounds+=1
                self._render_refill_status()
        finally:
            if epoch==self._refill_epoch:
                self._refill.running=False
                self._refill_task=None
                self._render_refill_status()

    def _append_refill(self,batch,records,epoch):
        if not self._refill_current(epoch) or self._storage_busy:return 0
        descriptions=getattr(batch,'descriptions',None)
        if descriptions:
            self._summary_descriptions.update(descriptions)
            head=self.state.current_artwork
            if head is not None and head.work_id in descriptions:
                self._summary_sync()
        existing={a.work_id for a in self.state.artworks}
        additions=tuple(a for a in batch.artworks if a.work_id not in existing)
        if not additions:return 0
        old_current=self.state.current_artwork
        pin=self.services.acquire_ranking_images(records)
        old=self._discovery_pin
        self._stop_prefetch(close=True)
        self._discovery_pin=self._images=pin
        if old is not None:self._refill_retired_pins.append((old,self.state.generation))
        if self._prefetch_task is not None and not self._prefetch_task.done():
            self._prefetch_task.add_done_callback(lambda _:self._release_refill_pins())
        self.state.artworks+=additions
        self._refill_merge_revision+=1
        known={a.work_id for a in self._artworks}
        self._artworks+=tuple(a for a in additions if a.work_id not in known)
        self._discovery_image_ids=frozenset(r.work_id for r in records)
        self._discovery_frames.update({a.work_id:batch.frames.get(a.work_id) for a in additions})
        if old_current is None:
            self._notice=''
        if old_current is None and not self.state.saving:self.refresh()
        else:
            self.progress.set_text(f"{self._baseline_count+self.state.index} / {len(self._artworks)} 件判定済み")
            self._start_prefetch()
        return len(additions)

    def _release_refill_pins(self,*,close=False):
        retained=[]
        prefetch_running=self._prefetch_task is not None and not self._prefetch_task.done()
        for pin,generation in self._refill_retired_pins:
            if close or (self.state.generation>generation and not prefetch_running):pin.close()
            else:retained.append((pin,generation))
        self._refill_retired_pins=retained

    async def fetch_discovery(self):
        if self._initial_task is not None and not self._initial_task.done():return
        if not self.active or self.state.closed or self._storage_busy or self.state.saving or self._managed_busy:return
        self._stop_candidate_refill()
        self._refill.arm(self._refill_clock())
        epoch=self._refill_epoch
        self._storage_busy=True
        self._update_buttons()
        try:
            refresh_context=getattr(self.services,'refresh_discovery_context',None)
            if callable(refresh_context):await refresh_context()
            if not self._refill_current(epoch):return
            batch=await self.discovery.fetch_candidates(is_current=lambda:self._refill_current(epoch))
            if not self._refill_current(epoch) or batch is None:return
            self._refill.last_failed=bool(batch.failed)
            notice=('候補を取得できませんでした。再試行できます。' if batch.nothing_and_failed else
                    '一部の候補ソースを取得できませんでした。' if batch.failed else
                    '候補を取得しました。' if batch.artworks else '新しい候補はありません。')
            if not batch.artworks and self.state.current_artwork is None:
                notice=''
        except Exception:
            self._refill.last_failed=True
            notice='候補を取得できませんでした。再試行できます。' if self.state.current_artwork is not None else ''
        finally:
            self._storage_busy=False
        if self._refill_current(epoch):self.reload_storage(notice=notice)

    def _on_managed_save(self,event):
        data=self._payload(event)
        item=self.state.current_artwork
        if (item is None or data.get('work_id')!=item.work_id or
            type(data.get('generation')) is not int or data['generation']!=self.state.generation):return
        background_tasks.create(self.save_managed(item.work_id),name='discovery managed save')

    async def save_managed(self, work_id=None):
        item=self.state.current_artwork
        wid=work_id if isinstance(work_id,str) else item.work_id if item else None
        if (not wid or not self.active or self.state.closed or self._managed_busy or self._storage_busy or self.state.saving):return
        self._managed_busy=True
        self._update_buttons()
        operation=self._managed_operations.setdefault(wid,uuid4().hex)
        try:
            job=await self.services.save_explicit_work(wid,operation,
                is_current=lambda:not self.state.closed and not self.services.closing)
            if job is None:return
            self._review_watched_jobs[job.job_id] = wid
            if self._review_open and self.active:
                self.review_status.set_text('保存を受け付けました。完了後に保存状態を更新します。')
            if not self.state.closed:
                self._notice='保存をキューに追加しました。完了・失敗・再試行はダウンロード画面で確認できます。'
                self.status.set_text(self._notice)
        except Exception:
            if self._review_open and self.active:
                self.review_status.set_text('保存を受け付けられませんでした。同じ操作で再試行できます。')
            if not self.state.closed:self.status.set_text('保存を受け付けられませんでした。同じ操作で再試行できます。')
        finally:
            self._managed_busy=False
            if self.state.closed or self.services.closing:
                self.services.release_managed_owner(self._managed_owner)
            else:self._update_buttons()

    def close(self) -> None:
        if self.state.closed:
            return
        if self.client.has_socket_connection:
            self.client.run_javascript(
                f"""document.getElementById('{self.root.html_id}')
                   ?.dispatchEvent(new CustomEvent('summary-dispose'))""")
        self.close_summary()
        self._summary_epoch += 1
        self._review_timer.cancel()
        if self._local_revision_task is not None:
            self._local_revision_task.cancel()
        self.toast.close()
        self.view_history.close()
        if self.reset_view is not None:self.reset_view.close()
        self._stop_initial_load()
        self._stop_candidate_refill()
        self._stop_prefetch(close=True)
        self._release_refill_pins(close=True)
        self._close_review()
        if self._review_image_pin is not None:
            self._review_image_pin.close()
            self._review_image_pin = None
        self._review_details.clear()
        self._review_records.clear()
        if self.discovery is not None and not self._managed_busy:
            self.services.release_managed_owner(self._managed_owner)
        if self.discovery is not None:
            self._close_settings()
        if self._discovery_pin is not None:
            self._discovery_pin.close()
            self._discovery_pin = None
        self.review_dialog.close()
        self.active = False
        self.state.close()
        self._image_token = None
        self._image_url = ""
        if self._session_id is not None:
            # submit_* already entered the same FIFO queue before judge returned.
            # Session release is therefore after its writes, even if the waiter
            # has not started or is cancelled by application shutdown.
            self.storage.release_session(self._session_id)
            self._session_id = None

    def _can_open_learning_work(self) -> bool:
        return (self.active and not self.state.closed and not self.services.closing
                and self._storage_ready and not self._review_open and not self._storage_busy
                and not self._review_mutating and not self._settings_busy and not self.state.saving
                and self._failed_command is None and self.state.last_error != 'image load failed')

    def _clicked_learning_work(self, event):
        payload = self._payload(event)
        item = self.state.current_artwork
        generation = payload.get('generation')
        if (not self._can_open_learning_work()
                or item is None or payload.get('work_id') != item.work_id
                or type(generation) is not int or generation != self.state.generation):
            return None
        if self.discovery is not None:
            if pixiv_url(item.work_id) is None:
                return None
            self._opened_work_ids.add(item.work_id)
        return item.work_id

    async def _open_learning_picture(self, event):
        if (work_id := self._clicked_learning_work(event)) is not None:
            await self.view_history.open_work(work_id, record=True)

    async def _open_learning_pixiv(self, event):
        if (work_id := self._clicked_learning_work(event)) is not None:
            await self.view_history.record_open(work_id)
