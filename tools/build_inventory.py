"""元リポ(Flet 版)を静的に走査し、機械的に識別子を列挙して docs/inventory.json へ出す。

読み取り専用。元リポのファイルは一切書き換えない。実行は AST のみ（元コードを import・実行しない）。
種別(kind):
  ui_text      ft.* コンストラクタ等の文字列(label/tooltip/hint_text/title/text/Text 本文 等)
  text_literal 上記で取れなかった日本語文字列(ログ・例外文言・SQL・docstring を除く)
  label_const  モジュール/クラス直下の定数(タプル・リスト・辞書)内の日本語文字列(並び替え名・モード名など)
  snack        snack/toast/通知関数へ渡す利用者向けメッセージ
  handler      on_click/on_change/on_submit/on_keyboard_event 等へ結ばれた関数
  setting_key  get_setting/set_setting 等・*_KEY 定数のキー(キーごとに 1 件)
  shortcut     on_keyboard_event 系の関数が見る e.key の文字列
  menu_item    PopupMenuItem/ContextMenu/MenuItemButton 等の項目
  background   Thread/Timer/Executor/while+sleep ループ(起動時・定期処理)
  db_table     database.py の CREATE TABLE / ALTER TABLE ADD COLUMN
  db_method    database.py の公開メソッド
  api_public   core.py・pixiv_client.py 等の公開関数/クラス/メソッド
  net_endpoint core.py・pixiv_client.py の外部 URL/エンドポイント文字列
  route        server.py のルート(self.path / parsed.path の比較)
使い方: python -B tools/build_inventory.py [--src S:\\Projects\\PixivVault] [--out docs/inventory.json]
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_SRC = r"S:\Projects\PixivVault"
CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uff00-\uffef]")
HAS_TEXT = re.compile(r"[A-Za-z\u3040-\u30ff\u3400-\u9fff]")

# 走査対象は元リポのルート直下 *.py のみ(.claude\worktrees 等の複製・tools・build は含めない)
UI_TEXT_KW = {
    "label", "tooltip", "hint_text", "helper_text", "error_text", "title", "text", "subtitle",
    "hint", "prefix_text", "suffix_text", "semantics_label", "message", "placeholder",
    "label_text", "button_text", "confirm_text", "cancel_text", "content", "value_text",
}
TEXT_VALUE_CONTROLS = {"Text", "Markdown", "Chip", "Tab", "Badge", "Tooltip", "SelectionArea"}
NON_TEXT_CTRL = {"Key", "ScrollKey", "Ref", "Offset", "Animation", "TextStyle", "ButtonStyle", "Padding"}
MENU_CTRL = {"PopupMenuItem", "MenuItemButton", "SubmenuButton", "MenuItem", "ContextMenu"}
HANDLER_KW = re.compile(r"^on_[a-z_]+$")
SETTING_CALL = re.compile(r"(get|set|del|delete|update)_?(setting|config|settings)$|^(get|set)_setting$|^(load|save)_json_setting$")
# 設定キーを 2 番目の引数(db の次)に取る薄い包み(filter_panel.load_json_setting / save_json_setting)。
# get_setting/set_setting を直接呼ばないので、これを拾わないと `*_KEY` 定数経由の設定キーが inventory から落ちる。
SETTING_WRAPPERS = {"load_json_setting", "save_json_setting"}
SNACK_NAME = re.compile(r"snack|toast|notify|show_msg|show_message|alert|_toast", re.I)
LOG_NAME = re.compile(r"(^|_)(log|logs|logger|logging)(_|$)|^(print|debug|info|warning|warn|error|exception|critical)$", re.I)
PASS_CALLS = {"format", "str", "join", "repr", "int", "strip", "lower", "upper", "replace", "len", "f"}

# 画面(タブ)名: ファイル単位。gui.py は行範囲(ledger 見出しの範囲)で上書きする
SCREEN_BY_FILE = {
    "unified_download_tab.py": "ダウンロード(統合)",
    "unified_download.py": "ダウンロード(統合)",
    "ranking_tab.py": "ランキング",
    "library_tab.py": "ライブラリ",
    "recommend_tab.py": "おすすめ",
    "discovery_tab.py": "学習",
    "discovery_engine.py": "学習", "discovery_ranking.py": "学習", "discovery_population.py": "学習",
    "discovery_description.py": "学習", "discovery_display_state.py": "学習", "discovery_outcome.py": "学習",
    "discovery_size_coordinator.py": "学習", "discovery_pref_viewport.py": "学習",
    "artbook_viewer.py": "画集ビューア",
    "filter_panel.py": "共通フィルター",
    "main.py": "起動・ライフサイクル",
    "tray.py": "起動・ライフサイクル",
    "registry_helper.py": "起動・ライフサイクル",
    "scheduler.py": "定期処理",
    "server.py": "拡張機能連携サーバー",
    "core.py": "(エンジン)core",
    "database.py": "(エンジン)database",
    "pixiv_client.py": "(エンジン)pixiv_client",
    "nana7mi_vault.py": "Shaft連携",
    "epub_builder.py": "(エンジン)小説保存",
    "style_infer.py": "(エンジン)画風推定",
    "similar_works.py": "(エンジン)類似作品",
}
GUI_SCREENS = [  # (開始行, 終了行, 画面)  gui.py の台帳見出し範囲
    (1, 1560, "共通(定数・部品・設定ダイアログ前)"),
    (1561, 2430, "設定ダイアログ"),
    (2431, 2870, "ヘッダー(閲覧履歴・テーマ・保存先)"),
    (2871, 4090, "キュー"),
    (4091, 4443, "失敗リスト"),
    (4444, 4810, "直近のバックアップ"),
    (4811, 5024, "共通(タブ間)"),
    (5025, 5860, "重複整理"),
    (5861, 99999, "起動・タブ切替・キーボード"),
]
# 非 UI だが列挙する公開 API ファイル
# 公開関数/クラス/メソッドを列挙するファイル。database.py は別途 db_method として列挙する。
# 画面(UI)モジュールも、関数名だけで挙動を表すもの(work_actually_saved など)を取りこぼさないよう含める
API_FILES = {"core.py", "pixiv_client.py", "scheduler.py", "epub_builder.py", "unified_download.py",
             "rate_coordinator.py", "tray.py", "registry_helper.py", "server.py", "library_tab.py",
             "ranking_tab.py", "recommend_tab.py", "discovery_tab.py", "unified_download_tab.py",
             "artbook_viewer.py", "filter_panel.py", "discovery_engine.py", "discovery_ranking.py",
             "discovery_population.py", "discovery_description.py", "discovery_display_state.py",
             "discovery_outcome.py", "discovery_size_coordinator.py", "discovery_pref_viewport.py",
             "grid_card_size.py", "grid_card_size_ui.py", "shared_image_cache.py", "similar_works.py",
             "style_infer.py", "zip_locks.py", "ui_dialog.py", "ui_dispatch.py", "main.py", "gui.py"}
# 非公開メソッド(_ 始まり)も列挙するファイル(クラスの内部判定が利用者の挙動を決めるもの)
PRIVATE_FILES = {"scheduler.py", "server.py"}
NET_FILES = {"core.py", "pixiv_client.py"}


def screen_of(fname: str, line: int) -> str:
    if fname == "gui.py":
        for a, b, s in GUI_SCREENS:
            if a <= line <= b:
                return s
    return SCREEN_BY_FILE.get(fname, fname)


def jtext(node) -> list[str]:
    """ノードから文字列リテラル候補を取り出す(f 文字列は {} 置換)。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        out = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                out.append(str(v.value))
            else:
                out.append("{}")
        return ["".join(out)]
    if isinstance(node, ast.IfExp):
        return jtext(node.body) + jtext(node.orelse)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        a, b = jtext(node.left), jtext(node.right)
        if a and b:
            return [a[0] + b[0]]
        return a or b
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        return jtext(node.func.value)
    return []


