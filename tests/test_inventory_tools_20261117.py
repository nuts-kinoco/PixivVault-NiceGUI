"""tools/build_inventory.py・build_ledger_map.py・check_inventory_coverage.py の単体テスト(小さな合成ソース)。

元リポ・実 DB・通信には触れない。合成ソースは tmp_path に書く。
"""
import json
import sys
import textwrap
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import build_inventory as bi  # noqa: E402
import build_ledger_map as blm  # noqa: E402
import check_inventory_coverage as cic  # noqa: E402

UI_SRC = textwrap.dedent('''
    import threading
    import flet as ft

    SORTS = [("name", "作者名順"), ("count", "保存済みの多い順")]

    def build(page, db):
        def on_save(e):
            db.set_setting("save_path", e.control.value)
        def on_key(e):
            key = (getattr(e, "key", "") or "").lower()
            if key == "escape":
                pass
            elif key in ("arrowleft", "a"):
                pass
        page.on_keyboard_event = on_key
        threading.Thread(target=on_save, daemon=True).start()
        menu = ft.PopupMenuItem(content=ft.Text("削除する"), on_click=on_save)
        btn = ft.ElevatedButton("保存", tooltip="いま保存する", on_click=on_save)
        sw = ft.Switch(label="起動時に確認", on_change=lambda e: db.get_setting("check_on_startup", "0"))
        msg = f"{3}件を保存しました"
        return [btn, sw, menu, ft.Text(msg), ft.Dropdown(options=[ft.DropdownOption("k", "並び替え")])]
''')

ENGINE_SRC = textwrap.dedent('''
    def verify_work(path):
        """品質検査"""
        if path == "":
            return False, "0バイトファイル検出"
        return True, None

    def unused_helper():
        return 1

    class Client:
        def fetch(self):
            return "https://www.pixiv.net/ajax/user/self"
''')

DB_SRC = textwrap.dedent('''
    class Database:
        def _create_table(self):
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS works (id TEXT)
            """)
            self.conn.execute("ALTER TABLE works ADD COLUMN missing_streak INTEGER")

        def get_work(self, wid):
            return wid
''')


@pytest.fixture()
def src(tmp_path):
    d = tmp_path / "orig"
    d.mkdir()
    (d / "gui.py").write_text(UI_SRC, encoding="utf-8")
    (d / "core.py").write_text(ENGINE_SRC + "\nverify_work('x')\n", encoding="utf-8")
    (d / "database.py").write_text(DB_SRC, encoding="utf-8")
    return d


def _by(items, kind):
    return [i for i in items if i["kind"] == kind]


def test_enumerates_ui_text_handlers_settings_shortcuts_menu_background(src):
    data = bi.build(src)
    items = data["items"]
    texts = {i["name"] for i in _by(items, "ui_text")}
    assert {"保存", "いま保存する", "起動時に確認", "削除する"} <= texts or {"保存", "いま保存する", "起動時に確認"} <= texts
    assert "{}件を保存しました" in {i["name"] for i in items if i["kind"] in ("text_literal", "ui_text")}
    assert {"作者名順", "保存済みの多い順"} <= {i["name"] for i in _by(items, "label_const")}
    assert "並び替え" in texts
    handlers = {i["name"] for i in _by(items, "handler")}
    assert "on_save" in handlers
    assert any(h.startswith("lambda:") for h in handlers)
    assert any(h == "on_key" and i["event"] == "on_keyboard_event" for i in _by(items, "handler") for h in [i["name"]])
    keys = {i["name"] for i in _by(items, "shortcut")}
    assert {"escape", "arrowleft", "a"} <= keys
    settings = {i["name"]: i for i in _by(items, "setting_key")}
    assert {"save_path", "check_on_startup"} <= set(settings)
    assert settings["save_path"]["use_count"] == 1
    menu = {i["name"] for i in _by(items, "menu_item")}
    assert "削除する" in menu
    assert any(i["name"].startswith("Thread->") for i in _by(items, "background"))


def test_menu_text_is_not_double_counted(src):
    items = bi.build(src)["items"]
    assert [i["name"] for i in items if i["name"] == "削除する"] == ["削除する"]


