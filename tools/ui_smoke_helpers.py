"""Pure helpers of the UI smoke (tools/ui_smoke_browser.py): no browser, no server.

Kept free of Playwright/NiceGUI imports so the judging rules are unit-testable
(tests/test_ui_smoke_helpers_20261117.py).
"""
from __future__ import annotations

import json
from pathlib import Path

EXPECTATIONS_PATH = Path(__file__).with_name("ui_smoke_expectations.json")
FORBIDDEN_TEXTS = ("Traceback", "Internal Server Error")
SIZES = ((1280, 800), (1920, 1080))
THEMES = ("light", "dark")
# Sub-pixel rounding / scrollbar slack for the horizontal scroll check.
SCROLL_TOLERANCE_PX = 1


class ExpectationError(ValueError):
    """The expectations file is malformed."""


def load_expectations(path: Path | str = EXPECTATIONS_PATH) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_expectations(data)


def validate_expectations(data) -> dict:
    if not isinstance(data, dict):
        raise ExpectationError("top level must be an object")
    for key in ("global", "screens"):
        if not isinstance(data.get(key), dict):
            raise ExpectationError(f"missing object: {key}")
    waiver = data["global"].get("startup_allowed_calls", [])
    if not isinstance(waiver, list) or not all(isinstance(v, str) and v for v in waiver):
        raise ExpectationError("global.startup_allowed_calls: must be a list of method names")
    for name, spec in [("global", data["global"])] + list(data["screens"].items()):
        if not isinstance(spec, dict):
            raise ExpectationError(f"{name}: must be an object")
        for field in ("testids", "texts", "absent_testids"):
            values = spec.get(field, [])
            if not isinstance(values, list) or not all(isinstance(v, str) and v for v in values):
                raise ExpectationError(f"{name}.{field}: must be a list of non-empty strings")
    for name, spec in data["screens"].items():
        if not isinstance(spec.get("label"), str) or not spec["label"]:
            raise ExpectationError(f"{name}: label is required")
        opener = spec.get("open")
        if opener is not None and not (isinstance(opener, str) and opener):
            raise ExpectationError(f"{name}.open: must be a test id string")
        if spec.get("kind") not in (None, "startup"):
            raise ExpectationError(f"{name}.kind: only 'startup' is known")
        allowed = spec.get("allowed_calls", [])
        if not isinstance(allowed, list) or not all(isinstance(v, str) and v for v in allowed):
            raise ExpectationError(f"{name}.allowed_calls: must be a list of method names")
    return data


def screen_order(expectations: dict) -> list[str]:
    return list(expectations["screens"])


def testid_present(pattern: str, present) -> bool:
    """A trailing '*' matches any test id with that prefix; otherwise exact."""
    if pattern.endswith("*"):
        prefix = pattern[:-1]
        return any(t.startswith(prefix) for t in present)
    return pattern in set(present)


def check_snapshot(snapshot: dict, global_spec: dict, screen_spec: dict) -> list[str]:
    """Return human-readable failures for one screen snapshot (empty list = PASS).

    snapshot: {testids: [...], text: str, scroll_width: int, client_width: int,
               console_errors: [...], page_errors: [...], theme_ok: bool|None}
    """
    failures: list[str] = []
    for message in snapshot.get("page_errors", []):
        failures.append(f"未処理例外: {message}")
    for message in snapshot.get("console_errors", []):
        failures.append(f"コンソールエラー: {message}")
    testids = snapshot.get("testids", [])
    text = snapshot.get("text", "")
    for spec in (global_spec, screen_spec):
        for pattern in spec.get("testids", []):
            if not testid_present(pattern, testids):
                failures.append(f"要素なし: data-testid={pattern}")
        for needle in spec.get("texts", []):
            if needle not in text:
                failures.append(f"文言なし: {needle}")
    for spec in (global_spec, screen_spec):
        for pattern in spec.get("absent_testids", []):
            if testid_present(pattern, testids):
                failures.append(f"あってはならない要素: data-testid={pattern}")
    for needle in FORBIDDEN_TEXTS:
        if needle in text:
            failures.append(f"本文に「{needle}」")
    overflow = snapshot.get("scroll_width", 0) - snapshot.get("client_width", 0)
    if overflow > SCROLL_TOLERANCE_PX:
        failures.append(f"横スクロール: {snapshot.get('scroll_width')} > {snapshot.get('client_width')}")
    if snapshot.get("theme_ok") is False:
        failures.append("テーマが切り替わっていない")
    return failures