def good_text(s: str) -> bool:
    s2 = s.strip()
    return bool(s2) and bool(HAS_TEXT.search(s2))


def fn_name(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def callee_names(node) -> list[str]:
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            nm = fn_name(n.func)
            if nm:
                out.append(nm)
    return out


def handler_names(node) -> list[str]:
    """条件式(X if c else None)・or/and で結んだハンドラは、各枝の名前を別々に返す。"""
    if isinstance(node, ast.IfExp):
        return handler_names(node.body) + handler_names(node.orelse)
    if isinstance(node, ast.BoolOp):
        out = []
        for v in node.values:
            out += handler_names(v)
        return out
    if isinstance(node, ast.Constant):      # None / False など
        return []
    hn = handler_name(node)
    return [hn] if hn else []


def handler_name(node) -> str:
    if isinstance(node, (ast.Name, ast.Attribute)):
        return fn_name(node)
    if isinstance(node, ast.Lambda):
        c = []
        for nm in callee_names(node.body):
            if nm not in c:
                c.append(nm)
        return "lambda:" + ",".join(c[:4])
    if isinstance(node, ast.Call):
        nm = fn_name(node.func)
        if nm == "partial" and node.args:
            return "partial:" + handler_name(node.args[0])
        inner = [handler_name(a) for a in node.args[:1] if isinstance(a, (ast.Name, ast.Attribute))]
        return nm + "()" + (":" + inner[0] if inner else "")
    return ""


class Collector:
    def __init__(self, fname: str, source: str, tree: ast.AST):
        self.fname = fname
        self.source = source
        self.tree = tree
        self.items: list[dict] = []
        self.consumed: set[int] = set()   # 他の種別が取り込み済みの Constant/JoinedStr ノード id
        self.captured_str: set[int] = set()
        self.parents: dict[int, ast.AST] = {}
        for p in ast.walk(tree):
            for c in ast.iter_child_nodes(p):
                self.parents[id(c)] = p
        self.consts: dict[str, str] = {}
        self.dict_consts: dict[str, list[str]] = {}
        seen_vals: dict[str, set] = {}
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                nm = n.targets[0].id
                if isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
                    seen_vals.setdefault(nm, set()).add(n.value.value)
                elif isinstance(n.value, ast.Dict):
                    vals = [v.value for v in n.value.values if isinstance(v, ast.Constant) and isinstance(v.value, str)]
                    if vals:
                        self.dict_consts[nm] = vals
        for nm, vs in seen_vals.items():
            if len(vs) == 1:       # 同名で値が複数あるものは曖昧なので使わない
                self.consts[nm] = next(iter(vs))
        self.kbd_funcs: set[int] = set()
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                seg = ast.get_source_segment(source, n) or ""
                if re.search(r"getattr\(\s*e\s*,\s*[\"']key[\"']|\be\.key\b|\bev\.key\b", seg):
                    self.kbd_funcs.add(id(n))
        self.docstrings: set[int] = set()
        for n in ast.walk(tree):
            if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.body:
                b0 = n.body[0]
                if isinstance(b0, ast.Expr) and isinstance(b0.value, ast.Constant) and isinstance(b0.value.value, str):
                    self.docstrings.add(id(b0.value))

    def qualname(self, node) -> str:
        names = []
        p = self.parents.get(id(node))
        while p is not None:
            if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.append(p.name)
            p = self.parents.get(id(p))
        return ".".join(reversed(names)) or "<module>"

    def add(self, kind, name, node, **extra):
        line = getattr(node, "lineno", 0)
        d = {"kind": kind, "name": name, "file": self.fname, "line": line,
             "qualname": self.qualname(node), "screen": screen_of(self.fname, line)}
        d.update(extra)
        self.items.append(d)

    # ---- 個別ルール ----
    def run(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call):
                self.on_call(node)
            elif isinstance(node, ast.Assign):
                self.on_assign(node)
            elif isinstance(node, ast.Compare):
                self.on_compare(node)
        self.keymap_loops()
        self.label_consts()
        self.background_loops()
        self.text_literals()

    def ft_ctrl(self, node: ast.Call):
        f = node.func
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in ("ft", "flet"):
            return f.attr
        return None

    def on_call(self, node: ast.Call):
        ctrl = self.ft_ctrl(node)
        nm = fn_name(node.func)
        if ctrl:
            if ctrl in MENU_CTRL:
                self.menu(node, ctrl)
            else:
                self.ui_texts(node, ctrl)
        for kw in node.keywords:
            if kw.arg and HANDLER_KW.match(kw.arg):
                for hn in handler_names(kw.value):
                    lab = self.control_label(node) if ctrl else ""
                    self.add("handler", hn, kw, event=kw.arg, control=ctrl or nm, label=lab)
        if SETTING_CALL.search(nm) and len(node.args) >= (2 if nm in SETTING_WRAPPERS else 1):
            self.setting(node, nm)
        if SNACK_NAME.search(nm) and not ctrl:
            for a in node.args[:3]:
                for s in jtext(a):
                    if good_text(s) and id(a) not in self.consumed:
                        self.consumed.add(id(a))
                        self.add("snack", s, a, via=nm)
        self.background(node, nm)

    def control_label(self, node: ast.Call) -> str:
        for a in node.args[:1]:
            for s in jtext(a):
                return s[:60]
        for kw in node.keywords:
            if kw.arg in ("label", "text", "tooltip", "title"):
                for s in jtext(kw.value):
                    return s[:60]
        return ""

    def ui_texts(self, node: ast.Call, ctrl: str):
        if ctrl in NON_TEXT_CTRL:
            return
        for i, a in enumerate(node.args):
            if ctrl == "DropdownOption" and i == 0:
                continue
            if id(a) in self.consumed:
                continue
            for s in jtext(a):
                if good_text(s):
                    self.consumed.add(id(a))
                    self.add("ui_text", s, a, control=ctrl, arg="arg%d" % i)
        for kw in node.keywords:
            if not kw.arg:
                continue
            ok = kw.arg in UI_TEXT_KW or (kw.arg == "value" and ctrl in TEXT_VALUE_CONTROLS)
            if not ok:
                continue
            for s in jtext(kw.value):
                if good_text(s):
                    self.consumed.add(id(kw.value))
                    self.add("ui_text", s, kw.value, control=ctrl, arg=kw.arg)

    def menu(self, node: ast.Call, ctrl: str):
        texts = []
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and sub is not node and self.ft_ctrl(sub) == "Text":
                for a in sub.args[:1]:
                    texts += [(s, a) for s in jtext(a)]
                for kw in sub.keywords:
                    if kw.arg == "value":
                        texts += [(s, kw.value) for s in jtext(kw.value)]
        for a in node.args[:1]:
            texts += [(s, a) for s in jtext(a)]
        for kw in node.keywords:
            if kw.arg in ("text", "content", "label") and not isinstance(kw.value, ast.Call):
                texts += [(s, kw.value) for s in jtext(kw.value)]
        if ctrl == "ContextMenu":
            # 項目は secondary_items 等で別途 PopupMenuItem として列挙される
            self.add("menu_item", "<ContextMenu>", node, control=ctrl,
                     triggers=[kw.arg for kw in node.keywords if kw.arg and "items" in kw.arg])
            return
        seen = set()
        for s, a in texts:
            if good_text(s) and s not in seen:
                seen.add(s)
                self.consumed.add(id(a))
                self.add("menu_item", s, node, control=ctrl)
        # 内側 Text 呼び出しが後で ui_text に重複して出ないよう、取り込み済み印を付ける
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and self.ft_ctrl(sub) == "Text":
                for a in sub.args[:1]:
                    self.consumed.add(id(a))

    def setting(self, node: ast.Call, nm: str):
        a = node.args[1] if nm in SETTING_WRAPPERS else node.args[0]
        key = None
        if isinstance(a, ast.Constant) and isinstance(a.value, str):
            key = a.value
        elif isinstance(a, ast.Name):
            key = self.consts.get(a.id) or "{const:%s}" % a.id
        elif isinstance(a, ast.Attribute):
            key = "{attr:%s}" % a.attr
        elif isinstance(a, ast.JoinedStr):
            key = jtext(a)[0]
        elif isinstance(a, ast.Subscript) and isinstance(a.value, ast.Name) and a.value.id in self.dict_consts:
            op = "set" if nm.startswith(("set", "update", "save")) else ("del" if "del" in nm else "get")
            for k in self.dict_consts[a.value.id]:
                self.add("setting_key", k, node, op=op, via="dict:" + a.value.id)
            return
        if key:
            op = "set" if nm.startswith(("set", "update", "save")) else ("del" if "del" in nm else "get")
            self.add("setting_key", key, node, op=op)

    def on_assign(self, node: ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Attribute):
                if HANDLER_KW.match(t.attr):
                    for hn in handler_names(node.value):
                        self.add("handler", hn, node, event=t.attr, control="(assign)", label=fn_name(t.value))
                if t.attr in ("tooltip", "label", "text", "hint_text", "title", "helper_text", "value", "error_text"):
                    for s in jtext(node.value):
                        if good_text(s) and CJK.search(s) and id(node.value) not in self.consumed:
                            self.consumed.add(id(node.value))
                            self.add("ui_text", s, node.value, control="(assign)", arg=t.attr)
            if isinstance(t, ast.Name) and re.search(r"(SETTING|CONFIG)_?(KEY|NAME)S?$|_SETTING_KEY$", t.id):
                for s in jtext(node.value):
                    self.add("setting_key", s, node, op="define", const=t.id)

    def on_compare(self, node: ast.Compare):
        # e.key == "Escape" / e.key in ("a","b")
        parts = [node.left] + list(node.comparators)
        fn0 = self.enclosing_func(node)

        def _is_ev_key(p):
            if (isinstance(p, ast.Attribute) and p.attr == "key"
                    and isinstance(p.value, ast.Name) and p.value.id in ("e", "ev", "evt", "event", "ke")):
                return True
            return bool(isinstance(p, ast.Name) and p.id.startswith(("key", "_key"))
                        and fn0 is not None and id(fn0) in self.kbd_funcs)
        has_key = any(_is_ev_key(p) for p in parts)
        if has_key:
            for p in parts:
                if _is_ev_key(p):
                    continue
                strs = []
                if isinstance(p, (ast.Tuple, ast.List, ast.Set)):
                    for e in p.elts:
                        strs += jtext(e)
                else:
                    strs += jtext(p)
                for s in strs:
                    if s and len(s) <= 24:
                        self.consumed.add(id(p))
                        mods = []
                        func = self.enclosing_func(node)
                        if func is not None:
                            src = ast.get_source_segment(self.source, func) or ""
                            for m in ("ctrl", "shift", "alt", "meta"):
                                if re.search(r"[\.\"']%s\b" % m, src):
                                    mods.append(m)
                        self.add("shortcut", s, node, modifiers_seen_in_handler=mods)
        # self.path == '/ping' 等(server.py)
        if self.fname == "server.py":
            for p in parts:
                if isinstance(p, ast.Attribute) and p.attr == "path":
                    for q in parts:
                        for s in jtext(q):
                            if s.startswith("/"):
                                self.add("route", s, node)
        # parsed.path.startswith は on_call で拾えないため別途 call 側
        # (server.py の startswith)

    def enclosing_func(self, node):
        p = self.parents.get(id(node))
        while p is not None:
            if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return p
            p = self.parents.get(id(p))
        return None

    def background(self, node: ast.Call, nm: str):
        if nm in ("Thread", "Timer", "Process"):
            target = None
            for kw in node.keywords:
                if kw.arg == "target":
                    target = handler_name(kw.value)
            if nm == "Timer" and len(node.args) >= 2:
                target = handler_name(node.args[1])
            if target is None and node.args:
                target = handler_name(node.args[0]) if nm == "Timer" else None
            self.add("background", "%s->%s" % (nm, target or "?"), node, how=nm)
        elif nm in ("ThreadPoolExecutor", "ProcessPoolExecutor", "create_task", "run_in_executor",
                    "run_thread", "run_task", "start_new_thread"):
            self.add("background", "%s(%s)" % (nm, ",".join(handler_name(a) for a in node.args[:2] if isinstance(a, (ast.Name, ast.Attribute)))),
                     node, how=nm)
        elif nm in ("startswith",) and self.fname == "server.py" and node.args:
            for s in jtext(node.args[0]):
                if s.startswith("/"):
                    self.add("route", s + "*", node)

    def background_loops(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.While):
                src = ast.get_source_segment(self.source, node) or ""
                if re.search(r"time\.sleep|\.wait\(|asyncio\.sleep", src):
                    fn = self.enclosing_func(node)
                    self.add("background", "loop@%s" % (fn.name if fn else "<module>"), node, how="while+sleep")

    def keymap_loops(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.For):
                seg = ast.get_source_segment(self.source, node) or ""
                if re.search(r"KEYMAP\w*\[|KEY_MAP\w*\[", seg):
                    elts = node.iter.elts if isinstance(node.iter, (ast.Tuple, ast.List)) else []
                    for e in elts:
                        # (("arrow left", "a", "1"), "skip") の先頭(キー名の組)だけを取り、動作名は取らない
                        scope = e.elts[0] if isinstance(e, (ast.Tuple, ast.List)) and e.elts and isinstance(e.elts[0], (ast.Tuple, ast.List)) else e
                        for sub in ast.walk(scope):
                            if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and len(sub.value) <= 24:
                                self.add("shortcut", sub.value, sub, via="keymap")

    def label_consts(self):
        """module/class 直下の代入内の日本語文字列(Tuple/List/Dict 内)。ui_text 等で未取り込みのものだけ。"""
        body_nodes = []
        for n in ast.walk(self.tree):
            if isinstance(n, (ast.Module, ast.ClassDef)):
                for s in n.body:
                    if isinstance(s, (ast.Assign, ast.AnnAssign)):
                        body_nodes.append(s)
        for s in body_nodes:
            val = s.value
            if val is None or isinstance(val, ast.Constant):
                continue
            for sub in ast.walk(val):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and CJK.search(sub.value):
                    if id(sub) in self.consumed or id(sub) in self.docstrings:
                        continue
                    self.consumed.add(id(sub))
                    tgt = ""
                    t0 = s.targets[0] if isinstance(s, ast.Assign) else s.target
                    if isinstance(t0, ast.Name):
                        tgt = t0.id
                    self.add("label_const", sub.value, sub, const=tgt)

    def text_literals(self):
        """他の規則で取り込めなかった日本語文字列(ログ・例外文言は除く)。変数へ代入してから表示する文言など。"""
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if isinstance(self.parents.get(id(node)), ast.JoinedStr):
                    continue
                txt = node.value
            elif isinstance(node, ast.JoinedStr):
                txt = jtext(node)[0]
            else:
                continue
            if not CJK.search(txt) or id(node) in self.consumed or id(node) in self.docstrings:
                continue
            if self.classify_residual(node) != "other":
                continue
            if "\n" in txt and re.search(r"\b(SELECT|CREATE|INSERT|UPDATE|ALTER)\b", txt):
                continue  # SQL 文
            self.consumed.add(id(node))
            self.add("text_literal", txt, node)

    # ---- 残差 ----
    def residual(self):
        res = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and CJK.search(node.value):
                if id(node) in self.consumed or id(node) in self.docstrings or isinstance(self.parents.get(id(node)), ast.JoinedStr):
                    continue
                cat = self.classify_residual(node)
                res.append((cat, node.lineno, node.value[:50]))
            elif isinstance(node, ast.JoinedStr):
                s = "".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
                if CJK.search(s) and id(node) not in self.consumed:
                    parent = self.parents.get(id(node))
                    cat = self.classify_residual(node)
                    res.append((cat, node.lineno, s[:50]))
        return res

    def classify_residual(self, node) -> str:
        """最も内側の「決め手になる呼び出し」で分類する(format/str などは素通し)。文・def・lambda を越えない。"""
        p = self.parents.get(id(node))
        depth = 0
        while p is not None and depth < 8:
            if isinstance(p, (ast.stmt, ast.Lambda)) and not isinstance(p, ast.Raise):
                return "other"
            if isinstance(p, ast.Raise):
                return "error_msg"
            if isinstance(p, ast.Call):
                nm = fn_name(p.func)
                if nm not in PASS_CALLS:
                    if LOG_NAME.search(nm):
                        return "log"
                    if nm.endswith(("Error", "Exception")):
                        return "error_msg"
                    return "other"
            p = self.parents.get(id(p))
            depth += 1
        return "other"


def public_defs(fname: str, tree: ast.Module, kind: str, closures: bool = False):
    """closures=True: 関数・メソッドの本体の中で定義された内部関数(クロージャ)も `外側.内側` の名前で列挙する。
    gui.py の main_window や discovery_tab.py の build_discovery_tab のように、画面のロジックの大半が
    1 つの外側関数の中の内部関数になっているため、これを数えないと inventory がその大半を見落とす。
    内部関数は先頭が _ でも列挙する(利用者の操作ごとのハンドラが _ 始まりのため)。"""
    out = []
    private = fname in PRIVATE_FILES

    def nested(body, prefix):
        for n in body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not (n.name.startswith("__") and n.name.endswith("__")):
                    out.append((prefix + n.name, n.lineno, "closure"))
                nested(n.body, prefix + n.name + ".")
            elif isinstance(n, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try)):
                for fld in ("body", "orelse", "finalbody"):
                    nested(getattr(n, fld, []) or [], prefix)
                for h in getattr(n, "handlers", []) or []:
                    nested(h.body, prefix)

    def rec(body, prefix, top):
        for n in body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not n.name.startswith("_") or (private and not n.name.startswith("__")):
                    out.append((prefix + n.name, n.lineno, "function" if not prefix else "method"))
                if closures:
                    nested(n.body, prefix + n.name + ".")
            elif isinstance(n, ast.ClassDef):
                if not n.name.startswith("_"):
                    out.append((prefix + n.name, n.lineno, "class"))
                    rec(n.body, prefix + n.name + ".", False)
    rec(tree.body, "", True)
    return out


