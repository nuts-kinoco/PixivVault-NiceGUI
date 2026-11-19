"""Supplement the drive snapshot with a Python file-creation audit for one pytest run."""

import atexit
from collections import Counter
import json
import os
from pathlib import Path
import sys
from urllib.parse import parse_qs, unquote, urlsplit


def write_destinations(event, args):
    if event == 'open':
        if args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            return args[:1]
    elif event in {'os.mkdir', 'tempfile.mkstemp', 'tempfile.mkdtemp'}:
        return args[:1]
    elif event in {'os.rename', 'os.link', 'os.symlink'}:
        return args[1:2]
    elif event == 'sqlite3.connect' and isinstance(args[0], (str, bytes)):
        name = os.fsdecode(args[0])
        if name.startswith('file:'):
            parts = urlsplit(name)
            if parse_qs(parts.query).get('mode') in (['ro'], ['memory']):
                return ()
            name = unquote(parts.path)
            if len(name) > 2 and name[0] == '/' and name[2] == ':':
                name = name[1:]
        if name != ':memory:':
            return (name,)
    return ()


def pytest_sessionstart(session):
    destination = os.environ.get('P6_C_AUDIT_OUT')
    if not destination or Path(destination).resolve().drive.upper() != 'S:':
        raise RuntimeError('C: audit requires an S: evidence destination')
    blocked, blocked_sites, subprocesses = Counter(), Counter(), Counter()
    root = Path(__file__).resolve().parents[1]
    relative = Path.relative_to
    realpath = os.path.realpath

    def guard(event, args):
        if event in {'os.system', 'os.startfile', 'subprocess.Popen'}:
            # Argument vectors may carry sensitive data. Retain event counts only.
            subprocesses[event] += 1
        for path in write_destinations(event, args):
            if not isinstance(path, (str, bytes, os.PathLike)):
                continue
            resolved = Path(realpath(os.fsdecode(path)))
            if resolved.drive.upper() in ('C:', '\\\\?\\C:', '\\\\.\\C:'):
                blocked[event] += 1
                frame = sys._getframe(1)
                while frame is not None:
                    code_path = Path(frame.f_code.co_filename)
                    if code_path.is_relative_to(root / 'tests') or code_path.is_relative_to(root / 'src'):
                        site = relative(code_path, root)
                        blocked_sites[f'{event} {site.as_posix()}:{frame.f_lineno}'] += 1
                        break
                    frame = frame.f_back
                else:
                    blocked_sites[f'{event} outside repo'] += 1
                raise PermissionError('P6 test audit blocked a write on C:')

    def save():
        Path(destination).write_text(json.dumps({'pid': os.getpid(), 'blocked': dict(blocked),
                                                 'blocked_sites': dict(blocked_sites),
                                                 'subprocess_events': dict(subprocesses)}, indent=2),
                                     encoding='utf-8')

    atexit.register(save)
    sys.addaudithook(guard)
