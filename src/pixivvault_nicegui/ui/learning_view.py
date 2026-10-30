"""架空作品のみを使う学習デモ画面。ブラウザ送信時点の作品世代を検証する。"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from concurrent.futures import Future
from datetime import datetime
from uuid import uuid4
from ..learning_review import ReviewFilter, PAGE_SIZE
from ..discovery_refill import DiscoveryRefill
from ..discovery_description import layout as summary_layout, is_blank as summary_is_blank, MAX_DESCRIPTION
from ..discovery_summary_fetch import fetch_summary, SummaryFetchFailed

from nicegui import background_tasks, ui

from ..image_events import source_matches
from ..learning import LearningState, MemoryJudgmentLedger, SaveCommand
from ..services import ApplicationServices
from .verified_image import VerifiedImage


class LearningView:
    CHOICES = (
        ("見送る", "skip", "arrowleft", "a", "1"),
        ("合わない", "hate", "arrowdown", "s", "2"),
        ("いいね", "like", "arrowright", "d", "3"),
        ("SuperLike", "superLike", "arrowup", "w", "4"),
    )

    def __init__(self, services: ApplicationServices) -> None:
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
        self._settings_open=False
        self._settings_snapshot=None
        self._settings_operations={}
        self.state = LearningState(services.artworks if self.storage is None else (), self.ledger)
        self._session_id: str | None = None
        self._storage_busy = False
        self._storage_ready = self.storage is None
        self._baseline_count = 0
        self._failed_command: SaveCommand | None = None
        self._notice = ""
        self._save_task = None
        self._review_session_id = None
        self._review_epoch = 0
        self._review_busy = False
        self._review_open = False
        self._review_rows = ()
        self._review_undone = {}
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
            ui.label("学習（デモ）").classes("pv-view-title")
            description = (
                "判定はデモ専用フォルダーに保存します。Undoはこの画面での直前の判定だけが対象です。"
                if self.storage is not None else
                "判定はこの画面内のデモ台帳だけに記録されます。実データへの保存はありません。"
            )
            if self.discovery is not None:
                description = "発掘の判定は専用保存先に記録します。設定された取得元から初回候補を自動で読み込み、この画面を開いている間に候補を補充します。作品保存は明示操作で実行します。"
            ui.label(description).classes("pv-subtitle")
            with ui.element('div').classes('w-full relative'):
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
            self.frame_label = ui.label().classes("pv-subtitle")
            self.frame_label.set_visibility(self.discovery is not None)
            self.progress = ui.label().classes("pv-counter")
            self.picture = VerifiedImage(alt="判定対象の作品画像").classes("pv-view-art")
            self.status = ui.label().classes("pv-subtitle pv-learning-status").props("role=status aria-live=polite")
            with ui.row().classes("items-center gap-2"):
                self.buttons = {}
                for label, choice, *_ in self.CHOICES:
                    button = ui.button(label, color=None).props(
                        "no-caps" if choice == "like" else "outline no-caps"
                    ).classes(f"pv-judgment pv-judgment-{choice}")
                    button.on("click", lambda event, c=choice: self._on_judgment(c, event),
                              js_handler=self._click_js())
                    self.buttons[choice] = button
            with ui.row().classes("items-center gap-2"):
                self.undo_button = ui.button("ひとつ戻す（Undo）", color=None).props("flat no-caps").classes("pv-undo")
                self.undo_button.on("click", self._on_undo, js_handler=self._click_js())
                self.retry_button = ui.button("画像を再読込", on_click=self.request_image).props("flat")
                self.save_retry_button = ui.button("保存を再試行", on_click=self.retry_save).props("flat")
                self.reload_button = ui.button("保存状態を再読込", on_click=self.reload_storage).props("flat")
            ui.label("矢印キー / WASD / 1〜4 で判定。画面を選択して操作してください。").classes("pv-subtitle")
            if self.discovery is not None:
                self.settings_button = ui.button('発掘設定',on_click=self.open_discovery_settings).props('flat data-testid=discovery-settings-open')
                self.fetch_button = ui.button('候補を取得', on_click=self.fetch_discovery).props('flat data-testid=discovery-fetch')
                self.refill_status = ui.label().classes('pv-subtitle').props('role=status data-testid=discovery-refill-status')
                self.managed_button = ui.button('作品を保存').props('flat data-testid=discovery-save')
                self.managed_button.on('click', self._on_managed_save, js_handler=self._click_js())
                with ui.row().classes('w-full items-center'):
                    self.trend_label = ui.label().classes('pv-subtitle').props('data-testid=discovery-trend')
                    self.trend_button = ui.button('傾向を見る',on_click=self.toggle_trend).props(
                        'flat dense aria-expanded=false data-testid=discovery-trend-toggle')
                    self.trend_button.tooltip('判定すると自動で閉じます')
                self.trend_content = ui.column().classes('w-full gap-1').props(
                    'data-testid=discovery-trend-panel')
                self.trend_content.set_visibility(False)
            self.review_button = ui.button('振り返り', on_click=self.open_review).props('flat data-testid=learning-review-open')
            self.review_button.set_visibility(self.storage is not None)
        with ui.dialog().props('data-testid=learning-review-dialog') as self.review_dialog, ui.card().classes('w-full').style('max-width:1000px'):
            ui.label('学習の振り返り').classes('pv-view-title')
            ui.label(('専用発掘保存先' if self.discovery is not None else 'デモ専用保存先') + 'の判定です。取消した作品は学習候補へ戻り、元に戻す操作はこの画面を閉じるまで有効です。').classes('pv-subtitle')
            with ui.row().classes('w-full'):
                self.review_text = ui.input('題名・タグ', on_change=lambda _: self._review_filter_changed()).props('outlined dense data-testid=learning-review-query')
                self.review_period = ui.select({'all':'全期間','today':'今日','7d':'7日','30d':'30日','session':'この学習セッション'},value='all',on_change=lambda _:self._review_filter_changed()).props('outlined dense data-testid=learning-review-period')
                self.review_unsaved = ui.checkbox('未保存だけ',value=True,on_change=lambda _:self._review_filter_changed())
                self.review_superlike = ui.checkbox('SuperLikeだけ',on_change=lambda _:self._review_filter_changed())
                self.review_hate = ui.checkbox('合わないだけ',on_change=lambda _:self._review_filter_changed())
            self.review_status = ui.label().props('role=status data-testid=learning-review-status')
            self.review_content = ui.row().classes('w-full items-start')
            ui.button('閉じる',on_click=self.review_dialog.close).props('flat data-testid=learning-review-close')
        self.review_dialog.on('hide',self._close_review)
        self._review_timer = ui.timer(1.0, self._poll_review_jobs)
        if self.discovery is not None:
            with ui.dialog().props('data-testid=discovery-settings-dialog') as self.settings_dialog,ui.card().classes('w-full').style('max-width:600px'):
                ui.label('発掘設定').classes('pv-view-title')
                self.settings_mode=ui.select({'all':'すべて','safe':'全年齢','r18':'R-18'},value='all',label='検索対象').props('outlined data-testid=discovery-settings-mode')
                self.settings_subject=ui.number('題材探索の割合 (%)',value=50,min=0,max=100).props('outlined data-testid=discovery-settings-subject')
                self.settings_style=ui.number('絵柄探索の割合 (%)',value=20,min=0,max=100).props('outlined data-testid=discovery-settings-style')
                ui.label('確実枠は残りの割合です。探索合計が95%を超える場合は比率を保って95%へ調整します。検索対象は次の候補取得から適用します。').classes('pv-subtitle')
                self.settings_status=ui.label().props('role=status data-testid=discovery-settings-status')
                with ui.row():
                    self.settings_save=ui.button('設定を保存',on_click=self.save_discovery_settings).props('data-testid=discovery-settings-save')
                    self.settings_reload=ui.button('設定を読込',on_click=self.load_discovery_settings).props('flat')
                    ui.button('閉じる',on_click=self.settings_dialog.close).props('flat')
            self.settings_dialog.on('hide',self._close_settings)


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
        self.picture.on("load", self._on_loaded)
        self.picture.on("error", self._on_failed)
        self.set_summary_viewport(1280, 720)
        self._summary_sync()
        self.set_active(False)
        if self.storage is None:
            self.refresh()
        else:
            self.picture.set_visibility(False)
            self.reload_storage()

    @staticmethod
    def _click_js() -> str:
        return """(event) => {
            const el = event.currentTarget;
            emit({work_id: el.getAttribute('data-work-id'),
                  generation: Number(el.getAttribute('data-generation')),
                  operation_id: crypto.randomUUID()});
        }"""

    @staticmethod
    def _key_js() -> str:
        return """(event) => {
            const tag = event.target?.tagName?.toLowerCase();
            if (event.repeat || event.isComposing || event.keyCode === 229 ||
                event.altKey || event.metaKey ||
                ['input', 'textarea', 'select'].includes(tag) ||
                event.target?.isContentEditable ||
                event.target?.closest('[role=dialog]') ||
                !event.currentTarget.getAttribute('data-active') ||
                event.currentTarget.getAttribute('data-active') !== 'true') return;
            const key = event.key.toLowerCase();
            if (event.ctrlKey) {
                if (key !== 'z' || event.shiftKey) return;
                event.preventDefault();
                event.stopPropagation();
                emit({action:'undo', work_id:event.currentTarget.getAttribute('data-work-id') || '',
                      generation:Number(event.currentTarget.getAttribute('data-generation')),
                      operation_id:crypto.randomUUID()});
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
            emit({choice, work_id:event.currentTarget.getAttribute('data-work-id'),
                  generation:Number(event.currentTarget.getAttribute('data-generation')),
                  operation_id:crypto.randomUUID()});
        }"""

    @staticmethod
    def _payload(event) -> dict:
        return event.args if isinstance(event.args, dict) else {}

    def _on_judgment(self, choice: str, event) -> None:
        self.judge(choice, self._payload(event))

    def _on_key(self, event) -> None:
        data = self._payload(event)
        if data.get('action') == 'undo':
            if self._review_open:
                return
            self._on_undo(event)
            return
        if data.get('action') == 'summary-close':
            if self.active:
                self.close_summary()
            return
        self.judge(data.get("choice"), data)

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
            raw = await asyncio.to_thread(fetch_summary, reader, *key)
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
        if not self.active or self.state.closed or self.services.closing or not self._storage_ready or self._storage_busy:
            return False
        generation = payload.get("generation")
        if type(generation) is not int:
            return False
        options = dict(expected_work_id=payload.get("work_id"),
                       expected_generation=generation, operation_id=payload.get("operation_id"))
        if self.storage is not None:
            command = self.state.prepare_judgment(choice, **options)
            if command is None:
                return False
            self.close_summary()
            return self._start_save(command)
        success = self.state.judge(choice, **options)
        if success:
            self.close_summary()
            self.refresh()
        elif self.state.last_error:
            self.status.set_text(self.state.last_error)
        return success

    def _on_undo(self, event) -> None:
        payload = self._payload(event)
        item = self.state.current_artwork
        # Undo は直前の操作で画面が切り替わった後に届くイベントを拒否する。
        if (not self.active or self.state.closed or self.services.closing or not self._storage_ready or self._storage_busy or
                payload.get("work_id") != (item.work_id if item else "") or
                type(payload.get("generation")) is not int or
                payload["generation"] != self.state.generation):
            return
        if self.storage is not None:
            command = self.state.prepare_undo(operation_id=payload.get("operation_id"))
            if command is not None:
                self._start_save(command)
            return
        if self.state.undo(operation_id=payload.get("operation_id")):
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
        self.status.set_text("取消を保存しています…" if command.undo else "判定を保存しています…")
        self._update_buttons()
        self._save_task = background_tasks.create(self._persist(command, pending), name="learning save")
        return True

    async def _persist(self, command: SaveCommand, pending: Future) -> None:
        try:
            # Cancelling a UI waiter must not cancel an accepted executor job.
            wrapped = asyncio.wrap_future(pending)
            wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            await asyncio.shield(wrapped)
        except Exception as exc:
            self._save_failed(command, exc)
        else:
            if self.services.closing:
                self.close()
            elif self.state.complete_save(command):
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
                self.refresh()
                if self.discovery is not None:
                    try:
                        trend=await self.discovery.trend_snapshot()
                        if not self.state.closed:self._render_trend(trend)
                    except Exception:
                        if not self.state.closed:self.trend_label.set_text('判定は保存済みです。傾向を読み込めませんでした。')

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
                not self._storage_ready or self._storage_busy):
            return
        if failed.undo:
            command = self.state.prepare_undo(operation_id=failed.operation_id)
        else:
            command = self.state.prepare_judgment(
                failed.judgment.choice, expected_work_id=failed.judgment.work_id,
                expected_generation=self.state.generation, operation_id=failed.operation_id,
            )
        if command is not None:
            self._start_save(command)

    def reload_storage(self, *, notice: str = "") -> None:
        if self.storage is None or self.state.closed or self.services.closing or self.state.saving or self._storage_busy:
            return
        self._stop_candidate_refill()
        self._storage_busy = True
        self._render_review()
        self._storage_ready = False
        self.status.set_text("保存された判定を読み込んでいます…")
        self._update_buttons()
        background_tasks.create(self._reload_storage(notice), name="learning restore")

    async def _reload_storage(self, notice: str) -> None:
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
        self.state.close()
        remaining = tuple(item for item in self._artworks if item.work_id not in snapshot.judged_ids)
        self._baseline_count = len(self._artworks) - len(remaining)
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
            self.settings_dialog.close()
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
                  }};
                  root.__pvSummaryResize=resize;
                  window.addEventListener('resize',resize,{{passive:true}});
                  root.addEventListener('summary-dispose',()=>{{
                    window.removeEventListener('resize',resize);
                    delete root.__pvSummaryResize;
                  }},{{once:true}});
                }})"""
            )

    def _update_buttons(self) -> None:
        item = self.state.current_artwork
        work_id = item.work_id if item else ""
        props = f"data-work-id={work_id} data-generation={self.state.generation}"
        available = self.active and not self.services.closing and self._storage_ready and not self._storage_busy and not self.state.saving and not self._settings_busy
        self.root.props(props + f" data-saving={'true' if self.state.saving else 'false'}")
        for button in self.buttons.values():
            button.props(props)
            button.set_enabled(available and self.state.ready)
        self.undo_button.props(props)
        self.undo_button.set_enabled(available and self.state.can_undo)
        self.retry_button.set_enabled(available and item is not None)
        self.save_retry_button.set_visibility(self._failed_command is not None)
        self.save_retry_button.set_enabled(available and (self.state.ready or self.state.can_undo))
        self.reload_button.set_visibility(self.storage is not None)
        self.reload_button.set_enabled(self.active and not self.state.closed and not self.services.closing and not self._storage_busy and not self.state.saving)
        if self.discovery is not None:
            self.settings_button.set_enabled(available and not self._managed_busy)
            self.fetch_button.set_enabled(available and not self._managed_busy and (self._initial_task is None or self._initial_task.done()))
            self.managed_button.props(props)
            self.managed_button.set_enabled(available and item is not None and not self._managed_busy and getattr(self.services, 'managed_downloads', None) is not None)
        self.review_button.set_enabled(available and self.storage is not None and not self._review_busy)

    def refresh(self) -> None:
        item = self.state.current_artwork
        self._summary_sync()
        if self.discovery is not None:
            frame=self._discovery_frames.get(item.work_id) if item else None
            self.frame_label.set_text({'sure':'確実枠','subject':'題材探索枠','style':'絵柄探索枠'}.get(frame,''))
        self.title.set_text(item.title if item else ("現在の候補がありません" if self._refill.armed else "初回候補を読み込んでいます") if self.discovery is not None else "判定が完了しました")
        self.progress.set_text(f"{self._baseline_count + self.state.index} / {len(self._artworks)} 件判定済み")
        if item is None:
            self._image_token = None
            self._image_url = ""
            self.picture.set_visibility(False)
            message = (("現在の候補が尽きました。" if self._refill.armed else "設定された取得元から初回候補を読み込みます。") if self.discovery is not None else "すべての判定が完了しました。")
            if self.state.can_undo:
                message += "Undo で戻せます。"
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
            self._prefetch=LearningImagePrefetch(preferences,pin,on_ready=lambda wid:self._prefetch_ready(wid,pin))
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
                self._start_prefetch()
                if not self.state.saving and not self._storage_busy:
                    self.status.set_text(self._resting_message())
                self._update_buttons()

    def _on_failed(self, event) -> None:
        if not self._storage_ready or self._storage_busy or self.state.closed or self.services.closing:
            return
        if self._image_token is not None and source_matches(event.args, self._image_url):
            self.state.finish_load(self._image_token, success=False)
            if self.state.last_error:
                self.status.set_text("画像を読み込めませんでした。再読込してください。")
                self._update_buttons()

    async def open_review(self):
        if (not self.active or self.storage is None or self.state.closed or self.services.closing
                or self.state.saving or self._storage_busy or self._review_busy or self._review_open):
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
                self._review_undone.clear()
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
        self._review_undone.clear()
        self._review_operations.clear()
        if self._review_session_id is not None:
            self.storage.release_session(self._review_session_id)
            self._review_session_id = None
        if not self.state.closed:self._update_buttons()

    def _review_filter_changed(self):
        self._review_pages = {True:0,False:0}
        self._render_review()

    def _review_page(self,positive,delta):
        self._review_pages[positive] = max(0,self._review_pages[positive]+delta)
        self._render_review()

    def _render_review(self):
        if not self._review_open:return
        filters = ReviewFilter(str(self.review_text.value or ''),self.review_period.value,
                               bool(self.review_unsaved.value),bool(self.review_superlike.value),bool(self.review_hate.value))
        self.review_content.clear()
        with self.review_content:
            for positive,label in ((True,'好意'),(False,'否定')):
                rows = filters.rows(self._review_rows,positive,now=datetime.now(),session_started=self._review_started)
                page = min(self._review_pages[positive],max(0,(len(rows)-1)//PAGE_SIZE))
                self._review_pages[positive] = page
                with ui.column().classes('flex-1 min-w-0').props('data-testid=learning-review-positive' if positive else 'data-testid=learning-review-negative'):
                    ui.label(f'{label} · {len(rows)} 件').classes('pv-view-title')
                    for row in rows[page*PAGE_SIZE:(page+1)*PAGE_SIZE]:
                        with ui.row().classes('w-full items-center'):
                            if self.discovery is None or row['work_id'] in self._discovery_image_ids:
                                ui.image(self._images.url(row['work_id'],0,'thumb')).style('width:60px;height:60px')
                            else:
                                ui.label('画像未取得').classes('pv-subtitle')
                            with ui.column().classes('flex-1 min-w-0 gap-0'):
                                ui.label(row.get('title') or row['work_id']).classes('break-words')
                                ui.label(f"{row['verdict']} · {row.get('judged_at','')}").classes('pv-subtitle')
                            if self.discovery is not None and positive:
                                saved = bool(row.get('work_meta')) and not row.get('is_deleted')
                                save_button = ui.button('保存済み' if saved else '作品を保存',
                                    on_click=lambda _,wid=row['work_id']:self.save_managed(wid)).props('flat data-testid=discovery-review-save')
                                save_button.set_enabled(not saved and not self._review_busy and not self._review_refresh_busy)
                            button=ui.button('取り消す',on_click=lambda _,row=row:self._review_change(row)).props(f"flat data-testid=learning-review-undo-{row['work_id']}")
                            button.set_enabled(not self._review_busy and not self._review_refresh_busy and not self._storage_busy and not self.state.saving)
                    for wid,(row,operation) in self._review_undone.items():
                        if (row['verdict'] in ('like','superLike'))!=positive:continue
                        with ui.row().classes('w-full items-center'):
                            ui.label(f"{row.get('title') or wid}：判定を取り消しました。学習候補を読み直しました。").classes('flex-1')
                            button=ui.button('元に戻す',on_click=lambda _,row=row,operation=operation:self._review_change(row,operation)).props(f'flat data-testid=learning-review-restore-{wid}')
                            button.set_enabled(not self._review_busy and not self._review_refresh_busy and not self._storage_busy and not self.state.saving)
                    with ui.row():
                        previous=ui.button('前へ',on_click=lambda _,positive=positive:self._review_page(positive,-1)).props('flat')
                        previous.set_enabled(page>0)
                        ui.label(f'{page+1} / {max(1,(len(rows)+PAGE_SIZE-1)//PAGE_SIZE)}')
                        following=ui.button('次へ',on_click=lambda _,positive=positive:self._review_page(positive,1)).props('flat')
                        following.set_enabled((page+1)*PAGE_SIZE<len(rows))

    async def _review_change(self,row,undo_operation=None):
        epoch=self._review_epoch
        if not self._review_valid(epoch) or self._review_busy or self._review_refresh_busy or self._storage_busy or self.state.saving or self._review_session_id is None:return
        self._review_busy=True
        action='restore' if undo_operation else 'undo'
        key=(row['work_id'],action,undo_operation or row['revision'])
        operation=self._review_operations.setdefault(key,uuid4().hex)
        self._render_review()
        self._update_buttons()
        try:
            if self.discovery is not None and not undo_operation:
                await self.discovery.resolve_history_candidate(row['work_id'])
                if not self._review_valid(epoch):return
            # Acceptance happens before awaiting. Closing this view cannot cancel the queued write.
            pending=(self.storage.submit_review_restore(self._review_session_id,undo_operation,operation) if undo_operation else
                     self.storage.submit_review_undo(self._review_session_id,row['work_id'],row['revision'],operation))
            await self.storage._wait(pending)
            if not self._review_valid(epoch):return
            if undo_operation:self._review_undone.pop(row['work_id'],None)
            else:self._review_undone[row['work_id']]=(row,operation)
            packet=await self.storage.review_snapshot(self._review_session_id)
            if not self._review_valid(epoch):return
            self._review_rows=tuple(packet['rows'])
            self.review_status.set_text('判定を元に戻しました。' if undo_operation else '判定を取り消しました。')
            self.reload_storage()
        except Exception as error:
            if self._review_valid(epoch):
                from ..learning_repository import LearningConflict
                self.review_status.set_text('別の判定に更新されています。閉じて一覧を読み直してください。' if isinstance(error,LearningConflict) else '保存できませんでした。同じ操作を再試行できます。')
        finally:
            if self._review_valid(epoch):
                self._review_busy=False
                self._render_review()
                self._update_buttons()

    def _settings_controls(self):
        enabled=not self._settings_busy and self._settings_snapshot is not None
        for control in (self.settings_mode,self.settings_subject,self.settings_style,self.settings_save):control.set_enabled(enabled)
        self.settings_reload.set_enabled(not self._settings_busy)
        self._update_buttons()

    async def open_discovery_settings(self):
        if not self.active or self.state.closed or self._storage_busy or self.state.saving or self._settings_busy:return
        self._settings_epoch+=1
        self._settings_open=True
        self.settings_dialog.open()
        await self.load_discovery_settings()

    def _close_settings(self):
        self._settings_epoch+=1
        self._settings_open=False
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
            self.settings_mode.set_value(snapshot.values['discovery_search_mode'])
            self.settings_subject.set_value(float(snapshot.values['discovery_ratio_subject']))
            self.settings_style.set_value(float(snapshot.values['discovery_ratio_style']))
            self.settings_status.set_text('設定を読み込みました。')
        except Exception:
            if not self.state.closed and epoch==self._settings_epoch:self.settings_status.set_text('設定を読み込めませんでした。再読込してください。')
        finally:
            if not self.state.closed and epoch==self._settings_epoch:
                self._settings_busy=False
                self._settings_controls()

    async def save_discovery_settings(self):
        if not self._settings_open or self._settings_busy or self._settings_snapshot is None:return
        epoch=self._settings_epoch
        values={'discovery_search_mode':self.settings_mode.value,'discovery_ratio_subject':self.settings_subject.value,'discovery_ratio_style':self.settings_style.value}
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
            self.reload_storage(notice='発掘設定を保存し、候補の順序を読み込み直しました。')
        except Exception as error:
            if not self.state.closed and epoch==self._settings_epoch:
                from ..learning_repository import LearningConflict
                self.settings_status.set_text('別の画面で設定が変わりました。設定を読込してから操作してください。' if isinstance(error,LearningConflict) else '設定を保存できませんでした。値を確認して再試行してください。')
        finally:
            if not self.state.closed and epoch==self._settings_epoch:
                self._settings_busy=False
                self._settings_controls()

    def _set_trend_open(self, opened: bool):
        if self.discovery is None or self.state.closed:return
        if opened:
            self.close_summary()
        self._trend_open=bool(opened)
        self.trend_content.set_visibility(self._trend_open)
        self.trend_button.set_text('傾向を閉じる' if self._trend_open else '傾向を見る')
        self.trend_button.props(f"aria-expanded={'true' if self._trend_open else 'false'}")

    def toggle_trend(self):
        if self.discovery is None or self.state.closed or not self.active or self.services.closing:return
        self._set_trend_open(not self._trend_open)

    def _render_trend(self,trend):
        self.trend_content.clear()
        self.trend_label.set_text(f"これまでの判定 {trend['judged']} 件" if trend['ready'] else f"これまでの判定 {trend['judged']} 件 / 傾向表示は20件から")
        if not trend['ready']:
            with self.trend_content:
                ui.label('20件の判定後に傾向を表示します。').classes('pv-subtitle')
            return
        peak=max((r['score'] for r in trend['rows']),default=0)
        with self.trend_content:
            for row in trend['rows']:
                with ui.row().classes('w-full items-center'):
                    ui.label(row['display']).classes('flex-1')
                    ui.label(f"好き {row['like_count']} / 表示 {row['shown_count']}" + (' MAX' if row.get('at_cap') else '')).classes('pv-subtitle')
                ui.linear_progress(value=max(0,row['score'])/peak if peak>0 else 0,show_value=False).classes('w-full')

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
                self._notice='候補を取得できませんでした。' if result.nothing_and_failed else '現在は新しい候補がありません。'
                self.refresh()
            else:self._kick_candidate_refill()
        except asyncio.CancelledError:
            raise
        except Exception:
            if self._initial_current(epoch):
                self._initial_done=True
                self._refill.arm(self._refill_clock())
                self._refill.last_failed=True
                self._notice='初回候補を取得できませんでした。'
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
        except Exception:
            self._refill.last_failed=True
            notice='候補を取得できませんでした。再試行できます。'
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
        self._stop_initial_load()
        self._stop_candidate_refill()
        self._stop_prefetch(close=True)
        self._release_refill_pins(close=True)
        self._close_review()
        if self.discovery is not None and not self._managed_busy:
            self.services.release_managed_owner(self._managed_owner)
        if self.discovery is not None:
            self._close_settings()
            self.settings_dialog.close()
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
