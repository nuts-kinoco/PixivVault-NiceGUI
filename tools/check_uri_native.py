"""Isolated packaged-product URI cold/warm checker. Never registers a URI scheme.

Only an explicit packaged exe (or a clearly marked source diagnostic entry)
receives the literal pixivvault://start argument. No global scheme is changed.
No Cookie, Pixiv credentials, real DB, network API, or foreign window is used.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import sys
import time
import subprocess
import hashlib
import threading
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))
from tools import check_native_window as native
from tools.check_native_tray_window import helper_shas, file_sha, loopback_listener
from tools.native_tray_ui import TrayProbeError
from tools.check_library_calendar_slider_owned import OwnedTree
from tools.run_browser_checks import source_hashes
from tools.check_native_tray_window import reject_links

URI = 'pixivvault://start'


class AcceptanceError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def error_code(error):
    return error.code if isinstance(error, (AcceptanceError, TrayProbeError)) else type(error).__name__


def require(condition, label):
    if not condition:
        raise AcceptanceError(label)


class _StartupInfo(ctypes.Structure):
    _fields_ = [('cbSize', w.DWORD), ('fMask', w.ULONG), ('hwnd', w.HWND),
        ('lpVerb', w.LPCWSTR), ('lpFile', w.LPCWSTR), ('lpParameters', w.LPCWSTR),
        ('lpDirectory', w.LPCWSTR), ('nShow', ctypes.c_int), ('hInstApp', w.HINSTANCE),
        ('lpIDList', ctypes.c_void_p), ('lpClass', w.LPCWSTR), ('hkeyClass', w.HKEY),
        ('dwHotKey', w.DWORD), ('hIconOrMonitor', w.HANDLE), ('hProcess', w.HANDLE)]


def shell_launch(exe, arguments, *, cwd):
    """Dispatch explicit exe pathname; never resolve URI through HKCU/Classes."""
    shell=ctypes.WinDLL('shell32',use_last_error=True)
    shell.ShellExecuteExW.argtypes=[ctypes.POINTER(_StartupInfo)]
    shell.ShellExecuteExW.restype=w.BOOL
    info=_StartupInfo()
    info.cbSize=ctypes.sizeof(info)
    info.fMask=0x40|0x100|0x400  # SEE_MASK_NOCLOSEPROCESS|NOASYNC|FLAG_NO_UI
    info.lpVerb='open'
    info.lpFile=str(exe)
    info.lpParameters=__import__('subprocess').list2cmdline(arguments)
    info.lpDirectory=str(cwd)
    info.nShow=1
    require(bool(shell.ShellExecuteExW(ctypes.byref(info))), 'explicit_exe_shell_dispatch_failed')
    require(bool(info.hProcess), 'process_handle_missing')
    return info.hProcess


class HandleProcess:
    """Adapter preserving ShellExecute's handle for OwnedTree and exact exit code."""
    def __init__(self, handle, audit=None):
        self.handle = handle
        self.kernel = None
        self.audit = audit if audit is not None else {}
        self.audit.update(handle_returned=True,identity_verified=False,
                          handle_released=False,pid=None,birth=None)
        try:
            self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            # Install cleanup before any failure-prone identity query.
            self.kernel.CloseHandle.argtypes = [w.HANDLE]
            self.kernel.CloseHandle.restype = w.BOOL
            self.kernel.GetProcessId.argtypes = [w.HANDLE]
            self.kernel.GetProcessId.restype = w.DWORD
            self.kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
            self.kernel.WaitForSingleObject.restype = w.DWORD
            self.kernel.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
            self.kernel.GetExitCodeProcess.restype = w.BOOL
            self.pid = int(self.kernel.GetProcessId(handle))
            self.audit['pid'] = self.pid
            require(self.pid > 0, 'shell_pid_missing')
            # Retained hProcess is authoritative even if a short-lived warm
            # invocation exits before a second OpenProcess-by-PID can occur.
            self.birth = native.process_creation_time(handle)
            require(type(self.birth) is int and self.birth > 0, 'shell_birth_missing')
            self.audit.update(birth=self.birth,identity_verified=True)
        except BaseException as error:
            self.audit['identity_error_type'] = type(error).__name__
            try:
                self.close()
            except Exception:
                self.audit['handle_release_error'] = True
            raise
    def poll(self):
        state = self.kernel.WaitForSingleObject(self.handle, 0)
        require(state in (0, 0x102), 'process_wait_failed')
        if state == 0x102:
            return None
        code = w.DWORD()
        require(bool(self.kernel.GetExitCodeProcess(self.handle, ctypes.byref(code))),
                'process_exit_code_missing')
        return int(code.value)
    def wait(self, seconds):
        state = self.kernel.WaitForSingleObject(self.handle, int(seconds * 1000))
        require(state == 0, 'process_exit_not_confirmed')
        return self.poll()
    def close(self):
        if self.handle:
            handle,self.handle = self.handle,None
            closed=bool(self.kernel and self.kernel.CloseHandle(handle))
            self.audit['handle_released']=closed
            require(closed,'process_handle_close_failed')