def test_engine_db_enumeration_and_refs(src):
    items = bi.build(src)["items"]
    tables = {i["name"] for i in _by(items, "db_table")}
    assert {"works", "works.missing_streak"} <= tables
    api = {i["name"]: i for i in _by(items, "api_public")}
    assert {"verify_work", "unused_helper", "Client", "Client.fetch"} <= set(api)
    assert api["verify_work"]["ref_count"] >= 1
    assert api["unused_helper"]["ref_count"] == 0
    assert any("pixiv.net/ajax/user/self" in i["name"] for i in _by(items, "net_endpoint"))
    methods = {i["name"] for i in _by(items, "db_method")}
    assert "Database.get_work" in methods


def test_ids_are_stable_and_unique_and_deterministic(src):
    a = bi.build(src)
    b = bi.build(src)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    ids = [i["id"] for i in a["items"]]
    assert len(ids) == len(set(ids))
    # 行番号が変わっても ID は変わらない(先頭に空行を足す)
    (src / "gui.py").write_text("\n\n" + UI_SRC, encoding="utf-8")
    c = bi.build(src)
    assert {i["id"] for i in a["items"] if i["file"] == "gui.py"} == {i["id"] for i in c["items"] if i["file"] == "gui.py"}


def test_residual_audit_counts_log_separately(tmp_path):
    d = tmp_path / "o"
    d.mkdir()
    (d / "m.py").write_text('import logging\nlogging.info("ログです")\nx = "ただの文字列です"\n', encoding="utf-8")
    data = bi.build(d)
    res = data["meta"]["residual_cjk_literals_not_captured"]["m.py"]
    assert res.get("log") == 1
    assert any(i["kind"] == "text_literal" for i in data["items"])  # log 以外は text_literal として列挙


LEDGER = textwrap.dedent('''
    # t
    ## A. test
    | ID | 元 | 元の場所 | 移植版 | 状態 | 備考 |
    |---|---|---|---|---|---|
    | FL-A01 | 保存 | gui.py:3-30 | x.py:1 | 有 | |
    | FL-A02 | 詳細 | gui.py:5-6, 9, core.py:1-3 `verify_work` | 無 | 無 | |
    ## R. 件数
    | FL-A99 | 除外 | gui.py:1 | | 有 | |
    ## T. 設定キー
    | 設定キー | 台帳行 | 備考 |
    |---|---|---|
    | save_path | FL-A01 | |
''')


def test_ledger_parse_refs_and_sections(tmp_path):
    p = tmp_path / "ledger.md"
    p.write_text(LEDGER, encoding="utf-8")
    rows, sec_t = blm.parse_ledger(p)
    assert set(rows) == {"FL-A01", "FL-A02"}  # R 以降の行は数えない
    assert ("gui.py", 3, 30) in rows["FL-A01"]["refs"]
    assert ("gui.py", 9, 9) in rows["FL-A02"]["refs"] and ("core.py", 1, 3) in rows["FL-A02"]["refs"]
    assert sec_t == {"save_path": ["FL-A01"]}
    assert blm.expand_ids("FL-C05, C06, O02") == ["FL-C05", "FL-C06", "FL-O02"]


def test_auto_map_narrowest_range_and_wide_flag(src, tmp_path):
    led = tmp_path / "ledger.md"
    led.write_text(textwrap.dedent('''
        ## A. t
        | FL-A01 | 狭い | gui.py:1-12 | | 有 | |
        | FL-A02 | 広い | gui.py:1-400 | | 有 | |
    '''), encoding="utf-8")
    rows, sec_t = blm.parse_ledger(led)
    inv = bi.build(src)
    auto, _ = blm.build_auto(inv, rows, sec_t, "")
    by_line = {i["line"]: auto.get(i["id"]) for i in inv["items"] if i["file"] == "gui.py" and i["kind"] == "ui_text"}
    near = [v for ln, v in by_line.items() if ln <= 12 and v]
    far = [v for ln, v in by_line.items() if ln > 20 and v]
    assert all(v["fl"] == "FL-A01" and v["method"] == "range" for v in near)
    assert all(v["fl"] == "FL-A02" for v in far)


