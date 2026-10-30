"""One-way tray close/restore commands for the single owned native window.

NiceGUI 3.18 forwards callable startup hooks through its spawn boundary. The
hook adds fixed methods to this application's Window instance, never a class or
framework source. The parent uses the existing native command queue without a
response waiter. No JS/HTTP API, extra process, pipe or OS hook is introduced.
"""
from __future__ import annotations

import hmac
import re
from threading import Lock

_METHOD = "_pixivvault_request_tray_exit"
_RESTORE_METHOD = "_pixivvault_request_tray_restore"


def _valid_token(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def configure_owned_tray_exit(native_config, token):
    """Reserve only an unused startup hook; leave foreign configuration intact."""
    if not _valid_token(token):
        return False
    start_args = getattr(native_config, "start_args", None)
    if not isinstance(start_args, dict):
        return False
    if (start_args.get("func") is install_owned_tray_exit
            and start_args.get("args") == (token,)):
        return True
    if "func" in start_args or "args" in start_args:
        return False
    start_args.update(func=install_owned_tray_exit, args=(token,))
    return True


def release_owned_tray_exit(native_config, token):
    start_args = getattr(native_config, "start_args", None)
    if (isinstance(start_args, dict)
            and start_args.get("func") is install_owned_tray_exit
            and start_args.get("args") == (token,)):
        start_args.pop("func")
        start_args.pop("args")


def install_owned_tray_exit(token):
    """Run in the native child; attach only to its unique master Window."""
    if not _valid_token(token):
        return None
    import webview

    windows = tuple(webview.windows)
    if len(windows) != 1:
        return None
    window = windows[0]
    if (getattr(window, "uid", None) != "master"
            or hasattr(window, _METHOD) or hasattr(window, _RESTORE_METHOD)):
        return None
    gate = Lock()

    def _close_owned_for_tray(supplied_token):
        # The native executor puts a response only when a method returns a
        # non-None value, or "dialog" occurs in its __name__. Keep BOTH parts
        # of this contract: this name contains no "dialog", and every path
        # returns None. Restores/getters must never consume a stray response.
        if (not _valid_token(supplied_token)
                or not hmac.compare_digest(supplied_token, token)
                or not gate.acquire(blocking=False)):
            return None
        try:
            if window.events.closed.is_set():
                return None
            if not any(candidate is window for candidate in webview.windows):
                return None
            previous = window.confirm_close
            try:
                window.confirm_close = False
                window.destroy()
            except Exception:
                # A rejected/failed close leaves the tray and server alive.
                # Do not include supplied arguments or platform errors in logs.
                pass
            finally:
                if not window.events.closed.is_set():
                    window.confirm_close = previous
        finally:
            gate.release()
        return None

    def _restore_owned_for_tray(supplied_token):
        if (not _valid_token(supplied_token)
                or not hmac.compare_digest(supplied_token, token)
                or not gate.acquire(blocking=False)):
            return None
        try:
            if (window.events.closed.is_set()
                    or not any(candidate is window for candidate in webview.windows)):
                return None
            # NiceGUI's executor starts a separate thread per queued method.
            # Keep show/restore in THIS method: two proxy calls can race.
            # WinForms may cache Normal while hidden without clearing the
            # HWND's iconic state, and Show then restores that Minimized state.
            # Make the same owned window visible before restoring it.
            window.show()
            if not window.events.closed.is_set():
                window.restore()
        except Exception:
            # Keep the tray available for an explicit retry. No private token
            # or native exception is logged or returned via response_queue.
            pass
        finally:
            gate.release()
        return None

    setattr(window, _METHOD, _close_owned_for_tray)
    setattr(window, _RESTORE_METHOD, _restore_owned_for_tray)
    return None


def send_owned_tray_exit(app, token):
    return _send_owned_tray_method(app, token, _METHOD)


def send_owned_tray_restore(app, token):
    return _send_owned_tray_method(app, token, _RESTORE_METHOD)


def _send_owned_tray_method(app, token, method):
    """Queue one allowlisted method to this app's existing native owner."""
    if not _valid_token(token) or method not in (_METHOD, _RESTORE_METHOD):
        return False
    from nicegui import core
    from nicegui.native import native

    if app is not core.app or getattr(app.native, "main_window", None) is None:
        return False
    start_args = app.native.start_args
    if (start_args.get("func") is not install_owned_tray_exit
            or start_args.get("args") != (token,)):
        return False
    command_queue = native.method_queue
    if command_queue is None:
        return False
    try:
        # WindowProxy lacks a public no-response custom-method API in 3.18.
        # Scope this pinned-version dependency to its existing queue protocol.
        command_queue.put_nowait((method, (token,), {}))
    except Exception:
        return False
    return True
