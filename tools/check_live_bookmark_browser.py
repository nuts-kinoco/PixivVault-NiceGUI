"""Browser acceptance of the production live-bookmark path with an in-memory API double.

No real credential, account, HTTP response or image is used in this check.
"""
from __future__ import annotations

import argparse
from io import BytesIO
from html import escape
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit
from urllib.parse import parse_qs
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import subprocess
import sys
import time

from PIL import Image

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.run_browser_checks import (child_environment, free_port, port_released,
                                      source_hashes, stop_child, wait_for_health)


class SyntheticReadClient:
    """Implements the same injected read interface, with all state synthetic."""
    def __init__(self):
        self.fail = False
        self.delay = 0.0
        self.route_calls = 0
        self.completed = 0

    def verify_identity(self):
        if self.delay:
            time.sleep(self.delay)
        return "12"

    def get_bookmarked_works(self, identity, rest_type):
        self.route_calls += 1
        if rest_type == "show":
            for index in range(70):
                yield dict(id=str(202 + index), title=f"合成作品 {index:03d}",
                           user_id="23", user_name="架空作者", tags=["目標" if index == 0 else "合成"],
                           thumb_url=f"https://i.pximg.net/synthetic/{202 + index}.png",
                           page_count=2 if index == 0 else 1)

    def get_bookmarked_novels(self, identity, rest_type):
        self.route_calls += 1
        if rest_type == "hide":
            self.completed += 1
            if self.fail:
                raise RuntimeError("synthetic route failure")
        if rest_type == "show":
            yield dict(id="202", title="合成小説 同番号", user_id="23", user_name="架空作者",
                       tags=["小説"], thumb_url="https://i.pximg.net/synthetic/cover202.png")

    def get_artwork_pages(self, work_id):
        count = 2 if work_id == "202" else 1
        return tuple(f"https://i.pximg.net/synthetic/{work_id}-page{p}.png" for p in range(count))

    def get_image_bytes(self, url):
        out = BytesIO()
        Image.new("RGB", (96, 64), (0, 0, 255) if "page1" in url else (255, 0, 0)).save(out, "PNG")
        return out.getvalue()

    def close(self):
        pass


def write_synthetic_cookie(directory):
    """Create a disposable, synthetic Netscape cookie accepted by the real client."""
    path = Path(directory) / "synthetic-cookies.txt"
    expires = int(time.time()) + 86400
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        f".pixiv.net\tTRUE\t/\tTRUE\t{expires}\tPHPSESSID\t12_synthetic_only\n",
        encoding="utf-8",
    )
    return path