def call_name(call: str) -> str:
    """'get_ranking:123' -> 'get_ranking'."""
    return call.split(":", 1)[0]


def check_screen_network(calls: list, denied: int, allowed: list, strict: bool = False) -> list[str]:
    """Calls to the fake live client seen while one screen was opened.

    ``allowed`` (the screen's ``allowed_calls``) names calls that screen is DESIGNED to make
    on its first open (user-initiated tab-open fetch).  strict=True ignores the allowance.
    """
    unexpected = [c for c in calls if strict or call_name(c) not in set(allowed)]
    failures = []
    if unexpected:
        failures.append(f"通信: 偽 live client への呼び出し {len(unexpected)} 件: {', '.join(unexpected[:5])}")
    if denied:
        failures.append(f"通信: サーバーの外部接続試行 {denied} 件（遮断済み）")
    return failures


def check_startup_network(call_log: dict, foreign: list, allowed: list = ()) -> list[str]:
    """Gate check for everything BEFORE a tab is clicked, plus any browser-side foreign request.

    ``allowed`` is global.startup_allowed_calls: a deliberate, reviewed waiver (default empty).
    """
    failures = []
    for phase, entry in call_log.items():
        if phase == "startup" or phase.startswith("load|"):
            unexpected = [c for c in entry["calls"] if call_name(c) not in set(allowed)]
            if unexpected:
                failures.append(f"{phase}: 起動・読込直後に偽 live client が呼ばれた "
                                f"{len(unexpected)} 件: {', '.join(unexpected[:5])}")
            if entry["denied"]:
                failures.append(f"{phase}: 起動・読込直後にサーバーが外部接続を試みた {entry['denied']} 件（遮断済み）")
    if foreign:
        failures.append(f"ブラウザの外部リクエスト {len(foreign)} 件: {', '.join(foreign[:3])}")
    return failures


def allowed_calls_seen(call_log: dict, expectations: dict, screens: list) -> dict:
    """Declared (allowed_calls) first-open calls that were actually observed, per screen."""
    seen: dict = {}
    for phase, entry in call_log.items():
        screen = phase.split("|", 1)[0]
        if screen in ("startup", "load") or screen not in screens:
            continue
        allowed = set(expectations["screens"][screen].get("allowed_calls", []))
        for call in entry["calls"]:
            if call_name(call) in allowed:
                seen.setdefault(screen, set()).add(call_name(call))
    return {k: sorted(v) for k, v in seen.items()}


def cell_key(screen: str, size: tuple[int, int], theme: str) -> str:
    return f"{screen}|{size[0]}x{size[1]}|{theme}"


def format_table(results: dict[str, list[str]], screens: list[str], labels: dict[str, str],
                 sizes=SIZES, themes=THEMES) -> str:
    """results maps cell_key -> failure list (empty = PASS; missing cell = SKIP)."""
    columns = [(size, theme) for size in sizes for theme in themes]
    header = ["画面"] + [f"{w}x{h}/{theme}" for (w, h), theme in columns]
    rows = [header]
    for screen in screens:
        row = [f"{screen} ({labels.get(screen, '')})"]
        for size, theme in columns:
            key = cell_key(screen, size, theme)
            row.append("SKIP" if key not in results else "PASS" if not results[key] else "FAIL")
        rows.append(row)
    # CJK is double width; pad by display width, not len().
    def width(text):
        return sum(2 if ord(c) > 0x2E80 else 1 for c in text)
    widths = [max(width(r[i]) for r in rows) for i in range(len(header))]
    lines = []
    for r in rows:
        lines.append("  ".join(cell + " " * (widths[i] - width(cell)) for i, cell in enumerate(r)).rstrip())
    return "\n".join(lines)


def overall_verdict(results: dict[str, list[str]], network_failures: list[str], harness_errors: list[str]) -> str:
    bad = any(results.values()) or bool(network_failures) or bool(harness_errors)
    return "FAIL" if bad or not results else "PASS"


def significant_tracebacks(log_text: str) -> list[str]:
    """Traceback blocks of the server log, minus known benign Windows asyncio noise.

    When the browser closes a socket abruptly, asyncio's Proactor loop logs
    ``_call_connection_lost`` + ``ConnectionResetError [WinError 10054]``.  That is
    not a product fault; every other traceback counts.
    """
    marker = "Traceback (most recent call last)"
    parts = log_text.split(marker)[1:]
    out = []
    for part in parts:
        if "_call_connection_lost" in part and ("ConnectionResetError" in part or "10054" in part):
            continue
        out.append(marker + part[:1500])
    return out
