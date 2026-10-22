"""Win32 native window smoke check. Run only against a dedicated demo process."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
import re
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlparse

if __package__:
    from .native_restore_gate import run_restore_gate
    from .native_ui_guard import NativeUiGuard
else:
    from native_restore_gate import run_restore_gate
    from native_ui_guard import NativeUiGuard


# Per-phase maximums, bounded by one total deadline (default 90 seconds).
PHASE_LIMITS = {"health": 30.0, "window": 20.0, "restore_baseline": 5.0, "minimize": 5.0,
                "restore": 5.0, "close": 30.0, "inspect_ui": 40.0}

LOG_FAILURE = re.compile(r"Traceback|Vue failed to load|Error occurred|ObjectDisposedException", re.I)


def log_failures(contents: str) -> list[str]:
    """Return only fixed labels; never copy private log lines into JSON."""
    return [term for term in ("Traceback", "Vue failed to load", "Error occurred",
                              "ObjectDisposedException") if re.search(re.escape(term), contents, re.I)]


def matching_app_page(url: str, port: int) -> bool:
    try:
        parsed = urlparse(url)
        return (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
                and parsed.port == port)
    except ValueError:
        return False


def inspect_library_ui(page, screenshot: Path, guard: NativeUiGuard) -> dict:
    """Exercise the synthetic author/page route in the owned WebView."""
    from playwright.sync_api import expect

    run = guard.run
    run(page.get_by_test_id("tab-library").click)
    library = page.get_by_test_id("library-view")
    run(expect(library).to_have_attribute, "data-active", "true")
    authors = library.locator(".pv-library-author-card")
    run(expect(authors).to_have_count, 4)
    author_shot = screenshot.with_name(screenshot.stem + "-library-authors.png")
    run(page.screenshot, path=str(author_shot), full_page=False)
    search = library.get_by_placeholder("作品名・作者・タグで検索")
    run(search.fill, "0003")
    run(expect(authors).to_have_count, 1)
    run(expect(authors).to_have_attribute, "data-representative-id", "demo-0060")
    run(authors.get_by_test_id("library-author-open").click)
    counter = library.get_by_test_id("library-page-count")
    run(expect(counter).to_have_text, "50 / 98 枚表示")
    run(expect(library.locator(".pv-library-page-card")).to_have_count, 50)
    card = library.locator('.pv-library-page-card[data-work-id="demo-0060"][data-page-no="2"]')
    original_id = run(card.get_attribute, "id")
    run(library.get_by_test_id("library-more").click)
    run(expect(counter).to_have_text, "98 / 98 枚表示")
    run(expect(library.locator(".pv-library-page-card")).to_have_count, 98)
    run(expect(library.get_by_test_id("library-more")).to_be_hidden)
    run(expect(card).to_have_attribute, "id", original_id)
    run(card.click)
    viewer = page.get_by_test_id("library-viewer")
    run(expect(viewer).to_be_visible)
    run(expect(viewer.locator(".pv-counter")).to_have_text, "3/3")
    image = page.get_by_test_id("library-viewer-image").locator("img:visible")
    run(expect(image).to_have_count, 1)
    guard.decode(image)
    displayed_path = run(image.evaluate, "img => new URL(img.currentSrc || img.src, document.baseURI).pathname")
    if displayed_path != "/media/demo-0060/2/view.png":
        raise AssertionError("native library viewer opened an incorrect work or page")
    run(expect(viewer.get_by_role("button", name="次のページ", exact=True)).to_be_disabled)
    run(expect(viewer).to_be_focused)
    run(page.keyboard.press, "ArrowLeft", with_timeout=False)
    run(expect(viewer.locator(".pv-counter")).to_have_text, "2/3")
    run(expect(viewer.get_by_text("画像読込中", exact=True)).to_be_hidden)
    guard.decode(image)
    previous_path = run(image.evaluate, "img => new URL(img.currentSrc || img.src, document.baseURI).pathname")
    if previous_path != "/media/demo-0060/1/view.png":
        raise AssertionError("native library previous page was not displayed")
    library_viewer_shot = screenshot.with_name(screenshot.stem + "-library-viewer.png")
    run(page.screenshot, path=str(library_viewer_shot), full_page=False)
    run(viewer.get_by_role("button", name="閉じる", exact=True).click)
    run(expect(viewer).to_be_hidden)
    run(expect(card).to_be_focused)
    run(page.get_by_test_id("tab-ranking").click)
    run(page.get_by_test_id("gallery-size-small").click)
    run(page.get_by_test_id("tab-library").click)
    run(expect(counter).to_have_text, "98 / 98 枚表示")
    run(expect(search).to_have_value, "0003")
    run(expect(library.get_by_test_id("library-size-small")).to_have_attribute, "aria-pressed", "true")
    run(expect(card).to_have_attribute, "id", original_id)
    detail_shot = screenshot.with_name(screenshot.stem + "-library-detail.png")
    run(page.screenshot, path=str(detail_shot), full_page=False)
    run(library.get_by_test_id("library-back").click)
    run(expect(authors.get_by_test_id("library-author-open")).to_be_focused)
    run(expect(authors).to_have_count, 1)
    run(expect(search).to_have_value, "0003")
    return {"author_count": 4, "filtered_author_count": 1, "detail_pages": 98,
            "selected_page_path": displayed_path, "previous_page": 1,
            "append_preserved_card": True, "tab_state_and_shared_size": True,
            "author_focus_restored": True, "author_screenshot": str(author_shot),
            "viewer_screenshot": str(library_viewer_shot), "detail_screenshot": str(detail_shot)}


def inspect_readonly_library_ui(page, screenshot: Path, guard: NativeUiGuard) -> dict:
    """Exercise real WebView2 controls backed by an isolated legacy-schema SQLite."""
    from playwright.sync_api import expect
    run = guard.run
    run(page.get_by_test_id("tab-library").click)
    library = page.get_by_test_id("library-view")
    authors = library.locator(".pv-library-author-card")
    run(expect(authors).to_have_count, 1)
    run(authors.get_by_test_id("library-author-open").click)
    cards = library.locator(".pv-library-page-card")
    run(expect(cards).to_have_count, 2)
    run(expect(library.get_by_test_id("library-page-count")).to_have_text, "2 / 2 枚表示")
    card = library.locator('.pv-library-page-card[data-work-id="101"][data-page-no="0"]')
    run(card.click)
    viewer = page.get_by_test_id("library-viewer")
    run(expect(viewer).to_be_visible)
    run(expect(viewer.locator(".pv-counter")).to_have_text, "1/2")
    img = page.get_by_test_id("library-viewer-image").locator("img:visible")
    run(expect(img).to_have_attribute, "src", re.compile(r"/media/101/0/view\.png\?catalog=[a-f0-9]+#request-"))
    guard.decode(img)
    run(page.keyboard.press, "ArrowRight", with_timeout=False)
    run(expect(viewer.locator(".pv-counter")).to_have_text, "2/2")
    run(expect(img).to_have_attribute, "src", re.compile(r"/media/101/1/view\.png\?catalog=[a-f0-9]+#request-"))
    guard.decode(img)
    path = run(img.evaluate, "img => new URL(img.currentSrc || img.src).pathname", with_timeout=False)
    if path != "/media/101/1/view.png":
        raise AssertionError("native readonly library image/page mismatch")
    shot = screenshot.with_name(screenshot.stem + "-readonly-library.png")
    run(page.screenshot, path=str(shot), full_page=False)
    run(viewer.get_by_role("button", name="閉じる", exact=True).click)
    run(expect(card).to_be_focused)
    run(library.get_by_test_id("library-back").click)
    run(expect(authors.get_by_test_id("library-author-open")).to_be_focused)
    run(library.get_by_test_id("library-subview-bookmarks").click)
    bookmarks = library.get_by_test_id("bookmark-view")
    run(expect(bookmarks).to_be_visible)
    run(expect(bookmarks.get_by_test_id("bookmark-count")).to_have_text, "1 / 1 件表示")
    run(expect(bookmarks.get_by_text("DL済み", exact=True)).to_be_visible)
    checkbox = bookmarks.get_by_role("checkbox", name="選択")
    run(expect(checkbox).not_to_be_checked)
    run(checkbox.click)
    run(expect(checkbox).to_be_checked)
    run(expect(bookmarks.get_by_test_id("bookmark-selection")).to_have_text, "選択中: 1件")
    run(bookmarks.get_by_label("ブックマーク内を検索").fill, "架空")
    opener = bookmarks.get_by_test_id("bookmark-open")
    run(opener.click)
    run(expect(viewer).to_be_visible)
    run(expect(viewer.locator(".pv-counter")).to_have_text, "1/2")
    run(expect(img).to_have_attribute, "src", re.compile(r"/media/101/0/view\.png\?catalog="))
    guard.decode(img)
    run(page.keyboard.press, "ArrowRight", with_timeout=False)
    run(expect(viewer.locator(".pv-counter")).to_have_text, "2/2")
    run(expect(img).to_have_attribute, "src", re.compile(r"/media/101/1/view\.png\?catalog="))
    guard.decode(img)
    run(viewer.get_by_role("button", name="閉じる", exact=True).click)
    run(expect(opener).to_be_focused)
    run(page.get_by_test_id("tab-ranking").click)
    run(page.get_by_test_id("tab-library").click)
    run(expect(bookmarks.get_by_label("ブックマーク内を検索")).to_have_value, "架空")
    run(expect(bookmarks.get_by_test_id("bookmark-selection")).to_have_text, "選択中: 1件")
    run(library.get_by_test_id("library-subview-downloaded").click)
    run(expect(authors).to_have_count, 1)
    run(library.get_by_test_id("library-filter-toggle").click)
    filters = library.get_by_test_id("library-filter-panel")
    now = library.get_by_test_id("library-filter-now")
    run(expect(filters).to_be_visible)
    run(expect(filters.locator(".pv-library-filter-seat")).to_have_count, 8)
    field = filters.get_by_placeholder("タグを追加")
    run(field.fill, "　診断用　")
    run(field.press, "Enter")
    run(expect(now.get_by_test_id("library-filter-now-chip")).to_have_count, 1)
    run(expect(authors).to_have_count, 1)
    run(field.fill, "存在しない合成タグ")
    run(field.press, "Enter")
    run(expect(authors).to_have_count, 0)
    run(filters.get_by_test_id("library-filter-mode").click)
    run(expect(authors).to_have_count, 1)
    run(filters.locator('.pv-library-tag-chip[data-tag="診断用"]').get_by_test_id(
        "library-filter-tag-flip").click)
    run(expect(authors).to_have_count, 0)
    run(now.get_by_test_id("library-filter-now-clear").click)
    run(expect(now).to_be_hidden)
    run(expect(authors).to_have_count, 1)
    run(field.fill, "診断用")
    run(field.press, "Enter")
    run(expect(now.get_by_test_id("library-filter-now-chip")).to_have_count, 1)
    run(library.get_by_test_id("library-subview-bookmarks").click)
    run(expect(bookmarks.get_by_test_id("bookmark-count")).to_have_text, "1 / 1 件表示")
    run(expect(bookmarks.get_by_test_id("bookmark-selection")).to_have_text, "選択中: 1件")
    filter_shot = screenshot.with_name(screenshot.stem + "-tag-filters.png")
    run(page.screenshot, path=str(filter_shot), full_page=False)
    run(bookmarks.get_by_test_id("bookmark-filter-toggle").click)
    run(expect(filters).to_be_hidden)
    run(expect(now).to_be_visible)
    run(now.get_by_test_id("library-filter-now-clear").click)
    run(expect(now).to_be_hidden)
    run(library.get_by_test_id("library-subview-downloaded").click)
    run(expect(authors).to_have_count, 1)
    return {"passed": True, "authors": 1, "pages": 2, "viewer_path": path,
            "focus_restored": True, "bookmark_roundtrip": True,
            "bookmark_search_selection_viewer": True, "screenshot": str(shot),
            "tag_filter_and_or_exclude_clear": True, "tag_filters_shared_subviews": True,
            "tag_filter_screenshot": str(filter_shot)}


def inspect_ui(debug_port: int, app_port: int, screenshot: Path, deadline: float,
               *, check_owner, learning_persistence: bool = False,
               readonly_library: bool = False) -> dict:
    """Use Playwright's WebView2 CDP attachment, never close remote browser/context."""
    from playwright.sync_api import expect, sync_playwright

    result = {"passed": False, "handshake": False, "cards": 0,
              "image_decoded": False, "bridge_ready": False,
              "console_errors": [], "page_errors": [], "screenshot": None,
              "interactions": []}
    with sync_playwright() as playwright:
        check_owner()
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        if remaining_ms <= 0:
            raise TimeoutError("native UI deadline expired before attachment")
        browser = playwright.chromium.connect_over_cdp(
            f"http://127.0.0.1:{debug_port}", timeout=min(5000, remaining_ms))
        # sync_playwright context exit disconnects CDP; do not call browser.close/context.close.
        pages = []
        def find_page() -> bool:
            nonlocal pages
            check_owner()
            pages = [page for context in browser.contexts for page in context.pages
                     if matching_app_page(page.url, app_port)]
            return bool(pages)
        wait_until(find_page, deadline)
        if len(pages) != 1:
            raise RuntimeError(f"Expected one matching WebView page, found {len(pages)}")
        page = pages[0]
        guard = NativeUiGuard(page, app_port, deadline, check_owner)
        run = guard.run
        result['browser_version'] = browser.version
        result['viewport'] = run(page.evaluate, '''() => ({
            width: window.innerWidth, height: window.innerHeight,
            device_pixel_ratio: window.devicePixelRatio
        })''', with_timeout=False)
        page.on("console", lambda msg: result["console_errors"].append(msg.text[:240])
                if msg.type == "error" else None)
        page.on("pageerror", lambda err: result["page_errors"].append(str(err)[:240]))
        def ready() -> bool:
            state = run(page.evaluate, """() => ({
              handshake: window.did_handshake === true,
              cards: document.querySelectorAll('.pv-card').length,
              decoded: (() => { const img = document.querySelector('.pv-card img');
                return !!img && img.complete && img.naturalWidth > 0; })(),
              bridge: !!(window.pywebview && window.pywebview.api)
            })""", with_timeout=False)
            result.update(handshake=state["handshake"], cards=state["cards"],
                          image_decoded=state["decoded"], bridge_ready=state["bridge"])
            return all((result["handshake"], result["cards"] == 50,
                        result["image_decoded"], result["bridge_ready"]))
        wait_until(ready, deadline, interval=0.2)
        if all((result['handshake'], result['cards'] == 50,
                result['image_decoded'], result['bridge_ready'])):
            # 実際のWebViewでイベント往復と画像完了を確認する。
            # 単にHTTPが起動したことをnative画面の受入と混同しない。
            run(page.locator('.pv-card').first.click)
            viewer = page.get_by_test_id('gallery-viewer')
            run(expect(viewer).to_be_visible)
            run(expect(viewer.get_by_text('画像読込中')).to_be_hidden)
            guard.decode(page.get_by_test_id('viewer-image').locator('img:visible'))
            run(expect(page.get_by_test_id('viewer-image')).to_have_css, 'opacity', '1')
            geometry = run(page.evaluate, '''() => {
                const box = selector => {
                    const r = document.querySelector(selector).getBoundingClientRect();
                    return {x:r.x, y:r.y, width:r.width, height:r.height, bottom:r.bottom};
                };
                return {viewer:box('.pv-viewer'), content:box('.pv-content'), nav:box('.pv-nav')};
            }''', with_timeout=False)
            if (any(abs(geometry['viewer'][key] - geometry['content'][key]) > 1
                    for key in ('x', 'y', 'width', 'height'))
                    or geometry['viewer']['y'] < geometry['nav']['bottom']):
                raise AssertionError(f'native viewer must stay below navigation: {geometry}')
            run(expect(viewer).to_have_attribute, 'aria-modal', 'false')
            result['viewer_boundary'] = geometry
            viewer_shot = screenshot.with_name(screenshot.stem + '-viewer.png')
            run(page.screenshot, path=str(viewer_shot), full_page=False)
            result['viewer_screenshot'] = str(viewer_shot)
            run(viewer.get_by_role('button', name='閉じる', exact=True).click)
            run(expect(viewer).to_be_hidden)
            run(expect(page.locator('.pv-card').first).to_be_focused)
            result['interactions'].append('gallery viewer open/decode/close')
            run(page.locator('.pv-card').first.click)
            run(expect(viewer).to_be_visible)
            run(page.get_by_test_id('tab-learning').click)
            run(expect(viewer).to_be_hidden)
            run(expect(viewer).to_have_attribute, 'aria-hidden', 'true')
            learning = page.locator('.pv-learning[data-active="true"]')
            like = learning.get_by_role('button', name='いいね', exact=True)
            run(expect(like).to_be_enabled)
            initial_count = int(run(learning.locator('.pv-counter').inner_text).split('/', 1)[0].strip())
            run(expect(learning).to_be_focused)
            run(page.keyboard.press, 'ArrowRight', with_timeout=False)
            run(expect(learning.locator('.pv-counter')).to_have_text, re.compile(rf'^{initial_count + 1} /'))
            run(expect(like).to_be_enabled)
            run(learning.get_by_role('button', name='ひとつ戻す（Undo）').click)
            run(expect(learning.locator('.pv-counter')).to_have_text, re.compile(rf'^{initial_count} /'))
            run(expect(learning.get_by_role('button', name='ひとつ戻す（Undo）')).to_be_disabled)
            run(expect(like).to_be_enabled)
            image = learning.locator('.pv-view-art img')
            guard.decode(image)
            displayed = run(image.evaluate, '''img => ({
                path: new URL(img.currentSrc || img.src, document.baseURI).pathname,
                complete: img.complete, width: img.naturalWidth, height: img.naturalHeight
            })''')
            expected_path = f"/media/{run(learning.get_attribute, 'data-work-id')}/0/view.png"
            if (displayed['path'] != expected_path or not displayed['complete']
                    or displayed['width'] <= 0 or displayed['height'] <= 0):
                raise AssertionError('native learning image does not match restored work')
            result['learning_image'] = displayed
            result['interactions'].append('learning judgment/Undo/decoded image')
            run(page.get_by_test_id('tab-ranking').click)
            run(expect(page.get_by_test_id('tab-ranking')).to_be_focused)
            run(expect(viewer).to_be_hidden)
            result['interactions'].append('tab-scoped viewer/leave/learning keyboard/return focus')
            if learning_persistence:
                run(page.get_by_test_id('tab-learning').click)
                run(expect(learning).to_have_attribute, 'data-storage-mode', 'sandbox')
                saved_work = run(learning.get_attribute, 'data-work-id')
                run(like.click)
                run(expect(learning.locator('.pv-counter')).to_have_text, re.compile(rf'^{initial_count + 1} /'))
                run(expect(like).to_be_enabled)
                expected_next = run(learning.get_attribute, 'data-work-id')
                learning_shot = screenshot.with_name(screenshot.stem + '-learning.png')
                run(page.screenshot, path=str(learning_shot), full_page=False)
                run(page.reload, wait_until='domcontentloaded')
                run(page.get_by_test_id('tab-learning').click)
                learning = page.locator('.pv-learning[data-active="true"]')
                run(expect(learning.get_by_role('button', name='いいね', exact=True)).to_be_enabled)
                run(expect(learning).to_have_attribute, 'data-storage-mode', 'sandbox')
                run(expect(learning).to_have_attribute, 'data-work-id', expected_next)
                run(expect(learning.locator('.pv-counter')).to_have_text, re.compile(rf'^{initial_count + 1} /'))
                run(expect(learning.get_by_role('button', name='ひとつ戻す（Undo）')).to_be_disabled)
                result['learning_persistence'] = {
                    'initial_count': initial_count, 'after_reload_count': initial_count + 1,
                    'saved_work': saved_work, 'next_work': expected_next,
                    'session_undo_reset': True, 'screenshot': str(learning_shot),
                }
                result['interactions'].append('sandbox judgment persists across WebView reload')
            if readonly_library:
                result['library'] = inspect_readonly_library_ui(page, screenshot, guard)
                result['interactions'].append('readonly SQLite library + saved images / bookmark roundtrip')
            else:
                result['library'] = inspect_library_ui(page, screenshot, guard)
                result['interactions'].append('library author/search/all pages/viewer/tab return/shared size')
        run(page.screenshot, path=str(screenshot), full_page=False)
        result["screenshot"] = str(screenshot)
        guard.check()
        result["passed"] = (result["handshake"] and result["cards"] == 50
                            and result["image_decoded"] and result["bridge_ready"]
                            and len(result['interactions']) == (5 if learning_persistence else 4)
                            and not result["console_errors"] and not result["page_errors"])
    return result


