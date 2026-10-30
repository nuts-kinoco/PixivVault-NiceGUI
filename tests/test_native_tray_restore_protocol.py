"""Restore/show must execute in one owned child call, not racing IPC threads."""
from queue import Queue
from threading import Event, Thread
from types import SimpleNamespace
import sys

import pytest

from pixivvault_nicegui import native_tray_exit as transport
from pixivvault_nicegui.native_lifecycle import bind_native_tray
from pixivvault_nicegui.preferences import PreferencesStore


TOKEN = 'b'*64
RESTORE = '_pixivvault_request_tray_restore'


class Window:
    def __init__(self):
        self.uid = 'master'
        self.events = SimpleNamespace(closed=Event())
        self.confirm_close = True
        self.calls = []
        self.visible = False
        self.iconic = True
        self.form_normal = False

    def restore(self):
        self.calls.append('restore')
        self.form_normal = True
        # Reproduce observed WinForms behavior: a hidden restore changes only
        # its cached state. The real HWND remains iconic until visible.
        if self.visible:
            self.iconic = False

    def show(self):
        self.calls.append('show')
        self.visible = True
        self.form_normal = not self.iconic

    def destroy(self):
        self.calls.append('destroy')
        self.events.closed.set()


def install(monkeypatch, window):
    monkeypatch.setitem(sys.modules, 'webview', SimpleNamespace(windows=[window]))
    transport.install_owned_tray_exit(TOKEN)
    assert hasattr(window, RESTORE)
    return getattr(window, RESTORE)


def test_child_restore_is_nonce_owned_serial_and_has_no_response(monkeypatch):
    window = Window()
    command = install(monkeypatch, window)
    assert 'dialog' not in command.__name__
    assert command('c'*64) is None and window.calls == []
    assert command(TOKEN) is None
    assert window.visible and not window.iconic and window.form_normal
    assert window.calls == ['show', 'restore'] and window.confirm_close
    window.events.closed.set()
    assert command(TOKEN) is None and window.calls == ['show', 'restore']


def test_bridge_sends_one_fixed_restore_command(tmp_path, monkeypatch):
    from nicegui import core
    from nicegui.native import native
    commands = Queue()
    unsafe_proxy_calls = []
    config = SimpleNamespace(start_args={}, main_window=SimpleNamespace(
        restore=lambda: unsafe_proxy_calls.append('restore'),
        show=lambda: unsafe_proxy_calls.append('show'), hide=lambda: None),
        on=lambda *args: None)
    app = SimpleNamespace(native=config)
    monkeypatch.setattr(core, 'app', app)
    monkeypatch.setattr(native, 'method_queue', commands)
    preferences = PreferencesStore(tmp_path/'preferences.sqlite3')
    bridge = bind_native_tray(app, preferences, factory=lambda *args: SimpleNamespace(stop=lambda: None))
    try:
        bridge.restore()
        assert unsafe_proxy_calls == []
        assert commands.get_nowait() == (RESTORE, (bridge._exit_token,), {})
        assert commands.empty()
        config.start_args['args'] = ('foreign',)
        bridge.restore()
        assert commands.empty(), 'ownership mismatch must not fall back to unsafe proxy commands'
    finally:
        bridge.close()
        preferences.close()


def test_real_native_executor_returns_no_restore_reply(monkeypatch):
    from nicegui.native import native_mode
    window = Window()
    install(monkeypatch, window)
    commands, responses = Queue(), Queue()
    stop, drained = Event(), Event()
    threads = []
    def tracked(*args, **kwargs):
        thread = Thread(*args, **kwargs)
        threads.append(thread)
        return thread
    monkeypatch.setattr(native_mode, 'Thread', tracked)
    window.drained = lambda: drained.set()
    try:
        native_mode._start_window_method_executor(window, commands, responses, stop)
        commands.put((RESTORE, (TOKEN,), {}))
        commands.put(('signal_server_shutdown', (), {}))
        commands.put(('drained', (), {}))
        assert drained.wait(3)
        assert window.calls == ['show', 'restore']
        assert window.visible and not window.iconic
        assert responses.empty()
    finally:
        stop.set()
        for thread in threads:
            thread.join(3)
        assert all(not thread.is_alive() for thread in threads)
