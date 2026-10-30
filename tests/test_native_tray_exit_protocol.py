"""Synthetic checks of the pinned native no-response tray-exit boundary."""
from queue import Queue
from threading import Event, Thread
from types import SimpleNamespace
import pickle
import sys

import pytest

from pixivvault_nicegui import native_tray_exit as transport

TOKEN = "a" * 64


class Window:
    def __init__(self, mode="closed", uid="master"):
        self.uid = uid
        self.confirm_close = True
        self.events = SimpleNamespace(closed=Event())
        self.mode = mode
        self.close_confirmation_values = []

    def destroy(self):
        self.close_confirmation_values.append(self.confirm_close)
        if self.mode == "raised":
            raise RuntimeError("synthetic owned-window close failure")
        if self.mode == "closed":
            self.events.closed.set()


def install(monkeypatch, windows):
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(windows=windows))
    assert transport.install_owned_tray_exit(TOKEN) is None


def test_existing_startup_function_and_arguments_are_not_overwritten():
    foreign = lambda: None
    native = SimpleNamespace(start_args={"func": foreign, "args": ("foreign",)})
    before = dict(native.start_args)
    assert transport.configure_owned_tray_exit(native, TOKEN) is False
    assert native.start_args == before


def test_spawn_configuration_is_picklable_and_release_does_not_remove_foreign_data():
    native = SimpleNamespace(start_args={"debug": False})
    assert transport.configure_owned_tray_exit(native, TOKEN)
    restored = pickle.loads(pickle.dumps(native.start_args))
    assert restored["func"] is transport.install_owned_tray_exit
    assert restored["args"] == (TOKEN,)
    native.start_args["args"] = ("replaced",)
    transport.release_owned_tray_exit(native, TOKEN)
    assert native.start_args["args"] == ("replaced",)
    native.start_args["args"] = (TOKEN,)
    transport.release_owned_tray_exit(native, TOKEN)
    assert native.start_args == {"debug": False}


def test_only_unique_owned_window_and_matching_nonce_can_close(monkeypatch):
    own, other = Window(), Window(uid="another-window")
    install(monkeypatch, [own, other])
    assert not hasattr(own, transport._METHOD)
    assert not hasattr(other, transport._METHOD)

    install(monkeypatch, [own])
    command = getattr(own, transport._METHOD)
    assert "dialog" not in command.__name__
    assert own.confirm_close is True, "ordinary x-close still requires confirmation"
    assert command("b" * 64) is None
    assert command("wrong") is None
    assert own.close_confirmation_values == []

    assert command(TOKEN) is None
    assert own.close_confirmation_values == [False]
    assert own.events.closed.is_set()
    assert other.confirm_close is True and other.close_confirmation_values == []
    assert command(TOKEN) is None
    assert own.close_confirmation_values == [False]


@pytest.mark.parametrize("mode", ["raised", "vetoed"])
def test_failed_or_vetoed_native_close_restores_ordinary_x_confirmation(
    monkeypatch, mode
):
    own = Window(mode=mode)
    install(monkeypatch, [own])
    command = getattr(own, transport._METHOD)

    assert command(TOKEN) is None
    assert not own.events.closed.is_set()
    assert own.confirm_close is True
    assert own.close_confirmation_values == [False]

    own.mode = "closed"
    assert command(TOKEN) is None
    assert own.events.closed.is_set()
    assert own.close_confirmation_values == [False, False]


def test_sender_uses_only_the_existing_owner_and_one_fixed_no_response_command(
    monkeypatch
):
    from nicegui import core
    from nicegui.native import native

    commands = Queue()
    app = SimpleNamespace(native=SimpleNamespace(start_args={}, main_window=object()))
    assert transport.configure_owned_tray_exit(app.native, TOKEN)
    monkeypatch.setattr(core, "app", app)
    monkeypatch.setattr(native, "method_queue", commands)

    assert transport.send_owned_tray_exit(SimpleNamespace(native=app.native), TOKEN) is False
    assert transport.send_owned_tray_exit(app, "b" * 64) is False
    assert commands.empty()
    assert transport.send_owned_tray_exit(app, TOKEN) is True
    assert commands.get_nowait() == (transport._METHOD, (TOKEN,), {})
    assert commands.empty()


def test_real_installed_executor_leaves_no_response_for_later_window_getters(
    monkeypatch
):
    # Import the real installed 3.18 command executor before replacing only the
    # child's webview registry. No actual GUI, process or platform module runs.
    from nicegui.native import native_mode

    own = Window()
    install(monkeypatch, [own])
    commands, responses = Queue(), Queue()
    stop, drained = Event(), Event()
    threads = []

    def tracked_thread(*args, **kwargs):
        thread = Thread(*args, **kwargs)
        threads.append(thread)
        return thread

    monkeypatch.setattr(native_mode, "Thread", tracked_thread)
    own.drained = lambda: drained.set()
    try:
        native_mode._start_window_method_executor(own, commands, responses, stop)
        commands.put((transport._METHOD, (TOKEN,), {}))
        # This is the real framework barrier which joins earlier method threads.
        commands.put(("signal_server_shutdown", (), {}))
        commands.put(("drained", (), {}))
        assert drained.wait(3), "owned method/executor did not finish"
        assert own.close_confirmation_values == [False]
        assert own.events.closed.is_set()
    finally:
        stop.set()
        for thread in tuple(threads):
            thread.join(3)
    assert threads and all(not thread.is_alive() for thread in threads)
    assert responses.empty(), "tray result must not corrupt a later get_size response"
