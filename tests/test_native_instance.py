"""Actual Windows exclusive owner handles; all profiles/data are generated."""
import json
import os
from types import SimpleNamespace

import pytest

from pixivvault_nicegui.native_instance import NativeInstance, NativeInstanceError, OWNER_FILE, _Windows
from pixivvault_nicegui.native_profile import NativeProfileStore


pytestmark=pytest.mark.skipif(os.name!='nt',reason='Windows owner handle required')


@pytest.fixture
def profile(tmp_path):
    path=tmp_path/'owned-profile'
    with_store=NativeProfileStore(path)
    with_store.close()
    return path


def test_handle_excludes_second_owner_but_allows_record_read(profile):
    one,two=NativeInstance(profile),NativeInstance(profile)
    try:
        assert one.acquire()
        assert not two.acquire()
        reader=two.backend.open(two.path,writable=False)
        try:
            record=two._read(reader)
            assert record['pid']==os.getpid() and record['phase']=='running'
        finally:
            os.close(reader)
    finally:
        one.close();two.close()
    assert two.acquire()
    two.close();two.close()


def test_foreign_and_oversized_owner_record_preserved(profile):
    owner=profile/OWNER_FILE
    for payload in (b'{"foreign":true}', b'x'*2049):
        owner.write_bytes(payload)
        instance=NativeInstance(profile)
        with pytest.raises(NativeInstanceError):
            instance.acquire()
        assert owner.read_bytes()==payload
        assert instance._fd is None


def test_a_stale_running_record_can_be_replaced_only_after_its_birth_changed(profile):
    one=NativeInstance(profile)
    assert one.acquire()
    record=dict(one._record)
    one.close()
    record['birth']-=1
    (profile/OWNER_FILE).write_text(json.dumps(record),encoding='ascii')
    two=NativeInstance(profile)
    assert two.acquire()
    two.close()


def test_live_owner_record_without_exclusive_handle_is_not_adopted(profile):
    one=NativeInstance(profile)
    assert one.acquire()
    record=dict(one._record)
    one.close()
    (profile/OWNER_FILE).write_text(json.dumps(record),encoding='ascii')
    two=NativeInstance(profile)
    with pytest.raises(NativeInstanceError):
        two.acquire()
    assert json.loads((profile/OWNER_FILE).read_text())==record


def test_second_profile_has_independent_owner(profile,tmp_path):
    other=tmp_path/'other'
    NativeProfileStore(other).close()
    one,two=NativeInstance(profile),NativeInstance(other)
    try:
        assert one.acquire() and two.acquire()
        assert one.namespace != two.namespace
    finally:
        two.close();one.close()


def test_only_verified_unique_owned_window_can_be_restored(profile):
    one=NativeInstance(profile)
    assert one.acquire()
    actual=one.backend
    calls=[]
    fake=SimpleNamespace(open=actual.open,process=actual.process,pairs=lambda:[],
        windows=lambda identities:[(111,os.getpid(),one._record['birth'])],
        restore=lambda *args:calls.append(args) or True)
    two=NativeInstance(profile,backend=fake)
    try:
        assert two.restore_existing(timeout=.5)
        assert calls==[(111,os.getpid(),one._record['birth'])]
        fake.windows=lambda identities:[(111,1,2),(222,3,4)]
        with pytest.raises(NativeInstanceError):
            two.restore_existing(timeout=.5)
        assert len(calls)==1
    finally:
        one.close()


def test_nonowned_profile_is_never_marked_or_created(tmp_path):
    foreign=tmp_path/'foreign'
    foreign.mkdir()
    with pytest.raises(ValueError):
        NativeInstance(foreign)
    assert list(foreign.iterdir())==[]


def test_profile_warm_start_returns_before_provision_or_server(profile,monkeypatch):
    from pixivvault_nicegui import bootstrap,native_instance,native_profile
    from test_native_profile_integration import _fake_native
    calls=_fake_native(monkeypatch)
    admitted=[]
    monkeypatch.setattr(native_instance,'claim_or_restore',lambda root: admitted.append(root))
    monkeypatch.setattr(native_profile,'provision_storage',lambda *args:pytest.fail('warm start must not provision'))
    monkeypatch.setattr(bootstrap,'register_pages',lambda **kw:pytest.fail('warm start must not start jobs'))
    assert bootstrap.main(['--native','--profile-root',str(profile),'--','pixivvault://start'])==0
    assert admitted==[profile] and calls==[]


def test_owner_is_released_when_native_startup_fails(profile,monkeypatch):
    from pixivvault_nicegui import bootstrap
    from test_native_profile_integration import _fake_native
    _fake_native(monkeypatch)
    def reject(**kwargs):
        raise RuntimeError('generated registration failure')
    monkeypatch.setattr(bootstrap,'register_pages',reject)
    with pytest.raises(RuntimeError,match='generated'):
        bootstrap.main(['--native','--profile-root',str(profile)])
    owner=NativeInstance(profile)
    assert owner.acquire()
    owner.close()


