"""N09/N10: one private static-work bridge save, disabled restart, owned port conflict.

Only the existing packaged CLI is used. The supplied Cookie and previously
accepted one-work managed root are read-only. Pixiv writes, real image capture,
URI registration, installed-extension certification and automatic POST retries
are outside this checker. Product data is private; reports contain counts,
hashes, fixed labels and owned process identities only.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import stat
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from tools import check_native_window as native
from tools.check_native_tray_window import (
    NativeTrayRun, file_sha, helper_shas, loopback_listener, write_new_json,
)
from tools.check_managed_download_native import (
    FOLDER, _read, _unique, preflight, cookie_unchanged, verify_storage,
)
from tools.job_store_schema import _job_schema, _job_payload, _job_bindings
from tools.native_ui_guard import NativeUiGuard
from pixivvault_nicegui.fs_paths import logical_path, native_io_path, sqlite_readonly_uri

BRIDGE_PORT = 25010
ORIGIN = "chrome-extension://" + "a" * 32
SCOPE = "N09/N10 private static-work protocol to packaged native save"
STATUS = {
    "enabled": "拡張連携: 受付中",
    "disabled": "拡張連携: 無効（明示した起動設定が必要）",
    "conflict": "拡張連携: ポートを使用できません。既存アプリの終了後に再起動してください。",
}
NEUTRAL = r"""() => {
 const visible = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
 const learning = document.querySelector('.pv-learning[data-active="true"]');
 if (!learning || !visible(learning) || learning.dataset.storageMode !== 'memory' ||
     !/^demo-[0-9]{4}$/.test(learning.dataset.workId || '')) return false;
 const identities = [...document.querySelectorAll('[data-work-id]')]
     .filter(e => visible(e) && e.dataset.workId);
 if (!identities.every(e => /^demo-[0-9]{4}$/.test(e.dataset.workId))) return false;
 if ([...document.querySelectorAll(
     '[data-testid="library-view"], [data-testid="managed-download-view"], [data-testid="queue-view"], .pv-viewer')]
     .some(visible)) return false;
 const imgs = [...document.querySelectorAll('img')].filter(img => {
     const r = img.getBoundingClientRect();
     return visible(img) && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
 });
 return imgs.length > 0 && imgs.every(img => {
     const source = new URL(img.currentSrc || img.src, document.baseURI);
     return learning.contains(img) && source.origin === location.origin &&
         source.pathname === '/media/' + learning.dataset.workId + '/0/view.png' &&
         img.complete && img.naturalWidth > 0 && img.naturalHeight > 0;
 });
}"""


class CheckFailed(RuntimeError):
    pass


def need(condition, code):
    if not condition:
        raise CheckFailed(code)


def error_label(error):
    # Never serialize arbitrary exception text, HTTP payloads or locators.
    if type(error) is CheckFailed and re.fullmatch(r"[a-z0-9_]{1,100}", str(error)):
        return str(error)
    return type(error).__name__


def safe_path(path, *, file=False):
    need(".." not in Path(path).parts, "parent_path_component_rejected")
    path = native_io_path(path)
    for node in (path, *path.parents):
        try:
            info = node.lstat()
        except FileNotFoundError:
            continue
        need(not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400,
             "path_link_rejected")
        if node == path and file:
            need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "file_alias_rejected")
    return path


def canonical_path(path, *, file=False):
    # Reject links/parent traversal first; then collapse Windows native, short
    # and drive-alias spellings before any output/readonly-boundary decision.
    spelling = safe_path(path, file=file)
    canonical = logical_path(spelling.resolve(strict=False))
    safe_path(canonical, file=file)
    return canonical


def overlap(a, b):
    a, b = canonical_path(a), canonical_path(b)
    return a == b or a in b.parents or b in a.parents


def private_output_contract(candidate):
    private_parent = canonical_path(ROOT / "artifacts" / "p3-318-extension-private")
    need(candidate.parent == private_parent, "dedicated_private_output_required")
    ignore_path = canonical_path(ROOT / ".gitignore", file=True)
    raw = _read(ignore_path, 64 * 1024)
    # Require the literal rule; leading spaces have meaning in .gitignore.
    active = [line for line in raw.decode("utf-8-sig").splitlines()
              if line and not line.startswith("#")]
    exclusions = [i for i, rule in enumerate(active) if rule in ("artifacts/", "/artifacts/")]
    need(bool(exclusions) and not any(rule.startswith("!")
         for rule in active[exclusions[-1] + 1:]), "private_output_ignore_rule_unverified")
    # A whole excluded parent is not traversed for nested .gitignore rules.
    # This verifies normal Git exclusion, not force-add behavior.
    return hashlib.sha256(raw).hexdigest()


def file_identity(path):
    info = safe_path(path, file=True).lstat()
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_nlink)


def tree_proof(root):
    """Hash private paths/content only in memory; export no path or file list."""
    base = safe_path(root)
    need(base.is_dir(), "proof_root_missing")
    digest = hashlib.sha256()
    count = size = nodes = 0
    root_before = base.lstat()
    def walk_error(_error):
        raise CheckFailed("proof_walk_failed")
    for directory, directories, files in os.walk(base, onerror=walk_error, followlinks=False):
        directories.sort()
        files.sort()
        for name in (*directories, *files):
            path = safe_path(Path(directory) / name)
            info = path.lstat()
            nodes += 1
            need(nodes <= 20000, "proof_budget_exceeded")
            relative = path.relative_to(base).as_posix()
            if stat.S_ISDIR(info.st_mode):
                digest.update(json.dumps([relative, "dir", info.st_mtime_ns]).encode("utf-8"))
                continue
            need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "proof_file_alias_rejected")
            count += 1
            size += info.st_size
            need(info.st_size <= 64 * 1024 * 1024, "proof_budget_exceeded")
            sha = file_sha(path)
            after = path.lstat()
            need((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_nlink)
                 == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_nlink),
                 "proof_file_changed_during_read")
            digest.update(json.dumps([relative, info.st_size, info.st_mtime_ns, sha]).encode("utf-8"))
    root_after = base.lstat()
    need((root_before.st_dev, root_before.st_ino, root_before.st_mtime_ns)
         == (root_after.st_dev, root_after.st_ino, root_after.st_mtime_ns), "proof_root_changed_during_read")
    digest.update(json.dumps(["root", root_after.st_mtime_ns]).encode("utf-8"))
    return {"files": count, "bytes": size, "sha256": digest.hexdigest()}


def checker_sources():
    result = helper_shas()
    result[Path(__file__).name] = file_sha(Path(__file__))
    for name in ("check_managed_download_native.py", "job_store_schema.py"):
        result[name] = file_sha(ROOT / "tools" / name)
    result["fs_paths.py"] = file_sha(ROOT / "src" / "pixivvault_nicegui" / "fs_paths.py")
    return result


def signature_sha(parts):
    return hashlib.sha256(b"".join(parts)).hexdigest()


def only_receipt(root):
    folders = list(safe_path(Path(root) / "outputs").iterdir())
    need(len(folders) == 1 and FOLDER.fullmatch(folders[0].name) is not None,
         "one_source_work_required")
    row = json.loads(_read(folders[0] / "receipt.json", 512 * 1024), object_pairs_hook=_unique)
    need(row["illustration_type"] in (0, 1) and row["x_restrict"] == 0
         and 1 <= row["page_count"] <= 3, "bounded_static_source_required")
    return row


def readonly_db(path):
    path = safe_path(path, file=True)
    need(path.is_file() and path.stat().st_size <= 32 * 1024 * 1024, "database_budget_rejected")
    for suffix in ("-wal", "-shm", "-journal"):
        need(not safe_path(Path(str(path) + suffix)).exists(), "database_sidecar_present")
    return sqlite3.connect(sqlite_readonly_uri(path), uri=True)


def job_rows(path):
    with closing(readonly_db(path)) as db:
        need(db.execute("PRAGMA quick_check").fetchone()[0] == "ok", "job_database_invalid")
        version = _job_schema(db)
        raw = list(db.execute("SELECT job_id,payload FROM jobs ORDER BY ordinal"))
        rows = {jid: _job_payload(payload, jid) for jid, payload in raw}
        bindings = _job_bindings(db, rows)
        need(version == 2, "current_job_schema_required")
        history = {jid: _job_payload(payload, jid)
                   for jid, payload in db.execute("SELECT job_id,payload FROM job_history")}
        need(history == rows and all(v["state"] == "done" and v["attempt"] == 1
                                    for v in rows.values()), "terminal_history_mismatch")
        return rows, bindings, [hashlib.sha256(payload.encode()).digest() for _, payload in raw]


def verify_completed(root, private_target, parent_id):
    state = verify_storage(root)
    receipt = only_receipt(root)
    need((receipt["work_id"], receipt["author_id"], receipt["page_count"])
         == private_target, "saved_selection_changed")
    static, static_ops, static_hashes = job_rows(Path(root) / "jobs.sqlite3")
    extension = Path(root) / "extension-commands"
    need(_read(safe_path(extension / "extension-owner-v1"), 256)
         == b"PixivVault extension commands v1\n", "extension_owner_marker_mismatch")
    parents, parent_ops, parent_hashes = job_rows(extension / "jobs.sqlite3")
    need(len(static) == len(static_ops) == len(parents) == len(parent_ops) == 1,
         "one_parent_child_operation_required")
    need(parent_id in parents, "accepted_parent_missing")
    parent = parents[parent_id]
    need(parent["kind"] == "extension-command", "parent_kind_mismatch")
    command = json.loads(parent["target_id"], object_pairs_hook=_unique)
    need(command == {"type": "work", "work_id": private_target[0], "is_novel": False},
         "parent_intent_mismatch")
    with closing(readonly_db(extension / "bindings.sqlite3")) as db:
        need(db.execute("PRAGMA application_id").fetchone()[0] == 1347831107,
             "extension_binding_identity_mismatch")
        children = list(db.execute("SELECT parent,key,job FROM children"))
    child_id = next(iter(static))
    need(children == [(parent_id, "work:" + private_target[0], child_id)],
         "extension_child_binding_mismatch")
    need(static[child_id]["target_id"] == private_target[0], "child_selection_mismatch")
    hashes = list(state["_signature"]) + static_hashes + parent_hashes
    hashes.append(hashlib.sha256(json.dumps([parent_ops, children], sort_keys=True).encode()).digest())
    return {
        "works": state["receipts"], "pages": state["pages"], "static_jobs": len(static),
        "extension_jobs": len(parents), "child_bindings": len(children),
        "completed_signature_sha256": signature_sha(hashes),
    }


class OwnedConflict:
    """One exclusive checker-owned listener. Never adopt or stop another server."""
    def __init__(self):
        self.nonce = secrets.token_hex(24)
        self.socket = None
        self.thread = None
        self.stop = threading.Event()
        self.errors = []
        self.pid = os.getpid()
        self.birth = native.process_identity(self.pid)

    def start(self):
        need(loopback_listener(BRIDGE_PORT) is None, "bridge_port_already_owned")
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        self.socket.bind(("127.0.0.1", BRIDGE_PORT))
        self.socket.listen(4)
        self.socket.settimeout(0.2)
        self.thread = threading.Thread(target=self._serve, name="owned-extension-conflict", daemon=True)
        self.thread.start()
        self.verify()

    def _serve(self):
        while not self.stop.is_set():
            try:
                conn, _ = self.socket.accept()
            except socket.timeout:
                continue
            except OSError:
                if not self.stop.is_set():
                    self.errors.append("owned_conflict_accept_failed")
                return
            with conn:
                try:
                    conn.settimeout(1.0)
                    request = conn.recv(4096)
                    if request:
                        body = json.dumps({"owned_nonce": self.nonce}).encode()
                        conn.sendall(b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: "
                                     + str(len(body)).encode() + b"\r\n\r\n" + body)
                except OSError:
                    if not self.stop.is_set():
                        self.errors.append("owned_conflict_response_failed")

    def verify(self):
        need(self.birth is not None and native.process_identity(self.pid) == self.birth
             and loopback_listener(BRIDGE_PORT) == self.pid, "conflict_owner_changed")
        conn = http.client.HTTPConnection("127.0.0.1", BRIDGE_PORT, timeout=2)
        try:
            conn.request("GET", "/owned-checker-probe")
            response = conn.getresponse()
            need(response.status == 200 and json.loads(response.read(1024))
                 == {"owned_nonce": self.nonce}, "conflict_nonce_unverified")
        finally:
            conn.close()
        need(not self.errors and self.thread.is_alive(), "conflict_listener_failed")

    def close(self):
        self.stop.set()
        if self.socket is not None:
            self.socket.close()
        if self.thread is not None:
            self.thread.join(timeout=3)
            need(not self.thread.is_alive(), "conflict_thread_not_closed")
        need(native.port_released(BRIDGE_PORT), "conflict_port_not_released")


class ExtensionRun(NativeTrayRun):
    def __init__(self, exe, output, timeout, cookie, managed, preferences, mode, nonce_hash):
        super().__init__(exe, output, timeout)
        while self.port == BRIDGE_PORT:
            self.port = native.available_port()
        while self.debug_port in (self.port, BRIDGE_PORT):
            self.debug_port = native.available_port()
        self.cookie, self.managed, self.preferences, self.mode = cookie, managed, preferences, mode
        self.nonce_hash = nonce_hash
        self.parent_id = None
        self.close_attempted = False
        self.report = {
            "status": "failed", "scope": SCOPE, "mode": mode, "nonce_sha256": nonce_hash,
            "exe_sha256": self.exe_sha, "app_port": self.port, "debug_port": self.debug_port,
            "bridge_port": BRIDGE_PORT, "errors": [], "forced_cleanup": [],
            "capture_count": 0, "console_error_count": 0, "page_error_count": 0,
            "local_posts_attempted": 0, "local_acceptance_unknown": False,
            "ui_passed": False, "neutral_return": False, "needs_manual_close": False,
        }

    def start(self):
        self.phase("launch")
        need(loopback_listener(self.port) is None and loopback_listener(self.debug_port) is None,
             "app_port_already_owned")
        temporary, webview = self.output / "temporary", self.output / "webview-private"
        temporary.mkdir()
        webview.mkdir()
        environment = os.environ.copy()
        for name in ("PIXIVVAULT_PROFILE_ROOT", "PWDEBUG", "DEBUG"):
            environment.pop(name, None)
        environment.update(TEMP=str(temporary), TMP=str(temporary),
            WEBVIEW2_USER_DATA_FOLDER=str(webview),
            WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=(
                f"--remote-debugging-port={self.debug_port} --remote-debugging-address=127.0.0.1"))
        command = [str(self.exe), "--host", "127.0.0.1", "--port", str(self.port),
                   "--pixiv-cookie-file", str(self.cookie), "--pixiv-download-root", str(self.managed),
                   "--preferences-root", str(self.preferences)]
        if self.mode != "disabled":
            command.append("--extension-bridge")
        # No command line, stdout/stderr, Cookie or work metadata is logged.
        self.root = subprocess.Popen(command, cwd=str(self.exe.parent), env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.root_birth = native.process_identity(self.root.pid)
        need(self.root_birth is not None, "root_identity_unknown")
        self.identities[self.root.pid] = self.root_birth
        self.report.update(root_pid=self.root.pid, root_birth=self.root_birth)
        self.wait(lambda: self.owned_port_ready(self.port)
                  and native.health_ready(self.port, "pixiv-readonly"), 45, "readonly_health_timeout")
        def locate():
            windows = native.owned_windows(self.scan())
            need(len(windows) <= 1, "main_window_ambiguous")
            if not windows:
                return False
            self.hwnd = windows[0]
            self.owner = native.owned_window_identity(
                self.root.pid, self.root_birth, self.hwnd, self.identities)
            return True
        self.wait(locate, 25, "main_window_missing")
        self.wait(lambda: native.ui_ready(self.port), 20, "webview_not_connected")
        self.wait(lambda: self.owned_port_ready(self.debug_port), 15, "owned_cdp_missing")
        self.report.update(main_hwnd=self.hwnd, main_owner=list(self.owner))
        self.phase("native_ready")

    def bridge_owner(self):
        self.check_running()
        pid = loopback_listener(BRIDGE_PORT)
        need(pid in self.identities and native.process_identity(pid) == self.identities[pid],
             "bridge_listener_not_owned")
        return pid

    def request_bridge(self, method, path, payload=None):
        self.bridge_owner()
        need(method in ("GET", "OPTIONS", "POST"), "invalid_probe_method")
        headers = {"Origin": ORIGIN, "Connection": "close"}
        body = None
        if method == "OPTIONS":
            headers.update({"Access-Control-Request-Method": "POST",
                            "Access-Control-Request-Headers": "content-type"})
        if method == "POST":
            need(path == "/download" and self.report["local_posts_attempted"] == 0,
                 "acceptance_post_replay_forbidden")
            need(self.report.get("owned_notifications_off_verified") is True,
                 "private_notification_fixture_not_ready")
            self.report["local_posts_attempted"] = 1
            self.report["local_acceptance_unknown"] = True
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, separators=(",", ":")).encode()
        connection = http.client.HTTPConnection("127.0.0.1", BRIDGE_PORT,
            timeout=max(0.1, min(10, self.operation_deadline - time.monotonic())))
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read(16385)
            need(len(data) <= 16384 and response.status == 200, "bridge_response_rejected")
            need(response.getheader("Access-Control-Allow-Origin") == ORIGIN
                 and response.getheader("Access-Control-Allow-Private-Network") == "true",
                 "bridge_origin_response_mismatch")
            result = json.loads(data, object_pairs_hook=_unique)
            self.bridge_owner()
            if method == "POST":
                need(type(result) is dict and set(result) == {"status", "jobId"}
                     and result["status"] == "ok" and type(result["jobId"]) is str
                     and re.fullmatch(r"[a-f0-9]{32}", result["jobId"]) is not None,
                     "bridge_acceptance_unverified")
                self.report["local_acceptance_unknown"] = False
            return result
        finally:
            connection.close()

    def inspect(self, private_target, accepted_parent, conflict):
        from playwright.sync_api import expect, sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{self.debug_port}")
            pages = [p for context in browser.contexts for p in context.pages
                     if native.matching_app_page(p.url, self.port)]
            need(len(pages) == 1, "owned_webview_page_not_unique")
            page = pages[0]
            page.on("console", lambda m: self.report.__setitem__(
                "console_error_count", self.report["console_error_count"] + int(m.type == "error")))
            page.on("pageerror", lambda _e: self.report.__setitem__(
                "page_error_count", self.report["page_error_count"] + 1))
            guard = NativeUiGuard(page, self.port, self.operation_deadline, self.check_running)
            run = guard.run
            self.phase("extension_status")
            run(page.get_by_test_id("preferences-open").click)
            dialog = page.get_by_test_id("preferences-dialog")
            run(expect(dialog).to_be_visible)
            run(expect(page.get_by_test_id("extension-status")).to_have_text, STATUS[self.mode])
            actions = page.get_by_test_id("preference-pixiv_actions_enabled")
            run(expect(actions).to_have_attribute, "data-value", "0")
            need("q-field--disabled" in (run(actions.get_attribute, "class") or ""),
                 "pixiv_write_capability_not_disabled")
            notification = page.get_by_test_id("preference-enable_notifications")
            run(expect(notification).to_be_visible)
            if self.mode == "enabled":
                # This is private fixture preparation through the real UI,
                # not an S09 toast acceptance or a user-settings change.
                self.phase("owned_notifications_off")
                need("q-field--disabled" not in (run(notification.get_attribute, "class") or ""),
                     "owned_notification_setting_unavailable")
                before = run(notification.get_attribute, "data-value")
                need(before in ("0", "1"), "owned_notification_setting_invalid")
                if before != "0":
                    run(notification.click)
                    run(page.get_by_role("option", name="OFF", exact=True).click)
                run(expect(notification).to_have_attribute, "data-value", "0")
                run(page.get_by_test_id("preferences-save").click)
                run(expect(page.get_by_test_id("preferences-status")).to_have_text,
                    re.compile(r"^(設定を保存しました。|設定の変更はありません。)$"))
                run(dialog.get_by_role("button", name="閉じる", exact=True).click)
                run(expect(dialog).to_be_hidden)
                run(page.get_by_test_id("preferences-open").click)
                run(expect(dialog).to_be_visible)
                notification = page.get_by_test_id("preference-enable_notifications")
            run(expect(notification).to_have_attribute, "data-value", "0")
            self.report["owned_notifications_off_verified"] = True
            run(dialog.get_by_role("button", name="閉じる", exact=True).click)
            run(expect(dialog).to_be_hidden)
            self.report["connection_state_observed"] = self.mode
            if self.mode == "enabled":
                self.wait(lambda: self.owned_port_ready(BRIDGE_PORT), 10, "bridge_not_listening")
                need(self.request_bridge("GET", "/ping") == {"status": "ok"}, "bridge_ping_failed")
                before = self.request_bridge("GET", "/api/work/" + private_target[0])
                need(before == {"downloaded": False}, "fresh_work_already_saved")
                need(self.request_bridge("OPTIONS", "/download") == {"status": "ok"},
                     "extension_preflight_failed")
                self.phase("single_private_acceptance")
                response = self.request_bridge("POST", "/download",
                    {"type": "work", "work_id": private_target[0], "is_novel": False})
                self.parent_id = response["jobId"]
            else:
                self.parent_id = accepted_parent
                if self.mode == "disabled":
                    need(loopback_listener(BRIDGE_PORT) is None, "disabled_bridge_listening")
                else:
                    conflict.verify()
                    self.report["owned_conflict_preserved"] = True
            need(self.parent_id is not None, "accepted_parent_unavailable")
            self.phase("queue_completion")
            run(page.get_by_test_id("tab-queue").click)
            queue = page.get_by_test_id("queue-view")
            run(expect(queue).to_be_visible)
            # Parent ID is used only in a private locator and never in a report.
            parent_row = queue.locator(f'[data-testid="job-row"][data-job-id="{self.parent_id}"]')
            run(expect(parent_row).to_be_visible)
            run(expect(parent_row).to_have_attribute, "data-state", "done")
            run(expect(queue.locator('[data-testid="job-row"][data-state="done"]')).to_have_count, 2)
            run(expect(queue.get_by_test_id("queue-filter-done")).to_have_text, "完了 2")
            self.report["queue_done_count"] = 2
            if self.mode == "enabled":
                after = self.request_bridge("GET", "/api/work/" + private_target[0])
                author = self.request_bridge("GET", "/api/user/" + private_target[1] + "/status")
                need(after.get("downloaded") is True and author == {"downloaded": 1, "total": 1},
                     "saved_status_mismatch")
                self.report.update(saved_work_status=True, saved_author_count=1)
            self.phase("neutral_return")
            run(page.get_by_test_id("tab-learning").click)
            learning = page.locator('.pv-learning[data-active="true"]')
            run(expect(learning).to_have_attribute, "data-storage-mode", "memory")
            run(expect(learning).to_have_attribute, "data-work-id", re.compile(r"^demo-[0-9]{4}$"))
            run(page.wait_for_function, NEUTRAL)
            need(run(page.evaluate, NEUTRAL, with_timeout=False) is True, "neutral_return_unverified")
            self.report["neutral_return"] = True
            need(not self.report["console_error_count"] and not self.report["page_error_count"],
                 "webview_error_observed")
            self.report["ui_passed"] = True
            # Only detach Playwright. Never call browser.close/context.close on WebView.

    def finish_owned(self):
        if not self.close_attempted:
            self.close_attempted = True
            try:
                self.phase("normal_close_once")
            except Exception as error:
                self.report["errors"].append("phase_" + error_label(error))
            try:
                self.cleanup()  # Existing owned WM_CLOSE/GW_OWNER confirmation; never force.
            except Exception as error:
                self.report["errors"].append("close_" + error_label(error))
                self.report["needs_manual_close"] = True
        try:
            self.scan()
            remaining = sorted(native.living_pids(dict(self.identities)))
            code = self.root.poll() if self.root is not None else None
            app_free, debug_free = native.port_released(self.port), native.port_released(self.debug_port)
            self.report.update(
                root_exit_code=code, observed_owned=[{"pid": pid, "birth": birth}
                    for pid, birth in sorted(self.identities.items())],
                remaining_owned=[{"pid": pid, "birth": self.identities[pid]} for pid in remaining],
                app_port_released=app_free, debug_port_released=debug_free)
            self.report["normal_exit"] = bool(
                self.report.get("cleanup_normal_close_sent")
                and self.report.get("cleanup_own_confirmation_accepted")
                and code == 0 and not remaining and app_free and debug_free)
            self.report["needs_manual_close"] |= bool(remaining or
                (self.root is not None and code is None))
            need(self.report["normal_exit"], "normal_exit_unverified")
            need(helper_shas() == self.helpers_before and file_sha(self.exe) == self.exe_sha,
                 "native_inputs_changed")
        except Exception as error:
            self.report["errors"].append(error_label(error))
        self.report["status"] = "passed" if (self.report["ui_passed"] and self.report["neutral_return"]
            and self.report.get("normal_exit") and not self.report["errors"]
            and not self.report["needs_manual_close"]) else "failed"
        return self.report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--cookie-file", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True,
                        help="Fresh child of artifacts/p3-318-extension-private (parent must exist)")
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args(argv)
    if os.name != "nt":
        parser.error("Windows only")
    if not 90 <= args.timeout <= 600:
        parser.error("--timeout must be within 90..600")
    # Avoid inherited Playwright wire/API debug printing private locators.
    os.environ.pop("DEBUG", None)
    os.environ.pop("PWDEBUG", None)
    result = {"status": "failed", "scope": SCOPE, "capture_count": 0,
        "installed_extension_tested": False, "author_download_tested": False,
        "novel_download_tested": False, "pixiv_write_capability_enabled": False,
        "runs": [], "errors": []}
    output = None
    try:
        exe = canonical_path(args.exe, file=True)
        cookie = canonical_path(args.cookie_file, file=True)
        source = canonical_path(args.source_root)
        candidate = canonical_path(args.output)
        need(exe.is_file() and exe.suffix.lower() == ".exe", "packaged_exe_missing")
        need(cookie.is_file() and source.is_dir(), "readonly_input_missing")
        need(candidate.is_relative_to(canonical_path(ROOT / "artifacts"))
             and candidate.parent.is_dir() and not candidate.exists(), "fresh_artifact_output_required")
        for protected in (source, cookie.parent, exe.parent):
            need(not overlap(candidate, protected), "output_overlaps_protected_input")
        ignore_sha = private_output_contract(candidate)
        need(loopback_listener(BRIDGE_PORT) is None, "bridge_port_already_owned")
        source_before = tree_proof(source)
        source_state = verify_storage(source)
        receipt = only_receipt(source)
        private_target = (receipt["work_id"], receipt["author_id"], receipt["page_count"])
        candidate.mkdir()
        output = candidate
        managed, preferences = output / "managed", output / "preferences"
        before = preflight(managed, cookie, restart=False)
        cookie_identity = file_identity(cookie)
        nonce = secrets.token_hex(32)
        nonce_hash = hashlib.sha256(nonce.encode()).hexdigest()
        write_new_json(output / "acceptance-owner.json", {
            "scope": SCOPE, "nonce_sha256": nonce_hash, "runner_pid": os.getpid(),
            "runner_birth": native.process_identity(os.getpid()), "exe_sha256": file_sha(exe)})
        result.update(exe_sha256=file_sha(exe), checker_sha256=file_sha(Path(__file__)),
                      checker_sources_before=checker_sources(),
                      private_output_ignore_sha256=ignore_sha,
                      private_output_scope="artifacts/p3-318-extension-private",
                      nonce_sha256=nonce_hash, source_before=source_before,
                      source_receipts=source_state["receipts"], source_pages=source_state["pages"])
        parent_id = None
        completed_before = None
        for mode in ("enabled", "disabled", "conflict"):
            conflict = OwnedConflict() if mode == "conflict" else None
            run = None
            observed = None
            try:
                need(private_output_contract(candidate) == ignore_sha, "private_ignore_rules_changed")
                need(loopback_listener(BRIDGE_PORT) is None, "bridge_port_not_released_between_runs")
                if conflict is not None:
                    conflict.start()
                run = ExtensionRun(exe, output / mode, args.timeout, cookie, managed,
                                   preferences, mode, nonce_hash)
                run.start()
                run.inspect(private_target, parent_id, conflict)
            except Exception as error:
                if run is not None:
                    run.report["errors"].append(error_label(error))
                else:
                    observed = {"status": "failed", "mode": mode, "errors": [error_label(error)]}
            finally:
                if run is not None:
                    observed = run.finish_owned()
                    if run.parent_id is not None:
                        parent_id = run.parent_id
                if conflict is not None:
                    try:
                        conflict.verify()
                        if observed is not None:
                            observed["conflict_preserved_until_app_exit"] = True
                    except Exception as error:
                        if observed is not None:
                            observed["errors"].append(error_label(error))
                    finally:
                        try:
                            conflict.close()
                        except Exception as error:
                            if observed is not None:
                                observed["errors"].append(error_label(error))
                if observed is None:
                    observed = {"status": "failed", "mode": mode, "errors": ["run_result_missing"]}
                try:
                    need(native.port_released(BRIDGE_PORT), "bridge_port_not_released")
                    observed["bridge_port_released"] = True
                    need(cookie_unchanged(cookie, before) and file_identity(cookie) == cookie_identity,
                         "cookie_source_changed")
                    need(tree_proof(source) == source_before, "prior_owned_source_changed")
                    observed["readonly_inputs_unchanged"] = True
                    if observed["status"] == "passed" and not observed["errors"]:
                        current = verify_completed(managed, private_target, parent_id)
                        if completed_before is not None:
                            need(current == completed_before, "restart_changed_accepted_save")
                        completed_before = current
                        observed["completed"] = current
                except Exception as error:
                    observed["errors"].append(error_label(error))
                if observed["errors"]:
                    observed["status"] = "failed"
                result["runs"].append(observed)
                write_new_json(output / (mode + "-result.json"), observed)
            if observed["status"] != "passed":
                result["errors"].append("phase_failed_following_runs_not_started")
                break
        need(len(result["runs"]) == 3 and all(r["status"] == "passed" for r in result["runs"]),
             "extension_native_acceptance_incomplete")
        need(sum(r.get("local_posts_attempted", 0) for r in result["runs"]) == 1,
             "acceptance_post_count_changed")
        need(file_sha(exe) == result["exe_sha256"], "exe_changed")
        need(checker_sources() == result["checker_sources_before"], "checker_sources_changed")
        need(private_output_contract(candidate) == ignore_sha, "private_ignore_rules_changed")
        result["checker_sources_unchanged"] = True
        result["status"] = "passed"
    except Exception as error:
        result["errors"].append(error_label(error))
    finally:
        if output is not None:
            try:
                write_new_json(output / "result.json", result)
            except Exception as error:
                result["status"] = "failed"
                result["errors"].append("report_" + error_label(error))
        print(json.dumps({"status": result["status"], "runs": len(result["runs"]),
                          "errors": result["errors"], "capture_count": 0}, ensure_ascii=True), flush=True)
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