class SyntheticPixivTransport:
    """Offline wire-format transport for LivePixivReadClient browser acceptance."""

    API_HOST = "www.pixiv.net"
    IMAGE_HOST = "i.pximg.net"

    def __init__(self, identity_format):
        if identity_format not in {"next", "meta"}:
            raise ValueError("identity_format must be next or meta")
        self.identity_format = identity_format
        self.fixture = SyntheticReadClient()
        self.fail = False
        self.delay = 0.0
        self.request_count = 0
        self.route_calls = 0
        self.completed = 0
        self.all_requests_get = True
        self.allowed_https_hosts_only = True
        self.image_requests_cookie_free = True
        self._illustrations = [self._wire_row(row, novel=False)
                               for row in self.fixture.get_bookmarked_works("12", "show")]
        self._novels = [self._wire_row(row, novel=True)
                        for row in self.fixture.get_bookmarked_novels("12", "show")]
        # The real service includes unavailable rows rather than omitting them.
        # Exercise their exclusion through the production parser and UI/cache,
        # without widening the downloadable image-host allowlist.
        masked = {"isMasked": True, "title": "合成の表示不可項目", "userId": "0",
                  "url": "https://s.pximg.net/common/images/limit_unknown.png"}
        self._illustrations.insert(1, {**masked, "id": "900001"})
        self._novels.insert(0, {**masked, "id": "900002"})

    @staticmethod
    def _wire_row(row, *, novel):
        raw = {
            "id": row["id"], "title": row["title"], "userId": row["user_id"],
            "userName": row["user_name"], "url": row["thumb_url"],
            "createDate": "2026-10-01T12:00:00", "xRestrict": 0,
            "tags": [{"tag": tag} for tag in row["tags"]],
        }
        if novel:
            raw["textCount"] = 100
        else:
            raw["illustType"] = 0
            raw["pageCount"] = row.get("page_count", 1)
        return raw

    def _home(self):
        if self.identity_format == "meta":
            data = escape(json.dumps({"userData": {"id": "12"}}), quote=True)
            return f'<html><head><meta id="meta-global-data" content="{data}"></head></html>'.encode()
        payload = json.dumps({"props": {"pageProps": {
            "isLoggedIn": True, "gaUserData": {"id": "12"},
        }}})
        return (f'<html><head></head><body><script id="__NEXT_DATA__" '
                f'type="application/json">{payload}</script></body></html>').encode()

    @staticmethod
    def _packet(body):
        return json.dumps({"error": False, "message": "", "body": body},
                          ensure_ascii=False).encode("utf-8")

    def __call__(self, request, timeout, limit, deadline):
        self.request_count += 1
        parsed = urlsplit(request.full_url)
        self.all_requests_get &= request.get_method() == "GET"
        self.allowed_https_hosts_only &= (
            parsed.scheme == "https" and parsed.hostname in {self.API_HOST, self.IMAGE_HOST}
            and parsed.port is None and parsed.username is None and parsed.password is None
        )
        if request.get_method() != "GET" or not self.allowed_https_hosts_only:
            raise AssertionError("synthetic transport accepts only allowlisted HTTPS GET requests")
        if parsed.hostname == self.IMAGE_HOST:
            self.image_requests_cookie_free &= not request.has_header("Cookie")
            if request.has_header("Cookie"):
                raise AssertionError("image request unexpectedly carried a cookie")
            return self.fixture.get_image_bytes(request.full_url)
        if not request.has_header("Cookie"):
            raise AssertionError("API request did not carry the disposable synthetic cookie")

        if parsed.path == "/":
            if self.delay:
                time.sleep(self.delay)
            return self._home()
        if parsed.path == "/ajax/user/extra":
            return self._packet({"userId": "12"})
        if parsed.path.endswith("/bookmarks"):
            query = parse_qs(parsed.query, keep_blank_values=True)
            offset = int(query["offset"][0])
            rest_type = query["rest"][0]
            is_novel = "/novels/" in parsed.path
            if offset == 0:
                self.route_calls += 1
            if self.fail and is_novel and rest_type == "hide" and offset == 0:
                raise RuntimeError("synthetic route failure")
            rows = [] if rest_type == "hide" else (self._novels if is_novel else self._illustrations)
            page = rows[offset:offset + 48]
            if not page:
                self.completed += 1
            return self._packet({"works": page})
        if parsed.path.startswith("/ajax/illust/") and parsed.path.endswith("/pages"):
            work_id = parsed.path.split("/")[3]
            return self._packet([{"urls": {"regular": url}} for url in
                                 self.fixture.get_artwork_pages(work_id)])
        raise AssertionError("unexpected synthetic endpoint")