def _mp(**kw):
    base = {"manual": {}, "auto": {}, "rules": [], "table_rules": {}, "column_rules": {}, "na_unused": "n/a:未使用"}
    base.update(kw)
    return base


def test_resolve_priority_manual_over_auto_over_rule_over_weak():
    it = {"id": "inv-1", "kind": "ui_text", "file": "gui.py", "line": 10, "name": "x"}
    rule = {"file": "gui.py", "lines": [1, 50], "fl": "FL-R", "kinds": None}
    assert cic.resolve(it, _mp(manual={"inv-1": "FL-M"}, auto={"inv-1": {"fl": "FL-A", "method": "range"}}, rules=[rule])) == ("FL-M", "manual")
    assert cic.resolve(it, _mp(auto={"inv-1": {"fl": "FL-A", "method": "range"}}, rules=[rule])) == ("FL-A", "auto-range")
    assert cic.resolve(it, _mp(auto={"inv-1": {"fl": "FL-A", "method": "range-wide"}}, rules=[rule])) == ("FL-R", "rule")
    assert cic.resolve(it, _mp(auto={"inv-1": {"fl": "FL-A", "method": "range-wide"}})) == ("FL-A", "auto-range-wide")
    assert cic.resolve(it, _mp()) == (None, "unmapped")


def test_rule_narrowest_wins_and_kind_filter():
    it = {"id": "i", "kind": "snack", "file": "core.py", "line": 100, "name": "x"}
    rules = [{"file": "core.py", "lines": [1, 500], "fl": "FL-WIDE", "kinds": None},
             {"file": "core.py", "lines": [90, 110], "fl": "FL-NARROW", "kinds": ["snack"]},
             {"file": "core.py", "lines": [95, 105], "fl": "FL-OTHERKIND", "kinds": ["ui_text"]}]
    assert cic.resolve(it, _mp(rules=rules))[0] == "FL-NARROW"


def test_unused_and_table_rules():
    api = {"id": "a", "kind": "api_public", "file": "core.py", "line": 1, "name": "f", "ref_count": 0}
    assert cic.resolve(api, _mp())[1] == "unused"
    tbl = {"id": "t", "kind": "db_table", "file": "database.py", "line": 1, "name": "works.missing_streak"}
    assert cic.resolve(tbl, _mp(table_rules={"works": "FL-D00"}, column_rules={"works.missing_streak": "FL-Z06"})) == ("FL-Z06", "table-rule")
    tbl2 = {"id": "t2", "kind": "db_table", "file": "database.py", "line": 1, "name": "works.title"}
    assert cic.resolve(tbl2, _mp(table_rules={"works": "FL-D00"})) == ("FL-D00", "table-rule")


def test_valid_target_rules():
    ids = {"FL-A01", "FL-Z01"}
    assert cic.valid_target("FL-A01", ids)[0]
    assert cic.valid_target("FL-A01, FL-Z01", ids)[0]
    assert not cic.valid_target("FL-NOPE", ids)[0]
    assert not cic.valid_target("n/a:", ids)[0]       # 理由が空
    assert not cic.valid_target("n/a", ids)[0]
    assert cic.valid_target("n/a:内部実装", ids)[0]


def _setup_repo(tmp_path, monkeypatch, mapping, inv_items, ledger=None, addenda=None):
    docs = tmp_path / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "inventory.json").write_text(json.dumps({"meta": {"total": len(inv_items)}, "items": inv_items}), encoding="utf-8")
    (docs / "ledger_map.json").write_text(json.dumps(mapping), encoding="utf-8")
    led = docs / "FEATURE_LEDGER_2026-11-17.md"
    led.write_text(ledger or "## A. t\n| FL-A01 | x | gui.py:1-5 | | 有 | |\n", encoding="utf-8")
    add = docs / "none.md"
    if addenda is not None:
        add = docs / "ADDENDA.md"
        add.write_text(addenda, encoding="utf-8")
    monkeypatch.setattr(cic, "HERE", tmp_path / "tools")
    monkeypatch.setattr(blm, "LEDGER", led)
    monkeypatch.setattr(blm, "ADDENDA", add)


