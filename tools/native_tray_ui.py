"""Read only the owned PixivVault notification icon; click its actual Windows UI.

No toolbar-memory reads, synthetic WM_NOTIFY callbacks, global keyboard input,
desktop screenshots, or enumeration of other applications' accessible names.
Run only from check_native_tray_window against its newly launched process tree.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as w
import math
import re
import time
from types import SimpleNamespace


class TrayProbeError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise TrayProbeError(code)


class GUID(ctypes.Structure):
    _fields_ = [("a", w.DWORD), ("b", w.WORD), ("c", w.WORD), ("d", w.BYTE * 8)]


class NOTIFYICONIDENTIFIER(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("hWnd", w.HWND), ("uID", w.UINT), ("guidItem", GUID)]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("flags", w.DWORD),
                ("hwndActive", w.HWND), ("hwndFocus", w.HWND), ("hwndCapture", w.HWND),
                ("hwndMenuOwner", w.HWND), ("hwndMoveSize", w.HWND), ("hwndCaret", w.HWND),
                ("rcCaret", w.RECT)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", w.LONG), ("dy", w.LONG), ("mouseData", w.DWORD),
                ("dwFlags", w.DWORD), ("time", w.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", w.WORD), ("wScan", w.WORD), ("dwFlags", w.DWORD),
                ("time", w.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", w.DWORD), ("wParamL", w.WORD), ("wParamH", w.WORD)]


class INPUTVALUE(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("type", w.DWORD), ("value", INPUTVALUE)]


class OwnedTrayUI:
    """Bind an exact notification hWnd/uID to its visible shell/UIA rectangle."""

    CLASS = re.compile(r"PixivVault([0-9]+)SystemTrayIcon\Z")
    SHELL_CLASSES = frozenset({
        "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow",
        "TopLevelWindowForOverflowXamlIsland",
    })
    CHEVRON_NAMES = (
        "非表示のアイコンを表示する 隠れているインジケーターを表示します",
        "Show hidden icons", "隠れているインジケーターを表示します",
        "隠れているインジケーターを表示", "隠れているアイコンを表示",
    )

    def __init__(self, run):
        # run owns PID/birth identities, a deadline, and the original main HWND.
        self.run = run
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.shell = ctypes.WinDLL("shell32", use_last_error=True)
        self.callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        signatures = {
            "EnumWindows": (w.BOOL, [self.callback_type, w.LPARAM]),
            "GetWindowThreadProcessId": (w.DWORD, [w.HWND, ctypes.POINTER(w.DWORD)]),
            "GetClassNameW": (ctypes.c_int, [w.HWND, w.LPWSTR, ctypes.c_int]),
            "IsWindow": (w.BOOL, [w.HWND]),
            "IsWindowVisible": (w.BOOL, [w.HWND]),
            "GetWindow": (w.HWND, [w.HWND, w.UINT]),
            "GetShellWindow": (w.HWND, []),
            "GetAncestor": (w.HWND, [w.HWND, w.UINT]),
            "WindowFromPoint": (w.HWND, [w.POINT]),
            "GetGUIThreadInfo": (w.BOOL, [w.DWORD, ctypes.POINTER(GUITHREADINFO)]),
            "SetCursorPos": (w.BOOL, [ctypes.c_int, ctypes.c_int]),
            "SendInput": (w.UINT, [w.UINT, ctypes.POINTER(INPUT), ctypes.c_int]),
        }
        for name, (result, arguments) in signatures.items():
            call = getattr(self.user, name)
            call.restype, call.argtypes = result, arguments
        self.shell.Shell_NotifyIconGetRect.argtypes = [
            ctypes.POINTER(NOTIFYICONIDENTIFIER), ctypes.POINTER(w.RECT)]
        self.shell.Shell_NotifyIconGetRect.restype = ctypes.c_long
        self.icon = None
        self.tray_windows = {}
        self.menu = None
        self.shell_identity = None
        self.expanded_by_checker = False

        # Keep CLR completely out of this process. Its initialization changed
        # native physical hit-testing in the measured environment even after
        # swapping FromPoint for COM. Native-only exact-name DTO lookup and
        # a fresh runtime-ID/PID hit-test retain the original safety gate.
        self.CT = SimpleNamespace(Button=50000, MenuItem=50011)

    def check(self):
        self.run.check_running()
        require(time.monotonic() < self.run.operation_deadline, "tray_ui_deadline")

    def enum_windows(self, predicate):
        found, errors = [], []

        def visit(hwnd, _):
            try:
                if predicate(int(hwnd)):
                    found.append(int(hwnd))
            except Exception as error:
                errors.append(error)
                return False
            return True

        cb = self.callback_type(visit)
        okay = self.user.EnumWindows(cb, 0)
        if errors:
            raise errors[0]
        require(okay, "window_enumeration_failed")
        return found

    def window_pid_thread(self, hwnd):
        pid = w.DWORD()
        thread = self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not thread and not self.user.IsWindow(hwnd):
            return 0, 0
        require(thread and pid.value, "window_owner_unknown")
        return int(pid.value), int(thread)

    def window_class(self, hwnd):
        value = ctypes.create_unicode_buffer(256)
        require(self.user.GetClassNameW(hwnd, value, len(value)) > 0, "window_class_unknown")
        return value.value

    def owned_identity(self, hwnd):
        self.check()
        require(self.user.IsWindow(hwnd), "tray_window_missing")
        pid, thread = self.window_pid_thread(hwnd)
        birth = self.run.identities.get(pid)
        require(birth is not None and self.run.native.process_identity(pid) == birth,
                "tray_window_not_owned")
        return pid, birth, thread

    def icon_rect(self, hwnd, uid):
        item = NOTIFYICONIDENTIFIER()
        item.cbSize, item.hWnd, item.uID = ctypes.sizeof(item), hwnd, uid
        bounds = w.RECT()
        result = self.shell.Shell_NotifyIconGetRect(ctypes.byref(item), ctypes.byref(bounds))
        if result != 0:
            return None
        box = (bounds.left, bounds.top, bounds.right, bounds.bottom)
        return box if box[2] > box[0] and box[3] > box[1] else None

    def pin_icon(self):
        self.check()
        self.run.scan()
        owned = set(self.run.identities)
        matches = {}
        # Read class names only after PID membership; do not read external titles.
        for hwnd in self.enum_windows(lambda h: self.window_pid_thread(h)[0] in owned):
            name = self.window_class(hwnd)
            matched = self.CLASS.fullmatch(name)
            if matched:
                identity = self.owned_identity(hwnd)
                matches[hwnd] = {"identity": identity, "class": name,
                                 "object_id": int(matched.group(1))}
        candidates = []
        for hwnd, item in matches.items():
            # This installed pystray uses hID= in a ctypes structure with uID.
            # Resolve only its bounded actual possibilities; never guess by name.
            for uid in sorted({0, item["object_id"] & 0xffffffff}):
                if self.icon_rect(hwnd, uid) is not None:
                    candidates.append((hwnd, uid))
        if not candidates:
            # A newly installed icon may be hidden until the shell overflow is
            # opened. Expanding that one verified system control does not select
            # any application icon; hWnd/uID must still resolve uniquely below.
            require(len(matches) == 2
                    and len({(v["identity"], v["class"]) for v in matches.values()}) == 1,
                    "owned_tray_window_pair_not_unique")
            self.expand_overflow()
            deadline = min(self.run.operation_deadline, time.monotonic() + 4)
            while time.monotonic() < deadline and not candidates:
                self.check()
                for hwnd, item in matches.items():
                    require(self.owned_identity(hwnd) == item["identity"],
                            "tray_window_identity_changed")
                    for uid in sorted({0, item["object_id"] & 0xffffffff}):
                        if self.icon_rect(hwnd, uid) is not None:
                            candidates.append((hwnd, uid))
                if not candidates:
                    time.sleep(0.1)
        require(len(candidates) == 1, "owned_notification_icon_not_unique")
        hwnd, uid = candidates[0]
        item = matches[hwnd]
        siblings = {h: v for h, v in matches.items()
                    if v["identity"] == item["identity"] and v["class"] == item["class"]}
        require(len(siblings) == 2, "owned_tray_window_pair_not_unique")
        self.tray_windows = siblings
        self.icon = {"hwnd": hwnd, "uid": uid, "identity": item["identity"]}
        self.verify_icon()
        return {"hwnd": hwnd, "uid": uid, "pid": item["identity"][0],
                "birth": item["identity"][1], "thread": item["identity"][2],
                "menu_owner_candidates": sorted(siblings)}

    def verify_icon(self):
        require(self.icon is not None, "notification_icon_not_pinned")
        for hwnd, item in self.tray_windows.items():
            require(self.owned_identity(hwnd) == item["identity"], "tray_window_identity_changed")
            require(self.window_class(hwnd) == item["class"], "tray_window_class_changed")
        bounds = self.icon_rect(self.icon["hwnd"], self.icon["uid"])
        require(bounds is not None, "owned_notification_icon_missing")
        return bounds

    def shell_surfaces(self):
        self.check()
        shell_hwnd = int(self.user.GetShellWindow() or 0)
        require(shell_hwnd, "shell_window_unavailable")
        shell_pid, _ = self.window_pid_thread(shell_hwnd)
        require(shell_pid > 0, "shell_window_unavailable")
        shell_birth = self.run.native.process_identity(shell_pid)
        require(shell_birth is not None, "shell_identity_unknown")
        current = (shell_pid, shell_birth)
        if self.shell_identity is None:
            self.shell_identity = current
        require(current == self.shell_identity, "shell_identity_changed")
        surfaces = []
        for hwnd in self.enum_windows(
                lambda h: self.window_pid_thread(h)[0] == shell_pid
                and bool(self.user.IsWindowVisible(h))):
            if self.window_class(hwnd) in self.SHELL_CLASSES:
                surfaces.append(hwnd)
        require(surfaces, "supported_taskbar_surface_unavailable")
        return surfaces

    @staticmethod
    def runtime_id(element):
        return tuple(int(value) for value in element.GetRuntimeId())

    @staticmethod
    def rectangle(element):
        bounds = element.Current.BoundingRectangle
        result = (float(bounds.Left), float(bounds.Top), float(bounds.Right), float(bounds.Bottom))
        require(all(math.isfinite(v) for v in result)
                and result[2] > result[0] and result[3] > result[1], "uia_bounds_invalid")
        return result

    @staticmethod
    def contains(bounds, point):
        return bounds[0] <= point[0] < bounds[2] and bounds[1] <= point[1] < bounds[3]

    def named(self, hwnd, name, control_type):
        self.check()
        if __package__:
            from .native_uia_hit import native_named_elements
        else:
            from native_uia_hit import native_named_elements
        pid, _ = self.window_pid_thread(hwnd)
        found = native_named_elements(int(hwnd), name, control_type, pid)
        self.check()
        return found

    def point_matches(self, element, point):
        # Use the native physical POINT ABI, not the CLR Point wrapper that
        # disagreed with the read-only WinSDK probe in this DPI environment.
        # This still requires the exact UIA target runtime identity or one of
        # its raw descendants; neither coordinates nor a shell PID alone can
        # authorize pointer input. HWND/birth are checked separately below.
        if __package__:
            from .native_uia_hit import native_point_matches
        else:
            from native_uia_hit import native_point_matches
        return native_point_matches((int(point[0]), int(point[1])),
                                    self.runtime_id(element), int(element.Current.ProcessId))

    def pointer_click(self, element, point, *, right, verify, refresh=None):
        """No retry: validate under the pointer, then send one down/up pair."""
        self.check()
        require(self.contains(self.rectangle(element), point), "click_outside_uia_item")
        verify(point)
        require(self.user.SetCursorPos(int(point[0]), int(point[1])), "owned_pointer_move_failed")
        self.check()
        verify(point)
        if not self.point_matches(element, point) and refresh is not None:
            self.run.report["pointer_before_icon_reobservation"] = {
                "point": list(point), "expected_runtime_id": list(self.runtime_id(element)),
                "expected_rectangle": list(self.rectangle(element)),
                "exact_native_hit": False,
            }
            # Shell hover can replace its UIA element. Re-observe only the
            # same hWnd/uID-pinned icon before any input; never retry a click.
            element = refresh()
            require(self.contains(self.rectangle(element), point), "refreshed_icon_moved")
            verify(point)
            self.run.report["icon_uia_reobserved_before_input"] = True
        if not self.point_matches(element, point):
            self.run.report["pointer_identity_mismatch"] = {
                "point": list(point), "expected_runtime_id": list(self.runtime_id(element)),
                "expected_rectangle": list(self.rectangle(element)),
                "exact_native_hit": False,
            }
        require(self.point_matches(element, point), "uia_item_under_pointer_changed")
        self.check()
        verify(point)
        flags = (0x0008, 0x0010) if right else (0x0002, 0x0004)
        inputs = (INPUT * 2)()
        for value, flag in zip(inputs, flags):
            value.type, value.mi.dwFlags = 0, flag
        require(self.user.SendInput(2, inputs, ctypes.sizeof(INPUT)) == 2,
                "owned_pointer_click_incomplete")
        # An Exit menu callback may have destroyed the application already.
        # Do not issue any second click or retry when its result is uncertain.

    def verify_shell_point(self, point, surfaces):
        self.shell_surfaces()
        hwnd = int(self.user.WindowFromPoint(w.POINT(int(point[0]), int(point[1]))) or 0)
        require(hwnd, "shell_point_window_missing")
        pid, _ = self.window_pid_thread(hwnd)
        require(pid == self.shell_identity[0], "shell_point_obscured")
        root = int(self.user.GetAncestor(hwnd, 2) or 0)
        require(root in surfaces and self.user.IsWindowVisible(root), "shell_point_not_in_surface")

    def visible_icon(self):
        bounds = self.verify_icon()
        center = ((bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2)
        surfaces = self.shell_surfaces()
        candidates = {}
        for hwnd in surfaces:
            for element in self.named(hwnd, "PixivVault", self.CT.Button):
                if (int(element.Current.ProcessId) == self.shell_identity[0]
                        and self.contains(self.rectangle(element), center)):
                    candidates[self.runtime_id(element)] = element
        require(len(candidates) <= 1, "owned_tray_uia_ambiguous")
        if not candidates:
            return None
        element = next(iter(candidates.values()))
        self.verify_shell_point(center, surfaces)
        require(self.point_matches(element, center), "owned_tray_uia_obscured")
        return element, center, surfaces

    def expand_overflow(self):
        surfaces = self.shell_surfaces()
        require(not any(self.window_class(hwnd) in {
            "NotifyIconOverflowWindow", "TopLevelWindowForOverflowXamlIsland"
        } for hwnd in surfaces), "visible_overflow_has_no_owned_icon")
        candidates = {}
        for hwnd in surfaces:
            if self.window_class(hwnd) not in {"Shell_TrayWnd", "Shell_SecondaryTrayWnd"}:
                continue
            for name in self.CHEVRON_NAMES:
                for element in self.named(hwnd, name, self.CT.Button):
                    if int(element.Current.ProcessId) == self.shell_identity[0]:
                        candidates[self.runtime_id(element)] = element
        require(len(candidates) == 1, "owned_tray_overflow_button_not_unique")
        element = next(iter(candidates.values()))
        bounds = self.rectangle(element)
        point = (int((bounds[0] + bounds[2]) / 2), int((bounds[1] + bounds[3]) / 2))
        self.pointer_click(element, point, right=False,
                           verify=lambda p: self.verify_shell_point(p, surfaces))
        self.expanded_by_checker = True

    def expose_icon(self):
        item = self.visible_icon()
        if item is not None:
            return item
        self.expand_overflow()
        deadline = min(self.run.operation_deadline, time.monotonic() + 4)
        while time.monotonic() < deadline:
            item = self.visible_icon()
            if item is not None:
                return item
            time.sleep(0.1)
        raise TrayProbeError("owned_notification_icon_not_visible")

    def owned_menus(self):
        self.verify_icon()
        pid, birth, thread = self.icon["identity"]
        info = GUITHREADINFO()
        info.cbSize = ctypes.sizeof(info)
        require(self.user.GetGUIThreadInfo(thread, ctypes.byref(info)), "tray_gui_thread_unknown")
        menu_owner = int(info.hwndMenuOwner or 0)
        if menu_owner not in self.tray_windows:
            return []
        require(info.flags & (0x0004 | 0x0010), "tray_menu_mode_unknown")
        require(self.owned_identity(menu_owner) == (pid, birth, thread), "tray_menu_owner_changed")
        candidates = []
        for hwnd in self.enum_windows(lambda h: self.window_pid_thread(h) == (pid, thread)
                                      and bool(self.user.IsWindowVisible(h))):
            if self.window_class(hwnd) != "#32768":
                continue
            owner = int(self.user.GetWindow(hwnd, 4) or 0)
            # Some native popup windows have no GW_OWNER. In that case the
            # exact GUI-thread MenuOwner plus PID/birth/thread is still required.
            if owner in self.tray_windows or owner == 0:
                candidates.append((hwnd, menu_owner))
        return candidates

    def verify_menu(self, point=None):
        self.check()
        require(self.menu is not None and self.menu in self.owned_menus(), "owned_tray_menu_changed")
        hwnd, _ = self.menu
        if point is not None:
            under = int(self.user.WindowFromPoint(w.POINT(int(point[0]), int(point[1]))) or 0)
            require(under and (under == hwnd or int(self.user.GetAncestor(under, 2) or 0) == hwnd),
                    "owned_tray_menu_point_obscured")
        return hwnd

    def select(self, label):
        require(label in {"PixivVaultを開く", "終了"}, "unsupported_tray_action")
        require(not self.owned_menus(), "owned_tray_menu_already_open")
        element, point, surfaces = self.expose_icon()

        def verify_icon_point(p):
            bounds = self.verify_icon()
            require(self.contains(bounds, p), "owned_icon_moved_before_click")
            self.verify_shell_point(p, surfaces)

        self.pointer_click(element, point, right=True, verify=verify_icon_point,
                           refresh=lambda: self.visible_icon()[0])
        deadline = min(self.run.operation_deadline, time.monotonic() + 5)
        candidates = []
        while time.monotonic() < deadline:
            candidates = self.owned_menus()
            require(len(candidates) <= 1, "owned_tray_menu_ambiguous")
            if len(candidates) == 1:
                break
            time.sleep(0.1)
        require(len(candidates) == 1, "owned_tray_menu_not_observed")
        self.menu = candidates[0]
        hwnd = self.verify_menu()
        items = {}
        for name in ("PixivVaultを開く", "終了"):
            found = self.named(hwnd, name, self.CT.MenuItem)
            require(len(found) == 1, "owned_tray_menu_item_not_unique")
            require(int(found[0].Current.ProcessId) == self.icon["identity"][0],
                    "tray_menu_item_process_changed")
            items[name] = found[0]
        selected = items[label]
        bounds = self.rectangle(selected)
        point = (int((bounds[0] + bounds[2]) / 2), int((bounds[1] + bounds[3]) / 2))
        record = {"action": "restore" if label == "PixivVaultを開く" else "exit",
                  "icon_hwnd": self.icon["hwnd"], "icon_uid": self.icon["uid"],
                  "menu_hwnd": self.menu[0], "menu_owner_hwnd": self.menu[1],
                  "pointer_events": "right_down_up_then_left_down_up",
                  "owned_identity_checked": True, "exact_uia_items_verified": True,
                  "overflow_expanded": self.expanded_by_checker}
        self.pointer_click(selected, point, right=False, verify=lambda p: self.verify_menu(p))
        self.menu = None
        return record

    def removed(self):
        """Read-only post-exit check; never reuse a dead owner's HWND."""
        if self.icon is None:
            return False
        for hwnd, item in self.tray_windows.items():
            if self.user.IsWindow(hwnd):
                pid, thread = self.window_pid_thread(hwnd)
                if ((pid, thread) == (item["identity"][0], item["identity"][2])
                        and self.run.native.process_identity(pid) == item["identity"][1]):
                    return False
        return self.icon_rect(self.icon["hwnd"], self.icon["uid"]) is None
