"""K14 fresh generated-only actual GUI append/scroll/busy/failure/tab test.

Never starts original Flet, uses original DB/Cookie or permits external network.
It verifies only a bounded NiceGUI owner scope, not formal 152 acceptance.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import expect, sync_playwright
from tools import check_p3_residuals_browser as fixture_server
from tools.p3_residuals_fixtures import FixtureReader, create_services
from tools.owned_process_tree import OwnedProcessTree
from tools.run_browser_checks import (
    child_environment, free_port, port_released, source_hashes,
    stop_child, wait_for_health,
)

LEDGER_SHA = "487065a9a52228266d540e36d3e3b83ea34a38bdde4d1fdb54e275d725e55bb4"


class MultiplePagesReader(FixtureReader):
    def get_ranking(self, **kwargs):
        p = kwargs["page"]
        self.calls.append(("ranking", kwargs))
        if p == 2:
            time.sleep(.9)  # deterministic busy state, only in private process
        if p == 4:
            raise RuntimeError("synthetic page-4 failure")
        low, high = (p - 1) * 50 + 1, min(p * 50, 120)
        return [dict(
            illust_id=str(i), user_id="91", user_name="Fixture author",
            title=f"Generated {i}", url=f"https://i.pximg.net/p3-generated/{i}.png",
            tags=["synthetic"], rank=i, yes_rank=i + 3,
            rating_count=i, view_count=i * 2, page_count=1,
            illust_type=0, ai_type=1, x_restrict=0,
        ) for i in range(low, high + 1)]


def serve(port, sandbox, nonce):
    def deny(event, args):
        if event == "socket.connect":
            addr = args[1]
            host = addr[0] if isinstance(addr, tuple) else None
            if host not in ("127.0.0.1", "::1", "localhost"):
                raise RuntimeError("external socket prohibited")
        elif event == "socket.getaddrinfo" and args[0] not in ("127.0.0.1", "::1", "localhost", None):
            raise RuntimeError("external DNS prohibited")
    sys.addaudithook(deny)
    fixture_server.serve(
        port, sandbox, nonce,
        fixture_factory=lambda app, root: create_services(app, root, reader=MultiplePagesReader()),
    )


def sha(path):
    return sha256(path.read_bytes()).hexdigest()


def validate_card_ids(actual, count):
    """Guard both the membership and order, not just the size/last card.

    These private synthetic pages intentionally contain consecutive IDs.
    Checking the entire sequence also detects a middle duplicate replacing
    a missing item when the count and last ID are otherwise unchanged.
    """
    expected = [str(i) for i in range(1, count + 1)]
    if (len(actual) != count or len(set(actual)) != count
            or set(actual) != set(expected) or list(actual) != expected):
        raise AssertionError(f"card IDs differ from expected ordered unique set ({count})")
    return expected


def inspect_more_grid(ranking, *, expected_count):
    """Measure the actual DOM position and operability after any append.

    Only one more-cell may exist in the ranking view and it must be the last
    DIRECT child of its grid (not a detached or fixed-position button).
    """
    grid = ranking.locator('.pv-grid')
    if grid.count() != 1:
        raise AssertionError('more-cell requires exactly one ranking grid')
    details = ranking.evaluate("""root => {
        const grids=[...root.querySelectorAll('.pv-grid')];
        const grid=grids[0];
        const cells=[...root.querySelectorAll('.pv-more-cell')];
        const direct=grid ? [...grid.children].filter(
            child => child.classList.contains('pv-more-cell')) : [];
        const button=cells[0]?.querySelector('button');
        const cards=grid ? [...grid.querySelectorAll('.pv-card')] : [];
        return {
            grid_count:grids.length,
            total_more_cells:cells.length,
            direct_child_count:direct.length,
            last_element_is_more:Boolean(grid && grid.lastElementChild===direct[0]),
            card_count:cards.length,
            button_present:Boolean(button),
            button_enabled:Boolean(button && !button.disabled
                && button.getAttribute('aria-disabled')!=='true'),
            button_text:button?.textContent?.trim() ?? null
        };
    }""")
    if (details['grid_count'] != 1 or details['total_more_cells'] != 1
            or details['direct_child_count'] != 1
            or not details['last_element_is_more']
            or details['card_count'] != expected_count
            or not details['button_present']
            or not details['button_enabled']
            or details['button_text'] != 'もっと見る'):
        raise AssertionError(f"more-cell not a unique, enabled last grid child: {details}")
    return details


def run(out):
    out = out.resolve()
    if out.exists() or not out.is_relative_to(ROOT / "artifacts"):
        raise ValueError("fresh owner-only artifact path required")
    if sha(ROOT / "docs/P3_FORMAL_152_OPERATIONS_LEDGER_2026-10-29_v8.json") != LEDGER_SHA:
        raise RuntimeError("immutable original v8 ledger changed")
    out.mkdir(parents=True, exist_ok=False)
    sandbox = out / "generated-data"
    nonce = secrets.token_hex(24)
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    headers = {"X-Acceptance": nonce}

    def snapshot():
        req = urllib.request.Request(url + "/_p3_test/snapshot", headers=headers)
        with opener.open(req, timeout=8) as response:
            return json.loads(response.read())

    watched = [Path(__file__), Path(fixture_server.__file__),
               ROOT / "tools/p3_residuals_fixtures.py",
               ROOT / "tools/owned_process_tree.py",
               ROOT / "tools/run_browser_checks.py"]
    initial_source = source_hashes()
    harness = {str(p.relative_to(ROOT)): sha(p) for p in watched}
    report = {
        "schema": 1, "original_id": "K14",
        "original_reference_ids": [f"v8-K14-{i:02}" for i in range(1, 6)],
        "immutable_v8_sha256": LEDGER_SHA,
        "scope": "real headless Playwright clicks on a generated loopback NiceGUI shell only",
        "actual_steps": [], "formal_pass_added": 0, "success": False,
        "browser_external_requests": 0, "javascript_errors": 0,
        "app": {"normal_exit": False, "forced_cleanup": False}, "failure": None,
    }
    proc = None
    tree = None
    confirmed = False
    try:
        with (out / "server.log").open("xb") as log:
            proc = subprocess.Popen(
                [sys.executable, "-B", str(Path(__file__)), "--serve", str(port),
                 "--sandbox", str(sandbox), "--nonce", nonce],
                cwd=ROOT, env=child_environment(), stdin=subprocess.DEVNULL,
                stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            report["app"]["pid"] = proc.pid
            tree = OwnedProcessTree(proc)
            if tree.errors:
                raise RuntimeError("cannot establish owned process identity")
            wait_for_health(url, proc, expected_data="pixiv-readonly")
            before = snapshot()
            if proc.poll() is not None or before["calls"] or before["receipts"]:
                raise RuntimeError("initial generated fixture not pristine")
            confirmed = True
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                try:
                    ctx = browser.new_context(viewport={"width": 1280, "height": 720},
                                              device_scale_factor=1)

                    def guard(route):
                        parsed = urlsplit(route.request.url)
                        if (parsed.scheme, parsed.netloc) != ("http", f"127.0.0.1:{port}"):
                            report["browser_external_requests"] += 1
                            route.abort()
                        else:
                            route.continue_()

                    ctx.route("**/*", guard)
                    page = ctx.new_page()
                    page.on("pageerror", lambda _: report.__setitem__(
                        "javascript_errors", report["javascript_errors"] + 1))
                    page.goto(url, wait_until="domcontentloaded", timeout=25000)
                    ranking = page.get_by_test_id("ranking-view")
                    expect(ranking).to_be_visible()
                    cards = ranking.locator(".pv-grid .pv-card")
                    more = ranking.locator(".pv-more-cell button")
                    observed_ids = {}
                    def check_cards(stage, count):
                        actual = cards.evaluate_all('nodes=>nodes.map(n=>n.dataset.workId)')
                        validate_card_ids(actual, count)
                        observed_ids[stage] = actual
                        return actual
                    ranking.get_by_test_id("ranking-fetch").click()
                    expect(cards).to_have_count(50, timeout=20000)
                    check_cards('page1', 50)
                    assert len([x for x in snapshot()["calls"] if x[0] == "ranking"]) == 1
                    report["actual_steps"].append("fetch first page by actual ranking-fetch click, 50 generated IDs")

                    grid = ranking.locator(".pv-grid")
                    assert grid.locator(".pv-more-cell").count() == 1
                    assert grid.locator(":scope > .pv-more-cell").count() == 1
                    initial_rect = more.bounding_box()
                    assert initial_rect and initial_rect["y"] > 720, "more button is fixed/initially on screen"
                    more.scroll_into_view_if_needed()
                    rect = more.bounding_box()
                    assert rect and 0 <= rect["y"] < 720, "more not reachable by scrolling to grid end"
                    assert len([x for x in snapshot()["calls"] if x[0] == "ranking"]) == 1, "auto-fetch on scroll"
                    report["actual_steps"].append("scrolling reaches grid-last-cell button without backend append")

                    more.click()
                    expect(more).to_have_text("読み込み中…", timeout=3000)
                    expect(more).to_be_disabled()
                    report["actual_steps"].append("actual click disables more and displays loading state")
                    expect(cards).to_have_count(100, timeout=20000)
                    expect(more).to_have_text("もっと見る")
                    expect(more).to_be_enabled()
                    check_cards('page2', 100)
                    report["actual_steps"].append("page 2 appended only after click; 100 distinct visible IDs")

                    more.scroll_into_view_if_needed()
                    more.click()
                    expect(cards).to_have_count(120, timeout=20000)
                    check_cards('page3', 120)
                    report["actual_steps"].append("page 3 click appends 20 new works, 120 total")
                    page.get_by_test_id("tab-library").click()
                    expect(page.get_by_test_id("tab-library")).to_have_attribute("aria-current", "page")
                    page.get_by_test_id("tab-ranking").click()
                    expect(page.get_by_test_id("tab-ranking")).to_have_attribute("aria-current", "page")
                    expect(cards).to_have_count(120)
                    check_cards('after_tab_roundtrip', 120)
                    report["actual_steps"].append("two physical DOM tab clicks after 3 pages, 120 cards preserved")

                    more.scroll_into_view_if_needed()
                    more.click()  # generated page 4 intentionally fails
                    expect(ranking.get_by_test_id("ranking-notice")).to_contain_text(
                        "取得できませんでした", timeout=20000)
                    expect(more).to_have_text("もっと見る")
                    expect(more).to_be_enabled()
                    expect(cards).to_have_count(120)
                    check_cards('after_page4_failure', 120)
                    layout = inspect_more_grid(ranking, expected_count=120)
                    report["actual_steps"].append("failed page 4 keeps 120 cards and re-enables more with original label")
                    after = snapshot()
                    calls = [x[1]["page"] for x in after["calls"] if x[0] == "ranking"]
                    if calls != [1, 2, 3, 4] or after["receipts"]:
                        raise AssertionError(f"unexpected ranking calls/physical writes: {calls}")
                    report["observed_backend_pages"] = calls
                    report["count_after_failure"] = cards.count()
                    report["more_grid_layout_after_failure"] = layout
                    report["observed_work_ids"] = observed_ids
                    report["browser_screenshot"] = "k14-after-failed-append.png"
                    page.screenshot(path=str(out / report["browser_screenshot"]), full_page=False)
                    if report["browser_external_requests"] or report["javascript_errors"]:
                        raise AssertionError("external request or JS exception")
                finally:
                    browser.close()
            report["tested"] = True
    except Exception as error:
        report["failure"] = type(error).__name__
        report["error_short"] = str(error)[:240]
        report["failure_last_step"] = report["actual_steps"][-1] if report["actual_steps"] else "startup"
    finally:
        if proc is not None:
            try:
                if proc.poll() is None and confirmed:
                    request = urllib.request.Request(
                        url + "/_p3_test/shutdown", headers=headers, data=b"", method="POST")
                    with opener.open(request, timeout=8) as response:
                        report["app"]["shutdown_ack"] = json.loads(response.read())
                    report["app"]["normal_exit"] = proc.wait(timeout=20) == 0
                if proc.poll() is None:
                    report["app"]["forced_cleanup"] = True
                    stop_child(proc)
            except Exception:
                report["app"]["shutdown_error"] = "owned natural shutdown unavailable"
                if proc.poll() is None:
                    report["app"]["forced_cleanup"] = True
                    try:
                        stop_child(proc)
                    except Exception:
                        pass
            report["app"]["exit_code"] = proc.poll()
        report["app"]["observed_tree"] = tree.finish() if tree is not None else None
        report["app"]["port_released"] = port_released(port)
        report["source_changed"] = initial_source != source_hashes()
        report["harness_changed"] = harness != {str(p.relative_to(ROOT)): sha(p) for p in watched}
        owned = report["app"].get("observed_tree") or {}
        report["success"] = bool(
            report.get("tested") and report["app"].get("exit_code") == 0
            and report["app"]["normal_exit"] and not report["app"]["forced_cleanup"]
            and report["app"]["port_released"] and owned.get("remaining_count") == 0
            and not owned.get("errors") and not report["source_changed"]
            and not report["harness_changed"]
        )
        with (out / "result.json").open("x", encoding="utf8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"success": report["success"], "steps": len(report["actual_steps"]),
                      "failure": report["failure"], "formal_pass_added": 0,
                      "owned_remaining": (report["app"].get("observed_tree") or {}).get("remaining_count")}))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", type=int)
    parser.add_argument("--sandbox", type=Path)
    parser.add_argument("--nonce")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.serve is not None:
        if not args.sandbox or not args.nonce:
            parser.error("--serve requires --sandbox and --nonce")
        serve(args.serve, args.sandbox, args.nonce)
    else:
        if not args.output:
            parser.error("--output required")
        raise SystemExit(run(args.output))
