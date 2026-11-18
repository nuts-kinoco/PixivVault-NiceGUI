"""「ダウンロード」タブ: 元の統合ダウンロード画面（Flet 版 unified_download_tab.py）の復元。

構成（元と同じ）: 見出し＋補助文 / 左カラム（モード切替・絞り込み・道具行・作者表・50 人ページ送り）/
右カラム（この回の対象・☆の自動監視・実行行）/ 下の進捗帯。
実行は `services.start_batch_run`（BatchRunEngine）へ渡す。一時停止・停止・再開もエンジンへ。
判断は `unified_download_state` の純関数に任せ、この画面は描くだけにする。

移植版独自の機能（作品単位の確認、IDから作品を確認、作者個別設定、旧DBと照合、未フォロー作者を確認、
ブックマークの選択など）は、これまでの `ManagedDownloadView`（または複数媒体のときは
`MediaDownloadView`）をそのまま「作品単位で選ぶ（詳細）」ダイアログの中に残した。
画面を開いただけでは通信しない（作者表はフォロー台帳のローカル読みだけ）。
"""
from __future__ import annotations

import asyncio
import queue
import re
import time
from datetime import datetime

from nicegui import ui

from .. import unified_download_state as U

PAGE_SIZE = 50
BODY_MIN_H = 240            # 元 BODY_MIN_H: 左右カラムがこれを下回ったら詳細を自動で閉じる
SUMMARY_BAND_H = 56         # 140 - 84
BAND_CLOSED_H, BAND_OPEN_H = 84, 140
LEFT_W, RIGHT_W = 776, 460
TYPE_CYCLE = ("illust", "novel", "both")
TYPE_LABELS = {"illust": "イラスト・漫画", "novel": "小説", "both": "両方"}
TYPE_TOOLTIPS = {"illust": "イラスト・漫画のみ", "novel": "小説のみ", "both": "イラスト・漫画+小説（両方）"}
TYPE_ICONS = {"illust": "image", "novel": "article", "both": "library_books"}
NEED_TARGET = "左の一覧で作者を選ぶと押せます"
_AUTHOR_ID = re.compile(r"[1-9][0-9]{0,19}\Z")