def phase_deadline(total_deadline: float, phase: str) -> float:
    return min(total_deadline, time.monotonic() + PHASE_LIMITS[phase])


def descendants(root: int, pairs: list[tuple[int, int]]) -> set[int]:
    """Only follow parent links that originate at our own process."""
    found = {root}
    while True:
        newer = found | {pid for pid, parent in pairs if parent in found and pid != parent}
        if newer == found:
            return found
        found = newer


def available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def port_released(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("127.0.0.1", port)) != 0


def health_ready(port: int, expected_data: str = "synthetic") -> bool:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/healthz", timeout=0.6) as response:
            body = json.load(response)
            return response.status == 200 and body == {
                "status": "ok", "mode": "demo" if expected_data == "synthetic" else "readonly",
                "data": expected_data}
    except (OSError, ValueError, TimeoutError):
        return False


def ui_ready(port: int) -> bool:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/readyz", timeout=0.6) as response:
            return bool(json.load(response).get("ui_connected"))
    except (OSError, ValueError, TimeoutError):
        return False


def process_pairs() -> list[tuple[int, int]]:
    from ctypes import wintypes as wt

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
            ("th32ProcessID", wt.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
            ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", wt.LONG),
            ("dwFlags", wt.DWORD), ("szExeFile", wt.WCHAR * 260),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wt.HANDLE
    kernel.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel.Process32FirstW.restype = wt.BOOL
    kernel.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel.Process32NextW.restype = wt.BOOL
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot failed")
    result: list[tuple[int, int]] = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        if not kernel.Process32FirstW(snapshot, ctypes.byref(entry)):
            # 自分自身すら列挙できないsnapshotを「全プロセス終了」と扱わない。
            raise OSError(ctypes.get_last_error(), "Process32FirstW failed")
        while True:
            result.append((entry.th32ProcessID, entry.th32ParentProcessID))
            if not kernel.Process32NextW(snapshot, ctypes.byref(entry)):
                error = ctypes.get_last_error()
                if error != 18:  # ERROR_NO_MORE_FILESのみ正常な列挙終了。
                    raise OSError(error, "Process32NextW failed")
                break
    finally:
        kernel.CloseHandle(snapshot)
    return result


def owned_windows(pids: set[int]) -> list[int]:
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.EnumWindows.restype = wintypes.BOOL
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowThreadProcessId.restype = wintypes.DWORD
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.IsWindowVisible.restype = wintypes.BOOL
    user.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetWindow.restype = wintypes.HWND
    windows: list[int] = []

    @callback_type
    def callback(hwnd, _):
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user.IsWindowVisible(hwnd) and not user.GetWindow(hwnd, 4):
            windows.append(int(hwnd))
        return True

    if not user.EnumWindows(callback, 0):
        raise OSError(ctypes.get_last_error(), "EnumWindows failed")
    return windows


def wait_until(predicate, deadline: float, interval: float = 0.15) -> bool:
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def process_creation_time(handle) -> int:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
                                       ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
                                       ctypes.POINTER(wintypes.FILETIME)]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    values = [wintypes.FILETIME() for _ in range(4)]
    if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in values)):
        raise OSError(ctypes.get_last_error(), "GetProcessTimes failed")
    return (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime


def _missing_from_snapshot(pid: int) -> bool:
    """A failed handle lookup means exited only if the process list proves absence."""
    return pid not in {item for item, _ in process_pairs()}


def process_identity(pid: int) -> int | None:
    """Return Windows creation time; unknown identities are never adopted."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000 | 0x00100000, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if _missing_from_snapshot(pid):
            return None
        raise OSError(error, f"OpenProcess identity failed for PID {pid}")
    try:
        return process_creation_time(handle)
    finally:
        kernel.CloseHandle(handle)


def valid_owned(identities: dict[int, int]) -> set[int]:
    return {pid for pid, creation in identities.items() if process_identity(pid) == creation}


def adopt_descendants(root: int, identities: dict[int, int],
                      pairs: list[tuple[int, int]], root_alive: bool) -> None:
    """Never discover new children after the original root exited."""
    if not root_alive:
        return
    candidates = descendants(root, pairs)
    parents = dict(pairs)
    for pid in sorted(candidates):
        if pid in identities:
            continue
        parent = parents.get(pid)
        if parent not in identities or process_identity(parent) != identities[parent]:
            continue
        identity = process_identity(pid)
        if identity is not None and identity >= identities[parent]:
            identities[pid] = identity


def living_pids(identities: dict[int, int]) -> set[int]:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    alive = set()
    for pid in valid_owned(identities):
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            error = ctypes.get_last_error()
            if _missing_from_snapshot(pid):
                continue
            raise OSError(error, f"OpenProcess wait failed for PID {pid}")
        try:
            result = kernel.WaitForSingleObject(handle, 0)
            if result == 0x102:
                alive.add(pid)
            elif result != 0:
                raise OSError(ctypes.get_last_error(), f"WaitForSingleObject failed for PID {pid}: {result}")
        finally:
            kernel.CloseHandle(handle)
    return alive


def terminate_owned(identities: dict[int, int]) -> list[int]:
    """Cleanup only observed descendants, child-first. Forced cleanup is a failure."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    terminated = []
    for pid in sorted(valid_owned(identities), reverse=True):
        handle = kernel.OpenProcess(0x0001 | 0x1000 | 0x00100000, False, pid)
        if handle:
            try:
                if (process_creation_time(handle) == identities[pid]
                        and kernel.WaitForSingleObject(handle, 0) == 0x102
                        and kernel.TerminateProcess(handle, 1)):
                    terminated.append(pid)
            finally:
                kernel.CloseHandle(handle)
    return terminated


def owned_window_identity(root_pid: int, root_birth: int, hwnd: int,
                          identities: dict[int, int]) -> tuple[int, int]:
    """Read a live owned HWND's PID/birth so callers can pin it before testing."""
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.IsWindow.argtypes = [wintypes.HWND]
    user.IsWindow.restype = wintypes.BOOL
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowThreadProcessId.restype = wintypes.DWORD
    if process_identity(root_pid) != root_birth or not user.IsWindow(hwnd):
        raise RuntimeError("owned close root/window identity changed")
    pid = wintypes.DWORD()
    if not user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
        raise RuntimeError("owned close window owner unknown")
    if (pid.value not in identities or process_identity(pid.value) != identities[pid.value]
            or process_identity(root_pid) != root_birth):
        raise RuntimeError("owned close window identity changed")
    return int(pid.value), identities[pid.value]


def close_owned_window(root_pid: int, root_birth: int, hwnd: int,
                       identities: dict[int, int], expected_owner: tuple[int, int]) -> bool:
    """Close the original HWND owner, including after a display gate failure."""
    if owned_window_identity(root_pid, root_birth, hwnd, identities) != expected_owner:
        raise RuntimeError("owned close window identity changed")
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.PostMessageW.restype = wintypes.BOOL
    return bool(user.PostMessageW(hwnd, 0x0010, 0, 0))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Windows native demo window smoke test")
    run = parser.add_mutually_exclusive_group(required=True)
    run.add_argument("--exe", type=Path, help="Existing built Windows exe")
    run.add_argument("--python-entry", type=Path, help="tools/native_entry.py")
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--timeout", type=float, default=90.0,
                        help="total seconds; phases max health30/window20/baseline5/minimize5/restore5/close30")
    parser.add_argument("--output", type=Path, default=Path("artifacts/windows-packaging/native-check.json"))
    parser.add_argument("--inspect-ui", action="store_true", help="Inspect this WebView over local CDP")
    parser.add_argument("--client-origin", nargs=2, type=int, metavar=("X", "Y"),
                        help="Explicit physical client origin for our owned test window before acceptance; never resize")
    parser.add_argument("--learning-sandbox", type=Path,
                        help="Explicit synthetic learning directory; reuse it to check a native restart")
    parser.add_argument("--library-readonly-db", type=Path,
                        help="Explicit isolated legacy-schema SQLite fixture")
    parser.add_argument("--library-image-root", type=Path,
                        help="Explicit isolated synthetic image fixture root")
    args = parser.parse_args(argv)
    if os.name != "nt":
        parser.error("Windows 専用です")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.learning_sandbox is not None and not args.learning_sandbox.is_absolute():
        parser.error("--learning-sandbox must be absolute")
    if (args.library_readonly_db is None) != (args.library_image_root is None):
        parser.error("read-only library requires both explicit DB and image root")
    if args.library_readonly_db is not None:
        if not (args.library_readonly_db.is_absolute() and args.library_readonly_db.is_file()
                and args.library_image_root.is_absolute() and args.library_image_root.is_dir()):
            parser.error("read-only library fixtures must exist at absolute paths")
        if not args.inspect_ui:
            parser.error("read-only library native acceptance requires --inspect-ui")
    target = (args.exe or args.python_entry).resolve()
    if not target.is_file():
        parser.error("Existing executable/entry file required")
    if args.python_entry and not args.python.is_file():
        parser.error("Python executable does not exist")
    if args.output.resolve() == target.resolve():
        parser.error("Output must differ from entry")
    port = available_port()
    command = ([str(args.exe.resolve())] if args.exe
               else [str(args.python.resolve()), str(args.python_entry.resolve())])
    command += ["--host", "127.0.0.1", "--port", str(port)]
    if args.learning_sandbox is not None:
        command += ["--learning-sandbox", str(args.learning_sandbox)]
    if args.library_readonly_db is not None:
        command += ["--library-readonly-db", str(args.library_readonly_db),
                    "--library-image-root", str(args.library_image_root)]
    report: dict = {"status": "failed", "port": port, "root_pid": None,
                    "owned_pids": [], "window_found": False, "health_ready": False,
                    "minimized": False, "restored": False,
                    "normal_exit": False, "port_released": False,
                    "forced_cleanup": [], "errors": []}
    root = None
    identities: dict[int, int] = {}
    deadline = time.monotonic() + args.timeout
    # Keep ordinary WM_CLOSE monitoring available when a display check times out.
    close_reserve = min(5.0, args.timeout / 3)
    operation_deadline = deadline - close_reserve
    report["close_reserve_seconds"] = close_reserve
    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    log_path = output_path.with_name(output_path.stem + ".process.log")
    report["process_log"] = str(log_path)
    report["inspect_ui_requested"] = args.inspect_ui
    report["ui_inspection"] = None
    child_env = os.environ.copy()
    debug_port = available_port() if args.inspect_ui else None
    if args.inspect_ui:
        # Scoped to this process only: never modify parent/global environment.
        child_env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
            f"--remote-debugging-port={debug_port} --remote-debugging-address=127.0.0.1")
    try:
        with log_path.open("wb") as log:
            root = subprocess.Popen(command, cwd=str(target.parent),
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                    env=child_env)
        report["root_pid"] = root.pid
        root_identity = process_identity(root.pid)
        if root_identity is None:
            raise RuntimeError("Cannot establish root process identity")
        identities[root.pid] = root_identity

        def scan() -> set[int]:
            root_alive = root.poll() is None and process_identity(root.pid) == root_identity
            if root_alive:
                # Discover only from live, identity-matched ancestry.
                pairs = process_pairs()
                for _ in range(len(pairs)):
                    before = len(identities)
                    adopt_descendants(root.pid, identities, pairs, True)
                    if len(identities) == before:
                        break
            report["owned_pids"] = sorted(identities)
            return valid_owned(identities)

        def expected_health():
            if args.library_readonly_db is not None:
                return health_ready(port, "library-readonly")
            return health_ready(port)

        report["health_ready"] = wait_until(lambda: (scan() and expected_health()),
                                            phase_deadline(operation_deadline, "health"))
        if not report["health_ready"]:
            report["errors"].append("health_timeout")
        else:
            handles: list[int] = []

            def locate() -> bool:
                nonlocal handles
                handles = owned_windows(scan())
                return bool(handles)

            window_deadline = phase_deadline(operation_deadline, "window")
            report["window_found"] = wait_until(locate, window_deadline)
            report["ui_connected"] = wait_until(lambda: ui_ready(port), window_deadline)
            if not report["ui_connected"]:
                raise RuntimeError("Native UI did not establish its websocket connection")
            if not handles:
                report["errors"].append("window_timeout")
            else:
                hwnd = handles[0]
                expected_owner = owned_window_identity(root.pid, root_identity, hwnd, identities)

                def check_ui_owner() -> None:
                    if owned_window_identity(root.pid, root_identity, hwnd, identities) != expected_owner:
                        raise RuntimeError("native UI window owner changed")

                if args.client_origin is not None:
                    try:
                        if __package__:
                            from .native_window_position import place_owned_client
                        else:
                            from native_window_position import place_owned_client
                        check_ui_owner()
                        report["initial_placement"] = place_owned_client(
                            root.pid, root_identity, hwnd, args.client_origin, expected_owner)
                        check_ui_owner()
                    except Exception as exc:
                        report["initial_placement"] = {"passed": False, "reason": type(exc).__name__}
                        report["errors"].append("initial_placement_failed")
                if args.inspect_ui:
                    try:
                        screenshot = output_path.with_name("webview-ui.png")
                        inspect_kwargs = {"check_owner": check_ui_owner,
                                          "learning_persistence": args.learning_sandbox is not None}
                        if args.library_readonly_db is not None:
                            inspect_kwargs["readonly_library"] = True
                        report["ui_inspection"] = inspect_ui(
                            debug_port, port, screenshot, phase_deadline(operation_deadline, "inspect_ui"),
                            **inspect_kwargs)
                        if not report["ui_inspection"]["passed"]:
                            report["errors"].append("ui_inspection_failed")
                    except Exception as exc:
                        report["ui_inspection"] = {"passed": False, "reason": type(exc).__name__}
                        report["errors"].append("ui_inspection_exception")
                restore_gate = run_restore_gate(
                    root.pid, root_identity, hwnd, output_path, operation_deadline, PHASE_LIMITS,
                    expected_top_identity=expected_owner)
                report["restore_gate"] = restore_gate
                report["minimized"] = restore_gate["minimized"]
                report["restored"] = restore_gate["passed"]
                if not restore_gate["passed"]:
                    report["errors"].append("restore_gate_failed")
                # Gate failure must still reach normal close and the existing cleanup.
                try:
                    report["wm_close_sent"] = close_owned_window(
                        root.pid, root_identity, hwnd, identities, expected_owner)
                except Exception as exc:
                    report["wm_close_sent"] = False
                    report["errors"].append("owned_close_" + type(exc).__name__)
                if not report["wm_close_sent"]:
                    report["errors"].append("wm_close_failed")
                close_deadline = phase_deadline(deadline, "close")

                def all_exited() -> bool:
                    scan()
                    return not living_pids(dict(identities))

                report["normal_exit"] = wait_until(all_exited, close_deadline)
                report["port_released"] = wait_until(lambda: port_released(port), close_deadline)
                if not report["normal_exit"]:
                    report["errors"].append("process_exit_timeout")
                if not report["port_released"]:
                    report["errors"].append("port_release_timeout")
        report["status"] = "passed" if (report["health_ready"] and report["window_found"]
                    and report["minimized"] and report["restored"]
                    and report["normal_exit"] and report["port_released"]
                    and report.get("wm_close_sent")
                    and not report["errors"]
                    and (args.client_origin is None or
                         (report.get("initial_placement") or {}).get("explicit_setup") is True)
                    and (not args.inspect_ui or (report["ui_inspection"] or {}).get("passed"))) else "failed"
    except Exception as exc:
        report["errors"].append(f"{type(exc).__name__}: {exc}")
    finally:
        if root is not None:
            try:
                survivors = living_pids(identities)
                if survivors:
                    report["errors"].append("forced_cleanup_required")
                    report["forced_cleanup"] = terminate_owned(
                        {pid: identities[pid] for pid in survivors})
                    report["status"] = "failed"
            except Exception as exc:
                report["errors"].append(f"cleanup: {type(exc).__name__}: {exc}")
                report["status"] = "failed"
        try:
            failures = log_failures(log_path.read_text(encoding="utf-8", errors="replace"))
            report["process_log_failures"] = failures
            if failures:
                report["errors"].append("process_log_error")
                report["status"] = "failed"
        except OSError:
            report["errors"].append("process_log_unreadable")
            report["status"] = "failed"
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