ITEMS = [{"id": "inv-a", "kind": "ui_text", "file": "gui.py", "line": 2, "name": "保存"},
         {"id": "inv-b", "kind": "ui_text", "file": "gui.py", "line": 99, "name": "未対応の文言"}]


def test_coverage_exit_codes(tmp_path, monkeypatch, capsys):
    mapping = _mp(auto={"inv-a": {"fl": "FL-A01", "method": "range"}})
    _setup_repo(tmp_path, monkeypatch, mapping, ITEMS)
    assert cic.main(["--list", "5"]) == 1            # 未対応 1 件
    out = capsys.readouterr().out
    assert "未対応: 1" in out and "inv-b" in out
    mapping["manual"] = {"inv-b": "n/a:テスト用の内部文言"}
    _setup_repo(tmp_path, monkeypatch, mapping, ITEMS)
    assert cic.main([]) == 0
    mapping["manual"] = {"inv-b": "n/a:"}            # 理由が空は整合エラー
    _setup_repo(tmp_path, monkeypatch, mapping, ITEMS)
    assert cic.main([]) == 2
    mapping["manual"] = {"inv-b": "FL-NOPE"}         # 存在しない FL は整合エラー
    _setup_repo(tmp_path, monkeypatch, mapping, ITEMS)
    assert cic.main([]) == 2
    mapping["manual"] = {"inv-zzz": "FL-A01"}        # inventory に無い項目は整合エラー
    _setup_repo(tmp_path, monkeypatch, mapping, ITEMS)
    assert cic.main([]) == 2


def test_strict_weak_counts_weak_as_unmapped(tmp_path, monkeypatch):
    mapping = _mp(auto={"inv-a": {"fl": "FL-A01", "method": "range"}, "inv-b": {"fl": "FL-A01", "method": "range-wide"}})
    _setup_repo(tmp_path, monkeypatch, mapping, ITEMS)
    assert cic.main([]) == 0
    assert cic.main(["--strict-weak"]) == 1


def test_rule_scope_name_re_and_wide_rule_are_graded():
    base = {"id": "i", "kind": "api_public", "file": "server.py", "line": 700, "name": "Handler._check_web_token"}
    rules = [{"file": "server.py", "lines": [1, 2000], "fl": "FL-N01", "kinds": ["api_public"], "scope": None},
             {"file": "server.py", "lines": [1, 2000], "fl": "FL-X02", "kinds": ["api_public"], "scope": None,
              "name_re": "_check_web_token"}]
    # 名前で決まる規則は、どの規則が採られるかでは優先する。ただし根拠の強さは幅で決めるので、広ければ tier C
    assert cic.resolve(base, _mp(rules=rules)) == ("FL-X02", "rule-wide")
    assert cic.resolve(base, _mp(rules=rules[:1])) == ("FL-N01", "rule-wide")  # 名前の無い広い手規則は弱い扱い
    narrow = [{"file": "server.py", "lines": [690, 710], "fl": "FL-N02", "kinds": None, "scope": None}]
    assert cic.resolve(base, _mp(rules=narrow)) == ("FL-N02", "rule")
    named_narrow = [{"file": "server.py", "lines": [600, 700], "fl": "FL-X03", "kinds": None, "scope": None, "name_re": "_check_web"}]
    assert cic.resolve(base, _mp(rules=named_narrow)) == ("FL-X03", "rule")    # 名前指定でも幅が狭ければ tier A
    na = [{"file": "server.py", "lines": [1, 2000], "fl": "n/a:内部", "kinds": None, "scope": None}]
    assert cic.resolve(base, _mp(rules=na)) == ("n/a:内部", "rule-wide")      # n/a も幅で判定する(広ければ tier C)
    na_narrow = [{"file": "server.py", "lines": [690, 710], "fl": "n/a:内部", "kinds": None, "scope": None}]
    assert cic.resolve(base, _mp(rules=na_narrow)) == ("n/a:内部", "rule")