CSS = """
.pv-ud { width:1246px; max-width:100%; margin:0 auto; flex:1 1 0; min-height:0; display:flex; flex-direction:column; gap:8px; box-sizing:border-box; padding:0; }
.pv-ud * { box-sizing:border-box; }
.pv-ud-header { flex:0 0 46px; height:46px; padding:0 4px; display:flex; flex-direction:row; align-items:center; gap:12px; flex-wrap:nowrap; }
.pv-ud-title { font-size:19px; color:var(--pv-text); line-height:27px; }
.pv-ud-sub { font-size:11px; color:var(--pv-muted); }
.pv-ud-body { flex:1 1 0; min-height:120px; display:flex; flex-direction:row; gap:10px; width:100%; }
.pv-ud-left { flex:0 0 776px; width:776px; min-width:0; min-height:0; display:flex; flex-direction:column; gap:10px; padding:16px; background:var(--pv-surface); border:1px solid var(--pv-border); border-radius:12px; }
.pv-ud-right { flex:0 0 460px; width:460px; min-width:0; min-height:0; display:flex; flex-direction:column; gap:12px; }
.pv-ud-pane { flex:1 1 0; min-height:0; display:flex; flex-direction:column; gap:8px; }
.pv-ud-moderow { display:flex; flex-direction:row; gap:4px; flex:0 0 42px; height:42px; }
.pv-ud-mode { flex:1 1 0; height:42px; border:0; border-radius:8px; background:var(--pv-raised); color:var(--pv-text); font:inherit; font-size:14px; font-weight:500; cursor:pointer; }
.pv-ud-mode[data-selected=true] { background:#0096FA; color:#0b0b10; }
.pv-ud-mode[disabled] { cursor:default; }
.pv-ud-search { width:300px; flex:0 0 42px; }
.pv-ud-search .q-field__control { height:42px; background:var(--pv-surface); }
.pv-ud-search .q-field__native, .pv-ud-search .q-field__label { font-size:16px; }
.pv-ud-idrow { display:flex; flex-direction:row; align-items:center; gap:8px; padding:8px; border-radius:8px; background:rgba(0,150,250,.10); flex:0 0 auto; }
.pv-ud-idrow span { font-size:12px; color:var(--pv-text); flex:1 1 auto; }
.pv-ud-tools { display:flex; flex-direction:row; align-items:center; gap:4px; flex:0 0 32px; height:32px; }
.pv-ud-tbtn { display:inline-flex; align-items:center; gap:8px; height:32px; padding:0 14px; border:0; border-radius:16px; background:transparent; color:var(--pv-primary); font:inherit; font-size:14px; cursor:pointer; white-space:nowrap; }
.pv-ud-tbtn .q-icon { font-size:18px; color:var(--pv-muted); }
.pv-ud-tbtn[data-on=true] .q-icon { color:#0096FA; }
.pv-ud-tbtn:disabled { opacity:.45; cursor:default; }
.pv-ud-div { width:1px; height:20px; background:var(--pv-border); }
.pv-ud-grow { flex:1 1 auto; }
.pv-ud-sortbtn { display:inline-flex; align-items:center; gap:2px; border:0; background:transparent; color:var(--pv-text); font:inherit; font-size:12px; cursor:pointer; padding:0 8px; height:28px; }
.pv-ud-sortbtn .q-icon { font-size:16px; color:var(--pv-muted); }
.pv-ud-ibtn { width:28px; height:28px; padding:0; border:0; border-radius:14px; background:transparent; color:var(--pv-muted); display:inline-flex; align-items:center; justify-content:center; cursor:pointer; }
.pv-ud-ibtn .q-icon { font-size:14px; }
.pv-ud-ibtn[data-on=true] { color:#0096FA; }
.pv-ud-ibtn:disabled { opacity:.4; cursor:default; }
.pv-ud-note { display:flex; flex-direction:row; align-items:center; gap:2px; flex:0 0 auto; font-size:11px; line-height:17px; color:var(--pv-muted); min-height:17px; }
.pv-ud-thead { display:flex; flex-direction:row; align-items:center; gap:8px; padding:4px 8px; border-bottom:1px solid var(--pv-border); flex:0 0 auto; font-size:11px; color:var(--pv-muted); }
.pv-ud-list { flex:1 1 0; min-height:0; overflow-y:auto; display:flex; flex-direction:column; gap:4px; }
.pv-ud-row { display:flex; flex-direction:row; align-items:center; gap:8px; height:34px; flex:0 0 34px; padding:0 8px; border-bottom:1px solid var(--pv-border); cursor:pointer; }
.pv-ud-check { flex:0 0 16px; width:16px; height:16px; border-radius:4px; background:var(--pv-raised); border:1px solid var(--pv-muted); display:flex; align-items:center; justify-content:center; color:#0b0b10; }
.pv-ud-check .q-icon { font-size:12px; }
.pv-ud-check[data-state=all], .pv-ud-check[data-state=partial], .pv-ud-row[data-picked=true] .pv-ud-check { background:#0096FA; border-color:#0096FA; }
.pv-ud-star { flex:0 0 22px; width:22px; height:22px; display:flex; align-items:center; justify-content:center; color:var(--pv-muted); cursor:pointer; }
.pv-ud-star .q-icon { font-size:15px; }
.pv-ud-star[data-on=true] { color:#0096FA; }
.pv-ud-c-name { flex:0 0 210px; width:210px; font-size:12.5px; color:var(--pv-text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.pv-ud-running .pv-ud-row[data-picked=false] .pv-ud-c-name { color:var(--pv-muted); }
.pv-ud-c-id { flex:0 0 96px; width:96px; font-size:11.5px; font-family:Consolas, monospace; color:var(--pv-muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.pv-ud-c-count { flex:0 0 86px; width:86px; text-align:right; font-size:11.5px; font-family:Consolas, monospace; color:var(--pv-muted); }
.pv-ud-c-last { flex:1 1 0; min-width:0; text-align:right; font-size:11.5px; font-family:Consolas, monospace; color:var(--pv-muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.pv-ud-thead .pv-ud-c-name, .pv-ud-thead .pv-ud-c-id, .pv-ud-thead .pv-ud-c-count, .pv-ud-thead .pv-ud-c-last { font-family:inherit; font-size:11px; color:var(--pv-muted); }
.pv-ud-empty { padding:8px 12px; font-size:11.5px; color:var(--pv-muted); }
.pv-ud-bm { flex:1 1 0; min-height:0; overflow-y:auto; display:flex; flex-direction:column; gap:10px; }
.pv-ud-bm p { margin:0; font-size:11px; color:var(--pv-muted); }
.pv-ud-card { flex:1 1 0; min-height:0; display:flex; flex-direction:column; gap:10px; padding:16px; background:var(--pv-surface); border:1px solid var(--pv-border); border-radius:12px; }
.pv-ud-cardhead { display:flex; flex-direction:row; align-items:center; gap:8px; flex:0 0 auto; }
.pv-ud-cardhead .a { font-size:13px; color:var(--pv-text); }
.pv-ud-cardhead .b { font-size:12px; font-family:Consolas, monospace; color:#0096FA; }
.pv-ud-chips { flex:1 1 0; min-height:0; overflow-y:auto; display:flex; flex-direction:column; gap:8px; }
.pv-ud-chiplist { display:flex; flex-direction:column; gap:8px; flex:0 0 auto; }
.pv-ud-chip { display:flex; flex-direction:row; align-items:center; gap:8px; height:44px; flex:0 0 44px; padding:0 4px 0 12px; border-radius:8px; background:var(--pv-raised); }
.pv-ud-chip .who { flex:1 1 0; min-width:0; display:flex; flex-direction:column; justify-content:center; }
.pv-ud-chip .nm { font-size:12.5px; color:var(--pv-text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; line-height:16px; }
.pv-ud-chip .id { font-size:11px; font-family:Consolas, monospace; color:var(--pv-muted); line-height:14px; }
.pv-ud-chip .st { font-size:11px; font-family:Consolas, monospace; color:var(--pv-muted); white-space:nowrap; }
.pv-ud-chip .st[data-kind=in_progress] { color:#0096FA; }
.pv-ud-chip .st[data-kind=failed] { color:#e05555; }
.pv-ud-chip .x { width:28px; height:28px; border:0; border-radius:14px; background:transparent; color:var(--pv-muted); display:inline-flex; align-items:center; justify-content:center; cursor:pointer; padding:0; }
.pv-ud-chip .x .q-icon { font-size:14px; }
.pv-ud-fav { flex:0 0 46px; height:46px; display:flex; flex-direction:row; align-items:center; gap:6px; padding:6px 8px 6px 12px; background:var(--pv-surface); border:1px solid var(--pv-border); border-radius:8px; }
.pv-ud-fav .q-icon { font-size:14px; color:#0096FA; }
.pv-ud-fav .lab { font-size:11.5px; color:var(--pv-muted); }
.pv-ud-fav .num { font-size:13px; font-family:Consolas, monospace; color:#0096FA; }
.pv-ud-fav .pv-ud-tbtn .q-icon { color:var(--pv-primary); font-size:18px; }
.pv-ud-runrow { flex:0 0 44px; height:44px; display:flex; flex-direction:row; gap:8px; }
.pv-ud-sq { flex:0 0 44px; width:44px; height:44px; border-radius:8px; border:1px solid var(--pv-border); background:var(--pv-raised); color:var(--pv-text); display:inline-flex; align-items:center; justify-content:center; cursor:pointer; padding:0; }
.pv-ud-sq .q-icon { font-size:18px; }
.pv-ud-sq:disabled { cursor:default; }
.pv-ud-type { background:#0096FA; border-color:#0096FA; color:#0b0b10; transition:background-color .12s; }
.pv-ud-type[data-dim=true] { background:var(--pv-raised); color:var(--pv-text); }
.pv-ud-run-wrap { flex:1 1 0; min-width:0; height:44px; }
.pv-ud-run { width:100%; height:44px; border-radius:8px; border:1px solid var(--pv-border); background:#0096FA; color:#0b0b10; font:inherit; font-size:14px; display:inline-flex; align-items:center; justify-content:center; gap:8px; cursor:pointer; padding:0; }
.pv-ud-run .q-icon { font-size:18px; }
.pv-ud-run:disabled { background:var(--pv-surface); color:var(--pv-muted); cursor:default; }
.pv-ud-band { flex:0 0 84px; height:84px; width:100%; padding:0; display:flex; flex-direction:column; gap:0; overflow:hidden; background:var(--pv-surface); border:1px solid var(--pv-border); border-radius:12px; }
.pv-ud-band[data-open=true] { border-color:#0096FA; }
.pv-ud-band-main { flex:1 1 0; min-height:0; padding:10px; display:flex; flex-direction:column; gap:6px; }
.pv-ud-sumband { flex:0 0 56px; height:56px; padding:0 16px; display:flex; flex-direction:row; align-items:center; gap:8px; background:var(--pv-raised); }
.pv-ud-sumband .cap { font-size:11.5px; color:var(--pv-muted); white-space:nowrap; }
.pv-ud-sumbox { flex:1 1 0; min-width:0; height:32px; display:flex; flex-direction:row; align-items:center; gap:6px; padding:0 8px; background:var(--pv-surface); border:1px solid var(--pv-border); border-radius:8px; }
.pv-ud-sumbox .k { font-size:10.5px; color:var(--pv-muted); white-space:nowrap; }
.pv-ud-sumbox .v { font-size:13px; font-family:Consolas, monospace; color:var(--pv-text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.pv-ud-sumbox .v[data-danger=true] { color:#e05555; }
.pv-ud-hintrow { cursor:pointer; }
.pv-ud-bmbox { display:flex; flex-direction:column; gap:10px; }
.pv-ud-bmbox .cap { font-size:13px; color:var(--pv-text); }
.pv-ud-bmbox .warn { font-size:11px; color:#e05555; }
.pv-ud-bmrow { display:flex; flex-direction:row; gap:4px; }
.pv-ud-bmrow .pv-ud-mode { height:36px; flex:1 1 0; }
.pv-ud-hr { height:1px; background:var(--pv-border); flex:0 0 1px; }
.pv-ud-body { position:relative; min-height:120px; }
.pv-ud-band-top { display:flex; flex-direction:row; align-items:center; gap:8px; min-height:19px; }
.pv-ud-band-status { font-size:13px; font-weight:700; color:var(--pv-text); line-height:19px; flex:1 1 auto; }
.pv-ud-bar { height:6px; flex:0 0 6px; border-radius:3px; background:var(--pv-raised); overflow:hidden; }
.pv-ud-bar > div { height:100%; background:#0096FA; }
.pv-ud-bar[data-mode=indeterminate] > div { width:30% !important; animation:pv-ud-slide 1.2s linear infinite; }
@keyframes pv-ud-slide { from { margin-left:-30%; } to { margin-left:100%; } }
.pv-ud-band-hint { display:flex; flex-direction:row; align-items:center; gap:4px; font-size:10.5px; color:var(--pv-muted); }
.pv-ud-band-hint .sum { flex:1 1 auto; font-size:11px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.pv-ud-band-hint .q-icon { font-size:13px; }
.pv-ud-textbtn { border:0; background:transparent; color:var(--pv-primary); font:inherit; font-size:12px; cursor:pointer; padding:2px 8px; border-radius:12px; }
"""


def local_stamp(value):
    """台帳の取得日は UTC で入っている。元は端末の時刻（naive）で書いていたので、表示は端末の時刻に直す。"""
    if not value:
        return value
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return value
    if moment.tzinfo is None:
        return value
    return moment.astimezone().replace(tzinfo=None).isoformat()


def _is_author_id(value) -> bool:
    return isinstance(value, str) and _AUTHOR_ID.fullmatch(value) is not None


