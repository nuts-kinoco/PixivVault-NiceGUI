"""Independent-review reproductions: no real HWND, original data or network."""
import os
from types import SimpleNamespace

import pytest

from pixivvault_nicegui import native_instance as mod
from pixivvault_nicegui.native_profile import NativeProfileStore


pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows owner-file handles')


@pytest.mark.parametrize('change', ('closed', 'replacement'))
def test_restore_true_cannot_publish_success_after_owner_record_changes(tmp_path, change):
    profile=tmp_path/'profile'
    NativeProfileStore(profile).close()
    owner=mod.NativeInstance(profile)
    assert owner.acquire()
    recorded=dict(owner._record)
    replacement=dict(recorded,pid=recorded['pid']+123456,birth=recorded['birth']+1000)
    actual=owner.backend
    calls=[]
    def process(pid):
        if pid==replacement['pid']:
            return replacement['birth'],replacement['image']
        return actual.process(pid)
    def restore(*args):
        calls.append(args)
        # The old process and its fake HWND remain alive, but the admission
        # record is no longer the pinned owner by the time restore returns.
        if change=='closed':
            owner.close()
        else:
            owner._write(replacement)
        return True
    backend=SimpleNamespace(open=actual.open,process=process,pairs=lambda:[],
        windows=lambda ids:[(12345,os.getpid(),recorded['birth'])],restore=restore)
    second=mod.NativeInstance(profile,backend=backend)
    try:
        with pytest.raises(mod.NativeInstanceOwnerClosed):
            second.restore_existing(timeout=.5)
        assert len(calls)==1
        assert second._fd is None
    finally:
        second.close();owner.close()


def test_foreground_success_does_not_skip_visible_state_stability(monkeypatch):
    api=object.__new__(mod._Windows)
    api.window_owner=lambda hwnd:(101,202)
    samples=iter((True,False,False,False))
    api.u=SimpleNamespace(ShowWindow=lambda *a:1,SetForegroundWindow=lambda *a:1,
        IsWindowVisible=lambda hwnd:next(samples,False),IsIconic=lambda hwnd:False,
        GetForegroundWindow=lambda:42,GetAncestor=lambda *a:42)
    clock=iter((0.0,.1,.2,.3,3.1))
    monkeypatch.setattr(mod.time,'monotonic',lambda:next(clock))
    monkeypatch.setattr(mod.time,'sleep',lambda _:None)
    assert api.restore(42,101,202) is False