def endpoints(fname: str, tree: ast.AST):
    out = []
    for node in ast.walk(tree):
        strs = []
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            strs = [node.value]
        elif isinstance(node, ast.JoinedStr):
            strs = jtext(node)
        for s in strs:
            if re.search(r"pixiv\.net|pximg\.net|/ajax/|/v1/|/touch/|api\.php|oauth", s) and len(s) < 200 and "\n" not in s:
                out.append((s, node.lineno))
    return out


def db_tables(source: str):
    out = []
    for m in re.finditer(r"CREATE TABLE IF NOT EXISTS\s+(\w+)|CREATE TABLE\s+(?!IF)(\w+)", source):
        name = m.group(1) or m.group(2)
        out.append((name, source.count("\n", 0, m.start()) + 1, "table"))
    for m in re.finditer(r"ALTER TABLE\s+(\w+)\s+ADD COLUMN\s+(\w+)", source, re.I):
        out.append(("%s.%s" % (m.group(1), m.group(2)), source.count("\n", 0, m.start()) + 1, "column"))
    return out


def add_refs(items, texts):
    """api_public / db_method の名前が、定義行以外のどこで語として現れるか(近似の静的参照数)。0 件=呼び出し元なし。"""
    for it in items:
        if it["kind"] not in ("api_public", "db_method"):
            continue
        bn = it["name"].split(".")[-1]
        pat = re.compile(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(bn))
        refs = {}
        for f, t in texts.items():
            n = 0
            for i, line in enumerate(t.splitlines(), 1):
                if pat.search(line) and not (f == it["file"] and i == it["line"]):
                    n += 1
            if n:
                refs[f] = n
        it["refs"] = refs
        it["ref_count"] = sum(refs.values())