class UnifiedDownloadView:
    """元の統合ダウンロード画面。実行はエンジンへ、細かい確認は詳細ダイアログの既存画面へ。"""

    def __init__(self, services, *, navigate=None, legacy_factory=None):
        self.services = services
        self.navigate = navigate
        self.legacy_factory = legacy_factory
        self.client = ui.context.client
        self.active = True
        self.closed = False
        self.epoch = 0
        self.legacy = None            # 作品単位の既存画面（最初に開いたときに作る）
        self.legacy_open = False
        self.mode = "followed"
        self.authors: list[dict] = []
        self.work_counts: dict[str, int] = {}
        self.targets: list[dict] = []
        self.query = ""
        self.favorite_only = False
        self.sort_key = "follow_order"
        self.sort_reverse = False
        self.page = 0
        self.target_type = "both"
        self.progress = U.RunProgress(())
        self.run_state = "idle"          # idle|running|paused|stopping（エンジンの state を写す）
        self.band_state = "idle"         # 帯の見出し: idle|running|paused|stopping|done|stopped|failed
        self.band_error = ""
        self.run_target_ids: list[str] = []
        self.run_started = None
        self.notice_text = ""
        self.resumable = None
        self._events: queue.SimpleQueue = queue.SimpleQueue()
        self._reload_task = None
        self.band_open = False
        self.band_height = 84
        self.body_h = 0
        self.run_ended = None
        self.starting = False
        self._run_task = None
        self.bm_show, self.bm_hide = True, False
        self.bm_hierarchical = self.bm_hardlink = False
        self.unfollowed_view = None
        self._read_bookmark_layout()
        self._mirrored: set[str] = set()
        self._counts_at = float("-inf")
        self._unsubscribe = None
        self._rows: dict[str, dict] = {}
        self._loaded = False
        self.engine_error = ""
        self._provision_engine()
        ui.add_css(CSS)
        self._build()
        self._subscribe()
        self.client.on_delete(self.close)
        self._adopt_active_run()
        self.render_all()
        self.refresh_resumable()
        self._timer = ui.timer(0.4, self.tick)
        self._first_load = ui.timer(0.05, self.reload, once=True)

    # ------------------------------------------------------------------ engine wiring
    def _provision_engine(self):
        """実行台帳とエンジンを開く（開くだけ。実行も通信もしない）。無ければ実行ボタンを塞ぐ。"""
        services = self.services
        if getattr(services, "batch_runs", None) is not None:
            return
        enable = getattr(services, "enable_batch_runs", None)
        if not callable(enable):
            self.engine_error = "unavailable"
            return
        try:
            enable()
        except Exception:
            self.engine_error = "unavailable"

    @property
    def engine(self):
        return getattr(self.services, "batch_runs", None)

    def _subscribe(self):
        subscribe = getattr(self.services, "subscribe_batch_events", None)
        if callable(subscribe):
            self._unsubscribe = subscribe(self._events.put)

    def _adopt_active_run(self):
        """画面を開いたとき、すでに走っている実行（定期チェックなど）があれば、その進み具合を写す。"""
        engine = self.engine
        if engine is None or not getattr(engine, "active", False):
            return
        try:
            ledger = self.services.batch_run_ledger
            rows = ledger.authors(engine.run_id)
            ids = [row.author_id for row in rows]
            self.run_target_ids = ids
            self.progress = U.RunProgress(ids)
            self.progress.kind = getattr(engine, "kind", "authors") or "authors"
            self.progress.state = engine.state
            self.progress.done = sum(1 for row in rows if row.state in ("done", "failed", "skipped"))
            current = next((row.author_id for row in rows if row.state in ("listing", "downloading")), None)
            self.progress.current = current
        except Exception:
            pass

    def refresh_resumable(self):
        engine = self.engine
        try:
            self.resumable = None if engine is None else engine.resumable_run()
        except Exception:
            self.resumable = None
        self._render_band()

    # ------------------------------------------------------------------ building
    def _build(self):
        with ui.column().classes("pv-ud w-full gap-0").props(
                "data-testid=unified-download-view data-active=true data-mode=followed") as self.root:
            with ui.element("div").classes("pv-ud-header").props("data-testid=unified-header"):
                ui.label("ダウンロード").classes("pv-ud-title")
                ui.label("作者を選ぶか、IDを入れて実行します").classes("pv-ud-sub")
                ui.element("div").classes("pv-ud-grow")
                self.details_button = ui.element("button").classes("pv-ud-tbtn").props(
                    "type=button data-testid=unified-details-open title=作品単位の確認・IDから作品を確認・作者個別設定・旧DBと照合・未フォロー作者を確認")
                with self.details_button:
                    ui.icon("tune")
                    ui.label("作品単位で選ぶ（詳細）")
                self.details_button.on("click", lambda _e: self.open_details())
            with ui.element("div").classes("pv-ud-body").props("data-testid=unified-body"):
                self._build_left()
                self._build_right()
                ui.element("q-resize-observer").props("debounce=200").on(
                    "resize", lambda e: self.on_body_size((e.args or {}).get("height")))
            self._build_band()
        self._build_details_dialog()

    def _build_left(self):
        with ui.element("div").classes("pv-ud-left").props("data-testid=unified-left") as self.left:
            with ui.element("div").classes("pv-ud-moderow"):
                self.mode_buttons = {}
                for key, label in (("followed", "フォロー中の作者"), ("bookmark", "マイブックマーク")):
                    button = ui.element("button").classes("pv-ud-mode").props(
                        f"type=button data-testid=unified-mode-{key} data-selected={'true' if key == 'followed' else 'false'}")
                    with button:
                        ui.label(label)
                    button.on("click", lambda _e, key=key: self.set_mode(key))
                    self.mode_buttons[key] = button
            with ui.element("div").classes("pv-ud-pane").props("data-testid=unified-followed-pane") as self.followed_pane:
                self.search_input = ui.input(label="絞り込み（数字はID指定）").props(
                    "outlined dense clearable debounce=200 data-testid=unified-search").classes("pv-ud-search")
                self.search_input.on_value_change(lambda e: self.set_query(e.value))
                with ui.element("div").classes("pv-ud-idrow").props("data-testid=unified-id-row") as self.id_row:
                    self.id_row_text = ui.element("span")
                    self.id_row_button = ui.element("button").classes("pv-ud-textbtn").props(
                        "type=button data-testid=unified-id-add")
                    with self.id_row_button:
                        ui.label("対象に積む")
                    self.id_row_button.on("click", lambda _e: self.pick_from_id())
                self.id_row.set_visibility(False)
                self._build_tools()
                with ui.element("div").classes("pv-ud-note"):
                    self.count_label = ui.label("0 / 0 人").props("data-testid=unified-count")
                    self.prev_button = ui.element("button").classes("pv-ud-ibtn").props(
                        "type=button data-testid=unified-page-prev title=前の50人")
                    with self.prev_button:
                        ui.icon("arrow_back_ios_new")
                    self.prev_button.on("click", lambda _e: self.go_page(-1))
                    self.next_button = ui.element("button").classes("pv-ud-ibtn").props(
                        "type=button data-testid=unified-page-next title=次の50人")
                    with self.next_button:
                        ui.icon("arrow_forward_ios")
                    self.next_button.on("click", lambda _e: self.go_page(1))
                with ui.element("div").classes("pv-ud-thead").props("data-testid=unified-thead") as self.thead:
                    self.header_check = ui.element("div").classes("pv-ud-check").props(
                        "data-testid=unified-header-check data-state=empty role=checkbox aria-label=表示中の作者をまとめて選ぶ")
                    self.header_check.style("cursor:pointer")
                    self.header_check.on("click", lambda _e: self.toggle_header_check())
                    with self.header_check:
                        self.header_check_icon = ui.icon("check")
                    ui.element("div").classes("pv-ud-star")
                    ui.label("作者名").classes("pv-ud-c-name")
                    ui.label("ID").classes("pv-ud-c-id")
                    ui.label("保存済み").classes("pv-ud-c-count")
                    ui.label("最後に取得した日").classes("pv-ud-c-last")
                self.author_list = ui.element("div").classes("pv-ud-list").props("data-testid=unified-author-list")
            with ui.element("div").classes("pv-ud-bm").props("data-testid=unified-bookmark-pane") as self.bookmark_pane:
                self._build_bookmark_pane()
            self.bookmark_pane.set_visibility(False)

    def _build_bookmark_pane(self):
        """マイブックマーク面（元 bookmark_pane）: 含めるブックマーク / 保存方式 / 未フォロー作者を確認。"""
        with ui.element("div").classes("pv-ud-bmbox"):
            ui.label("対象の選び方はありません").classes("pv-ud-sub")
            ui.label("含めるブックマーク").classes("cap")
            with ui.column().classes("gap-0"):
                self.bm_checks = {}
                for key, label in (("show", "公開ブックマーク"), ("hide", "非公開ブックマーク")):
                    box = ui.checkbox(label, value=(self.bm_show if key == "show" else self.bm_hide),
                                      on_change=lambda e, key=key: self.set_bm(key, bool(e.value))).props(
                        f"dense data-testid=unified-bm-{key}")
                    self.bm_checks[key] = box
            self.bm_hint = ui.label("含めるブックマークを1つ選びます").classes("warn").props("data-testid=unified-bm-hint")
            self.bm_hint.set_visibility(False)
            ui.element("div").classes("pv-ud-hr")
            ui.label("保存方式").classes("cap")
            with ui.element("div").classes("pv-ud-bmrow"):
                self.bm_buttons = {}
                for key, label in (("hier", "階層化（作者別フォルダ）"), ("flat", "フラット配置")):
                    button = ui.element("button").classes("pv-ud-mode").props(
                        f"type=button data-testid=unified-bm-{key} data-selected=false")
                    with button:
                        ui.label(label)
                    button.on("click", lambda _e, key=key: self.set_bm_layout(hierarchical=key == "hier"))
                    self.bm_buttons[key] = button
            self.bm_hardlink_box = ui.checkbox(
                "ハードリンクで置く（同じ画像を二重に保存しない）", value=self.bm_hardlink,
                on_change=lambda e: self.set_bm_layout(hardlink=bool(e.value))).props(
                "dense data-testid=unified-bm-hardlink")
            ui.element("div").classes("pv-ud-hr")
            self.bm_unfollowed_button = ui.element("button").classes("pv-ud-tbtn").props(
                "type=button data-testid=unified-bm-unfollowed")
            with self.bm_unfollowed_button:
                ui.icon("person_search")
                ui.label("未フォロー作者を確認")
            self.bm_unfollowed_button.on("click", lambda _e: self.on_unfollowed_click())
            bookmark_open = ui.element("button").classes("pv-ud-tbtn").props(
                "type=button data-testid=unified-bookmark-open")
            with bookmark_open:
                ui.icon("bookmarks")
                ui.label("ブックマークから作品を選ぶ")
            bookmark_open.on("click", lambda _e: self.open_details("bookmark"))
        self._render_bm_layout()

    def _build_tools(self):
        with ui.element("div").classes("pv-ud-tools").props("data-testid=unified-tools"):
            self.select_all_button = self._tool_button("check_box", "表示中を全部選ぶ", "unified-select-all",
                                                       self.select_all_shown)
            self.deselect_all_button = self._tool_button("check_box_outline_blank", "選択を全部外す",
                                                         "unified-deselect-all", self.deselect_all_shown)
            ui.element("div").classes("pv-ud-div")
            self.star_filter_button = self._tool_button("star_border", "☆のみ", "unified-star-only",
                                                        self.toggle_favorite_filter)
            ui.element("div").classes("pv-ud-grow")
            self.sort_button = ui.element("button").classes("pv-ud-sortbtn").props(
                "type=button data-testid=unified-sort")
            with self.sort_button:
                self.sort_label = ui.label(U.SORT_LABELS[self.sort_key])
                ui.icon("arrow_drop_down")
                with ui.menu().props("data-testid=unified-sort-menu") as self.sort_menu:
                    self.sort_items = {}
                    for key, label in U.SORT_LABELS.items():
                        item = ui.menu_item(label, on_click=lambda _e=None, key=key: self.set_sort(key)).props(
                            f"data-testid=unified-sort-{key}")
                        self.sort_items[key] = item
            self.asc_button = self._icon_button("arrow_upward", "unified-sort-asc", "昇順",
                                                lambda: self.set_sort_dir(False))
            self.desc_button = self._icon_button("arrow_downward", "unified-sort-desc", "降順",
                                                 lambda: self.set_sort_dir(True))

    def _tool_button(self, icon, text, testid, handler):
        button = ui.element("button").classes("pv-ud-tbtn").props(f"type=button data-testid={testid}")
        with button:
            self_icon = ui.icon(icon)
            ui.label(text)
        button.on("click", lambda _e: handler())
        button.icon = self_icon
        return button

    def _icon_button(self, icon, testid, title, handler):
        button = ui.element("button").classes("pv-ud-ibtn").props(
            f"type=button data-testid={testid} title={title} aria-label={title}")
        with button:
            ui.icon(icon)
        button.on("click", lambda _e: handler())
        return button

    def _build_right(self):
        with ui.element("div").classes("pv-ud-right").props("data-testid=unified-right"):
            with ui.element("div").classes("pv-ud-card").props("data-testid=unified-target-card"):
                with ui.element("div").classes("pv-ud-cardhead"):
                    ui.label("この回の対象").classes("a")
                    self.target_count = ui.label("0 人").classes("b").props("data-testid=unified-target-count")
                self.chips = ui.element("div").classes("pv-ud-chips").props("data-testid=unified-targets")
                with self.chips:
                    self.targets_empty = ui.label("左の一覧でチェックすると、ここに積まれます").classes("pv-ud-empty").props(
                        "data-testid=unified-targets-empty")
                    self.chip_list = ui.element("div").classes("pv-ud-chiplist")
                    self.bookmark_chip = ui.element("div").classes("pv-ud-chip").props("data-testid=unified-chip-bookmark")
                    with self.bookmark_chip:
                        with ui.element("div").classes("who"):
                            ui.label("マイブックマーク（全体）").classes("nm")
                        ui.label("対象").classes("st").props("data-kind=in_progress")
                    self.bookmark_chip.set_visibility(False)
                self._chips = {}
            with ui.element("div").classes("pv-ud-fav").props("data-testid=unified-favorite-card"):
                ui.icon("star")
                ui.label("自動監視").classes("lab")
                self.favorite_count = ui.label("0 人").classes("num").props("data-testid=unified-favorite-count")
                ui.element("div").classes("pv-ud-grow")
                self.favorite_add_button = self._tool_button("playlist_add", "☆を対象にする",
                                                             "unified-favorite-add", self.add_favorites_to_targets)
            with ui.element("div").classes("pv-ud-runrow").props("data-testid=unified-runrow"):
                self.type_button = ui.element("button").classes("pv-ud-sq pv-ud-type").props(
                    "type=button data-testid=unified-type data-dim=false")
                with self.type_button:
                    self.type_icon = ui.icon(TYPE_ICONS[self.target_type])
                self.type_button.on("click", lambda _e: self.cycle_type())
                self.run_wrap = ui.element("div").classes("pv-ud-run-wrap")
                with self.run_wrap:
                    self.run_button = ui.element("button").classes("pv-ud-run").props(
                        "type=button data-testid=unified-run")
                    with self.run_button:
                        ui.icon("play_arrow")
                        self.run_label = ui.label("実行")
                self.run_button.on("click", lambda _e: self.run())
                self.pause_button = ui.element("button").classes("pv-ud-sq").props(
                    "type=button data-testid=unified-pause title=一時停止 aria-label=一時停止")
                with self.pause_button:
                    self.pause_icon = ui.icon("pause")
                self.pause_button.on("click", lambda _e: self.toggle_pause())
                self.stop_button = ui.element("button").classes("pv-ud-sq").props(
                    "type=button data-testid=unified-stop title=停止 aria-label=停止")
                with self.stop_button:
                    ui.icon("stop")
                self.stop_button.on("click", lambda _e: self.stop())

    def _build_band(self):
        """進捗帯: 閉じた 84px ⇔ 開いた 140px（上に「この回の記録」56px）。ダブルクリックと案内行のクリックで開閉。"""
        with ui.element("div").classes("pv-ud-band").props("data-testid=unified-band data-open=false") as self.band:
            self.sumband = ui.element("div").classes("pv-ud-sumband").props("data-testid=unified-sumband")
            with self.sumband:
                ui.label("この回の記録").classes("cap")
                self.box_values = {}
                for key, caption in (("start", "開始"), ("now", "進捗"), ("eta", "完了見込"), ("elapsed", "経過")):
                    with ui.element("div").classes("pv-ud-sumbox").props(f"data-testid=unified-box-{key}"):
                        ui.label(caption).classes("k")
                        self.box_values[key] = ui.label("").classes("v")
            self.sumband.set_visibility(False)
            self.band.on("dblclick", lambda _e: self.toggle_band(),
                         js_handler="e => { if (e.target.closest('button')) return; emit(); }")
            with ui.element("div").classes("pv-ud-band-main"):
                self._build_band_main()

    def _build_band_main(self):
        if True:
            with ui.element("div").classes("pv-ud-band-top"):
                self.band_status = ui.label("待機中").classes("pv-ud-band-status").props(
                    "data-testid=unified-band-status role=status")
                self.resume_button = ui.element("button").classes("pv-ud-textbtn").props(
                    "type=button data-testid=unified-resume")
                with self.resume_button:
                    ui.label("再開")
                self.resume_button.on("click", lambda _e: self.resume_interrupted())
                self.discard_button = ui.element("button").classes("pv-ud-textbtn").props(
                    "type=button data-testid=unified-discard")
                with self.discard_button:
                    ui.label("破棄")
                self.discard_button.on("click", lambda _e: self.discard_interrupted())
            self.bar = ui.element("div").classes("pv-ud-bar").props("data-testid=unified-bar data-mode=hidden")
            with self.bar:
                self.bar_fill = ui.element("div")
            self.hint_row = ui.element("div").classes("pv-ud-band-hint pv-ud-hintrow").props(
                "data-testid=unified-band-hint-row role=button")
            with self.hint_row:
                self.band_summary = ui.label("").classes("sum").props("data-testid=unified-band-summary")
                self.hint_caret = ui.icon("keyboard_arrow_up")
                self.hint_text = ui.label("クリックで詳細を開く").props("data-testid=unified-band-hint")
            self.hint_row.on("click", lambda _e: self.toggle_band())

    def _build_details_dialog(self):
        with ui.dialog().props("data-testid=unified-details-dialog") as self.dialog:
            with ui.card().classes("w-full").style(
                    "width:min(1100px,96vw);max-width:96vw;max-height:92vh;overflow-y:auto") as self.dialog_card:
                with ui.row().classes("w-full items-center"):
                    ui.label("作品単位で選ぶ（詳細）").classes("pv-library-heading")
                    ui.element("div").classes("pv-ud-grow")
                    ui.button(icon="close", color=None, on_click=self.close_details).props(
                        "flat round dense aria-label=閉じる data-testid=unified-details-close")
                # 従来画面の根は flex:1 1 0 なので、高さの決まった入れ物に入れる（無いと高さ 0 に潰れる）。
                self.dialog_body = ui.column().classes("w-full gap-0").style("height:min(78vh,820px);min-height:0")
        self.dialog.on("hide", lambda _e: self._details_hidden())

    # ------------------------------------------------------------------ the legacy (per-work) screens
    def _ensure_legacy(self):
        if self.legacy is None:
            with self.dialog_body:
                if self.legacy_factory is not None:
                    self.legacy = self.legacy_factory()
                else:
                    from .managed_download_view import ManagedDownloadView
                    self.legacy = ManagedDownloadView(self.services)
        return self.legacy

    @property
    def managed(self):
        """作品単位の確認を持つ ManagedDownloadView（複数媒体のときは MediaDownloadView の中の 1 つ）。"""
        legacy = self.legacy
        if legacy is None:
            return None
        views = getattr(legacy, "views", None)
        return views.get("illustration") if isinstance(views, dict) else legacy

    def open_details(self, mode=None):
        if self.closed:
            return False
        self._ensure_legacy()
        self.legacy_open = True
        self._mirror_to_legacy()
        if mode in ("bookmark", "author"):
            managed = self.managed
            if managed is not None and hasattr(managed, "set_target_mode"):
                managed.set_target_mode(mode)
        self._set_legacy_active()
        self.dialog.open()
        return True

    def close_details(self):
        self.dialog.close()
        self._details_hidden()

    def _details_hidden(self):
        if self.legacy_open:
            self.legacy_open = False
            self._set_legacy_active()

    def _set_legacy_active(self):
        if self.legacy is not None:
            self.legacy.set_active(self.active and self.legacy_open and not self.closed)

    def _mirror_to_legacy(self):
        """この回の対象とフォロー台帳の作者を、詳細の作品単位画面へ渡す（同じ作者で差分確認ができるように）。"""
        managed = self.managed
        if managed is None or not hasattr(managed, "author_targets"):
            return
        try:
            from ..author_download import AuthorTarget
            by_id = {str(a.get("user_id")): a for a in self.authors}

            def target(uid, name, origin):
                known = by_id.get(uid, {})
                favourite = known.get("is_favorite")
                return AuthorTarget(uid, str(name or uid)[:256], origin,
                                    is_favorite=None if not known else bool(favourite),
                                    follow_order=known.get("follow_order") if isinstance(known.get("follow_order"), int)
                                    and known.get("follow_order") >= 0 else None)
            desired = {t["user_id"]: t for t in self.targets if _is_author_id(t["user_id"])}
            existing = tuple(managed.author_targets)
            # 詳細の中で自分で足した作者は残す。この画面が前に渡して、いまは外れた作者だけを外す。
            kept = tuple(a for a in existing if not (a.user_id in self._mirrored and a.user_id not in desired))
            have = {a.user_id for a in kept}
            added = tuple(target(uid, t["name"], t["origin"]) for uid, t in desired.items() if uid not in have)
            merged = kept + added
            if merged != existing:
                managed.author_targets = merged
                managed.author_revision += 1
            self._mirrored = set(desired)
            if not managed.follow_authors and self.authors:
                managed.follow_authors = tuple(target(str(a["user_id"]), a.get("name"), "follow")
                                               for a in self.authors if _is_author_id(str(a.get("user_id"))))
            managed._render_author_targets()
            managed._render_follow_list()
        except Exception:
            pass

    # ------------------------------------------------------------------ data
    async def reload(self, *, force_counts=False):
        """作者表をフォロー台帳（ローカル）から読み直す。通信はしない。

        保存済み件数（受領記録の集計）は重いので、直近 30 秒以内に読んだ値があれば読み直さない
        （実行が終わったときだけ force_counts で読み直す）。
        """
        if self.closed:
            return False
        read = getattr(self.services, "unified_download_authors", None)
        try:
            rows = await asyncio.to_thread(read) if callable(read) else ()
        except Exception:
            rows = ()
        if self.closed:
            return False
        self.authors = [dict(row) for row in rows]
        counts = getattr(self.services, "saved_author_counts", None)
        now = time.monotonic()
        if callable(counts) and (force_counts or now - self._counts_at >= 30.0):
            try:
                self.work_counts = dict(await asyncio.to_thread(counts))
                self._counts_at = time.monotonic()
            except Exception:
                self.work_counts = {}
            if self.closed:
                return False
        self._loaded = True
        self.render_all()
        return True

    def apply_authors(self, rows):
        self.authors = [dict(row) for row in rows]
        self._loaded = True
        self.page = 0
        self.render_all()

    # ------------------------------------------------------------------ state queries
    def shown_authors(self):
        shown = U.filter_authors(self.authors, self.query)
        if self.favorite_only:
            shown = [a for a in shown if a.get("is_favorite")]
        return U.sort_authors(shown, self.work_counts, self.sort_key, self.sort_reverse)

    def page_count(self, total=None):
        total = len(self.shown_authors()) if total is None else total
        return max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    def window(self):
        shown = self.shown_authors()
        pages = self.page_count(len(shown))
        self.page = min(max(self.page, 0), pages - 1)
        start = self.page * PAGE_SIZE
        return shown, shown[start:start + PAGE_SIZE], pages

    @property
    def running(self):
        return self.run_state != "idle"

    def target_ids(self):
        return [str(t["user_id"]) for t in self.targets]

    def header_check_state(self):
        shown = {str(a["user_id"]) for a in self.shown_authors()}
        if not shown:
            return "empty"
        n = len(shown & set(self.target_ids()))
        return "empty" if n == 0 else "all" if n == len(shown) else "partial"

    def run_block_reason(self):
        """実行できない理由（できるなら ""）。"""
        if self.running:
            return "実行中です"
        if self.starting:
            return "開始しています"
        if self.mode == "bookmark":
            if not (self.bm_show or self.bm_hide):
                return "含めるブックマークを1つ選びます"
            if self.engine is None or not callable(getattr(self.services, "start_bookmark_run", None)):
                return "一括実行の準備ができていません（保存先を設定してください）"
            if self._download_busy():
                return "いまダウンロードが走っています。終わってから実行してください"
            return ""
        if not self.targets:
            return NEED_TARGET
        if self.engine is None:
            return "一括実行の準備ができていません（保存先とフォロー中リストの同期を設定してください）"
        if self._download_busy():
            return "いまダウンロードが走っています。終わってから実行してください"
        return ""

    def _download_busy(self):
        active = getattr(self.services, "batch_run_active", None)
        try:
            return bool(callable(active) and active())
        except Exception:
            return False

    # ------------------------------------------------------------------ actions (left)
    def set_mode(self, mode):
        if self.closed or mode not in ("followed", "bookmark") or self.running:
            return False
        if mode != self.mode:
            self.mode = mode
            self.root.props["data-mode"] = mode
            self.root.update()
            self.render_all()
        return True

    def set_query(self, value):
        self.query = str(value or "")
        self.page = 0
        self.render_list()
        self._render_id_row()

    def set_sort(self, key):
        if key not in U.SORT_LABELS:
            return False
        self.sort_key = key
        self.page = 0
        self.sort_label.set_text(U.SORT_LABELS[key])
        self.render_list()
        return True

    def set_sort_dir(self, reverse):
        self.sort_reverse = bool(reverse)
        self.page = 0
        self._render_sort_dir()
        self.render_list()

    def toggle_favorite_filter(self):
        self.favorite_only = not self.favorite_only
        self.page = 0
        self._render_tools()
        self.render_list()

    def go_page(self, delta):
        self.page += int(delta)
        self.render_list()

    def select_all_shown(self):
        if self.running:
            return
        self.targets = U.add_targets_bulk(self.targets, self.shown_authors())
        self.after_targets_changed()

    def deselect_all_shown(self):
        if self.running:
            return
        self.targets = U.remove_targets_bulk(self.targets, [a["user_id"] for a in self.shown_authors()])
        self.after_targets_changed()

    def toggle_header_check(self):
        if self.header_check_state() == "all":
            self.deselect_all_shown()
        else:
            self.select_all_shown()

    def toggle_pick(self, user_id):
        if self.running:
            return
        uid = str(user_id)
        if uid in self.target_ids():
            self.targets = U.remove_target(self.targets, uid)
            self.after_targets_changed(removed=uid)
        else:
            author = U.find_author(self.authors, uid) or {"user_id": uid, "name": uid}
            self.targets, added = U.add_target(self.targets, author, "follow")
            self.after_targets_changed(added=self.targets[-1] if added else None)

    def drop_target(self, user_id):
        if self.running:
            return
        self.targets = U.remove_target(self.targets, user_id)
        self.after_targets_changed(removed=str(user_id))

    def pick_from_id(self):
        uid = U.normalize_query(self.query)
        if self.running or not _is_author_id(uid):
            self.set_notice("作者IDは先頭が0でない数字（最大20桁）で入力してください")
            return False
        known = U.find_author(self.authors, uid)
        author = known or {"user_id": uid, "name": f"ID {uid}"}
        self.targets, added = U.add_target(self.targets, author, "follow" if known else "id")
        if not added:
            self.set_notice(f"{author.get('name')} は既に対象に入っています。")
        self.after_targets_changed(added=self.targets[-1] if added else None)
        return added

    def add_favorites_to_targets(self):
        favourites = [a for a in self.authors if a.get("is_favorite")]
        if self.running:
            return False
        if not favourites:
            self.set_notice("☆の付いた作者がいません。")
            return False
        self.targets = U.add_targets_bulk(self.targets, favourites)
        self.after_targets_changed()
        return True

    def toggle_favorite(self, user_id):
        uid = str(user_id)
        author = U.find_author(self.authors, uid)
        if author is None:
            return False
        want = not bool(author.get("is_favorite"))
        setter = getattr(self.services, "set_favourite", None)
        try:
            if not callable(setter):
                raise RuntimeError("no favourite book")
            setter(uid, want)
        except Exception:
            self.set_notice("☆を保存できませんでした。")
            return False
        author["is_favorite"] = want
        self.render_list() if self.favorite_only else self._update_row(uid)
        self._render_favorite_card()
        return True

    def cycle_type(self):
        if self.running:
            return
        self.target_type = TYPE_CYCLE[(TYPE_CYCLE.index(self.target_type) + 1) % len(TYPE_CYCLE)]
        self.type_button.props["data-dim"] = "true"
        self.type_button.update()
        self.render_controls()
        with self.root:
            ui.timer(0.12, self._type_restore, once=True)

    def _type_restore(self):
        if not self.closed:
            self.type_button.props["data-dim"] = "false"
            self.type_button.update()

    def set_notice(self, text):
        self.notice_text = text
        self._render_band()

    def after_targets_changed(self, *, added=None, removed=None):
        """added / removed: 1 件だけ増減したときはチップを 1 つだけ足す・消す（数百人でも重くしない）。"""
        self._sync_marks()
        self._render_id_row()
        if added is None and removed is None:
            self.render_targets()
        else:
            if removed is not None:
                entry = self._chips.pop(removed, None)
                if entry is not None:
                    entry["row"].delete()
            if added is not None and str(added["user_id"]) not in self._chips and self.mode == "followed":
                with self.chip_list:
                    self._chip(added)
            self._sync_target_header()
        self.render_controls()
        if self.legacy_open:
            self._mirror_to_legacy()

    # ------------------------------------------------------------------ actions (run)
    def run(self):
        if self.mode == "bookmark":
            if self.run_block_reason():
                self.set_notice(self.run_block_reason())
                return False
            self._run_task = asyncio.ensure_future(self.run_bookmarks())
            return True
        reason = self.run_block_reason()
        if reason:
            self.set_notice(reason if reason != NEED_TARGET else "")
            return False
        ids = [uid for uid in self.target_ids() if _is_author_id(uid)]
        if not ids:
            return False
        try:
            self.services.start_batch_run(tuple(ids), origin="manual", target_type=self.target_type)
        except Exception as exc:
            self.set_notice("開始できませんでした。設定を確認してください。"
                            if not isinstance(exc, RuntimeError) else "いまダウンロードが走っています。終わってから実行してください。")
            return False
        self.run_target_ids = ids
        self.progress = U.RunProgress(ids)
        self.progress.state = "running"
        self.run_started = datetime.now()
        self.run_ended = None
        self.run_state = "running"
        self.band_state, self.band_error, self.notice_text = "running", "", ""
        self.resumable = None
        self.render_all()
        return True

    async def run_bookmarks(self):
        """「マイブックマーク」の実行: 一覧を取り直し（取得停止ゲートの内側）、選んだ公開/非公開を保存する。"""
        from ..bookmark_run import VISIBILITY_ROWS
        reason = self.run_block_reason()
        if reason:
            self.set_notice(reason)
            return False
        chosen = [v for v, on in (("show", self.bm_show), ("hide", self.bm_hide)) if on]
        self.starting = True
        self.render_controls()
        try:
            await self.services.start_bookmark_run(tuple(chosen), origin="manual", target_type=self.target_type)
        except Exception as exc:
            self.starting = False
            self.set_notice("いまダウンロードが走っています。終わってから実行してください。"
                            if isinstance(exc, RuntimeError) and "active" in str(exc)
                            else "開始できませんでした。設定を確認してください。")
            self.render_controls()
            return False
        self.starting = False
        ids = [VISIBILITY_ROWS[v] for v in chosen]
        self.run_target_ids = ids
        self.progress = U.RunProgress(ids)
        self.progress.kind = "bookmarks"
        self.progress.state = "running"
        self.run_started = datetime.now()
        self.run_ended = None
        self.run_state = "running"
        self.band_state, self.band_error, self.notice_text = "running", "", ""
        self.resumable = None
        self.render_all()
        return True

    # ---- the bookmark pane ---------------------------------------------------------------------
    def _read_bookmark_layout(self):
        read = getattr(self.services, "bookmark_layout", None)
        try:
            layout = read() if callable(read) else {}
        except Exception:
            layout = {}
        self.bm_hierarchical = bool(layout.get("hierarchical", False))
        self.bm_hardlink = bool(layout.get("hardlink", False))

    def _render_bm_layout(self):
        self.bm_buttons["hier"].props["data-selected"] = "true" if self.bm_hierarchical else "false"
        self.bm_buttons["flat"].props["data-selected"] = "false" if self.bm_hierarchical else "true"
        for button in self.bm_buttons.values():
            button.update()
        if self.bm_hardlink_box.value != self.bm_hardlink:
            self.bm_hardlink_box.set_value(self.bm_hardlink)

    def set_bm(self, which, value):
        if which not in ("show", "hide") or self.running:
            return False
        if which == "show":
            self.bm_show = bool(value)
        else:
            self.bm_hide = bool(value)
        self.bm_hint.set_visibility(not (self.bm_show or self.bm_hide))
        self.render_controls()
        self._render_band()
        return True

    def set_bm_layout(self, *, hierarchical=None, hardlink=None):
        """階層化/フラットは use_work_folder、ハードリンクは bookmark_download_mode（link/direct）へ書く。"""
        setter = getattr(self.services, "set_bookmark_layout", None)
        if not callable(setter):
            self.set_notice("保存方式を変更できません（設定の保存先がありません）。")
            return False
        try:
            setter(hierarchical=hierarchical, hardlink=hardlink)
        except Exception:
            self.set_notice("保存方式を保存できませんでした。")
        self._read_bookmark_layout()
        self._render_bm_layout()
        self._render_band()
        return True

    def on_unfollowed_click(self):
        self.open_unfollowed()

    def open_unfollowed(self):
        """「未フォロー作者を確認」: 従来のダイアログ（代表作つき・その場でフォロー）をそのまま開く。"""
        if self.closed:
            return False
        if self.unfollowed_view is None:
            from .unfollowed_authors_view import UnfollowedAuthorsView
            with self.client:
                self.unfollowed_view = UnfollowedAuthorsView(self.services)
        self.unfollowed_view.show()
        return True

    def toggle_pause(self):
        engine = self.engine
        if engine is None or not engine.active:
            return False
        if engine.state == "paused":
            engine.resume()
        else:
            engine.pause("user")
        self.sync_engine()
        self.render_controls()
        return True

    def stop(self):
        engine = self.engine
        if engine is None or not engine.active:
            return False
        engine.stop()
        self.sync_engine()
        self.render_controls()
        return True

    def resume_interrupted(self):
        resume = getattr(self.services, "resume_batch_run", None)
        if not callable(resume) or self.running:
            return False
        try:
            resume()
        except Exception:
            self.set_notice("再開できませんでした。")
            return False
        try:
            ids = [row.author_id for row in self.engine.ledger.authors(self.engine.run_id)]
        except Exception:
            ids = []
        self.run_target_ids = ids
        self.progress = U.RunProgress(ids)
        self.progress.kind = getattr(self.engine, "kind", "authors") or "authors"
        self.progress.state = "running"
        self.run_started = datetime.now()
        self.run_ended = None
        self.resumable = None
        self.band_state = "running"
        self.sync_engine()
        return True

    def discard_interrupted(self):
        engine = self.engine
        if engine is not None:
            try:
                engine.discard_resumable()
            except Exception:
                pass
        self.resumable = None
        self._render_band()

    # ------------------------------------------------------------------ engine → screen
    def tick(self):
        """0.4 秒ごと: エンジンの進捗イベントを取り込み、状態を画面へ写す。UI スレッドで呼ぶ。"""
        if self.closed or not self.active:
            return
        changed = False
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            self.progress.apply(event)
            changed = True
            if event.get("event") == "run_end":
                self.run_ended = datetime.now()
                self.band_state = str(event.get("outcome", "done"))
                self.band_error = str(event.get("error_code") or "")
                self._after_run_end()
        if self.sync_engine() or changed:
            self.refresh_chip_status()
            self.render_controls()
            if changed:
                self._render_band()
        if self.band_open and self.running:
            self.render_band_boxes()

    def sync_engine(self):
        """エンジンの state を画面の run_state へ。変わったら True。"""
        engine = self.engine
        state = "idle"
        if engine is not None and getattr(engine, "active", False):
            state = engine.state
        before = (self.run_state, self.band_state)
        self.run_state = state if state in ("running", "paused", "stopping") else "idle"
        if self.run_state != "idle":
            self.band_state = self.run_state
            self.progress.state = self.run_state
            self.progress.pause_reason = getattr(engine, "pause_reason", "") or ""
        elif before[0] != "idle" and self.band_state in ("running", "paused", "stopping"):
            # イベントを取りこぼしたまま止まった（再起動回復など）。台帳の最終状態を見る。
            self.band_state = self._ledger_outcome() or "done"
            self._after_run_end()
        if (self.run_state, self.band_state) != before:
            self._render_band()
            return True
        return False

    def _ledger_outcome(self):
        try:
            engine = self.engine
            run = engine.ledger.run(engine.run_id)
            return {"interrupted": "stopped"}.get(run.state, run.state if run.state in ("done", "stopped", "failed") else None)
        except Exception:
            return None

    def _after_run_end(self):
        """実行が終わった: 作者表（最後に取得した日・保存済み）を読み直し、再開の提示を確かめる。"""
        self.refresh_resumable()
        self._schedule_reload(force_counts=True)

    def _schedule_reload(self, force_counts=False):
        if self.closed:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self._reload_task = asyncio.ensure_future(self.reload(force_counts=force_counts))

    # ------------------------------------------------------------------ rendering
    def render_all(self):
        if self.closed:
            return
        followed = self.mode == "followed"
        for key, button in self.mode_buttons.items():
            button.props["data-selected"] = "true" if key == self.mode else "false"
            if self.running:
                button.props["disabled"] = True
            else:
                button.props.pop("disabled", None)
            button.update()
        self.followed_pane.set_visibility(followed)
        self.bookmark_pane.set_visibility(not followed)
        self._render_tools()
        self._render_sort_dir()
        self._render_id_row()
        self.render_list()
        self.render_targets()
        self._render_favorite_card()
        self.render_controls()
        self._render_band()

    def _set_disabled(self, element, disabled):
        if disabled:
            element.props["disabled"] = True
        else:
            element.props.pop("disabled", None)
        element.update()

    def _render_tools(self):
        self.star_filter_button.props["data-on"] = "true" if self.favorite_only else "false"
        self.star_filter_button.icon.props["name"] = "star" if self.favorite_only else "star_border"
        self.star_filter_button.icon.update()
        self.star_filter_button.update()

    def _render_sort_dir(self):
        self.asc_button.props["data-on"] = "false" if self.sort_reverse else "true"
        self.desc_button.props["data-on"] = "true" if self.sort_reverse else "false"
        self.asc_button.update()
        self.desc_button.update()

    def _render_id_row(self):
        if not U.is_id_query(self.query):
            self.id_row.set_visibility(False)
            return
        uid = U.normalize_query(self.query)
        known = U.find_author(self.authors, uid)
        self.id_row_text.text = (f"フォロー中の {known['name']} です（ID: {uid}）" if known
                                 else f"ID {uid} を対象にする（フォロー中にはいません）")
        self.id_row_text.update()
        self.id_row.set_visibility(True)

    def render_list(self):
        if self.closed:
            return
        shown, window, pages = self.window()
        chosen = set(self.target_ids())
        self.author_list.clear()
        self._rows = {}
        with self.author_list:
            if not window and self._loaded:
                ui.label("フォロー中の作者がまだ取り込まれていません。設定の「フォロー中リストの同期」を実行してください。"
                         if not self.authors else "条件に合う作者はいません。").classes("pv-ud-empty").props(
                    "data-testid=unified-empty")
            for author in window:
                self._row(author, str(author["user_id"]) in chosen)
        total = len(shown)
        if total == 0:
            note = f"0 / {len(self.authors)} 人"
        else:
            start = self.page * PAGE_SIZE
            note = f"{start + 1}-{start + len(window)} / {total} 人"
            if total != len(self.authors):
                note += f"（全{len(self.authors)}人中）"
            if pages > 1:
                note += f"　{self.page + 1} / {pages} ページ"
        self.count_label.set_text(note)
        for button, disabled in ((self.prev_button, self.page <= 0), (self.next_button, self.page >= pages - 1)):
            button.set_visibility(pages > 1)
            self._set_disabled(button, disabled)
        self._sync_header_check()

    def _row(self, author, picked):
        uid = str(author["user_id"])
        row = ui.element("div").classes("pv-ud-row").props(
            f"data-testid=unified-author-row data-user-id={uid} data-picked={'true' if picked else 'false'} role=row")
        with row:
            check = ui.element("div").classes("pv-ud-check").props("data-testid=unified-row-check")
            with check:
                icon = ui.icon("check")
            icon.set_visibility(picked)
            star = ui.element("div").classes("pv-ud-star").props(
                f"data-testid=unified-row-star data-on={'true' if author.get('is_favorite') else 'false'} "
                "role=button aria-label=☆自動監視の切り替え")
            with star:
                star_icon = ui.icon("star" if author.get("is_favorite") else "star_border")
            star.on("click.stop", lambda _e, u=uid: self.toggle_favorite(u))
            ui.label(str(author.get("name") or uid)).classes("pv-ud-c-name")
            ui.label(uid).classes("pv-ud-c-id")
            ui.label(str(self.work_counts.get(uid, 0))).classes("pv-ud-c-count").props("data-testid=unified-row-count")
            ui.label(U.format_last_downloaded(local_stamp(author.get("last_downloaded")))).classes("pv-ud-c-last").props(
                "data-testid=unified-row-last")
        row.on("click", lambda _e, u=uid: self.toggle_pick(u))
        self._rows[uid] = dict(row=row, icon=icon, star=star, star_icon=star_icon)

    def _update_row(self, uid):
        entry = self._rows.get(uid)
        author = U.find_author(self.authors, uid)
        if entry is None or author is None:
            return
        on = bool(author.get("is_favorite"))
        entry["star"].props["data-on"] = "true" if on else "false"
        entry["star"].update()
        entry["star_icon"].props["name"] = "star" if on else "star_border"
        entry["star_icon"].update()

    def _sync_marks(self):
        """チェック印だけを合わせる（表は作り直さない）。"""
        chosen = set(self.target_ids())
        for uid, entry in self._rows.items():
            picked = uid in chosen
            entry["row"].props["data-picked"] = "true" if picked else "false"
            entry["row"].update()
            entry["icon"].set_visibility(picked)
        self._sync_header_check()

    def _sync_header_check(self):
        state = self.header_check_state()
        self.header_check.props["data-state"] = state
        self.header_check.update()
        self.header_check_icon.props["name"] = "remove" if state == "partial" else "check"
        self.header_check_icon.update()
        self.header_check_icon.set_visibility(state in ("all", "partial"))

    def render_targets(self):
        """この回の対象チップを全部作り直す（まとめて増減したとき・モードを替えたとき）。"""
        if self.closed:
            return
        self.chip_list.clear()
        self._chips = {}
        if self.mode == "followed":
            with self.chip_list:
                for target in self.targets:
                    self._chip(target)
        self._sync_target_header()

    def _chip_status(self, uid):
        if uid in self.progress.failed_authors:
            return "failed", "失敗"
        kind, index, total = self.progress.chip(uid)
        text = "完了" if kind == "done" else f"取得中 {index}/{total}" if kind == "in_progress" else "待機"
        return kind, text

    def _chip(self, target):
        uid = str(target["user_id"])
        kind, text = self._chip_status(uid)
        row = ui.element("div").classes("pv-ud-chip").props(
            f"data-testid=unified-chip data-user-id={uid} data-origin={target.get('origin')} data-status={kind}")
        with row:
            with ui.element("div").classes("who"):
                ui.label(str(target.get("name") or uid)).classes("nm")
                ui.label(uid).classes("id")
            status = ui.label(text).classes("st").props(f"data-kind={kind}")
            drop = ui.element("button").classes("x").props(
                "type=button data-testid=unified-chip-remove title=取り消す aria-label=取り消す")
            with drop:
                ui.icon("close")
            drop.on("click", lambda _e, u=uid: self.drop_target(u))
        self._chips[uid] = dict(row=row, status=status)

    def _sync_target_header(self):
        followed = self.mode == "followed"
        self.chip_list.set_visibility(followed)
        self.bookmark_chip.set_visibility(not followed)
        self.targets_empty.set_visibility(followed and not self.targets)
        if followed:
            self.target_count.set_text(f"{U.summarize_targets(self.targets)['total']} 人")
        else:
            self.target_count.set_text("全体")

    def refresh_chip_status(self):
        """チップの状態（待機/取得中/完了/失敗）だけを書き換える。チップは作り直さない。"""
        for uid, entry in self._chips.items():
            kind, text = self._chip_status(uid)
            if entry["status"].text != text:
                entry["status"].set_text(text)
            if entry["status"].props.get("data-kind") != kind:
                entry["status"].props["data-kind"] = kind
                entry["status"].update()
                entry["row"].props["data-status"] = kind
                entry["row"].update()

    def _render_favorite_card(self):
        count = sum(1 for a in self.authors if a.get("is_favorite"))
        self.favorite_count.set_text(f"{count} 人")

    def render_controls(self):
        if self.closed:
            return
        if self.running:
            self.root.classes(add="pv-ud-running")
        else:
            self.root.classes(remove="pv-ud-running")
        reason = self.run_block_reason()
        runnable = not reason
        self._set_disabled(self.run_button, not runnable)
        title = reason or ""
        self.run_wrap.props["title"] = title
        self.run_wrap.update()
        self.run_button.props["aria-label"] = reason if reason else "実行"
        self.run_button.update()
        self.run_label.set_text("実行中" if self.running else "実行")
        idle = not self.running
        paused = self.run_state == "paused"
        self._set_disabled(self.pause_button, idle or self.engine is None or self.run_state == "stopping")
        self._set_disabled(self.stop_button, idle or self.engine is None or self.run_state == "stopping")
        self._set_disabled(self.type_button, not idle)
        self.pause_icon.props["name"] = "play_arrow" if paused else "pause"
        self.pause_icon.update()
        self.pause_button.props["title"] = "再開" if paused else "一時停止"
        self.pause_button.update()
        self.type_icon.props["name"] = TYPE_ICONS[self.target_type]
        self.type_icon.update()
        self.type_button.props["title"] = TYPE_TOOLTIPS[self.target_type]
        self.type_button.update()
        for button in (self.select_all_button, self.deselect_all_button, self.favorite_add_button):
            self._set_disabled(button, self.running)

    def _save_label(self):
        getter = getattr(self.services, "managed_save_label", None)
        return getter() if callable(getter) else "専用管理フォルダー"

    # ---- the band: open / close, the record boxes ---------------------------------------------
    def on_body_size(self, height):
        """左右カラムの実測の高さ（q-resize-observer）。小さくなりすぎたら詳細を自動で閉じる。"""
        try:
            height = float(height)
        except (TypeError, ValueError):
            return
        if height <= 0:
            return
        self.body_h = height
        if self.band_open and height < BODY_MIN_H:
            self._set_band_open(False)

    def toggle_band(self):
        if self.closed:
            return False
        if not self.band_open:
            # 開くと本体が 56px 縮む。それで BODY_MIN_H を割るなら開かない（元 T-0190）。
            if self.body_h and self.body_h - SUMMARY_BAND_H < BODY_MIN_H:
                self.set_notice("ウィンドウの高さが足りないため詳細を開けません。縦に広げて再度お試しください。")
                return False
            self.notice_text = ""
        self._set_band_open(not self.band_open)
        return True

    def _set_band_open(self, value):
        self.band_open = bool(value)
        self.band_height = BAND_OPEN_H if self.band_open else BAND_CLOSED_H
        self.band.props["data-open"] = "true" if self.band_open else "false"
        self.band.style(replace=f"height:{self.band_height}px;flex-basis:{self.band_height}px")
        self.sumband.set_visibility(self.band_open)
        self.hint_caret.props["name"] = "keyboard_arrow_down" if self.band_open else "keyboard_arrow_up"
        self.hint_caret.update()
        self.hint_text.set_text("クリックで詳細を閉じる" if self.band_open else "クリックで詳細を開く")
        self.render_band_boxes()
        self._render_band()

    def render_band_boxes(self):
        """「この回の記録」: 開始・進捗・完了見込・経過（元 _apply_detail）。閉じている間は作らない。"""
        if not self.band_open or self.closed:
            return
        progress = self.progress
        start = self.run_started
        if start is not None:
            end = self.run_ended or datetime.now()
            elapsed_sec = max(0.0, (end - start).total_seconds())
        else:
            elapsed_sec = 0.0
        elapsed_text, eta_text = U.estimate_completion(start, progress.done, progress.total, elapsed_sec,
                                                       work_idx=progress.work_idx, work_total=progress.work_total)
        failed = int((progress.counts or {}).get("n_failed", 0)) + len(progress.failed_authors)
        if progress.kind == "bookmarks":
            now_value = f"{progress.done}/{progress.total}件の一覧"
            if self.band_state in ("running", "paused", "stopping") and progress.total:
                now_value = self._bookmark_text() or now_value
        elif progress.done <= 0 and progress.work_total > 0:
            now_value = f"作品 {progress.work_idx}/{progress.work_total}"
            if progress.work_title:
                now_value += f": {progress.work_title}"
        else:
            now_value = f"{progress.done}/{progress.total}人"
        if failed > 0:
            now_value += f" ・失敗{failed}"
        self.box_values["start"].set_text(U.format_hms(start))
        self.box_values["now"].set_text(now_value)
        self.box_values["now"].props["data-danger"] = "true" if failed > 0 else "false"
        self.box_values["now"].update()
        self.box_values["eta"].set_text(eta_text or "算出中…")
        self.box_values["elapsed"].set_text(elapsed_text if start is not None else "")

    def _bookmark_text(self):
        """元の「公開ブックマーク: c / t 件」。"""
        from ..bookmark_run import ROW_VISIBILITY, VISIBILITY_LABELS
        progress = self.progress
        name = ROW_VISIBILITY.get(str(progress.current))
        if name is None or not progress.work_total:
            return ""
        return f"{VISIBILITY_LABELS[name]}: {max(0, min(progress.work_idx - 1, progress.work_total))} / {progress.work_total} 件"

    def _render_band(self):
        if self.closed:
            return
        progress = self.progress
        if self.band_state in ("idle",):
            text = U.status_text("idle")
        else:
            index = min(progress.done + (1 if progress.current else 0), progress.total) if progress.total else 0
            text = U.status_text(self.band_state, pause_reason=progress.pause_reason,
                                 index=index, total=progress.total, outcome_code=self.band_error)
            if progress.kind == "bookmarks" and self.band_state == "running":
                text = self._bookmark_text() or text
        interrupted = self.resumable is not None and not self.running
        if interrupted and self.band_state == "idle":
            text = U.INTERRUPTED_TEXT
        self.band_status.set_text(text)
        self.resume_button.set_visibility(interrupted)
        self.discard_button.set_visibility(interrupted)
        if self.mode == "bookmark" or progress.kind == "bookmarks" and self.running:
            summary = (f"ブックマークDL · {'階層化' if self.bm_hierarchical else 'フラット'} · "
                       f"保存先 {self._save_label()}")
        else:
            summary = f"差分DL · {TYPE_LABELS[self.target_type]} · 保存先 {self._save_label()}"
        if self.band_state in ("done", "stopped", "failed") and progress.failed_authors:
            summary = f"取得に失敗した作者 {len(progress.failed_authors)} 人 · " + summary
        if self.notice_text:
            summary = self.notice_text
        self.band_summary.set_text(summary)
        mode, width = "hidden", 0
        if self.band_state in ("running", "paused", "stopping") and progress.total:
            if progress.done <= 0:
                mode = "indeterminate"
            else:
                mode, width = "determinate", min(100, progress.done * 100 // progress.total)
        elif self.band_state == "done":
            mode, width = "determinate", 100
        self.bar.props["data-mode"] = mode
        self.bar.update()
        self.bar_fill.style(replace=f"width:{width}%")
        self.bar.set_visibility(mode != "hidden")
        self.render_band_boxes()

    # ------------------------------------------------------------------ shell contract
    def set_active(self, active):
        if self.closed:
            return
        active = bool(active)
        if active != self.active:
            self.epoch += 1
        self.active = active
        self.root.props["data-active"] = str(active).lower()
        self.root.update()
        if not active and self.unfollowed_view is not None:
            self.unfollowed_view.hide()
        if not active and self.legacy_open:
            self.dialog.close()            # 他のタブへ移るとき、詳細ダイアログを開いたままにしない
            self.legacy_open = False
        self._set_legacy_active()
        if active:
            self.sync_engine()
            self.refresh_resumable()
            self.render_targets()
            self.render_controls()
            if self._loaded:
                self._schedule_reload()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.active = False
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if self.unfollowed_view is not None:
            self.unfollowed_view.close()
        if self.legacy is not None:
            self.legacy.close()