def shell_owned(exe, params, environment, *, launch_audit=None):
    """ShellExecuteEx has no per-child env parameter; scope process env under lock."""
    with _SHELL_ENV_LOCK:
        keys = ('TEMP', 'TMP', 'WEBVIEW2_USER_DATA_FOLDER',
                'WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS', 'PIXIVVAULT_PROFILE_ROOT',
                'PYTHONPATH', 'PYTHONDONTWRITEBYTECODE', 'PYTHONUTF8')
        original = {key: os.environ.get(key) for key in keys}
        try:
            os.environ.pop('PIXIVVAULT_PROFILE_ROOT', None)
            os.environ.update(environment)
            record={'dispatch_requested':True,'handle_returned':False,
                    'identity_verified':False,'handle_released':False}
            if launch_audit is not None:
                launch_audit.append(record)
            return HandleProcess(shell_launch(exe, params, cwd=exe.parent),audit=record)
        finally:
            for key, value in original.items():
                if value is None: os.environ.pop(key, None)
                else: os.environ[key] = value


_SHELL_ENV_LOCK = threading.RLock()


def helper_fingerprints():
    observed=helper_shas()
    for name in ('check_library_calendar_slider_owned.py','run_browser_checks.py'):
        observed[name]=file_sha(ROOT/'tools'/name)
    return observed