def make_ids(items):
    counter: dict[tuple, int] = {}
    for it in items:
        k = (it["file"], it["kind"], it["qualname"], it["name"])
        o = counter.get(k, 0)
        counter[k] = o + 1
        h = hashlib.sha1(("|".join(k) + "|%d" % o).encode("utf-8")).hexdigest()[:10]
        it["id"] = "inv-" + h
        it["ordinal"] = o


def source_files(src: Path):
    try:
        r = subprocess.run(["git", "-C", str(src), "ls-files", "*.py"], capture_output=True, text=True, timeout=60)
        files = [l.strip() for l in r.stdout.splitlines() if l.strip() and "/" not in l and "\\" not in l]
        if files:
            return sorted(files)
    except Exception:
        pass
    return sorted(p.name for p in src.glob("*.py"))


def src_commit(src: Path):
    try:
        h = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(src), "status", "--porcelain", "--", "*.py"], capture_output=True, text=True, timeout=60).stdout
        d = sorted(l[3:].strip() for l in dirty.splitlines() if "/" not in l[3:])
        return h, d
    except Exception:
        return "", []


def build(src: Path):
    items = []
    residual_by_file = {}
    files = source_files(src)
    scanned = []
    texts: dict[str, str] = {}
    for fname in files:
        p = src / fname
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text, filename=fname)
        except SyntaxError as e:
            print("SKIP (syntax)", fname, e, file=sys.stderr)
            continue
        scanned.append(fname)
        texts[fname] = text
        c = Collector(fname, text, tree)
        if fname == "database.py":
            c_db = True
        c.run()
        items.extend(c.items)
        if fname == "database.py":
            for nm, ln, sub in db_tables(text):
                items.append({"kind": "db_table", "name": nm, "file": fname, "line": ln, "qualname": "<schema>",
                              "screen": SCREEN_BY_FILE[fname], "sub": sub})
            for nm, ln, sub in public_defs(fname, tree, "db_method"):
                items.append({"kind": "db_method", "name": nm, "file": fname, "line": ln, "qualname": "<module>",
                              "screen": SCREEN_BY_FILE[fname], "sub": sub})
        if fname in API_FILES:
            for nm, ln, sub in public_defs(fname, tree, "api_public", closures=True):
                items.append({"kind": "api_public", "name": nm, "file": fname, "line": ln, "qualname": "<module>",
                              "screen": screen_of(fname, ln), "sub": sub})
        if fname in NET_FILES:
            seen = set()
            for s, ln in endpoints(fname, tree):
                if s in seen:
                    continue
                seen.add(s)
                items.append({"kind": "net_endpoint", "name": s, "file": fname, "line": ln, "qualname": "<module>",
                              "screen": screen_of(fname, ln)})
        res = c.residual()
        residual_by_file[fname] = res
    # setting_key はキーごとに 1 件へ集約
    merged = []
    keyed: dict[str, dict] = {}
    for it in items:
        if it["kind"] == "setting_key":
            k = it["name"]
            if k not in keyed:
                it = dict(it)
                it["uses"] = []
                it["screen"] = "(設定キー)"
                keyed[k] = it
                merged.append(it)
            keyed[k]["uses"].append("%s:%d:%s" % (it["file"], it["line"], it.get("op", "")))
            # 最小行を代表に
            f, l = keyed[k]["file"], keyed[k]["line"]
        else:
            merged.append(it)
    for it in keyed.values():
        it["uses"] = sorted(set(it["uses"]))
        it["use_count"] = len(it["uses"])
        # 代表位置: 最初の use
        f, l, _ = it["uses"][0].split(":")[0], it["uses"][0].split(":")[1], 0
        it["file"], it["line"] = f, int(l)
        it["qualname"] = "<settings>"
    items = merged
    add_refs(items, texts)
    items.sort(key=lambda d: (d["file"], d["line"], d["kind"], d["name"]))
    make_ids(items)
    items.sort(key=lambda d: (d["file"], d["line"], d["kind"], d["id"]))
    counts: dict[str, int] = {}
    for it in items:
        counts[it["kind"]] = counts.get(it["kind"], 0) + 1
    audit = {}
    for fname, res in residual_by_file.items():
        cat: dict[str, int] = {}
        for c_, _, _ in res:
            cat[c_] = cat.get(c_, 0) + 1
        audit[fname] = cat
    other_samples = []
    for fname, res in residual_by_file.items():
        for c_, ln, s in res:
            if c_ == "other":
                other_samples.append("%s:%d %s" % (fname, ln, s))
    commit, dirty = src_commit(src)
    meta = {
        "source": "S:\\Projects\\PixivVault (root *.py only)",
        "source_commit": commit,
        "source_dirty_py": dirty,
        "files_scanned": scanned,
        "counts": counts,
        "total": len(items),
        "residual_cjk_literals_not_captured": audit,
        "residual_other_samples_first200": sorted(other_samples)[:200],
    }
    return {"meta": meta, "items": items}


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=DEFAULT_SRC)
    here = Path(__file__).resolve().parent.parent
    ap.add_argument("--out", default=str(here / "docs" / "inventory.json"))
    a = ap.parse_args(argv)
    data = build(Path(a.src))
    Path(a.out).write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                           encoding="utf-8", newline="\n")
    print("inventory:", data["meta"]["total"], json.dumps(data["meta"]["counts"], ensure_ascii=False))
    tot = {}
    for f, cat in data["meta"]["residual_cjk_literals_not_captured"].items():
        for k, v in cat.items():
            tot[k] = tot.get(k, 0) + v
    print("residual:", json.dumps(tot, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
