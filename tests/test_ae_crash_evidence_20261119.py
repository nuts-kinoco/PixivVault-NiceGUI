import os
import subprocess
import sys
from pathlib import Path


def test_exception_hooks_write_no_exception_payload_and_delegate(tmp_path, monkeypatch):
    from pixivvault_nicegui import crash_evidence
    seen=[]
    monkeypatch.setattr(crash_evidence.faulthandler, 'enable', lambda **kwargs: seen.append(('fault', kwargs['all_threads'])))
    monkeypatch.setattr(crash_evidence.faulthandler, 'disable', lambda: None)
    monkeypatch.setattr(crash_evidence.faulthandler, 'cancel_dump_traceback_later', lambda: None)
    monkeypatch.setattr(sys, 'excepthook', lambda *args: seen.append(('sys', args[0])))
    monkeypatch.setattr(crash_evidence.threading, 'excepthook', lambda args: seen.append(('thread', args.exc_type)))
    path=tmp_path/'crash_log.txt'
    guard=crash_evidence.install(path)
    assert guard is not None
    try:
        try: raise ValueError('synthetic-sensitive-body')
        except ValueError as error:
            sys.excepthook(type(error),error,error.__traceback__)
            args=type('Args', (), {'exc_type':type(error), 'exc_value':error, 'exc_traceback':error.__traceback__, 'thread':None})()
            crash_evidence.threading.excepthook(args)
        content=path.read_text(encoding='utf-8')
        assert 'synthetic-sensitive-body' not in content
        assert '未捕捉の例外' in content
        assert '型: ValueError' in content
        assert 'test_ae_crash_evidence_20261119.py:' in content and str(tmp_path) not in content
        assert seen == [('fault', True), ('sys', ValueError), ('thread', ValueError)]
    finally:
        guard.close()


def test_capture_setup_failure_preserves_hooks_and_allows_caller_to_continue(tmp_path, monkeypatch):
    from pixivvault_nicegui import crash_evidence
    old_sys=sys.excepthook; old_thread=crash_evidence.threading.excepthook
    monkeypatch.setattr(crash_evidence.faulthandler, 'enable', lambda **_: (_ for _ in ()).throw(OSError('synthetic')))
    assert crash_evidence.install(tmp_path/'crash_log.txt') is None
    assert sys.excepthook is old_sys and crash_evidence.threading.excepthook is old_thread


def test_module_entry_installs_before_bootstrap_import_and_sanitizes(tmp_path):
    hook = tmp_path/'sitecustomize.py'
    hook.write_text("import builtins\nold=builtins.__import__\ndef blocked(name,*a,**k):\n if name in ('bootstrap','pixivvault_nicegui.bootstrap'): raise RuntimeError('synthetic-sensitive-body')\n return old(name,*a,**k)\nbuiltins.__import__=blocked\n",encoding='utf-8')
    path=tmp_path/'crash_log.txt'
    env=dict(os.environ, PYTHONPATH=os.pathsep.join([str(tmp_path), str(Path.cwd()/'src')]),
             PIXIVVAULT_CRASH_LOG_PATH=str(path), TEMP=str(tmp_path), TMP=str(tmp_path), PYTHONUTF8='1')
    result=subprocess.run([sys.executable,'-B','-m','pixivvault_nicegui','--check'],
                          env=env,cwd=tmp_path,capture_output=True,timeout=20)
    assert result.returncode != 0
    content=path.read_text(encoding='utf-8')
    assert '起動' in content and '未捕捉の例外' in content
    assert 'synthetic-sensitive-body' not in content


def test_non_s_drive_rejected_without_opening_file(monkeypatch):
    from pixivvault_nicegui import crash_evidence
    opened=[]
    monkeypatch.setattr(Path, 'open', lambda *a, **k: opened.append(True))
    assert crash_evidence.install(Path('C:/do-not-create-crash-log.txt')) is None
    assert not opened


def test_console_entry_continues_when_diagnostics_raise(monkeypatch):
    from pixivvault_nicegui import crash_entry, crash_evidence, bootstrap
    def broken(): raise OSError('synthetic failure')
    monkeypatch.setattr(crash_evidence, 'install', broken)
    monkeypatch.setattr(bootstrap, 'main', lambda: 17)
    assert crash_entry.main() == 17


def test_real_faulthandler_targets_the_s_drive_log(tmp_path):
    path=tmp_path/'crash_log.txt'
    env=dict(os.environ, PYTHONPATH=str(Path.cwd()/'src'), TEMP=str(tmp_path), TMP=str(tmp_path), PYTHONUTF8='1')
    script=('import sys,faulthandler; from pixivvault_nicegui.crash_evidence import install; '
            'g=install(sys.argv[1]); assert g is not None; '
            'faulthandler.dump_traceback(file=g.stream, all_threads=True); g.close()')
    result=subprocess.run([sys.executable,'-B','-c',script,str(path)],env=env,cwd=tmp_path,
                          capture_output=True,timeout=20)
    assert result.returncode == 0
    content=path.read_text(encoding='utf-8')
    assert '起動' in content and 'Current thread' in content