def test_scope_label_is_capped_by_unit_max():
    base = {"id": "i", "kind": "api_public", "file": "server.py", "line": 700, "name": "Handler.x"}
    unit = [{"file": "server.py", "lines": [600, 600 + cic.UNIT_MAX - 1], "fl": "FL-N01", "kinds": None, "scope": "function"}]
    assert cic.resolve(base, _mp(rules=unit)) == ("FL-N01", "rule-function")   # 関数 1 つ分の大きさなら tier B
    file_unit = [{"file": "server.py", "lines": [1, cic.UNIT_MAX], "fl": "FL-N01", "kinds": None, "scope": "file"}]
    base2 = dict(base, line=10)
    assert cic.resolve(base2, _mp(rules=file_unit)) == ("FL-N01", "rule-file")
    huge = [{"file": "server.py", "lines": [1, 6000], "fl": "FL-N01", "kinds": None, "scope": "function"}]
    assert cic.resolve(base, _mp(rules=huge)) == ("FL-N01", "rule-wide")        # 巨大な関数・ファイルは scope ラベルだけでは tier B にしない
    huge_file = [{"file": "server.py", "lines": [1, 3000], "fl": "FL-N01", "kinds": None, "scope": "file"}]
    assert cic.resolve(base, _mp(rules=huge_file)) == ("FL-N01", "rule-wide")
    assert "rule-wide" in cic.WEAK_SRC


def test_dynamic_dispatch_methods_are_not_auto_unused():
    mk = lambda name: {"id": "i", "kind": "api_public", "file": "server.py", "line": 1, "name": name, "ref_count": 0}
    for dyn in ("Handler.do_OPTIONS", "Handler.do_DELETE", "Handler.log_message", "Worker.run"):
        assert cic.resolve(mk(dyn), _mp()) == (None, "unmapped"), dyn          # 呼び出し元なしにせず、個別に対応づける
    assert cic.resolve(mk("Handler.helper"), _mp())[1] == "unused"
    # 動的ディスパッチでも rules に範囲があれば、そちらで対応づく
    rule = [{"file": "server.py", "lines": [1, 40], "fl": "FL-N04", "kinds": None, "scope": None}]
    assert cic.resolve(mk("Handler.do_OPTIONS"), _mp(rules=rule)) == ("FL-N04", "rule")


def test_auto_also_targets_are_all_kept():
    it = {"id": "inv-1", "kind": "api_public", "file": "gui.py", "line": 10, "name": "f"}
    au = {"inv-1": {"fl": "FL-A", "also": ["FL-B", "FL-A"], "method": "name"}}
    assert cic.resolve(it, _mp(auto=au)) == ("FL-A, FL-B", "auto-name")
    assert cic.split_targets("FL-A, FL-B") == ["FL-A", "FL-B"] and cic.split_targets("n/a:理由") == []
    assert cic.valid_target("FL-A, FL-B", {"FL-A", "FL-B"})[0]


def test_log_residual_is_limited_to_real_loggers(tmp_path):
    d = tmp_path / "o2"
    d.mkdir()
    src = textwrap.dedent("""
        import logging
        import flet as ft
        def f(page):
            page.show_dialog("ダイアログの本文です")
            logging.info("ログです")
            catalog = open_catalog("カタログの文言です")
    """)
    (d / "m.py").write_text(src, encoding="utf-8")
    data = bi.build(d)
    names = {i["name"] for i in data["items"]}
    assert "ダイアログの本文です" in names and "カタログの文言です" in names     # 'log' を含む名前でも落とさない
    assert data["meta"]["residual_cjk_literals_not_captured"]["m.py"].get("log") == 1


ADDENDA_TEXT = textwrap.dedent('''
    # 追記
    ## 追記行
    | ID | 元の機能 | 元の場所 | 移植版の場所 | 状態 | 根拠 |
    |---|---|---|---|---|---|
    | FL-Y01 | 追記の機能 | gui.py:20-40 | 無 | 無 | 根拠に `save_path` などの識別子を書いても名前の対応には使わない |
    | FL-Z01 | もう一つ | core.py:1-3 `verify_work` | x.py:1 | 有 | |
    ## 付録A
    | 台帳 FL | 台帳の状態 | 未検出の文言 件数 | 例(元の場所) |
    |---|---|---|---|
    | FL-Z01 | 有 | 2 | 「例」core.py:1 |
''')