def serve(port, identity_format=None):
    from nicegui import app, ui
    from pixivvault_nicegui.bootstrap import register_pages
    fake = SyntheticReadClient() if identity_format is None else None
    transport = None
    live_client = None
    cookie_temp = None
    try:
        if identity_format is None:
            register_pages(live_client=fake)
        else:
            from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient
            cookie_temp = tempfile.TemporaryDirectory(prefix="pixiv-synthetic-cookie-")
            cookie_path = write_synthetic_cookie(cookie_temp.name)
            transport = SyntheticPixivTransport(identity_format)
            live_client = LivePixivReadClient(cookie_path, transport=transport)
            register_pages(live_client=live_client)

        @app.post("/_acceptance/control")
        def control(fail: bool = False, delay_ms: int = 0):
            if transport is None:
                fake.fail = fail
                fake.delay = min(1000, max(0, delay_ms)) / 1000
            else:
                transport.fail = fail
                transport.delay = min(1000, max(0, delay_ms)) / 1000
            return {"synthetic": True}

        @app.get("/_acceptance/status")
        def status():
            source = transport if transport is not None else fake
            return {
                "calls": source.route_calls, "completed": source.completed, "synthetic": True,
                "request_count": transport.request_count if transport is not None else 0,
                "actual_parser_used": identity_format,
                "all_requests_get": transport.all_requests_get if transport is not None else True,
                "allowed_https_hosts_only": transport.allowed_https_hosts_only if transport is not None else True,
                "image_requests_cookie_free": transport.image_requests_cookie_free if transport is not None else True,
            }

        @app.post("/_acceptance/shutdown")
        def shutdown():
            app.shutdown()
            return {"stopping": True}

        ui.run(host="127.0.0.1", port=port, show=False, reload=False,
               binding_refresh_interval=None)
    finally:
        if live_client is not None:
            live_client.close()
        if cookie_temp is not None:
            cookie_temp.cleanup()


def inspect(page, url, output, width, height):
    from playwright.sync_api import expect
    page.goto(url, wait_until="domcontentloaded")
    page.get_by_test_id("tab-library").click()
    library = page.get_by_test_id("library-view")
    library.get_by_test_id("library-subview-bookmarks").click()
    bookmarks = library.get_by_test_id("bookmark-view")
    expect(bookmarks.get_by_test_id("bookmark-count")).to_have_text("50 / 71 件表示", timeout=15000)
    first = bookmarks.locator('.pv-bookmark-card[data-work-id="202"]')
    checkbox = first.get_by_role("checkbox", name="選択")
    expect(checkbox).not_to_be_checked()
    checkbox.click()
    expect(checkbox).to_be_checked()
    expect(bookmarks.get_by_test_id("bookmark-selection")).to_have_text("選択中: 1件")
    bookmarks.get_by_test_id("bookmark-more").click()
    expect(bookmarks.get_by_test_id("bookmark-count")).to_have_text("71 / 71 件表示")
    bookmarks.get_by_test_id("bookmark-filter-toggle").click()
    filters = library.get_by_test_id("library-filter-panel")
    tag_input = filters.get_by_placeholder("タグを追加")
    tag_input.fill("目標")
    tag_input.press("Enter")
    expect(bookmarks.get_by_test_id("bookmark-count")).to_have_text("1 / 1 件表示")
    search = bookmarks.get_by_label("ブックマーク内を検索")
    search.fill("目標")
    expect(bookmarks.get_by_test_id("bookmark-count")).to_have_text("1 / 1 件表示")
    opener = first.get_by_test_id("bookmark-open")
    opener.click()
    viewer = page.get_by_test_id("library-viewer")
    expect(viewer).to_be_visible()
    expect(viewer.locator(".pv-counter")).to_have_text("1/2")
    image = page.get_by_test_id("library-viewer-image").locator("img:visible")
    expect(image).to_have_attribute("src", re.compile(r"/media/202/0/view\.png\?catalog="))
    image.evaluate("image => image.decode()")
    page.keyboard.press("ArrowRight")
    expect(viewer.locator(".pv-counter")).to_have_text("2/2")
    expect(image).to_have_attribute("src", re.compile(r"/media/202/1/view\.png\?catalog="))
    image.evaluate("image => image.decode()")
    rgb = image.evaluate("""image => {
        const canvas = document.createElement('canvas'); canvas.width=1; canvas.height=1;
        const ctx=canvas.getContext('2d'); ctx.drawImage(image,0,0,1,1);
        return Array.from(ctx.getImageData(0,0,1,1).data).slice(0,3);
    }""")
    assert rgb == [0, 0, 255], "incorrect page pixels"
    page.keyboard.press("i")
    expect(viewer.get_by_role("button", name="詳細", exact=True)).to_have_attribute("aria-expanded", "true")
    page.keyboard.press("Tab")
    expect(viewer.get_by_role("button", name="詳細", exact=True)).to_have_attribute("aria-expanded", "false")
    page.screenshot(path=str(output / f"synthetic-remote-{width}x{height}.png"))
    viewer.get_by_role("button", name="閉じる", exact=True).click()
    expect(opener).to_be_focused()
    page.get_by_test_id("tab-ranking").click()
    page.get_by_test_id("tab-library").click()
    expect(search).to_have_value("目標")
    expect(bookmarks.get_by_test_id("bookmark-selection")).to_have_text("選択中: 1件")
    expect(library.get_by_test_id("library-filter-now-chip")).to_have_count(1)
    page.request.post(url + "/_acceptance/control?fail=true")
    bookmarks.get_by_test_id("bookmark-refresh").click()
    expect(bookmarks.get_by_test_id("bookmark-status")).to_contain_text("失敗", timeout=15000)
    expect(bookmarks.get_by_test_id("bookmark-count")).to_have_text("1 / 1 件表示")
    page.request.post(url + "/_acceptance/control")
    bookmarks.get_by_test_id("bookmark-refresh").click()
    expect(bookmarks.get_by_test_id("bookmark-status")).to_be_hidden(timeout=15000)
    expect(bookmarks.get_by_test_id("bookmark-selection")).to_have_text("選択中: 1件")
    expect(library.get_by_test_id("library-filter-now-chip")).to_have_count(1)
    expect(bookmarks.get_by_test_id("bookmark-count")).to_have_text("1 / 1 件表示")
    return {"viewport": [width, height], "remote_only_viewer": True,
            "correct_page_pixels": True, "search_selection_tab_return": True,
            "failure_preserves_cache": True, "retry_restores": True,
            "detail_keys": True, "focus_restored": True,
            "tag_filter_survives_refresh_failure_and_recovery": True}


