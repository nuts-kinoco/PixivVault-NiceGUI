from types import SimpleNamespace
from pixivvault_nicegui.native_lifecycle import bind_native_tray
from pixivvault_nicegui.preferences import PreferencesStore

class Native:
    def __init__(self,calls):
        self.handlers={}
        self.window_args={};self.start_args={}
        self.main_window=SimpleNamespace(restore=lambda:calls.append('restore'),show=lambda:calls.append('show'),hide=lambda:calls.append('hide'))
    def on(self,event,callback):self.handlers[event]=callback

def test_owned_native_events_gate_minimize_restore_and_cleanup(tmp_path,monkeypatch):
    prefs=PreferencesStore(tmp_path/'prefs.sqlite3');calls=[];callbacks={}
    native=Native(calls);app=SimpleNamespace(native=native,shutdown=lambda:calls.append('shutdown'))
    monkeypatch.setattr('pixivvault_nicegui.native_lifecycle.send_owned_tray_exit',lambda *_:calls.append('request') or True)
    monkeypatch.setattr('pixivvault_nicegui.native_lifecycle.send_owned_tray_restore',lambda *_:calls.append('restore-command') or True)
    def factory(show,exit):
        callbacks.update(show=show,exit=exit);calls.append('icon')
        return SimpleNamespace(stop=lambda:calls.append('stop'))
    bridge=bind_native_tray(app,prefs,factory=factory)
    try:
        assert not bridge.available and calls==[]
        native.handlers['minimized']();assert calls==[]
        native.handlers['shown']();native.handlers['shown']()
        assert bridge.available and calls==['icon']
        native.handlers['minimized']();assert calls[-1]=='hide'
        callbacks['show']();assert calls[-1]=='restore-command'
        assert 'restore' not in calls and 'show' not in calls
        prefs.update({'minimize_to_tray':'0'},expected_revision=prefs.snapshot().revision)
        count=len(calls);native.handlers['minimized']();assert len(calls)==count
        callbacks['exit']();assert calls[-1]=='request'
        assert 'stop' not in calls and 'shutdown' not in calls
        native.handlers['closed']();bridge.close();assert calls.count('stop')==1
        assert 'shutdown' not in calls
    finally:bridge.close();prefs.close()

def test_missing_dependency_and_failed_icon_never_hide_or_spawn(tmp_path,monkeypatch):
    prefs=PreferencesStore(tmp_path/'prefs.sqlite3');calls=[];app=SimpleNamespace(native=Native(calls),shutdown=lambda:None)
    monkeypatch.setattr('pixivvault_nicegui.native_lifecycle.find_spec',lambda _:None)
    assert bind_native_tray(app,prefs) is None and not app.native.handlers
    def unavailable(*_):raise RuntimeError('fixture unavailable')
    bridge=bind_native_tray(app,prefs,factory=unavailable)
    try:
        app.native.handlers['shown']();app.native.handlers['minimized']()
        assert not bridge.available and calls==[]
    finally:bridge.close();prefs.close()