def test_parse_addenda_accepts_fl_y_and_ignores_appendix_table(tmp_path):
    p = tmp_path / "add.md"
    p.write_text(ADDENDA_TEXT, encoding="utf-8")
    rows = blm.parse_addenda(p)
    assert set(rows) == {"FL-Y01", "FL-Z01"}
    assert rows["FL-Z01"]["orig"] == "もう一つ" and rows["FL-Z01"]["state"] == "有"   # 付録の表で本文の行を上書きしない
    assert ("gui.py", 20, 40) in rows["FL-Y01"]["refs"]
    assert set(blm.parse_addenda_ids(p)) == {"FL-Y01", "FL-Z01"}
    assert blm.parse_addenda_ids(tmp_path / "none.md") == {}


def test_build_ledger_map_main_uses_addenda(src, tmp_path):
    led = tmp_path / "ledger.md"
    led.write_text("## A. t\n| FL-A01 | 狭い | gui.py:1-12 | | 有 | |\n", encoding="utf-8")
    add = tmp_path / "add.md"
    add.write_text(ADDENDA_TEXT, encoding="utf-8")
    inv = tmp_path / "inventory.json"
    inv.write_text(json.dumps(bi.build(src)), encoding="utf-8")
    mp = tmp_path / "map.json"
    assert blm.main(["--ledger", str(led), "--addenda", str(add), "--inventory", str(inv), "--map", str(mp),
                     "--port", str(tmp_path / "noport")]) == 0
    data = json.loads(mp.read_text(encoding="utf-8"))
    assert data["meta"]["addenda_rows"] == 2 and data["meta"]["ledger_rows_A_to_P"] == 1
    items = {i["id"]: i for i in json.loads(inv.read_text(encoding="utf-8"))["items"]}
    hits = {items[k]["line"]: v["fl"] for k, v in data["auto"].items() if items[k]["file"] == "gui.py" and v["method"] == "range"}
    assert any(fl == "FL-Y01" for fl in hits.values())                    # 追記の引用範囲が対応づけに参加する
    assert any(fl == "FL-Z01" and items[k]["file"] == "core.py" for k, v in data["auto"].items() for fl in [v["fl"]])
    # 追記の根拠欄の識別子(save_path)は、元の設定キーの名前対応を奪わない
    assert all(v["fl"] != "FL-Y01" or v["method"] != "name" for v in data["auto"].values())


def test_closure_functions_are_listed_as_api_public(src):
    names = {i["name"]: i for i in _by(bi.build(src)["items"], "api_public") if i["file"] == "gui.py"}
    assert {"build", "build.on_save", "build.on_key"} <= set(names)
    assert names["build.on_save"]["sub"] == "closure" and names["build"]["sub"] == "function"
    db = {i["name"] for i in _by(bi.build(src)["items"], "db_method")}
    assert "Database.get_work" in db and not any("." in n and n.split(".")[0] == "get_work" for n in db)


WRAPPER_SRC = textwrap.dedent('''
    import filter_panel
    _OPEN_KEY = "ranking_open_flag"
    _FILTERS_SETTING_KEY = "ranking_filters"

    def build(db):
        opened = filter_panel.load_json_setting(db, _OPEN_KEY, True)
        filter_panel.save_json_setting(db, _OPEN_KEY, not opened)
        filter_panel.load_json_setting(db, "literal_key")
        filter_panel.load_json_setting(db)
        return filter_panel.load_json_setting(db, _FILTERS_SETTING_KEY)
''')


def test_setting_keys_through_json_setting_wrappers(tmp_path):
    d = tmp_path / "w"
    d.mkdir()
    (d / "m.py").write_text(WRAPPER_SRC, encoding="utf-8")
    keys = {i["name"]: i for i in _by(bi.build(d)["items"], "setting_key")}
    assert {"ranking_open_flag", "literal_key", "ranking_filters"} <= set(keys)
    assert keys["ranking_open_flag"]["use_count"] == 2
    ops = {u.split(":")[2] for u in keys["ranking_open_flag"]["uses"]}
    assert ops == {"get", "set"}                                           # load=get / save=set