def run(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--profile-root',type=Path,required=True)
    parser.add_argument('--app-port',type=int,required=True)
    parser.add_argument('--debug-port',type=int,required=True)
    parser.add_argument('--execute',action='store_true',help='requires owner GUI slot release')
    parser.add_argument('--source-entry',type=Path,
                        help='Explicit owned native_entry diagnostic; --exe must be Python')
    args=parser.parse_args(argv)
    require(sys.platform=='win32','windows_only')
    require('..' not in args.output.parts,'output_parent_reference_rejected')
    require('..' not in args.profile_root.parts,'profile_parent_reference_rejected')
    require('..' not in args.exe.parts,'exe_parent_reference_rejected')
    require(args.exe.is_absolute() and args.exe.is_file() and args.exe.suffix.lower()=='.exe','exe_invalid')
    require(args.output.is_absolute() and not args.output.exists() and args.output.parent.is_dir(),'output_must_be_new')
    require(args.output.is_relative_to(ROOT/'artifacts'),'output_outside_artifacts')
    for path in (args.exe,args.output,args.profile_root):
        reject_links(path)
    if args.source_entry is not None:
        require(args.source_entry.is_absolute() and args.source_entry.is_file()
                and args.source_entry==ROOT/'tools/native_entry.py','source_entry_not_owned')
        reject_links(args.source_entry)
    require(args.profile_root.is_absolute() and args.profile_root.is_dir(),'synthetic_profile_missing')
    require(args.app_port!=args.debug_port and all(1024<=port<=65535 for port in (args.app_port,args.debug_port)),'ports_invalid')
    require(not (args.output==args.profile_root or args.output in args.profile_root.parents or args.profile_root in args.output.parents),'profile_output_overlap')
    # Never use an existing user profile. The caller must provision a dedicated
    # disposable synthetic profile; this checker never reads Cookie bytes.
    require('uri-native-' in args.profile_root.name,'dedicated_profile_name_required')
    # A synthetic profile must not carry any live Cookie or original DB paths.
    from pixivvault_nicegui.native_profile import NativeProfileStore
    snapshot=NativeProfileStore.peek(args.profile_root)
    require(snapshot.cookie_file is None and snapshot.library_readonly_db is None
            and snapshot.library_image_root is None,
            'live_profile_reference_rejected')
    require(snapshot.save_parent is not None and
            args.profile_root.parent == snapshot.save_parent.parent and
            'uri-native-' in snapshot.save_parent.name,
            'dedicated_save_parent_required')
    params=['--native','--host','127.0.0.1','--port',str(args.app_port),
            '--profile-root',str(args.profile_root),'--',URI]
    if args.source_entry is not None:
        params=['-B',str(args.source_entry),*params]
    # Explicit path dispatch is NOT proof of Windows URI scheme registration.
    if not args.execute:
        return {'status':'prepared','uri':URI,'exe_sha256':file_sha(args.exe),
                'arguments':params,'notes':['product exe argument only','OS association untested']}
    require(native.port_released(args.app_port) and native.port_released(args.debug_port), 'ports_in_use')
    return execute_native(args, params)


def execute_native(args, params):
    """Fail closed: inspect only the exact ShellExecute-owned process tree."""
    result={'status':'failed','registration_tested':False,'forced_termination':False,
            'errors':[],'remaining_owned':None,'normal_exit':False,
            'cold_exit_code':None,'warm_exit_code':None,'launch_audit':[]}
    result['execution_mode']='source-diagnostic' if args.source_entry is not None else 'packaged-product'
    result['exe_sha256']=file_sha(args.exe)
    result['checker_sha256']=file_sha(Path(__file__))
    result['helper_sha256']=helper_fingerprints()
    result['source_before']=source_hashes()
    result['profile_db_sha256']=file_sha(args.profile_root/'profile.sqlite3')
    args.output.mkdir(exist_ok=False)
    temp=args.output/'temp';temp.mkdir()
    webview=args.output/'webview';webview.mkdir()
    cold=None;warm=None;first_tree=None;second_tree=None
    owned={};pid=None;birth=None;hwnd=None;owner=None
    def refresh():
        if first_tree is None:return set()
        first_tree.sample()
        with first_tree._lock:
            require(not first_tree.errors, 'cold_owned_observer_failed')
            owned.update(first_tree.identities)
        return native.valid_owned(owned)
    def wait_for(predicate,seconds,reason):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            if predicate():return
            time.sleep(.15)
        raise AcceptanceError(reason)
    try:
        env={'TEMP':str(temp),'TMP':str(temp),'WEBVIEW2_USER_DATA_FOLDER':str(webview),
             'WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS':
             f'--remote-debugging-port={args.debug_port} --remote-debugging-address=127.0.0.1'}
        if args.source_entry is not None:
            env.update(PYTHONPATH=str(ROOT/'src'),PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1')
        cold=shell_owned(args.exe,params,env,launch_audit=result['launch_audit'])
        pid,birth=cold.pid,cold.birth
        first_tree=OwnedTree(cold,native)
        owned[pid]=birth
        def health():
            tree=refresh()
            return (loopback_listener(args.app_port) in tree and
                    native.health_ready(args.app_port,'synthetic'))
        wait_for(health,45,'cold_owned_health_failed')
        def pin():
            nonlocal hwnd,owner
            windows=native.owned_windows(refresh())
            require(len(windows)<=1,'multiple_cold_windows')
            if not windows:return False
            hwnd=windows[0]
            owner=native.owned_window_identity(pid,birth,hwnd,owned)
            return True
        wait_for(pin,25,'cold_window_missing')
        wait_for(lambda:native.ui_ready(args.app_port),20,'cold_webview_not_ready')
        wait_for(lambda:loopback_listener(args.debug_port) in refresh(),15,'cold_debug_owner_missing')
        result['cold']={'root_pid':pid,'birth':birth,'hwnd':hwnd,'owner':list(owner)}
        user=ctypes.WinDLL('user32',use_last_error=True)
        user.IsWindowVisible.argtypes=[w.HWND];user.IsWindowVisible.restype=w.BOOL
        user.IsIconic.argtypes=[w.HWND];user.IsIconic.restype=w.BOOL
        user.ShowWindow.argtypes=[w.HWND,ctypes.c_int];user.ShowWindow.restype=w.BOOL
        require(native.owned_window_identity(pid,birth,hwnd,owned)==owner,
                'minimize_owned_identity_changed')
        user.ShowWindow(hwnd,6)
        def minimized():
            refresh()
            return bool(user.IsIconic(hwnd)) or not bool(user.IsWindowVisible(hwnd))
        wait_for(minimized,10,'warm_precondition_minimize_unobserved')
        # A single iconic observation can precede an asynchronous pystray
        # hide. Require a stable owned OS state before testing a warm wake.
        stable=[]
        for _ in range(10):
            require(minimized(),'warm_precondition_not_stable')
            stable.append({'visible':bool(user.IsWindowVisible(hwnd)),
                           'iconic':bool(user.IsIconic(hwnd))})
            time.sleep(.1)
        result['cold']['minimized_or_hidden']=True
        result['cold']['precondition_stable_samples']=len(stable)
        result['cold']['precondition_hidden']=not stable[-1]['visible']
        # Second explicit EXE invocation must dispatch to the existing window.
        # The root-owned first window cannot be replaced by a second resident.
        warm=shell_owned(args.exe,params,env,launch_audit=result['launch_audit'])
        second_pid,second_birth=warm.pid,warm.birth
        second_tree=OwnedTree(warm,native)
        require(second_pid!=pid,'warm_launch_identity_invalid')
        result['warm']={'second_pid':second_pid,'second_birth':second_birth}
        # Product restore_existing permits up to 30s *after* packed boot.
        # Distinguish a slow response from a stuck native error dialog.
        result['warm_exit_code']=warm.wait(48)
        require(result['warm_exit_code']==0,'warm_process_exit_nonzero')
        second_observed=second_tree.finish()
        result['warm_observation']=second_observed
        second_tree=None
        require(not second_observed['errors'] and not second_observed['living'],
                'warm_child_process_remains_or_unverified')
        tree=refresh()
        require(native.owned_window_identity(pid,birth,hwnd,owned)==owner,
                'warm_original_hwnd_changed')
        wait_for(lambda: bool(user.IsWindowVisible(hwnd)) and
                 not bool(user.IsIconic(hwnd)),15,'warm_original_not_restored')
        # A momentary SW_RESTORE can be followed by a delayed pywebview tray
        # hide. Do not count a single visible sample as a successful wake.
        stable_visible=0
        for _ in range(12):
            refresh()
            require(native.owned_window_identity(pid,birth,hwnd,owned)==owner,
                    'warm_restore_owner_changed_during_stability')
            require(bool(user.IsWindowVisible(hwnd)) and not bool(user.IsIconic(hwnd)),
                    'warm_restore_visible_state_unstable')
            stable_visible+=1
            time.sleep(.1)
        tree=refresh()
        require(native.owned_window_identity(pid,birth,hwnd,owned)==owner,
                'warm_original_hwnd_changed')
        require(native.owned_windows(tree)==[hwnd],'warm_second_window_detected')
        require(loopback_listener(args.app_port) in tree and
                loopback_listener(args.debug_port) in tree,'warm_port_owner_changed')
        result['warm'].update(second_exited=True,same_hwnd=True,restored=True,
                              stable_visible_samples=stable_visible)
        require(native.close_owned_window(pid,birth,hwnd,owned,owner),'owned_wm_close_failed')
        require(native.acknowledge_owned_close_confirmation(owner,hwnd,time.monotonic()+8),
                'owned_close_confirmation_missing')
        result['cold_exit_code']=cold.wait(35)
        require(result['cold_exit_code']==0,'cold_process_exit_nonzero')
        wait_for(lambda:not native.living_pids(owned),20,'owned_tree_not_exited')
        wait_for(lambda:native.port_released(args.app_port) and
                 native.port_released(args.debug_port),15,'owned_ports_not_released')
        result['normal_exit']=True
        result['status']='passed'
    except Exception as error:
        result['errors'].append(error_code(error))
    finally:
        # Failure cleanup is ordinary owned WM_CLOSE only, never kill/terminate.
        # A second packaged process can remain alive behind a native error
        # dialog even if the first owner shuts down. Inspect only the pinned
        # warm process ancestry, and record window classes (never dialog text,
        # paths or secrets) before attempting its own ordinary WM_CLOSE.
        if warm is not None and warm.poll() is None and second_tree is not None:
            try:
                second_tree.sample()
                with second_tree._lock:
                    require(not second_tree.errors, 'warm_observer_failed')
                    warm_ids=dict(second_tree.identities)
                require(native.process_identity(warm.pid)==warm.birth,
                        'warm_cleanup_root_identity_changed')
                candidates=native.owned_windows(native.valid_owned(warm_ids))
                u=ctypes.WinDLL('user32',use_last_error=True)
                u.GetClassNameW.argtypes=[w.HWND,w.LPWSTR,ctypes.c_int]
                u.GetClassNameW.restype=ctypes.c_int
                u.GetWindowTextLengthW.argtypes=[w.HWND]
                u.GetWindowTextLengthW.restype=ctypes.c_int
                result['warm_failure_windows']=[]
                for wh in candidates:
                    owner_w=native.owned_window_identity(warm.pid,warm.birth,wh,warm_ids)
                    name=ctypes.create_unicode_buffer(128)
                    require(u.GetClassNameW(wh,name,len(name))>0,'warm_window_class_missing')
                    result['warm_failure_windows'].append({
                        'hwnd':wh,'owner':list(owner_w),'class':name.value,
                        'title_chars':int(u.GetWindowTextLengthW(wh))})
                    # Dialog body may contain private paths or traceback;
                    # never capture its child-control text in evidence.
                if len(candidates)==1:
                    wh=candidates[0]
                    owner_w=native.owned_window_identity(warm.pid,warm.birth,wh,warm_ids)
                    result['failure_warm_wm_close']=native.close_owned_window(
                        warm.pid,warm.birth,wh,warm_ids,owner_w)
                    if result['failure_warm_wm_close']:
                        result['failure_warm_confirmation']=native.acknowledge_owned_close_confirmation(
                            owner_w,wh,time.monotonic()+8)
                        result['warm_exit_code']=warm.wait(20)
            except Exception as error:
                result['errors'].append('cleanup_warm_'+error_code(error))
        if cold is not None and cold.poll() is None:
            try:
                refresh()
                if hwnd is None:
                    candidates=native.owned_windows(native.valid_owned(owned))
                    if len(candidates)==1:
                        hwnd=candidates[0]
                        owner=native.owned_window_identity(pid,birth,hwnd,owned)
                if hwnd is not None and owner is not None:
                    require(native.owned_window_identity(pid,birth,hwnd,owned)==owner,
                            'cleanup_owner_changed')
                    user=ctypes.WinDLL('user32',use_last_error=True)
                    user.ShowWindow.argtypes=[w.HWND,ctypes.c_int]
                    user.ShowWindow(hwnd,9)
                    user.ShowWindow(hwnd,5)
                    native.close_owned_window(pid,birth,hwnd,owned,owner)
                    result['failure_close_confirmation']=native.acknowledge_owned_close_confirmation(
                        owner,hwnd,time.monotonic()+8)
                result['cold_exit_code']=cold.wait(20)
            except Exception as error:
                result['errors'].append('cleanup_'+error_code(error))
        if second_tree is not None:
            result['warm_observation']=second_tree.finish()
        if first_tree is not None:
            result['cold_observation']=first_tree.finish()
            owned.update({int(k):v for k,v in result['cold_observation']['observed'].items()})
        # Keep both trees even when the short-lived warm root exits before a
        # child. Recheck each PID/birth map separately so PID reuse cannot merge
        # away a surviving descendant, and never classify that state as clean.
        remaining=set(native.living_pids(owned))
        observation_unverified=False
        for observation in (result.get('cold_observation'),result.get('warm_observation')):
            if observation is None:
                continue
            identities={int(k):v for k,v in observation.get('observed',{}).items()}
            remaining.update(native.living_pids(identities))
            if observation.get('errors') or observation.get('living') is None:
                observation_unverified=True
                result['errors'].append('owned_observation_unverified')
        result['remaining_owned']=sorted(remaining)
        for label,process in (('warm',warm),('cold',cold)):
            if process is None:
                continue
            try:
                try:
                    if result[label+'_exit_code'] is None:
                        result[label+'_exit_code']=process.poll()
                finally:
                    process.close()
            except Exception as error:
                result['errors'].append(label+'_handle_cleanup_'+error_code(error))
        result['ports_released']=(native.port_released(args.app_port) and
                                  native.port_released(args.debug_port))
        result['source_unchanged']=source_hashes()==result['source_before']
        result['exe_unchanged']=file_sha(args.exe)==result['exe_sha256']
        result['checker_unchanged']=file_sha(Path(__file__))==result['checker_sha256']
        try:
            result['helper_unchanged']=helper_fingerprints()==result['helper_sha256']
        except Exception:
            result['helper_unchanged']=False
        result['profile_db_unchanged']=file_sha(args.profile_root/'profile.sqlite3')==result['profile_db_sha256']
        if not all(result[key] for key in ('source_unchanged','exe_unchanged','checker_unchanged','helper_unchanged','profile_db_unchanged')):
            result['errors'].append('source_or_profile_identity_changed')
        unverified_launch=any(not item.get('identity_verified') or not item.get('handle_released')
                              for item in result['launch_audit'])
        if unverified_launch:
            result['errors'].append('shell_launch_or_handle_unverified')
        result['needs_manual_close']=bool(result['remaining_owned']) or (
            result['cold_exit_code'] is None) or (
            warm is not None and result['warm_exit_code'] is None) or observation_unverified or unverified_launch
        if result['needs_manual_close'] or result['errors'] or not result['ports_released']:
            result['status']='failed'
        (args.output/'result.json').write_text(json.dumps(result,sort_keys=True,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    try:
        result=run()
        print(json.dumps({key:result.get(key) for key in
            ('status','execution_mode','cold_exit_code','warm_exit_code','ports_released',
             'normal_exit','errors','remaining_owned')},ensure_ascii=True))
        raise SystemExit(0 if result['status'] in ('prepared','passed') else 1)
    except SystemExit:
        raise
    except Exception as error:
        print(json.dumps({'status':'failed','reason':error_code(error)},ensure_ascii=True))
        raise SystemExit(1)
