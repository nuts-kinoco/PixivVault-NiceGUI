"""Tray close/restore commands for the single owned native window.

NiceGUI 3.18 forwards callable startup hooks through its spawn boundary. The
hook adds fixed methods to this application's Window instance, never a class or
framework source. The parent uses the existing native command queue without a
response waiter. A dedicated pipe reports the outcome without sharing NiceGUI responses.
"""
from __future__ import annotations

import hmac
import re
from threading import Lock, RLock

_METHOD = "_pixivvault_request_tray_exit"
_RESTORE_METHOD = "_pixivvault_request_tray_restore"


def _confirm_exit(window, *, active):
    """Display the original yes/no prompt on the owned form's UI thread."""
    from System import Action
    from System.Windows.Forms import MessageBox, MessageBoxButtons, MessageBoxIcon, DialogResult
    message = ('ダウンロードが実行中です。アプリを終了しますか？' if active is True
               else 'アプリを終了します。よろしいですか？' if active is False
               else '処理中または状態を確認できません。終了しますか？')
    result = []
    def show():
        result.append(MessageBox.Show(window.native, message, '終了確認',
                                      MessageBoxButtons.YesNo, MessageBoxIcon.Question))
    window.native.Invoke(Action(show))
    return bool(result and result[0] == DialogResult.Yes)


def _valid_token(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def configure_owned_tray_exit(native_config, token, sender=None):
    """Reserve only an unused startup hook; leave foreign configuration intact."""
    if not _valid_token(token):
        return False
    start_args = getattr(native_config, "start_args", None)
    if not isinstance(start_args, dict):
        return False
    if (start_args.get("func") is install_owned_tray_exit
            and start_args.get("args") == (token, sender)):
        return True
    if "func" in start_args or "args" in start_args:
        return False
    start_args.update(func=install_owned_tray_exit, args=(token, sender))
    return True


def release_owned_tray_exit(native_config, token, sender=None):
    start_args = getattr(native_config, "start_args", None)
    if (isinstance(start_args, dict)
            and start_args.get("func") is install_owned_tray_exit
            and start_args.get("args") == (token, sender)):
        start_args.pop("func")
        start_args.pop("args")


def install_owned_tray_exit(token, sender=None):
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
    send_lock = RLock()
    allow_close = [False]
    def send(result):
        if sender is not None:
            with send_lock:
                try: sender.send(result)
                except Exception: pass
    from .native_webview_visibility import install_owned_visibility
    install_owned_visibility(window)
    from .native_restore_size import install_window_restore_size
    install_window_restore_size(window)

    def _close_owned_for_tray(supplied_token, needs_confirmation=True, window_request=False):
        # The native executor puts a response only when a method returns a
        # non-None value, or "dialog" occurs in its __name__. Keep BOTH parts
        # of this contract: this name contains no "dialog", and every path
        # returns None. Restores/getters must never consume a stray response.
        if (not _valid_token(supplied_token)
                or not hmac.compare_digest(supplied_token, token)):
            return None
        if not gate.acquire(blocking=False):
            # The parent may hold an idle admission fence for this request.
            # Explicit failure lets it release the fence and allow a retry.
            send('failed')
            return None
        outcome = 'failed'
        try:
            if window.events.closed.is_set():
                outcome = 'closed'
            elif any(candidate is window for candidate in webview.windows):
                previous = window.confirm_close
                try:
                    if not window_request:
                        window.show()
                        window.restore()
                    if window_request or needs_confirmation is not False:
                        mode = (needs_confirmation if window_request else
                                ('active' if needs_confirmation == 'active' else
                                 'unknown' if needs_confirmation else 'idle'))
                        answer=_confirm_exit(window, active={'active': True, 'idle': False}.get(mode))
                        if answer is not True:
                            outcome = 'cancelled' if answer is False else 'failed'
                            return None
                    window.confirm_close = False
                    allow_close[0] = True
                    try:
                        window.destroy()
                    finally:
                        allow_close[0] = False
                    outcome = 'closed' if window.events.closed.is_set() else 'failed'
                finally:
                    if not window.events.closed.is_set():window.confirm_close = previous
        except Exception:
            outcome = 'failed'
        finally:
            # A reply permits the parent to submit another request. Release
            # first, otherwise a fast retry can be silently discarded.
            gate.release()
            send(outcome)
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
    closing = getattr(window.events, 'closing', None)
    if sender is not None and closing is not None:
        def on_closing():
            if allow_close[0]:
                return True
            send('close_request')
            return False
        window.events.closing += on_closing
    send('ready')
    return None


def send_owned_tray_exit(app, token, needs_confirmation=True):
    return _send_owned_tray_method(app, token, _METHOD, needs_confirmation)


def send_owned_tray_restore(app, token):
    return _send_owned_tray_method(app, token, _RESTORE_METHOD)


def send_owned_window_exit(app, token, needs_confirmation):
    return _send_owned_tray_method(app, token, _METHOD, needs_confirmation, window_request=True)


def _send_owned_tray_method(app, token, method, needs_confirmation=None, *, window_request=False):
    """Queue one allowlisted method to this app's existing native owner."""
    if not _valid_token(token) or method not in (_METHOD, _RESTORE_METHOD):
        return False
    from nicegui import core
    from nicegui.native import native

    if app is not core.app or getattr(app.native, "main_window", None) is None:
        return False
    start_args = app.native.start_args
    if (start_args.get("func") is not install_owned_tray_exit
            or not isinstance(start_args.get("args"), tuple)
            or not start_args["args"] or start_args["args"][0] != token):
        return False
    command_queue = native.method_queue
    if command_queue is None:
        return False
    try:
        # WindowProxy lacks a public no-response custom-method API in 3.18.
        # Scope this pinned-version dependency to its existing queue protocol.
        args = ((token, needs_confirmation, True) if window_request else (token, needs_confirmation)) if method == _METHOD else (token,)
        command_queue.put_nowait((method, args, {}))
    except Exception:
        return False
    return True