def test_visible_restored_window_is_not_treated_as_launch_failure_on_foreground_denial(monkeypatch):
    """Windows may deny SetForegroundWindow even though SW_RESTORE worked.

    A second --noconsole launch must not turn a successfully restored owned
    window into an unhandled-error MessageBox merely because foreground rights
    were withheld by the desktop. The request still attempts focus once.
    """
    from pixivvault_nicegui import native_instance
    calls=[]
    panel=SimpleNamespace(
        ShowWindow=lambda wh,mode: calls.append(('show',wh,mode)) or 1,
        SetForegroundWindow=lambda wh: calls.append(('focus',wh)) or 0,
        IsWindowVisible=lambda wh: True,
        IsIconic=lambda wh: False,
        GetForegroundWindow=lambda: 99999,
        GetAncestor=lambda wh,kind: 99999,
    )
    api=object.__new__(_Windows)
    api.u=panel
    api.window_owner=lambda wh:(101,202)
    ticker=iter((0.0,1.0,2.0,3.5,4.0,5.0))
    monkeypatch.setattr(native_instance.time,'monotonic',lambda:next(ticker))
    monkeypatch.setattr(native_instance.time,'sleep',lambda _:None)
    assert api.restore(42,101,202)
    assert ('focus',42) in calls


def test_hidden_minimized_winforms_window_needs_show_before_restore(monkeypatch):
    """The real pywebview tray case can re-iconify a previously hidden form."""
    from pixivvault_nicegui import native_instance
    state={'visible':False,'iconic':True}
    calls=[]
    def show(hwnd, mode):
        calls.append(mode)
        if mode==5:
            state['visible']=True
        elif mode==9 and state['visible']:
            state['iconic']=False
        return 1
    api=object.__new__(_Windows)
    api.window_owner=lambda wh:(101,202)
    api.u=SimpleNamespace(ShowWindow=show,
        SetForegroundWindow=lambda wh:1,
        IsWindowVisible=lambda wh:state['visible'],
        IsIconic=lambda wh:state['iconic'],
        GetForegroundWindow=lambda:42,
        GetAncestor=lambda wh,kind:42)
    ticker=iter((0.0,1.0,2.0,3.5,4.0,5.0))
    monkeypatch.setattr(native_instance.time,'monotonic',lambda:next(ticker))
    monkeypatch.setattr(native_instance.time,'sleep',lambda _:None)
    assert api.restore(42,101,202)
    assert calls[:2]==[5,9]


def test_owner_closing_during_warm_start_reacquires_fresh_lock(profile, monkeypatch):
    from pixivvault_nicegui import native_instance as mod
    old=NativeInstance(profile)
    assert old.acquire()
    actual=mod.NativeInstance.restore_existing
    readings=[]
    def close_during_restore(self, timeout=30):
        readings.append(timeout)
        old.close()  # The older owner releases its lock after refusing acquire.
        return actual(self, timeout=.5)
    monkeypatch.setattr(mod.NativeInstance,'restore_existing',close_during_restore)
    new=mod.claim_or_restore(profile,timeout=1)
    try:
        assert new is not None and new._fd is not None
        assert len(readings)==1
        assert new._record['pid']==os.getpid() and new._record['phase']=='running'
    finally:
        new.close();old.close()


def test_broken_owner_json_stays_fail_closed_without_reclaim(profile, monkeypatch):
    from pixivvault_nicegui import native_instance as mod
    p=profile/OWNER_FILE
    p.write_text('{broken',encoding='utf-8')
    with pytest.raises(NativeInstanceError):
        mod.claim_or_restore(profile,timeout=.5)
    assert p.read_text(encoding='utf-8')=='{broken'


def test_profile_registration_failure_after_native_claim_closes_owner(tmp_path,monkeypatch):
    from pixivvault_nicegui import bootstrap,native_instance,native_profile,uri_launch
    from test_native_profile_integration import _fake_native
    root=tmp_path/'profile';managed=tmp_path/'managed';managed.mkdir()
    secret=tmp_path/'synthetic-credentials'/'cookie.txt';secret.parent.mkdir()
    secret.write_text('synthetic-only',encoding='utf-8')
    store=native_profile.NativeProfileStore(root)
    store.update(cookie_file=secret,save_parent=managed,expected_revision=0)
    store.close()
    _fake_native(monkeypatch)
    captured=[]
    original=native_instance.claim_or_restore
    def claimed(*args,**kwargs):
        result=original(*args,**kwargs);captured.append(result);return result
    monkeypatch.setattr(native_instance,'claim_or_restore',claimed)
    monkeypatch.setattr(uri_launch,'build_protocol_command',
        lambda *a,**kw: (_ for _ in ()).throw(ValueError('synthetic registry refusal')))
    monkeypatch.setattr(bootstrap,'register_pages',lambda **kw:pytest.fail('cannot reach UI'))
    try:
        with pytest.raises(ValueError,match='synthetic registry refusal'):
            bootstrap.main(['--native','--profile-root',str(root)])
        assert captured and captured[0] is not None
        assert captured[0]._fd is None, 'failed admission must release Windows lock'
        replacement=native_instance.NativeInstance(root)
        try:assert replacement.acquire()
        finally:replacement.close()
    finally:
        for own in captured:
            if own is not None:own.close()


