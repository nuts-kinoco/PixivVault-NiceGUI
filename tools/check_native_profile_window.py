"""No-argument native profile acceptance with real, operator-driven OS pickers.

Uses a fresh synthetic profile and an empty synthetic Cookie, never credentials.
The operator enters only the supplied paths in the observed owned OS pickers.
The product receives no arguments and no callbacks are replaced.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import time
import urllib.request

if __package__:
    from . import check_native_window as native
else:
    import check_native_window as native

REPO = Path(__file__).resolve().parents[1]
APP_PORT = 8089
PICKER_SECONDS = 120.0
CLOSE_SECONDS = 35.0
FIRST_HEALTH = {"status": "ok", "mode": "demo", "data": "synthetic"}
RESTART_HEALTH = {"status": "configuration-required", "mode": "configuration", "data": "none"}


class ProbeError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def error_code(error):
    return error.code if isinstance(error, ProbeError) else type(error).__name__


def require(condition, code):
    if not condition:
        raise ProbeError(code)


def reject_links(path):
    for component in (path, *path.parents):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ProbeError("path_contains_reparse_or_symlink")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_new_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def update_phase(path, value):
    temporary = path.with_name("phase.next.json")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def require_unused_app_port():
    require(native.port_released(APP_PORT), "app_port_already_listening")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind(("127.0.0.1", APP_PORT))
    except OSError:
        raise ProbeError("app_port_already_bound") from None


def read_health():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{APP_PORT}/healthz", timeout=0.6) as response:
            if response.status != 200:
                return None
            body = json.load(response)
            return body if type(body) is dict else None
    except (OSError, ValueError, TimeoutError):
        return None


def source_signature(cookie):
    reject_links(cookie)
    require(cookie.is_file(), "synthetic_cookie_missing")
    info = cookie.lstat()
    require(info.st_nlink == 1, "synthetic_cookie_is_hardlinked")
    require(sorted(p.name for p in cookie.parent.iterdir()) == [cookie.name],
            "synthetic_input_directory_changed")
    return {"size": info.st_size, "mtime_ns": info.st_mtime_ns, "sha256": sha256(cookie)}


def profile_snapshot(profile, cookie, save_parent):
    from pixivvault_nicegui.native_profile import NativeProfileStore
    snapshot = NativeProfileStore.peek(profile)
    require(snapshot.revision == 1, "unexpected_profile_revision")
    require(snapshot.cookie_file == cookie, "persisted_cookie_reference_mismatch")
    require(snapshot.save_parent == save_parent, "persisted_save_parent_mismatch")
    require(snapshot.library_readonly_db is None and snapshot.library_image_root is None,
            "unexpected_readonly_library_reference")
    return {"revision": snapshot.revision, "cookie_file": str(snapshot.cookie_file),
            "save_parent": str(snapshot.save_parent), "library_readonly_db": None,
            "library_image_root": None}


def verify_restart_storage(save_parent):
    from pixivvault_nicegui.native_profile import STORAGE_FOLDER, STORAGE_IDENTITY, STORAGE_MARKER
    # Empty Cookie is rejected before opener/get_services; only the parent marker
    # is provisioned by bootstrap before the configuration repair page.
    reject_links(save_parent)
    require(sorted(p.name for p in save_parent.iterdir()) == [STORAGE_FOLDER],
            "unexpected_restart_storage_entries")
    storage = save_parent / STORAGE_FOLDER
    reject_links(storage)
    require(storage.is_dir(), "restart_storage_directory_missing")
    require(sorted(p.name for p in storage.iterdir()) == [STORAGE_MARKER],
            "restart_created_more_than_storage_marker")
    marker = storage / STORAGE_MARKER
    reject_links(marker)
    require(marker.is_file(), "restart_storage_marker_missing")
    require(marker.lstat().st_nlink == 1, "restart_storage_marker_hardlinked")
    require(marker.read_bytes() == STORAGE_IDENTITY, "storage_marker_mismatch")
    return {"only_owned_storage_marker": True, "marker_sha256": sha256(marker)}


class NativeRun:
    def __init__(self, *, name, exe, output, profile, cookie, save_parent, timeout, used_debug_ports):
        self.name, self.exe, self.output = name, exe, output
        self.profile, self.cookie, self.save_parent = profile, cookie, save_parent
        self.used_debug_ports = used_debug_ports
        output.mkdir(exist_ok=False)
        self.webview_data = output / "webview-user-data"
        self.webview_data.mkdir(exist_ok=False)
        self.root = None
        self.root_birth = None
        self.identities = {}
        self.hwnd = None
        self.owner = None
        self.active_picker = None
        self.debug_port = None
        self.operation_deadline = time.monotonic() + timeout
        self.log_path = output / "process.log"
        self.report = {
            "status": "failed", "run": name, "product_arguments": [],
            "app_port": APP_PORT, "debug_port": None, "profile_root": str(profile),
            "webview_user_data": str(self.webview_data),
            "temp_directory": str(output.parent / "temp"),
            "root_pid": None, "root_birth": None, "owned_processes": [],
            "main_hwnd": None, "main_owner": None, "pickers": [],
            "auth_button_clicked": False, "health_ready": False, "ui_passed": False,
            "page_error_count": 0, "restore_gate": None, "wm_close_sent": False,
            "close_confirmation_accepted": False, "all_owned_exited": False,
            "normal_exit": False, "app_port_released": False, "debug_port_released": False,
            "forced_cleanup": [], "needs_manual_close": False, "errors": [],
        }
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        self.user.EnumWindows.argtypes = [self.callback_type, w.LPARAM]
        self.user.EnumWindows.restype = w.BOOL
        self.user.IsWindow.argtypes = [w.HWND]
        self.user.IsWindow.restype = w.BOOL
        self.user.IsWindowVisible.argtypes = [w.HWND]
        self.user.IsWindowVisible.restype = w.BOOL
        self.user.GetWindow.argtypes = [w.HWND, w.UINT]
        self.user.GetWindow.restype = w.HWND
        self.user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        self.user.GetWindowThreadProcessId.restype = w.DWORD
        self.user.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
        self.user.GetClassNameW.restype = ctypes.c_int

    def phase(self, phase, **details):
        packet = {"run": self.name, "phase": phase, "utc_epoch_seconds": time.time(),
                  "phase_file": str(self.output / "phase.json"),
                  "root_pid": self.report["root_pid"], "root_birth": self.root_birth,
                  "main_hwnd": self.hwnd, "main_owner": list(self.owner) if self.owner else None,
                  **details}
        update_phase(self.output / "phase.json", packet)
        print(json.dumps(packet, ensure_ascii=True), flush=True)

    def scan(self):
        if self.root is None or self.root_birth is None:
            return set()
        root_alive = self.root.poll() is None and native.process_identity(self.root.pid) == self.root_birth
        if root_alive:
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
        require(self.root is not None, "root_not_started")
        require(self.root.poll() is None, "root_exited_during_operation")
        require(self.root_birth is not None and native.process_identity(self.root.pid) == self.root_birth,
                "root_identity_changed")
        self.scan()

    def check_owner(self):
        self.check_running()
        require(self.hwnd is not None and self.owner is not None, "window_not_pinned")
        actual = native.owned_window_identity(self.root.pid, self.root_birth, self.hwnd, self.identities)
        require(actual == self.owner, "main_window_identity_changed")

    def wait(self, predicate, seconds, code):
        deadline = min(self.operation_deadline, time.monotonic() + seconds)
        def checked():
            self.check_running()
            return predicate()
        require(native.wait_until(checked, deadline), code)

    def pin_window(self, seconds=20.0):
        def locate():
            handles = native.owned_windows(self.scan())
            require(len(handles) <= 1, "ambiguous_owned_top_windows")
            if not handles:
                return False
            candidate = handles[0]
            owner = native.owned_window_identity(self.root.pid, self.root_birth, candidate, self.identities)
            self.hwnd, self.owner = candidate, owner
            self.report["main_hwnd"] = candidate
            self.report["main_owner"] = {"pid": owner[0], "birth": owner[1]}
            return True
        self.wait(locate, seconds, "native_window_timeout")

    def modal_info(self, hwnd, live):
        if hwnd == self.hwnd or not self.user.IsWindowVisible(hwnd):
            return None
        pid = w.DWORD()
        if not self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
            return None
        if pid.value not in live or not self.user.GetWindow(hwnd, 4):
            return None
        chain, seen, current = [], set(), hwnd
        while current:
            require(current not in seen, "owner_chain_cycle")
            require(len(chain) < 32, "owner_chain_too_long")
            seen.add(current)
            current_pid = w.DWORD()
            if not self.user.GetWindowThreadProcessId(current, ctypes.byref(current_pid)):
                return None
            if current_pid.value not in live:
                return None
            identity = native.owned_window_identity(self.root.pid, self.root_birth, current, self.identities)
            chain.append({"hwnd": int(current), "pid": identity[0], "birth": identity[1]})
            if current == self.hwnd:
                require(identity == self.owner, "modal_main_owner_changed")
                name = ctypes.create_unicode_buffer(256)
                require(self.user.GetClassNameW(hwnd, name, len(name)) > 0, "modal_class_unavailable")
                return {"hwnd": int(hwnd), "pid": chain[0]["pid"], "birth": chain[0]["birth"],
                        "window_class": name.value, "gw_owner_chain": chain}
            current = int(self.user.GetWindow(current, 4) or 0)
        return None

    def modals(self):
        self.check_owner()
        live, found, errors = self.scan(), [], []
        def visit(hwnd, _):
            try:
                item = self.modal_info(int(hwnd), live)
                if item is not None:
                    found.append(item)
                return True
            except Exception as error:
                errors.append(error)
                return False
        callback = self.callback_type(visit)
        ok = self.user.EnumWindows(callback, 0)
        if errors:
            raise errors[0]
        require(bool(ok), "modal_enumeration_failed")
        self.check_owner()
        return found

    def verify_picker(self, expected):
        current = next((item for item in self.modals() if item["hwnd"] == expected["hwnd"]), None)
        if current is None:
            return False
        require(current == expected, "picker_identity_or_owner_chain_changed")
        return True

    def choose_with_real_picker(self, page, guard, *, kind, button, label, target):
        before = self.modals()
        require(not before, "modal_present_before_picker")
        before_handles = {item["hwnd"] for item in before}
        self.phase("opening_" + kind + "_picker", expected_path=str(target))
        guard.run(page.get_by_test_id(button).click, timeout=8000)
        observed = []
        def find_new_modal():
            nonlocal observed
            observed = [item for item in self.modals() if item["hwnd"] not in before_handles]
            if len(observed) > 1:
                self.phase("ambiguous_" + kind + "_picker", picker_candidates=observed)
                raise ProbeError("ambiguous_new_owned_picker")
            return len(observed) == 1
        self.wait(find_new_modal, 20.0, "owned_picker_not_observed")
        pinned = observed[0]
        self.active_picker = pinned
        record = {"kind": kind, "new_unique_owned_modal": True, "expected_path": str(target),
                  "modal": pinned, "selection_returned": False}
        self.report["pickers"].append(record)
        self.phase("await_" + kind + "_picker_input", expected_path=str(target),
                   maximum_wait_seconds=PICKER_SECONDS, picker=pinned)
        started = time.monotonic()
        deadline = min(self.operation_deadline, started + PICKER_SECONDS)
        closed_since = None
        value = page.get_by_test_id(label)
        while time.monotonic() < deadline:
            guard.check()
            visible = self.verify_picker(pinned)
            text = guard.run(value.inner_text, timeout=1000)
            if text == str(target) and not visible:
                require(not self.modals(), "modal_remained_after_selection")
                record["selection_returned"] = True
                record["elapsed_seconds"] = time.monotonic() - started
                self.active_picker = None
                self.phase(kind + "_picker_selection_verified")
                return
            if visible:
                closed_since = None
            elif closed_since is None:
                closed_since = time.monotonic()
            elif time.monotonic() - closed_since > 10:
                raise ProbeError("picker_closed_without_expected_selection")
            time.sleep(0.15)
        raise ProbeError(kind + "_picker_input_timeout")

    def inspect_ui(self):
        from playwright.sync_api import expect, sync_playwright
        self.phase("attach_owned_webview")
        with sync_playwright() as playwright:
            self.check_owner()
            browser = playwright.chromium.connect_over_cdp(
                f"http://127.0.0.1:{self.debug_port}", timeout=5000)
            # Context manager disconnects CDP; never close the actual WebView.
            pages = []
            def find_page():
                nonlocal pages
                pages = [page for context in browser.contexts for page in context.pages
                         if native.matching_app_page(page.url, APP_PORT)]
                require(len(pages) <= 1, "ambiguous_native_webview_pages")
                return len(pages) == 1
            self.wait(find_page, 15.0, "owned_webview_page_timeout")
            page = pages[0]
            def page_error(_error):
                self.report["page_error_count"] += 1
            page.on("pageerror", page_error)
            guard = native.NativeUiGuard(page, APP_PORT, self.operation_deadline, self.check_owner)
            def visible(test_id):
                guard.run(expect(page.get_by_test_id(test_id)).to_be_visible, timeout=8000)
            def references():
                guard.run(expect(page.get_by_test_id("native-profile-cookie-value")).to_have_text,
                          str(self.cookie), timeout=8000)
                guard.run(expect(page.get_by_test_id("native-profile-folder-value")).to_have_text,
                          str(self.save_parent), timeout=8000)
            if self.name == "first":
                visible("preferences-open")
                guard.run(page.get_by_test_id("preferences-open").click, timeout=8000)
                visible("native-profile-dialog")
                for test_id in ("native-profile-cookie-value", "native-profile-folder-value"):
                    guard.run(expect(page.get_by_test_id(test_id)).to_have_text, "未設定", timeout=8000)
                self.choose_with_real_picker(page, guard, kind="cookie",
                    button="native-profile-pick-cookie", label="native-profile-cookie-value", target=self.cookie)
                self.choose_with_real_picker(page, guard, kind="folder",
                    button="native-profile-pick-folder", label="native-profile-folder-value", target=self.save_parent)
                references()
                self.phase("save_selected_references")
                guard.run(page.get_by_test_id("native-profile-save").click, timeout=8000)
                for text in ("保存しました。", "次回起動"):
                    guard.run(expect(page.get_by_test_id("native-profile-status")).to_contain_text,
                              text, timeout=8000)
                guard.run(page.get_by_test_id("native-profile-cancel").click, timeout=8000)
                guard.run(expect(page.get_by_test_id("native-profile-dialog")).not_to_be_visible, timeout=8000)
                guard.run(page.get_by_test_id("preferences-open").click, timeout=8000)
                visible("native-profile-dialog")
                references()
                self.report["save_and_reopen_references"] = True
            else:
                visible("native-profile-recovery")
                references()
                self.report["configuration_recovery_references"] = True
            require(not self.modals(), "modal_present_before_webview_capture")
            screenshot = self.output / "references.png"
            require(not screenshot.exists(), "references_screenshot_exists")
            guard.run(page.screenshot, path=str(screenshot), full_page=False, timeout=8000)
            self.report["references_screenshot"] = str(screenshot)
            if self.name == "first":
                guard.run(page.get_by_test_id("native-profile-cancel").click, timeout=8000)
                guard.run(expect(page.get_by_test_id("native-profile-dialog")).not_to_be_visible, timeout=8000)
            guard.check()
            require(self.report["page_error_count"] == 0, "webview_page_error")
            self.report["ui_passed"] = True
            self.phase("profile_ui_verified")

    def all_exited(self):
        self.scan()
        return not native.living_pids(dict(self.identities))

    def close_normally(self):
        if self.root is None:
            return
        self.phase("normal_close")
        close_deadline = time.monotonic() + CLOSE_SECONDS
        try:
            if self.root.poll() is None:
                if self.hwnd is None:
                    previous_deadline = self.operation_deadline
                    try:
                        self.operation_deadline = time.monotonic() + 5
                        self.pin_window(5)
                    finally:
                        self.operation_deadline = previous_deadline
                self.check_owner()
                if self.active_picker is not None:
                    pinned = self.active_picker
                    if self.verify_picker(pinned):
                        self.report["error_cleanup_cancelled_picker"] = True
                        sent = native.close_owned_window(self.root.pid, self.root_birth,
                            pinned["hwnd"], self.identities, (pinned["pid"], pinned["birth"]))
                        require(sent, "owned_picker_cancel_failed")
                        require(native.wait_until(lambda: not self.verify_picker(pinned),
                            min(close_deadline, time.monotonic() + 5)), "owned_picker_did_not_close")
                    self.active_picker = None
                require(not self.modals(), "unobserved_modal_blocks_close")
                self.report["wm_close_sent"] = native.close_owned_window(
                    self.root.pid, self.root_birth, self.hwnd, self.identities, self.owner)
                require(self.report["wm_close_sent"], "owned_wm_close_failed")
                self.report["close_confirmation_accepted"] = native.acknowledge_owned_close_confirmation(
                    self.owner, self.hwnd, min(close_deadline, time.monotonic() + 8))
                require(self.report["close_confirmation_accepted"], "owned_close_confirmation_not_accepted")
        except Exception as error:
            self.report["errors"].append("close_" + error_code(error))
        try:
            self.report["all_owned_exited"] = native.wait_until(self.all_exited, close_deadline)
            self.report["app_port_released"] = native.wait_until(
                lambda: native.port_released(APP_PORT), close_deadline)
            self.report["debug_port_released"] = self.debug_port is not None and native.wait_until(
                lambda: native.port_released(self.debug_port), close_deadline)
            code = self.root.poll()
            self.report["root_exit_code"] = code
            self.report["normal_exit"] = self.report["all_owned_exited"] and code == 0
            remaining = sorted(native.living_pids(dict(self.identities)))
            self.report["remaining_owned_pids"] = remaining
            self.report["needs_manual_close"] = self.root.poll() is None or bool(remaining)
        except Exception as error:
            self.report["errors"].append("exit_" + error_code(error))
            self.report["needs_manual_close"] = True
        # No forced cleanup. Unresolved owned processes require observed normal close.

    def run(self):
        try:
            require_unused_app_port()
            for _ in range(10):
                candidate = native.available_port()
                if candidate != APP_PORT and candidate not in self.used_debug_ports:
                    self.debug_port = candidate
                    break
            require(self.debug_port is not None, "fresh_debug_port_unavailable")
            self.used_debug_ports.add(self.debug_port)
            self.report["debug_port"] = self.debug_port
            owned_temp = self.output.parent / "temp"
            reject_links(owned_temp)
            require(owned_temp.is_dir(), "owned_temp_directory_missing")
            child_env = os.environ.copy()
            child_env.update(
                PIXIVVAULT_PROFILE_ROOT=str(self.profile), WEBVIEW2_USER_DATA_FOLDER=str(self.webview_data),
                WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=(
                    f"--remote-debugging-port={self.debug_port} --remote-debugging-address=127.0.0.1"),
                TEMP=str(owned_temp), TMP=str(owned_temp), TMPDIR=str(owned_temp),
                PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
            self.phase("launching_without_product_arguments")
            with self.log_path.open("xb") as log:
                self.root = subprocess.Popen([str(self.exe)], cwd=str(self.exe.parent), env=child_env,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            self.report["root_pid"] = self.root.pid
            self.root_birth = native.process_identity(self.root.pid)
            require(self.root_birth is not None, "root_identity_unavailable")
            self.identities[self.root.pid] = self.root_birth
            self.report["root_birth"] = self.root_birth
            expected = FIRST_HEALTH if self.name == "first" else RESTART_HEALTH
            self.report["expected_health"] = expected
            self.wait(lambda: read_health() == expected, 30.0, "expected_profile_health_timeout")
            self.report["health_ready"] = True
            self.report["observed_health"] = read_health()
            require(self.report["observed_health"] == expected, "profile_health_changed")
            self.pin_window()
            self.wait(lambda: native.ui_ready(APP_PORT), 20.0, "native_websocket_timeout")
            self.inspect_ui()
            require(not self.modals(), "modal_present_before_restore")
            self.phase("restore_gdi_acceptance")
            self.report["restore_gate"] = native.run_restore_gate(
                self.root.pid, self.root_birth, self.hwnd, self.output / "result.json",
                self.operation_deadline, native.PHASE_LIMITS, expected_top_identity=self.owner)
            require(self.report["restore_gate"].get("passed") is True, "native_restore_gate_failed")
        except Exception as error:
            self.report["errors"].append(error_code(error))
        finally:
            self.close_normally()
            if self.log_path.exists():
                failures = native.log_failures(self.log_path.read_text(encoding="utf-8", errors="replace"))
                self.report["process_log_failures"] = failures
                if failures:
                    self.report["errors"].append("process_log_failure")
            passed = (self.report["health_ready"] and self.report["ui_passed"]
                and (self.report["restore_gate"] or {}).get("passed") is True
                and self.report["wm_close_sent"] and self.report["close_confirmation_accepted"]
                and self.report["normal_exit"] and self.report["app_port_released"]
                and self.report["debug_port_released"] and not self.report["needs_manual_close"]
                and not self.report["errors"])
            self.report["status"] = "passed" if passed else "failed"
            write_new_json(self.output / "result.json", self.report)
            self.phase("run_finished", status=self.report["status"],
                result_file=str(self.output / "result.json"),
                needs_manual_close=self.report["needs_manual_close"],
                remaining_owned_pids=self.report.get("remaining_owned_pids", []),
                errors=self.report["errors"])
        return self.report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=360.0)
    args = parser.parse_args(argv)
    if os.name != "nt":
        parser.error("Windows only")
    if not 60 <= args.timeout <= 600:
        parser.error("--timeout must be within 60..600 seconds")
    exe, output = args.exe, args.output_root
    try:
        require(exe.is_absolute(), "absolute_exe_path_required")
        require(output.is_absolute(), "absolute_output_path_required")
        require(".." not in output.parts, "parent_reference_in_output")
        reject_links(exe)
        reject_links(output)
        exe, output = exe.resolve(), output.resolve()
        artifacts = (REPO / "artifacts").resolve()
        require(exe.is_file(), "exe_missing")
        require(output != artifacts and output.is_relative_to(artifacts),
                "output_must_be_inside_this_repository_artifacts")
        require(output.parent.is_dir(), "output_parent_must_exist")
        require(not output.exists(), "output_already_exists")
        output.mkdir(exist_ok=False)
    except Exception as error:
        print(json.dumps({"status": "failed", "phase": "preflight", "error": error_code(error)}), flush=True)
        return 2
    overall = {"status": "failed", "acceptance": "synthetic-profile-real-native-pickers-and-restart",
        "exe": str(exe), "product_arguments": [], "app_port": APP_PORT,
        "auth_button_clicked": False, "real_cookie_used": False,
        "first": None, "restart": None, "errors": []}
    try:
        profile = output / "profile"
        owned_temp = output / "temp"
        owned_temp.mkdir(exist_ok=False)
        overall["temp_directory"] = str(owned_temp)
        inputs = output / "synthetic-input"
        inputs.mkdir(exist_ok=False)
        cookie = inputs / "synthetic-invalid-cookie.txt"
        with cookie.open("xb"):
            pass
        save_parent = output / "save-parent"
        save_parent.mkdir(exist_ok=False)
        require(not profile.exists(), "profile_was_precreated")
        original_source = source_signature(cookie)
        require(original_source["size"] == 0, "synthetic_cookie_not_empty")
        original_exe_sha = sha256(exe)
        overall["exe_sha256"] = original_exe_sha
        overall["synthetic_source_before"] = original_source
        used_debug_ports = set()
        first = NativeRun(name="first", exe=exe, output=output / "first", profile=profile,
            cookie=cookie, save_parent=save_parent, timeout=args.timeout,
            used_debug_ports=used_debug_ports).run()
        overall["first"] = first
        require(first["status"] == "passed", "first_native_run_failed")
        require(source_signature(cookie) == original_source, "source_changed_after_first_run")
        require(not list(save_parent.iterdir()), "save_created_storage_early")
        first_snapshot = profile_snapshot(profile, cookie, save_parent)
        overall["snapshot_after_first"] = first_snapshot
        overall["save_parent_empty_after_first"] = True
        require(sha256(exe) == original_exe_sha, "exe_changed_before_restart")
        restart = NativeRun(name="restart", exe=exe, output=output / "restart", profile=profile,
            cookie=cookie, save_parent=save_parent, timeout=args.timeout,
            used_debug_ports=used_debug_ports).run()
        overall["restart"] = restart
        require(restart["status"] == "passed", "restart_native_run_failed")
        second_snapshot = profile_snapshot(profile, cookie, save_parent)
        require(second_snapshot == first_snapshot, "profile_changed_on_restart")
        overall["snapshot_after_restart"] = second_snapshot
        overall["restart_storage"] = verify_restart_storage(save_parent)
        require(source_signature(cookie) == original_source, "source_changed_after_restart")
        require(sha256(exe) == original_exe_sha, "exe_changed_after_restart")
        overall["synthetic_source_unchanged"] = True
        overall["profile_references_and_revision_preserved"] = True
        overall["status"] = "passed"
    except Exception as error:
        overall["errors"].append(error_code(error))
    finally:
        write_new_json(output / "result.json", overall)
        print(json.dumps({"phase": "profile_native_acceptance_finished",
            "status": overall["status"], "result_file": str(output / "result.json"),
            "errors": overall["errors"]}), flush=True)
    return 0 if overall["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