class _NoShutdownRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def shutdown_fixture(url):
    """Request normal shutdown only after Playwright and its sockets are closed."""
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or not parsed.port or
            parsed.netloc != f"127.0.0.1:{parsed.port}" or
            parsed.path or parsed.query or parsed.fragment):
        raise AssertionError("invalid synthetic fixture origin")
    request = Request(url + "/_acceptance/shutdown", data=b"", method="POST")
    with build_opener(ProxyHandler({}), _NoShutdownRedirects()).open(request, timeout=5) as response:
        if response.status != 200 or json.loads(response.read(1024)) != {"stopping": True}:
            raise AssertionError("synthetic shutdown acknowledgement missing")


def assert_transport_evidence(status, identity_format):
    """A requested real-reader fixture must not silently fall back to the old fake."""
    if status.get("synthetic") is not True:
        raise AssertionError("non-synthetic transport evidence")
    for field in ("all_requests_get", "allowed_https_hosts_only", "image_requests_cookie_free"):
        if status.get(field) is not True:
            raise AssertionError("transport safety evidence missing")
    if status.get("actual_parser_used") != identity_format:
        raise AssertionError("requested identity transport was not used")
    if identity_format is not None:
        # Multiple refreshes are exercised, so counts grow beyond one four-route cycle.
        for field, minimum in (("request_count", 6), ("calls", 4), ("completed", 4)):
            value = status.get(field)
            if type(value) is not int or value < minimum:
                raise AssertionError("real reader transport did not complete required requests")