def test_owner_record_is_fixed_length_and_valid_before_and_after_close(profile):
    from pixivvault_nicegui.native_instance import MAX_BYTES
    instance=NativeInstance(profile)
    try:
        assert instance.acquire()
        raw=(profile/OWNER_FILE).read_bytes()
        assert len(raw)==MAX_BYTES
        assert json.loads(raw)['phase']=='running'
        instance.close()
        final=(profile/OWNER_FILE).read_bytes()
        assert len(final)==len(raw)==MAX_BYTES
        assert json.loads(final)['phase']=='closed'
        assert final.endswith(b' ')
    finally:instance.close()


def test_in_progress_locked_owner_record_can_be_retried_without_adoption(profile,monkeypatch):
    from pixivvault_nicegui import native_instance as mod
    first=NativeInstance(profile)
    assert first.acquire()
    backend=first.backend
    mocked=SimpleNamespace(open=backend.open,process=backend.process,pairs=lambda:(),
        windows=lambda identities:[(123,os.getpid(),first._record['birth'])],
        restore=lambda *args:True)
    second=NativeInstance(profile,backend=mocked)
    orig=second._read
    seen=[]
    def interrupted(fd):
        seen.append(True)
        if len(seen)==1:raise NativeInstanceError('synthetic partial JSON')
        return orig(fd)
    monkeypatch.setattr(second,'_read',interrupted)
    try:
        assert second.restore_existing(timeout=.6)
        # One interrupted read, one entry read, one post-restore owner check.
        assert len(seen)==3
        assert not second._record and second._fd is None
    finally:first.close()


@pytest.mark.parametrize('reused_pid', (False, True, 'old_alive'))
def test_running_owner_switch_is_only_retried_after_previous_birth_is_dead(profile,monkeypatch,reused_pid):
    from pixivvault_nicegui import native_instance as mod
    first=NativeInstance(profile)
    assert first.acquire()
    old=dict(first._record)
    replacement=dict(old,pid=old['pid']+1000,birth=old['birth']+2000)
    rounds=[]
    def process(pid):
        if pid==os.getpid() and len(rounds)<2:return old['birth'],old['image']
        if pid==old['pid'] and reused_pid=='old_alive':
            return old['birth'],old['image']
        if pid==old['pid']:
            return ((old['birth']+9000,old['image']) if reused_pid else None)
        if pid==replacement['pid']:return replacement['birth'],replacement['image']
        return None
    fake=SimpleNamespace(open=first.backend.open,process=process,pairs=lambda:(),
        windows=lambda ids:(),restore=lambda *a:True)
    second=NativeInstance(profile,backend=fake)
    def read(fd):
        rounds.append(True)
        return old if len(rounds)==1 else replacement
    monkeypatch.setattr(second,'_read',read)
    monkeypatch.setattr(mod.time,'sleep',lambda _:None)
    try:
        with pytest.raises(mod.NativeInstanceOwnerClosed):
            second.restore_existing(timeout=.5)
        assert len(rounds)==2
    finally:first.close()


def test_warm_window_disappearing_during_owner_close_retries_admission(profile):
    """An exiting owner can stay alive after closing its HWND and lock.

    A warm launch must re-read the pinned record, then acquire exclusively;
    it must not adopt a foreign HWND or fail before observing 'closed'.
    """
    from pixivvault_nicegui import native_instance as mod
    first=NativeInstance(profile)
    assert first.acquire()
    actual=first.backend
    attempts=[]

    def disappearing_window(*args):
        attempts.append(args)
        first.close()  # The old PID remains alive throughout this test.
        raise NativeInstanceError('synthetic HWND destroyed during close')

    second=NativeInstance(profile,backend=SimpleNamespace(
        open=actual.open,process=actual.process,pairs=lambda:[],
        windows=lambda identities:[(12345,os.getpid(),first._record['birth'])],
        restore=disappearing_window))
    try:
        assert not second.acquire()
        with pytest.raises(mod.NativeInstanceOwnerClosed):
            second.restore_existing(timeout=.8)
        assert len(attempts)==1
        assert second.acquire()  # Fresh deny-write/delete handle is mandatory.
    finally:
        second.close()
        first.close()
