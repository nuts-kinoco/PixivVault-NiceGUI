"""N02/N03/S15: actual owned Windows tray UI on a fresh packaged synthetic app.

Use a new --output directory. No credentials, original data, browser/session,
registry modifications, global keyboard input, or forced process cleanup.
The checker uses actual notification-area/menu pointer clicks and own-client GDI.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import time
from urllib.parse import urlsplit

if __package__:
    from . import check_native_window as native
    from . import native_restore_gate as restore
    from .native_ui_guard import NativeUiGuard
    from .native_tray_ui import OwnedTrayUI, TrayProbeError, require
    from .native_window_position import resize_owned_client, place_owned_client
else:
    import check_native_window as native
    import native_restore_gate as restore
    from native_ui_guard import NativeUiGuard
    from native_tray_ui import OwnedTrayUI, TrayProbeError, require
    from native_window_position import resize_owned_client, place_owned_client


class TCPROW(ctypes.Structure):
    _fields_ = [(name, w.DWORD) for name in (
        "state", "local_addr", "local_port", "remote_addr", "remote_port", "pid")]


def loopback_listener(port):
    """Read only the requested IPv4 listener; never log other process/socket rows."""
    library = ctypes.WinDLL("iphlpapi", use_last_error=True)
    call = library.GetExtendedTcpTable
    call.argtypes = [ctypes.c_void_p, ctypes.POINTER(w.DWORD), w.BOOL,
                     w.ULONG, ctypes.c_int, w.ULONG]
    call.restype = w.DWORD
    size = w.DWORD()
    code = call(None, ctypes.byref(size), False, 2, 3, 0)
    require(code in (0, 122) and 4 <= size.value <= 16 * 1024 * 1024,
            "tcp_listener_table_unavailable")
    buffer = ctypes.create_string_buffer(size.value)
    code = call(buffer, ctypes.byref(size), False, 2, 3, 0)
    require(code == 0, "tcp_listener_table_changed")
    count = w.DWORD.from_buffer_copy(buffer.raw[:4]).value
    require(4 + count * ctypes.sizeof(TCPROW) <= size.value, "tcp_listener_table_invalid")
    rows = (TCPROW.from_buffer_copy(buffer, 4 + index * ctypes.sizeof(TCPROW))
            for index in range(count))
    return _select_loopback_listener(rows, port)


def _select_loopback_listener(rows, port):
    """Class-3 listener rows only; verify exact 127.0.0.1 and unique owner."""
    candidates = set()
    for row in rows:
        if socket.ntohs(row.local_port & 0xffff) != port:
            continue
        # TCP_TABLE_OWNER_PID_LISTENER (class 3, specified above) already
        # selects listener rows. Some Windows 11 systems return dwState=0 or
        # dwState=6 in that listener-only table. No extra state filter is
        # needed or reliable here. Still reject *every* non-loopback bind and
        # multiple owner PIDs; the caller independently checks PID/birth.
        require(row.local_addr == 0x0100007f,
                "listener_is_not_exclusive_ipv4_loopback")
        candidates.add(int(row.pid))
    require(len(candidates) <= 1, "loopback_listener_ambiguous")
    return next(iter(candidates), None)


def reject_links(path):
    for node in (path, *path.parents):
        try:
            info = node.lstat()
        except FileNotFoundError:
            continue
        require(not stat.S_ISLNK(info.st_mode)
                and not getattr(info, "st_file_attributes", 0) & 0x400,
                "path_contains_reparse_or_symlink")


def file_sha(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def helper_shas():
    folder = Path(__file__).absolute().parent
    names = (Path(__file__).name, "native_tray_ui.py", "native_uia_hit.py", "check_native_window.py",
             "native_restore_gate.py", "native_screen_capture.py",
             "benchmark_native_pixels.py", "native_ui_guard.py", "native_window_position.py")
    return {name: file_sha(folder / name) for name in names}


def write_new_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def error_code(error):
    if isinstance(error, TrayProbeError):
        return error.code
    if isinstance(error, restore.GateFailure):
        return error.reason
    return type(error).__name__


class NativeTrayRun:
    def __init__(self, exe, output, timeout, *, client_size=None, client_origin=None, source_entry=None):
        self.exe, self.output = exe, output
        self.source_entry = source_entry
        self.source_entry_sha = file_sha(source_entry) if source_entry is not None else None
        self.client_size, self.client_origin = client_size, client_origin
        output.mkdir(parents=True, exist_ok=False)
        self.native = native
        self.root = None
        self.root_birth = None
        self.identities = {}
        self.hwnd = None
        self.owner = None
        self.tray = None
        self.operation_deadline = time.monotonic() + timeout
        self.port = native.available_port()
        self.debug_port = native.available_port()
        while self.debug_port == self.port:
            self.debug_port = native.available_port()
        self.helpers_before = helper_shas()
        self.exe_sha = file_sha(exe)
        self.report = {
            "status": "failed", "success": False, "scope": ["N02", "N03", "S15"],
            "data": "synthetic", "exe_sha256": self.exe_sha,
            "execution_mode": "source-diagnostic" if source_entry is not None else "packaged-product",
            "source_entry_sha256": self.source_entry_sha,
            "checker_helpers_before_sha256": self.helpers_before,
            "app_port": self.port, "debug_port": self.debug_port,
            "owned_processes": [], "tray_actions": [], "preference_roundtrips": [],
            "forced_cleanup": [], "errors": [], "needs_manual_close": False,
            "health_ready": False, "ui_connected": False, "initial_ui_ready": False,
            "on_minimize_hidden": False, "tray_restored": False,
            "gdi_restored": False, "topmost_restored": False,
            "off_minimize_visible": False, "close_cancelled": False,
            "usable_after_cancel": False, "tray_exit_clicked": False,
            "tray_exit_confirmation_observed": False, "normal_exit": False,
            "all_owned_exited": False, "app_port_released": False,
            "debug_port_released": False, "tray_icon_removed": False,
            "console_error_count": 0, "page_error_count": 0,
            "external_request_attempts_after_attach": 0,
        }
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        signatures = {
            "EnumWindows": (w.BOOL, [self.callback_type, w.LPARAM]),
            "GetWindowThreadProcessId": (w.DWORD, [w.HWND, ctypes.POINTER(w.DWORD)]),
            "GetClassNameW": (ctypes.c_int, [w.HWND, w.LPWSTR, ctypes.c_int]),
            "IsWindow": (w.BOOL, [w.HWND]), "IsWindowVisible": (w.BOOL, [w.HWND]),
            "IsIconic": (w.BOOL, [w.HWND]), "GetWindow": (w.HWND, [w.HWND, w.UINT]),
            "GetDlgItem": (w.HWND, [w.HWND, ctypes.c_int]),
            "PostMessageW": (w.BOOL, [w.HWND, w.UINT, w.WPARAM, w.LPARAM]),
            "ShowWindow": (w.BOOL, [w.HWND, ctypes.c_int]),
        }
        for name, (result, arguments) in signatures.items():
            call = getattr(self.user, name)
            call.restype, call.argtypes = result, arguments

    def phase(self, name):
        packet = {"phase": name, "root_pid": self.root.pid if self.root else None,
                  "root_birth": self.root_birth, "main_hwnd": self.hwnd,
                  "main_owner": list(self.owner) if self.owner else None,
                  "utc_epoch_seconds": time.time()}
        temporary = self.output / "phase-next.json"
        write_new_json(temporary, packet)
        os.replace(temporary, self.output / "phase.json")
        print(json.dumps(packet, ensure_ascii=True), flush=True)

    def scan(self):
        if self.root is None or self.root_birth is None:
            return set()
        if self.root.poll() is None and native.process_identity(self.root.pid) == self.root_birth:
            pairs = native.process_pairs()
            for _ in range(len(pairs)):
                before = len(self.identities)
                native.adopt_descendants(self.root.pid, self.identities, pairs, True)
                if len(self.identities) == before:
                    break
        self.report["owned_processes"] = [
            {"pid": pid, "birth": birth} for pid, birth in sorted(self.identities.items())]
        return native.valid_owned(self.identities)

    def check_running(self):
        require(time.monotonic() < self.operation_deadline, "native_tray_deadline")
        self.scan()
        require(self.root is not None and self.root.poll() is None
                and native.process_identity(self.root.pid) == self.root_birth,
                "owned_root_not_running")
        if self.hwnd is not None:
            require(native.owned_window_identity(
                self.root.pid, self.root_birth, self.hwnd, self.identities) == self.owner,
                "main_window_identity_changed")

    def wait(self, predicate, seconds, code):
        deadline = min(self.operation_deadline, time.monotonic() + seconds)
        while time.monotonic() < deadline:
            self.check_running()
            if predicate():
                return
            time.sleep(0.1)
        raise TrayProbeError(code)

    def hold(self, predicate, seconds, code):
        require(time.monotonic() + seconds < self.operation_deadline, "native_tray_deadline")
        end = time.monotonic() + seconds
        samples = 0
        while time.monotonic() < end:
            self.check_running()
            require(predicate(), code)
            samples += 1
            time.sleep(0.1)
        return samples

    def owned_port_ready(self, port):
        self.scan()
        pid = loopback_listener(port)
        if pid is None:
            return False
        require(pid in self.identities and native.process_identity(pid) == self.identities[pid],
                "loopback_listener_not_owned")
        return True

    def start(self):
        self.phase("start")
        for port in (self.port, self.debug_port):
            require(native.port_released(port), "selected_port_already_listening")
        temporary = self.output / "temporary"
        webview_data = self.output / "webview-user-data"
        temporary.mkdir()
        webview_data.mkdir()
        environment = os.environ.copy()
        environment.pop("PIXIVVAULT_PROFILE_ROOT", None)
        environment["WEBVIEW2_USER_DATA_FOLDER"] = str(webview_data)
        environment["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
            f"--remote-debugging-port={self.debug_port} --remote-debugging-address=127.0.0.1")
        environment["TEMP"], environment["TMP"] = str(temporary), str(temporary)
        # Any explicit arguments suppress native_startup's implicit user profile.
        # No Cookie, original DB, external service, or write capability is passed.
        prefix = [str(self.exe)]
        if self.source_entry is not None:
            prefix += ['-B', str(self.source_entry)]
            environment['PV_TRAY_DIAGNOSTIC_ROOT'] = str(self.output)
            environment['PYTHONPATH'] = str(Path(__file__).absolute().parents[1]/'src')
            environment['PYTHONDONTWRITEBYTECODE'] = '1'
        command = [*prefix, "--host", "127.0.0.1", "--port", str(self.port),
                   "--preferences-root", str(self.output / "preferences")]
        self.report["product_arguments"] = command[1:]
        self.root = subprocess.Popen(
            command, cwd=str(self.exe.parent), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=environment)
        self.root_birth = native.process_identity(self.root.pid)
        require(self.root_birth is not None, "root_identity_unknown")
        self.identities[self.root.pid] = self.root_birth
        self.report["root_pid"], self.report["root_birth"] = self.root.pid, self.root_birth
        self.wait(lambda: self.owned_port_ready(self.port)
                  and native.health_ready(self.port, "synthetic"), 40, "synthetic_health_timeout")
        self.report["health_ready"] = True

        def locate():
            handles = native.owned_windows(self.scan())
            require(len(handles) <= 1, "main_window_ambiguous")
            if not handles:
                return False
            self.hwnd = handles[0]
            self.owner = native.owned_window_identity(
                self.root.pid, self.root_birth, self.hwnd, self.identities)
            return True

        self.wait(locate, 25, "main_window_not_observed")
        self.wait(lambda: native.ui_ready(self.port), 20, "native_ui_not_connected")
        self.wait(lambda: self.owned_port_ready(self.debug_port), 15, "owned_cdp_port_missing")
        self.report["ui_connected"] = True
        self.report["main_hwnd"], self.report["main_owner"] = self.hwnd, list(self.owner)
        self.report["listener_owners"] = {
            "app": loopback_listener(self.port), "cdp": loopback_listener(self.debug_port)}
        if self.client_size is not None:
            self.report["initial_resize"] = resize_owned_client(
                self.root.pid, self.root_birth, self.hwnd, self.client_size, self.owner)
        if self.client_origin is not None:
            self.report["initial_placement"] = place_owned_client(
                self.root.pid, self.root_birth, self.hwnd, self.client_origin, self.owner)
        self.phase("owned_native_ready")

    def window_normal(self):
        self.check_running()
        return bool(self.user.IsWindowVisible(self.hwnd) and not self.user.IsIconic(self.hwnd))

    def minimize(self, *, hidden):
        self.check_running()
        self.user.ShowWindow(self.hwnd, 6)
        if hidden:
            predicate = lambda: not bool(self.user.IsWindowVisible(self.hwnd))
            self.wait(predicate, 8, "tray_on_did_not_hide")
            samples = self.hold(predicate, 1.0, "tray_on_hidden_state_unstable")
        else:
            predicate = lambda: bool(self.user.IsWindowVisible(self.hwnd)
                                     and self.user.IsIconic(self.hwnd))
            self.wait(predicate, 5, "tray_off_did_not_minimize_visibly")
            samples = self.hold(predicate, 1.5, "tray_off_unexpectedly_hidden")
        require(native.health_ready(self.port, "synthetic"), "health_lost_while_minimized")
        return {"hidden": hidden, "visible": bool(self.user.IsWindowVisible(self.hwnd)),
                "iconic": bool(self.user.IsIconic(self.hwnd)), "stable_samples": samples}

    def confirmations(self, *, buttons_required=True):
        if self.owner is None or native.process_identity(self.owner[0]) != self.owner[1]:
            return []
        candidates, errors = [], []

        def visit(hwnd, _):
            try:
                pid = w.DWORD()
                self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if (pid.value != self.owner[0] or not self.user.IsWindowVisible(hwnd)
                        or int(self.user.GetWindow(hwnd, 4) or 0) != self.hwnd):
                    return True
                name = ctypes.create_unicode_buffer(64)
                if self.user.GetClassNameW(hwnd, name, len(name)) <= 0 or name.value != "#32770":
                    return True
                ok, cancel = self.user.GetDlgItem(hwnd, 1), self.user.GetDlgItem(hwnd, 2)
                if (ok and cancel) or not buttons_required:
                    candidates.append({"hwnd": int(hwnd), "ok": int(ok or 0),
                                       "cancel": int(cancel or 0)})
            except Exception as error:
                errors.append(error)
                return False
            return True

        cb = self.callback_type(visit)
        okay = self.user.EnumWindows(cb, 0)
        if errors:
            raise errors[0]
        require(okay, "close_confirmation_enumeration_failed")
        require(len(candidates) <= 1, "owned_close_confirmation_ambiguous")
        return candidates

    def cancel_normal_close(self):
        self.phase("normal_close_cancel")
        require(not self.confirmations(), "unexpected_confirmation_before_normal_close")
        require(native.close_owned_window(
            self.root.pid, self.root_birth, self.hwnd, self.identities, self.owner),
            "normal_wm_close_not_sent")
        self.wait(lambda: bool(self.confirmations()), 8, "normal_close_confirmation_missing")
        pinned = self.confirmations()[0]
        self.check_running()
        require(self.confirmations() == [pinned], "close_confirmation_changed_before_cancel")
        require(self.user.PostMessageW(pinned["cancel"], 0x00F5, 0, 0),
                "owned_cancel_click_failed")
        self.wait(lambda: not self.confirmations() and self.window_normal(), 8,
                  "cancel_did_not_preserve_normal_window")
        require(native.health_ready(self.port, "synthetic"), "health_lost_after_cancel")
        self.report["close_cancelled"] = True
        self.report["normal_close_confirmation"] = {
            "hwnd": pinned["hwnd"], "owner_hwnd": self.hwnd, "owner": list(self.owner),
            "cancel_control_id": 2, "cancel_clicked": True}

    def set_tray_preference(self, page, guard, value):
        from playwright.sync_api import expect

        run = guard.run
        run(page.get_by_test_id("preferences-open").click)
        dialog = page.get_by_test_id("preferences-dialog")
        run(expect(dialog).to_be_visible)
        control = page.get_by_test_id("preference-minimize_to_tray")
        run(expect(control).to_be_visible)
        run(expect(control).to_be_enabled)
        require("q-field--disabled" not in (run(control.get_attribute, "class") or ""),
                "tray_preference_unavailable")
        previous = run(control.get_attribute, "data-value")
        if previous != value:
            run(control.click)
            run(page.get_by_role("option", name="ON" if value == "1" else "OFF", exact=True).click)
        run(expect(control).to_have_attribute, "data-value", value)
        run(page.get_by_test_id("preferences-save").click)
        run(expect(page.get_by_test_id("preferences-status")).to_have_text,
            re.compile(r"^(設定を保存しました。|設定の変更はありません。)$"))
        run(dialog.get_by_role("button", name="閉じる", exact=True).click)
        run(expect(dialog).to_be_hidden)
        # Read the persisted store through the product's real reopen path.
        run(page.get_by_test_id("preferences-open").click)
        run(expect(dialog).to_be_visible)
        run(expect(page.get_by_test_id("preference-minimize_to_tray")).to_have_attribute,
            "data-value", value)
        run(dialog.get_by_role("button", name="閉じる", exact=True).click)
        run(expect(dialog).to_be_hidden)
        self.report["preference_roundtrips"].append(
            {"before": previous, "saved": value, "reopened_value": value})

    def gdi_tray_roundtrip(self):
        self.phase("tray_on_minimize_restore_gdi")
        window = restore.Win32Window(self.root.pid, self.root_birth, self.hwnd)
        require(window.top_identity == self.owner, "gdi_main_owner_changed")
        original_topmost = window.topmost()
        report = {"capture_mode": "screen-gdi", "restore_reparent_observations": 0,
                  "baseline_stable_samples": 0, "restore_stable_samples": 0,
                  "restored_os_state": False, "additional_show_restore_after_tray": False,
                  "pixel_mean_diff_limit": float(restore.pixels.STABLE_DIFF),
                  "original_topmost": original_topmost}
        self.report["gdi"] = report
        try:
            window.set_topmost(True)
            window.activate_for_capture()
            window.prepare()
            report["window"] = window.record()
            baseline, geometry = restore._stable(
                window, min(self.operation_deadline, time.monotonic() + 10), "baseline", report)
            with (self.output / "owned-client-before.png").open("xb") as stream:
                baseline.save(stream, "PNG")
            report["baseline_geometry"] = geometry
            report["baseline_pixel_sha256"] = restore.pixels.sha(baseline)
            report["baseline_png_sha256"] = file_sha(self.output / "owned-client-before.png")
            self.report["on_minimize_observation"] = self.minimize(hidden=True)
            self.report["on_minimize_hidden"] = True
            self.tray = OwnedTrayUI(self)
            self.report["notification_icon"] = self.tray.pin_icon()
            self.report["tray_actions"].append(self.tray.select("PixivVaultを開く"))
            self.wait(self.window_normal, 8, "actual_tray_action_did_not_restore")
            self.report["tray_restored"] = True
            # Raising an already-restored owned window only makes its client
            # capturable. There is deliberately no ShowWindow(9) rescue here.
            window.activate_for_capture()
            restored, restored_geometry = restore._stable(
                window, min(self.operation_deadline, time.monotonic() + 10), "restore", report,
                expected_geometry=geometry, template=baseline)
            with (self.output / "owned-client-after-tray.png").open("xb") as stream:
                restored.save(stream, "PNG")
            report["restored_geometry"] = restored_geometry
            report["restored_pixel_sha256"] = restore.pixels.sha(restored)
            report["restored_png_sha256"] = file_sha(self.output / "owned-client-after-tray.png")
            report["restored_mean_diff"] = restore.pixels.mean_diff(restored, baseline)
            self.report["gdi_restored"] = True
        finally:
            window.set_topmost(original_topmost)
            self.report["topmost_restored"] = True

    def inspect(self):
        from playwright.sync_api import expect, sync_playwright

        with sync_playwright() as playwright:
            self.check_running()
            require(self.owned_port_ready(self.debug_port), "cdp_listener_owner_lost")
            browser = playwright.chromium.connect_over_cdp(
                f"http://127.0.0.1:{self.debug_port}",
                timeout=min(8000, max(1, int((self.operation_deadline-time.monotonic())*1000))))
            pages = []
            deadline = min(self.operation_deadline, time.monotonic() + 20)
            while time.monotonic() < deadline:
                self.check_running()
                pages = [page for context in browser.contexts for page in context.pages
                         if native.matching_app_page(page.url, self.port)]
                if len(pages) == 1:
                    break
                candidates = [page for context in browser.contexts for page in context.pages]
                if candidates:
                    # Pump sync Playwright's pending navigation events. Never
                    # navigate or replace the naturally opened native target.
                    candidates[0].wait_for_timeout(150)
                else:
                    session = browser.new_browser_cdp_session()
                    try:
                        session.send('Target.getTargets')
                    finally:
                        session.detach()
            require(len(pages) == 1, "owned_webview_page_not_unique")
            page = pages[0]
            require(native.matching_app_page(page.evaluate('window.location.href'), self.port),
                    "owned_webview_location_mismatch")
            self.report["startup_navigation_injected"] = False
            guard = NativeUiGuard(page, self.port, self.operation_deadline, self.check_running)

            def observe_request(request):
                parsed = urlsplit(request.url)
                if not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
                        and parsed.port == self.port and not parsed.username and not parsed.password):
                    self.report["external_request_attempts_after_attach"] += 1

            # Passive observation only: route interception would stall browser
            # requests while this synchronous checker is using Win32/UIA/GDI.
            # Explicit demo arguments and a fresh WebView profile select the
            # loopback synthetic product path, with no real credentials/data.
            page.on("request", observe_request)
            page.on("console", lambda message: self.report.__setitem__(
                "console_error_count", self.report["console_error_count"]+1)
                if message.type == "error" else None)
            page.on("pageerror", lambda _error: self.report.__setitem__(
                "page_error_count", self.report["page_error_count"]+1))
            self.report["webview_version"] = browser.version
            self.report["viewport"] = guard.run(page.evaluate, """() => ({
              width: innerWidth, height: innerHeight, device_pixel_ratio: devicePixelRatio
            })""", with_timeout=False)

            def ready():
                value = guard.run(page.evaluate, """() => {
                  const images = [...document.querySelectorAll('.pv-card img')].filter(img => {
                    const r = img.getBoundingClientRect();
                    return r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0
                           && r.top < innerHeight && r.left < innerWidth;
                  });
                  return window.did_handshake === true &&
                    document.querySelectorAll('.pv-card').length === 50 &&
                    !!(window.pywebview && window.pywebview.api) &&
                    images.length > 0 && images.every(img => img.complete && img.naturalWidth > 0
                      && new URL(img.currentSrc || img.src, location.href).origin === location.origin
                      && new URL(img.currentSrc || img.src, location.href).pathname.startsWith('/media/demo-'));
                }""", with_timeout=False)
                return value is True

            self.wait(ready, 20, "synthetic_webview_not_ready")
            self.report["initial_ui_ready"] = True
            self.set_tray_preference(page, guard, "1")
            self.wait(ready, 10, "synthetic_scene_not_ready")
            self.gdi_tray_roundtrip()
            guard.check()

            self.phase("tray_preference_off")
            self.set_tray_preference(page, guard, "0")
            self.report["off_minimize_observation"] = self.minimize(hidden=False)
            self.report["off_minimize_visible"] = True
            self.check_running()
            self.user.ShowWindow(self.hwnd, 9)  # Ordinary OS restore in the OFF case only.
            self.wait(self.window_normal, 8, "tray_off_os_restore_failed")

            self.cancel_normal_close()
            guard.run(page.locator(".pv-card").first.click)
            viewer = page.get_by_test_id("gallery-viewer")
            guard.run(expect(viewer).to_be_visible)
            image = page.get_by_test_id("viewer-image").locator("img:visible")
            decoded = guard.decode(image)
            require(urlsplit(decoded["src"]).path.startswith("/media/demo-"),
                    "post_cancel_viewer_not_synthetic")
            guard.run(viewer.get_by_role("button", name="閉じる", exact=True).click)
            guard.run(expect(viewer).to_be_hidden)
            self.report["usable_after_cancel"] = True

            self.set_tray_preference(page, guard, "1")
            self.minimize(hidden=True)
            require(not self.confirmations(buttons_required=False),
                    "confirmation_present_before_tray_exit")
            self.phase("actual_tray_exit")
            self.scan()
            self.report["tray_actions"].append(self.tray.select("終了"))
            self.report["tray_exit_clicked"] = True
            self.wait_for_tray_exit()
            # Context exit disconnects our CDP client. Do not close browser/context.

    def wait_for_tray_exit(self):
        deadline = min(self.operation_deadline, time.monotonic() + 35)
        while time.monotonic() < deadline:
            self.scan()
            if self.confirmations(buttons_required=False):
                self.report["tray_exit_confirmation_observed"] = True
                raise TrayProbeError("tray_exit_displayed_close_confirmation")
            alive = native.living_pids(dict(self.identities))
            if self.root.poll() is not None and not alive:
                break
            time.sleep(0.1)
        self.report["all_owned_exited"] = not native.living_pids(dict(self.identities))
        self.report["root_exit_code"] = self.root.poll()
        self.report["normal_exit"] = (
            self.report["all_owned_exited"] and self.report["root_exit_code"] == 0)
        require(self.report["normal_exit"], "tray_exit_not_normal_or_owned_child_remained")
        for key, port in (("app_port_released", self.port), ("debug_port_released", self.debug_port)):
            self.report[key] = native.wait_until(lambda port=port: native.port_released(port), deadline)
            require(self.report[key], key + "_timeout")
        self.report["tray_icon_removed"] = native.wait_until(self.tray.removed, deadline)
        require(self.report["tray_icon_removed"], "owned_tray_icon_remained_after_exit")

    def cleanup(self):
        """Failure cleanup is an owned ordinary close, never force termination."""
        if self.root is None:
            return
        self.scan()
        alive = native.living_pids(dict(self.identities))
        if not alive:
            return
        self.report["failure_cleanup_attempted"] = True
        deadline = time.monotonic() + 25
        # Do not turn this cleanup into a tray success observation.
        if self.hwnd is None and self.root.poll() is None:
            handles = native.owned_windows(self.scan())
            if len(handles) == 1:
                self.hwnd = handles[0]
                self.owner = native.owned_window_identity(
                    self.root.pid, self.root_birth, self.hwnd, self.identities)
        if self.hwnd is not None and self.owner is not None and self.root.poll() is None:
            prompts = self.confirmations()
            if not prompts and not self.user.IsWindowVisible(self.hwnd):
                # Failure-only recovery: show the exact tested window before
                # WM_CLOSE so WinForms can own its confirmation dialog. This
                # never counts as a successful tray restoration.
                require(native.owned_window_identity(self.root.pid, self.root_birth,
                    self.hwnd, self.identities) == self.owner, "cleanup_window_owner_changed")
                self.user.ShowWindow(self.hwnd, 9)
                self.user.ShowWindow(self.hwnd, 5)
                window = restore.Win32Window(self.root.pid, self.root_birth, self.hwnd)
                require(window.top_identity == self.owner, "cleanup_window_owner_changed")
                self.report["cleanup_show_owned_window"] = window.activate_for_capture()
            sent = bool(prompts) or native.close_owned_window(
                self.root.pid, self.root_birth, self.hwnd, self.identities, self.owner)
            self.report["cleanup_normal_close_sent"] = sent
            if sent:
                self.report["cleanup_own_confirmation_accepted"] = (
                    native.acknowledge_owned_close_confirmation(
                        self.owner, self.hwnd, min(deadline, time.monotonic()+8)))

        def exited():
            self.scan()
            return not native.living_pids(dict(self.identities))

        self.report["cleanup_all_owned_exited"] = native.wait_until(exited, deadline)
        self.report["needs_manual_close"] = not self.report["cleanup_all_owned_exited"]
        self.report["remaining_owned_pids"] = sorted(native.living_pids(dict(self.identities)))
        self.report["cleanup_app_port_released"] = native.port_released(self.port)
        self.report["cleanup_debug_port_released"] = native.port_released(self.debug_port)

    def finish(self):
        try:
            self.cleanup()
        except Exception as error:
            self.report["errors"].append("cleanup_" + error_code(error))
            self.report["needs_manual_close"] = True
        self.report["observed_root_exit_code"] = self.root.poll() if self.root else None
        try:
            after = helper_shas()
            self.report["checker_helpers_after_sha256"] = after
            self.report["checker_helpers_changed"] = after != self.helpers_before
            self.report["exe_changed"] = file_sha(self.exe) != self.exe_sha
            if self.source_entry is not None:
                self.report['source_entry_changed'] = file_sha(self.source_entry) != self.source_entry_sha
                if self.report['source_entry_changed']:
                    self.report['errors'].append('source_diagnostic_entry_changed')
            if self.report["checker_helpers_changed"] or self.report["exe_changed"]:
                self.report["errors"].append("acceptance_inputs_changed")
        except Exception as error:
            self.report["errors"].append("input_recheck_" + error_code(error))
        required = (
            "health_ready", "ui_connected", "initial_ui_ready", "on_minimize_hidden",
            "tray_restored", "gdi_restored", "topmost_restored", "off_minimize_visible",
            "close_cancelled", "usable_after_cancel", "tray_exit_clicked",
            "normal_exit", "all_owned_exited", "app_port_released",
            "debug_port_released", "tray_icon_removed",
        )
        self.report["success"] = (
            all(self.report[key] is True for key in required)
            and not self.report["tray_exit_confirmation_observed"]
            and not self.report["external_request_attempts_after_attach"]
            and not self.report["console_error_count"] and not self.report["page_error_count"]
            and not self.report["forced_cleanup"] and not self.report["errors"]
            and not self.report["needs_manual_close"])
        self.report["status"] = "passed" if self.report["success"] else "failed"
        write_new_json(self.output / "result.json", self.report)
        print(json.dumps({"status": self.report["status"], "errors": self.report["errors"],
                          "forced_cleanup": 0,
                          "needs_manual_close": self.report["needs_manual_close"]}), flush=True)
        return 0 if self.report["success"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", required=True, type=Path)
    parser.add_argument('--source-entry', type=Path,
                        help='Explicit artifacts-only diagnostic entry; --exe is its Python interpreter')
    parser.add_argument("--output", required=True, type=Path, help="Fresh run directory")
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--client-size", nargs=2, type=int, metavar=("WIDTH", "HEIGHT"))
    parser.add_argument("--client-origin", nargs=2, type=int, metavar=("X", "Y"))
    args = parser.parse_args(argv)
    if os.name != "nt":
        parser.error("This checker requires an interactive Windows desktop.")
    if not 90 <= args.timeout <= 600:
        parser.error("--timeout must be 90..600 seconds.")
    exe = Path(os.path.abspath(args.exe))
    output = Path(os.path.abspath(args.output))
    reject_links(exe)
    reject_links(output)
    require(exe.is_file() and exe.suffix.lower() == ".exe", "packaged_executable_missing")
    require(not output.exists() and output != exe, "output_must_be_fresh")
    source_entry = None
    if args.source_entry is not None:
        source_entry = args.source_entry.absolute()
        reject_links(source_entry)
        require(source_entry.is_file() and source_entry.suffix == '.py'
                and source_entry.is_relative_to(Path(__file__).absolute().parents[1]/'artifacts'),
                'source_diagnostic_must_be_owned_artifact')
    restore.ensure_physical_dpi()
    run = NativeTrayRun(exe, output, args.timeout, client_size=args.client_size,
                        client_origin=args.client_origin, source_entry=source_entry)
    try:
        run.start()
        run.inspect()
    except Exception as error:
        run.report["errors"].append(error_code(error))
    return run.finish()


if __name__ == "__main__":
    raise SystemExit(main())
