"""Judging rules of the UI smoke (tools/ui_smoke_helpers.py). No browser, no server."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import ui_smoke_helpers as H  # noqa: E402

NAV = ["download", "queue", "failed", "history", "dedup", "learning", "ranking", "library",
       "recommend", "personal", "bookmarks"]


def snap(**kw):
    base = dict(testids=["a", "b-1"], text="見出し ボタン", scroll_width=1280, client_width=1280,
                console_errors=[], page_errors=[], theme_ok=True)
    base.update(kw)
    return base


GLOBAL = {"testids": ["a"], "texts": ["見出し"]}


# ----------------------------------------------------------------- expectations file
def test_shipped_expectations_load_and_cover_every_nav_tab_and_settings():
    data = H.load_expectations()
    order = H.screen_order(data)
    assert set(NAV) | {"startup", "settings", "scheduler", "metadata"} == set(order)
    for tab in NAV:
        ids = data["screens"][tab]["testids"]
        assert ids, tab  # a screen with no expected element would never fail
    assert data["screens"]["startup"]["kind"] == "startup"
    # header items the task names: auth label, DB banner, borrow toggle
    for needed in ("auth-status", "db-status-banner", "premium-borrow-toggle"):
        assert needed in data["global"]["testids"]


def test_every_screen_has_a_label_and_the_nav_tabs_are_in_global():
    data = H.load_expectations()
    for name, spec in data["screens"].items():
        assert spec["label"]
    for tab in NAV:
        assert f"tab-{tab}" in data["global"]["testids"]
    for key in ("scheduler", "metadata"):
        assert f"tab-{key}" not in data["global"]["testids"]
        assert data["screens"][key]["open"] == "automation-open"
        assert data["screens"][key]["steps"] == [f"automation-{key}"]
    assert "library-subview-bookmarks" in data["screens"]["library"]["absent_testids"]


@pytest.mark.parametrize("mutate,message", [
    (lambda d: d.pop("screens"), "screens"),
    (lambda d: d["screens"]["queue"].update(testids="queue-view"), "testids"),
    (lambda d: d["screens"]["queue"].update(texts=[""]), "texts"),
    (lambda d: d["screens"]["queue"].pop("label"), "label"),
    (lambda d: d["screens"]["queue"].update(kind="bogus"), "kind"),
    (lambda d: d["screens"]["queue"].update(allowed_calls="get_ranking"), "allowed_calls"),
    (lambda d: d["global"].update(startup_allowed_calls=[1]), "startup_allowed_calls"),
])
def test_malformed_expectations_are_rejected(mutate, message):
    data = json.loads(H.EXPECTATIONS_PATH.read_text(encoding="utf-8"))
    mutate(data)
    with pytest.raises(H.ExpectationError, match=message):
        H.validate_expectations(data)


def test_load_expectations_reads_a_custom_file(tmp_path):
    path = tmp_path / "e.json"
    path.write_text(json.dumps({"global": {}, "screens": {"x": {"label": "X", "testids": ["t"]}}}), encoding="utf-8")
    assert H.screen_order(H.load_expectations(path)) == ["x"]


# ----------------------------------------------------------------- testid matching
def test_testid_present_exact_and_prefix():
    present = ["queue-view", "backup-open-12"]
    assert H.testid_present("queue-view", present)
    assert not H.testid_present("queue", present)  # exact match is not a prefix match
    assert H.testid_present("backup-open-*", present)
    assert not H.testid_present("backup-close-*", present)


# ----------------------------------------------------------------- snapshot judging
def test_clean_snapshot_passes():
    assert H.check_snapshot(snap(), GLOBAL, {"testids": ["b-*"], "texts": ["ボタン"]}) == []


def test_missing_testid_and_text_are_reported_from_global_and_screen_specs():
    fails = H.check_snapshot(snap(testids=["b-1"], text="なし"), GLOBAL, {"testids": ["zzz"], "texts": ["欠落"]})
    assert "要素なし: data-testid=a" in fails
    assert "要素なし: data-testid=zzz" in fails
    assert "文言なし: 見出し" in fails
    assert "文言なし: 欠落" in fails


def test_absent_testids_fail_when_visible():
    fails = H.check_snapshot(snap(), GLOBAL, {"absent_testids": ["b-*"]})
    assert any("あってはならない要素" in f for f in fails)
    assert H.check_snapshot(snap(), GLOBAL, {"absent_testids": ["nope"]}) == []


@pytest.mark.parametrize("needle", ["Traceback", "Internal Server Error"])
def test_forbidden_text_fails(needle):
    fails = H.check_snapshot(snap(text="見出し " + needle), GLOBAL, {})
    assert any(needle in f for f in fails)


def test_console_and_page_errors_fail():
    fails = H.check_snapshot(snap(console_errors=["boom"], page_errors=["TypeError: x"]), GLOBAL, {})
    assert any("コンソールエラー" in f and "boom" in f for f in fails)
    assert any("未処理例外" in f and "TypeError" in f for f in fails)


def test_horizontal_scroll_has_a_one_pixel_tolerance():
    assert H.check_snapshot(snap(scroll_width=1281), GLOBAL, {}) == []
    fails = H.check_snapshot(snap(scroll_width=1282), GLOBAL, {})
    assert any("横スクロール" in f for f in fails)


def test_theme_not_applied_fails_but_unknown_does_not():
    assert any("テーマ" in f for f in H.check_snapshot(snap(theme_ok=False), GLOBAL, {}))
    assert H.check_snapshot(snap(theme_ok=None), GLOBAL, {}) == []


# ----------------------------------------------------------------- network gate
def test_screen_network_allows_only_declared_calls():
    assert H.check_screen_network([], 0, []) == []
    assert H.check_screen_network(["get_ranking"], 0, ["get_ranking"]) == []
    assert H.check_screen_network(["get_ranking:7"], 0, ["get_ranking"]) == []  # argument suffix ignored
    fails = H.check_screen_network(["get_ranking", "get_user_works"], 0, ["get_ranking"])
    assert len(fails) == 1 and "get_user_works" in fails[0] and "get_ranking" not in fails[0]


def test_screen_network_strict_ignores_the_allowance_and_denied_attempts_fail():
    assert H.check_screen_network(["get_ranking"], 0, ["get_ranking"], strict=True)
    assert any("外部接続" in f for f in H.check_screen_network([], 2, []))


def test_startup_network_requires_zero_unless_waived():
    log = {"startup": dict(calls=["require_identity"], denied=0),
           "load|1280x800|dark": dict(calls=[], denied=0),
           "ranking|1280x800|dark": dict(calls=["get_ranking"], denied=0)}  # tab phases are not judged here
    fails = H.check_startup_network(log, [])
    assert len(fails) == 1 and "startup" in fails[0] and "require_identity" in fails[0]
    assert H.check_startup_network(log, [], allowed=["require_identity"]) == []


def test_startup_network_reports_denied_connections_and_browser_foreign_requests():
    log = {"load|1920x1080|light": dict(calls=[], denied=1)}
    fails = H.check_startup_network(log, ["https://example.invalid/x.png"])
    assert any("外部接続" in f for f in fails)
    assert any("ブラウザの外部リクエスト" in f for f in fails)


def test_allowed_calls_seen_lists_declared_calls_actually_made():
    data = {"screens": {"ranking": {"allowed_calls": ["get_ranking"]}, "queue": {}}}
    log = {"startup": dict(calls=["require_identity"], denied=0),
           "ranking|1280x800|dark": dict(calls=["get_ranking", "get_ranking:2", "other"], denied=0),
           "queue|1280x800|dark": dict(calls=[], denied=0)}
    assert H.allowed_calls_seen(log, data, ["ranking", "queue"]) == {"ranking": ["get_ranking"]}


def test_call_name_strips_the_argument():
    assert H.call_name("get_artwork_download_record:123") == "get_artwork_download_record"
    assert H.call_name("verify_identity") == "verify_identity"


# ----------------------------------------------------------------- server log
BENIGN = """NiceGUI ready to go on http://127.0.0.1:22488
Exception in callback _ProactorBasePipeTransport._call_connection_lost()
Traceback (most recent call last):
  File "asyncio\\events.py", line 89, in _run
    self._context.run(self._callback, *self._args)
  File "asyncio\\proactor_events.py", line 165, in _call_connection_lost
    self._sock.shutdown(socket.SHUT_RDWR)
