"""Tray exit waits for the owned native window, not an optimistic server stop.

All boundaries are synthetic. No native window, process, browser, tray icon,
registry key or network connection is created.
"""
from types import SimpleNamespace

import pytest

from pixivvault_nicegui.desktop_integration import TrayController
from pixivvault_nicegui import native_lifecycle
from pixivvault_nicegui.preferences import PreferencesStore


@pytest.mark.parametrize("failure", ["rejected", "raised"])
def test_rejected_tray_exit_preserves_restore_and_the_existing_icon(failure):
    calls = []
    icon = SimpleNamespace(stop=lambda: calls.append("icon-stop"))

    def request():
        calls.append("request")
        if failure == "raised":
            raise RuntimeError("synthetic native dispatch failure")
        return False

    controller = TrayController(
        SimpleNamespace(get_setting=lambda *_: "1"),
        show=lambda: calls.append("show"),
        hide=lambda: calls.append("hide"),
        request_shutdown=request,
        factory=lambda *_: icon,
    )
    controller.start()
    try:
        controller.exit_from_tray()
    except RuntimeError:
        # Baseline propagates the request failure after already losing the tray.
        pass

    assert controller.closed is False, "failed dispatch must not commit shutdown"
    assert controller.icon is icon, "the only restore/exit route must remain owned"
    assert calls == ["request"], "a rejected request must not stop the icon"
    controller.restore()
    assert calls[-1] == "show"
    controller.exit_from_tray()
    assert calls.count("request") == 2, "a rejected request must be retryable"


def test_accepted_tray_exit_is_pending_until_native_closed_and_is_not_resent():
    calls = []
    icon = SimpleNamespace(stop=lambda: calls.append("icon-stop"))

    def request():
        calls.append("request")
        return True

    controller = TrayController(
        SimpleNamespace(get_setting=lambda *_: "1"),
        show=lambda: calls.append("show"),
        hide=lambda: calls.append("hide"),
        request_shutdown=request,
        factory=lambda *_: icon,
    )
    controller.start()
    controller.exit_from_tray()
    controller.exit_from_tray()

    assert calls == ["request"], "accepting a command is not native close evidence"
    assert controller.icon is icon
    assert controller.closed is False
    controller.restore()
    assert calls[-1] == "show", "pending exit must not remove recovery"


class FakeNative:
    def __init__(self, calls):
        self.handlers = {}
        self.window_args = {}
        self.start_args = {}
        self.main_window = SimpleNamespace(
            restore=lambda: calls.append("restore"),
            show=lambda: calls.append("show"),
            hide=lambda: calls.append("hide"),
        )

    def on(self, event, handler):
        self.handlers[event] = handler


def make_bridge(tmp_path, monkeypatch, dispatch):
    prefs = PreferencesStore(tmp_path / "prefs.sqlite3")
    calls = []
    callbacks = {}
    native = FakeNative(calls)
    app = SimpleNamespace(
        native=native,
        shutdown=lambda: calls.append("server-shutdown"),
    )

    # This bounded application adapter sends only to the existing native owner.
    # Raising=False lets the original implementation reach the behavior assertions.
    monkeypatch.setattr(
        native_lifecycle, "send_owned_tray_exit", dispatch, raising=False
    )
    monkeypatch.setattr(native_lifecycle, 'send_owned_tray_restore',
                        lambda *_: calls.append('restore-command') or True)

    def factory(show, exit):
        callbacks.update(show=show, exit=exit)
        return SimpleNamespace(stop=lambda: calls.append("icon-stop"))

    bridge = native_lifecycle.bind_native_tray(app, prefs, factory=factory)
    native.handlers["shown"]()
    return bridge, native, app, prefs, callbacks, calls


def test_native_tray_exit_keeps_server_and_icon_until_owned_closed(
    tmp_path, monkeypatch
):
    requests = []
    bridge, native, app, prefs, callbacks, calls = make_bridge(
        tmp_path,
        monkeypatch,
        lambda owner, token: requests.append((owner, token)) or True,
    )
    try:
        callbacks["exit"]()
        callbacks["exit"]()

        assert "server-shutdown" not in calls, (
            "app.shutdown destroys with confirm_close=True and stops the server "
            "before the user can answer the resulting native dialog"
        )
        assert "icon-stop" not in calls
        assert len(requests) == 1
        assert requests[0][0] is app
        assert isinstance(requests[0][1], str) and requests[0][1]
        assert bridge.available
        callbacks["show"]()
        assert calls[-1] == 'restore-command'
        assert 'restore' not in calls and 'show' not in calls

        native.handlers["closed"]()
        bridge.close()
        assert calls.count("icon-stop") == 1
        assert "server-shutdown" not in calls
        assert bridge.closed and not bridge.available
    finally:
        bridge.close()
        prefs.close()


def test_native_dispatch_failure_preserves_icon_then_accepts_explicit_retry(
    tmp_path, monkeypatch
):
    attempts = []

    def dispatch(owner, token):
        attempts.append((owner, token))
        if len(attempts) == 1:
            raise RuntimeError("synthetic native queue unavailable")
        return True

    bridge, native, app, prefs, callbacks, calls = make_bridge(
        tmp_path, monkeypatch, dispatch
    )
    try:
        try:
            callbacks["exit"]()
        except RuntimeError:
            pass
        assert bridge.available, "failed native request must retain the tray"
        assert not bridge.closed and not bridge.controller.closed
        assert calls == [], "failure must not stop either server or icon"
        callbacks["show"]()
        assert calls == ['restore-command']
        callbacks["exit"]()
        assert len(attempts) == 2
        assert attempts[0][0] is app and attempts[1][0] is app
        assert attempts[0][1] == attempts[1][1]
        assert "server-shutdown" not in calls and "icon-stop" not in calls
        native.handlers["closed"]()
        assert calls.count("icon-stop") == 1
    finally:
        bridge.close()
        prefs.close()
