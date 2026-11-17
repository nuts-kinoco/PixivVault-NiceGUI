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


def _setup_repo(tmp_path, monkeypatch, mapping, inv_items):
    docs = tmp_path / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "inventory.json").write_text(json.dumps({"meta": {"total": len(inv_items)}, "items": inv_items}), encoding="utf-8")
    (docs / "ledger_map.json").write_text(json.dumps(mapping), encoding="utf-8")
    led = docs / "FEATURE_LEDGER_2026-11-17.md"
    led.write_text("## A. t\n| FL-A01 | x | gui.py:1-5 | | 有 | |\n", encoding="utf-8")
    monkeypatch.setattr(cic, "HERE", tmp_path / "tools")
    monkeypatch.setattr(blm, "LEDGER", led)
    monkeypatch.setattr(blm, "ADDENDA", docs / "none.md")


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