GAP_LEDGER = textwrap.dedent('''
    ## A. t
    | FL-A01 | 弱い行 | gui.py:1-5 | | 弱 | |
    | FL-A02 | 引用が空 | gui.py:900-905 | | 有 | |
    | FL-B02 | 有の行 | core.py:1-5 | | 有 | |
''')


def test_unresolved_gaps_and_strict_gaps(tmp_path, monkeypatch, capsys):
    items = ITEMS + [{"id": "inv-c", "kind": "ui_text", "file": "core.py", "line": 2, "name": "有の行の文言"}]
    mapping = _mp(auto={"inv-a": {"fl": "FL-A01", "method": "range"}, "inv-b": {"fl": "FL-B02", "method": "range"},
                        "inv-c": {"fl": "FL-B02", "method": "range"}},
                  port_text_missing=["inv-c"])
    _setup_repo(tmp_path, monkeypatch, mapping, items, ledger=GAP_LEDGER)
    assert cic.main([]) == 0
    out = capsys.readouterr().out
    assert "未解決の差: 2 件" in out and "対応先が無・弱の項目 1 件" in out and "有でも元の UI 文言が移植版に無い項目 1 件" in out
    assert "弱 1 行" in out and "port_text_missing: 1 件" in out
    assert cic.main(["--strict-gaps"]) == 1                                # 差が残っているので非 0
    out = capsys.readouterr().out
    assert "警告: 引用範囲に inventory 項目が 0 件" in out and "FL-A02" in out
    # 差が無ければ --strict-gaps でも 0
    clean = _mp(auto={"inv-a": {"fl": "FL-B02", "method": "range"}, "inv-b": {"fl": "FL-B02", "method": "range"}})
    _setup_repo(tmp_path, monkeypatch, clean, ITEMS, ledger=GAP_LEDGER)
    assert cic.main(["--strict-gaps"]) == 0


def test_strict_reverse_turns_empty_rows_into_errors(tmp_path, monkeypatch, capsys):
    mapping = _mp(auto={"inv-a": {"fl": "FL-A01", "method": "range"}, "inv-b": {"fl": "FL-B02", "method": "range"}})
    _setup_repo(tmp_path, monkeypatch, mapping, ITEMS, ledger=GAP_LEDGER)
    assert cic.main([]) == 0                       # 既定では警告だけ
    assert "FL-A02" in capsys.readouterr().out
    assert cic.main(["--strict-reverse"]) == 2     # 厳格ならエラー
    assert "逆方向: FL-A02" in capsys.readouterr().out


def test_multiple_fl_targets_are_counted_for_every_fl(tmp_path, monkeypatch, capsys):
    mapping = _mp(manual={"inv-a": "FL-A01, FL-B02"}, auto={"inv-b": {"fl": "FL-A01", "also": ["FL-B02"], "method": "name"}})
    _setup_repo(tmp_path, monkeypatch, mapping, ITEMS, ledger=GAP_LEDGER)
    assert cic.main([]) == 0
    out = capsys.readouterr().out
    assert "対応先に使われた FL の数: 2" in out                              # 先頭の FL だけに切り詰めない
    assert "無・弱の台帳行: 1 行" in out                                      # 後続の FL-A01(弱)も差として数える
    assert "未解決の差: 2 件" in out


def test_addenda_ids_are_valid_targets_and_weak_rows_use_addenda_state(tmp_path, monkeypatch, capsys):
    mapping = _mp(manual={"inv-a": "FL-Y01"}, auto={"inv-b": {"fl": "FL-Z01", "method": "range"}})
    _setup_repo(tmp_path, monkeypatch, mapping, ITEMS, ledger=GAP_LEDGER, addenda=ADDENDA_TEXT)
    assert cic.main([]) == 0
    out = capsys.readouterr().out
    assert "未解決の差: 1 件" in out and "無 1 行" in out                     # FL-Y01 は追記の状態が「無」