ConnectionResetError: [WinError 10054]
"""


def test_benign_windows_connection_reset_noise_is_ignored():
    assert H.significant_tracebacks(BENIGN) == []


def test_other_tracebacks_are_kept():
    log = BENIGN + "Traceback (most recent call last):\n  File \"x.py\", line 1, in f\nValueError: bad\n"
    kept = H.significant_tracebacks(log)
    assert len(kept) == 1 and "ValueError: bad" in kept[0]
    assert H.significant_tracebacks("") == []


# ----------------------------------------------------------------- table and verdict
def test_table_marks_pass_fail_and_skip_per_size_and_theme():
    results = {H.cell_key("queue", (1280, 800), "light"): [],
               H.cell_key("queue", (1280, 800), "dark"): ["x"]}
    table = H.format_table(results, ["queue", "failed"], {"queue": "キュー", "failed": "失敗リスト"})
    lines = table.splitlines()
    assert lines[0].split() == ["画面", "1280x800/light", "1280x800/dark", "1920x1080/light", "1920x1080/dark"]
    assert lines[1].split()[2:] == ["PASS", "FAIL", "SKIP", "SKIP"]
    assert lines[2].split()[2:] == ["SKIP"] * 4


def test_table_columns_are_aligned_for_cjk_labels():
    results = {H.cell_key(s, (1280, 800), "light"): [] for s in ("queue", "history")}
    lines = H.format_table(results, ["queue", "history"], {"queue": "キュー", "history": "直近のバックアップ"}).splitlines()

    def width(text):
        return sum(2 if ord(c) > 0x2E80 else 1 for c in text)
    starts = [width(line[:line.index("PASS")]) for line in lines[1:]]
    assert starts[0] == starts[1]


def test_overall_verdict():
    ok = {"a|1280x800|dark": []}
    assert H.overall_verdict(ok, [], []) == "PASS"
    assert H.overall_verdict({"a|1280x800|dark": ["x"]}, [], []) == "FAIL"
    assert H.overall_verdict(ok, ["net"], []) == "FAIL"
    assert H.overall_verdict(ok, [], ["harness"]) == "FAIL"
    assert H.overall_verdict({}, [], []) == "FAIL"  # nothing ran is not a pass