def run(channel, output, identity_format=None):
    from playwright.sync_api import expect, sync_playwright
    output.mkdir(parents=True, exist_ok=True)
    before = source_hashes()
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    process = None
    report = {
        "success": False, "synthetic_only": True, "viewports": [],
        "identity_format": identity_format or "fake", "actual_parser_used": None,
        "synthetic_request_count": 0,
    }
    try:
        with (output / "app-stderr.log").open("wb") as log:
            command = [sys.executable, str(Path(__file__).resolve()), "--serve",
                       "--port", str(port)]
            if identity_format is not None:
                command.extend(["--identity-format", identity_format])
            process = subprocess.Popen(command, env=child_environment(),
                                       cwd=Path(__file__).resolve().parents[1], stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=log)
        wait_for_health(url, process, expected_data="pixiv-readonly")
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge" if channel == "msedge" else None, headless=True)
            page_errors, external_hosts = [], set()
            def observe(page):
                page.on("pageerror", lambda error: page_errors.append(str(error)[:300]))
                page.on("request", lambda request: external_hosts.add(urlsplit(request.url).hostname)
                        if urlsplit(request.url).scheme in {"http", "https"} and
                        urlsplit(request.url).hostname not in {"127.0.0.1", "localhost"} else None)
            try:
                for width, height in ((1280, 720), (1920, 1080)):
                    page = browser.new_page(viewport={"width": width, "height": height})
                    observe(page)
                    report["viewports"].append(inspect(page, url, output, width, height))
                    page.close()
                a, b = browser.new_page(), browser.new_page()
                for page in (a, b):
                    observe(page)
                    page.goto(url, wait_until="domcontentloaded")
                    page.get_by_test_id("tab-library").click()
                    page.get_by_test_id("library-subview-bookmarks").click()
                a.get_by_label("ブックマーク内を検索").fill("目標")
                expect(a.get_by_test_id("bookmark-count")).to_have_text("1 / 1 件表示")
                expect(b.get_by_test_id("bookmark-count")).to_have_text("50 / 71 件表示")
                b.locator('.pv-bookmark-card[data-work-id="202"]').get_by_test_id("bookmark-open").click()
                expect(b.get_by_test_id("library-viewer")).to_be_visible()
                image_b = b.get_by_test_id("library-viewer-image").locator("img:visible")
                old_source = image_b.get_attribute("src").split("#")[0]
                a.get_by_test_id("bookmark-refresh").click()
                expect(a.get_by_test_id("bookmark-status")).to_be_hidden(timeout=15000)
                assert b.request.get(url + old_source).status == 200
                expect(image_b).to_have_attribute("src", re.compile(re.escape(old_source)))
                report["two_clients_independent"] = True
                report["old_image_snapshot_survives_other_client_refresh"] = True
                transport_status = a.request.get(url + "/_acceptance/status").json()
                report["actual_parser_used"] = transport_status["actual_parser_used"]
                report["synthetic_request_count"] = transport_status["request_count"]
                report["all_requests_get"] = transport_status["all_requests_get"]
                report["allowed_https_hosts_only"] = transport_status["allowed_https_hosts_only"]
                report["image_requests_cookie_free"] = transport_status["image_requests_cookie_free"]
                assert_transport_evidence(transport_status, identity_format)
                report["page_errors"] = page_errors
                report["external_hosts"] = sorted(external_hosts)
                assert not page_errors and not external_hosts
                a.close(); b.close()
            finally:
                browser.close()
        # Close the browser AND Playwright driver before the shutdown request;
        # neither a new UI nor the driver's keep-alive sockets are needed here.
        shutdown_fixture(url)
        process.wait(timeout=15)
        report["normal_exit"] = process.returncode == 0
        report["success"] = process.returncode == 0
    except Exception as exc:
        report["error"] = type(exc).__name__ + ": " + str(exc)[:600]
    finally:
        report["forced_cleanup"] = process is not None and process.poll() is None
        stop_child(process)
        report["port_released"] = port_released(port)
        report["source_changed"] = before != source_hashes()
        report["success"] &= report["port_released"] and not report["source_changed"]
        (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int)
    parser.add_argument("--channel", choices=["msedge", "chromium"], default="msedge")
    parser.add_argument("--output", type=Path, default=Path("artifacts/live-bookmark-browser"))
    parser.add_argument("--identity-format", choices=["next", "meta"], default=None,
                        help="Use the real read client with a synthetic cookie and offline transport.")
    args = parser.parse_args()
    if args.serve:
        serve(args.port, args.identity_format)
    else:
        raise SystemExit(run(args.channel, args.output, args.identity_format))
