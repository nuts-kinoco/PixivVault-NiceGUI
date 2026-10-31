"""Launch REAL copied Flet gui.main_window with the SAME 17 generated failures.

This does not recreate any tab. We inject only a read-only FakeDB.get_failed_jobs
adapter for the genuine inline gui.py failed tab. Source-copy 33 Python files
must remain byte-identical and read-only. Owner native GUI operations/capture
and NiceGUI matched native GDI remain separate evidence gates.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / 'artifacts/p3-368-real-flet-source-preflight-r1'
SOURCE = HERE / 'source-copy'
FROZEN = HERE / 'source-dependency-preflight.json'
EXACT_GUI_SHA = '731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939'


def sha(path):
    return sha256(path.read_bytes()).hexdigest()


def read_pinned_source():
    frozen = json.loads(FROZEN.read_text(encoding='utf8'))
    items = frozen['source_files']
    if (len(items) != 33 or items['gui.py']['sha256'] != EXACT_GUI_SHA
            or sha(SOURCE / 'gui.py') != EXACT_GUI_SHA):
        raise RuntimeError('original GUI exact source-copy identity mismatch')
    actual = {}
    for name, entry in items.items():
        path = SOURCE / name
        if (path.is_symlink() or path.resolve().parent != SOURCE.resolve()
                or not path.is_file()):
            raise RuntimeError('original copied source path changed')
        actual[name] = sha(path)
        if actual[name] != entry['sha256']:
            raise RuntimeError('original copied source SHA mismatch: ' + name)
    return actual


@contextmanager
def pinned_win32_sources(paths, *, require_single_link=False):
    """Keep each exact source file locked against writes, delete and rename.

    A path-only Python audit cannot prevent another process writing through a
    different hardlink. CreateFileW with FILE_SHARE_READ only denies WRITE and
    DELETE access on the same NTFS file even through an outside alias. Fail
    before executing the original GUI if any pinned source is inaccessible.
    This is a non-mutating lease, not an ACL or a lasting protection change.
    """
    if os.name != 'nt':
        raise RuntimeError('Windows source handle protections required')
    import ctypes
    from ctypes import wintypes

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ('dwFileAttributes', wintypes.DWORD),
            ('ftCreationTime', wintypes.FILETIME),
            ('ftLastAccessTime', wintypes.FILETIME),
            ('ftLastWriteTime', wintypes.FILETIME),
            ('dwVolumeSerialNumber', wintypes.DWORD),
            ('nFileSizeHigh', wintypes.DWORD),
            ('nFileSizeLow', wintypes.DWORD),
            ('nNumberOfLinks', wintypes.DWORD),
            ('nFileIndexHigh', wintypes.DWORD),
            ('nFileIndexLow', wintypes.DWORD),
        ]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(ByHandleFileInformation),
    ]
    kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    invalid = ctypes.c_void_p(-1).value
    handles = []
    try:
        for raw in paths:
            path = Path(raw)
            # Refuse source reparse/junction components even when an earlier
            # resolve() was clean. The handle's own attributes are checked
            # again after CreateFileW, inside the protected sharing interval.
            for component in (path, *path.parents):
                if not component.exists():
                    raise RuntimeError('pinned source ancestor missing')
                attrs = getattr(component.lstat(), 'st_file_attributes', 0)
                if attrs & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
                    raise RuntimeError('pinned source has reparse ancestor')
            handle = kernel.CreateFileW(
                str(path), 0x80000000, 0x00000001, None,
                3, 0x00200000, None,
            )
            if handle is None or handle == invalid:
                raise ctypes.WinError(ctypes.get_last_error())
            handles.append(handle)
            info = ByHandleFileInformation()
            if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            if info.dwFileAttributes & 0x400:
                raise RuntimeError('pinned source file is a reparse point')
            if require_single_link and info.nNumberOfLinks != 1:
                raise RuntimeError('pinned original source has unexpected hardlink')
            # Windows stat identity is based on the same file ID. A path swap
            # after this protected open cannot overwrite the pinned name.
            opened_id = (info.dwVolumeSerialNumber,
                         info.nFileIndexHigh, info.nFileIndexLow)
            if not all(isinstance(value, int) for value in opened_id):
                raise RuntimeError('pinned original source identity missing')
        yield
    finally:
        for handle in reversed(handles):
            kernel.CloseHandle(handle)


def generated_failure_rows():
    sys.path.insert(0, str(ROOT / 'src'))
    from pixivvault_nicegui.failed_records_fixtures import build_failed_records_fixture
    records = build_failed_records_fixture()
    rows = tuple(asdict(rec) for rec in records)
    if (len(rows) != 17 or len({row['work_id'] for row in rows}) != 17
            or rows[0]['work_id'] != 'failure-0001'
            or not all(set(row) == {'work_id', 'user_id', 'title',
                                    'work_type', 'error_reason', 'failed_at',
                                    'retry_count'} for row in rows)
            or '\n' not in rows[0]['error_reason']):
        raise RuntimeError('failure record fixture contract changed')
    return rows


def source_copy_write_denial(event, args):
    """Additional Python audit hook on top of the existing Flet sandbox."""
    def is_copy_path(value):
        if not isinstance(value, (str, bytes, os.PathLike)):
            return False
        try:
            target = Path(value).resolve()
            return target == SOURCE.resolve() or target.is_relative_to(SOURCE.resolve())
        except (OSError, ValueError, TypeError):
            return False
    if event == 'open' and args and is_copy_path(args[0]):
        mode = str(args[1]) if len(args) > 1 else 'r'
        flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
        write = (any(x in mode for x in ('w', 'a', 'x', '+'))
                 or bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT |
                                  os.O_TRUNC | os.O_APPEND)))
        if write:
            raise PermissionError('read-only original source-copy write denied')
    if event in ('os.remove', 'os.unlink', 'os.rename', 'os.replace',
                 'os.link', 'os.symlink',
                 'os.mkdir', 'os.rmdir', 'os.chmod', 'os.utime', 'os.truncate'):
        if any(is_copy_path(value) for value in args[:2]):
            raise PermissionError('read-only original source-copy mutation denied')


def run(out):
    out = out.resolve()
    if out.exists() or not out.is_relative_to((ROOT / 'artifacts').resolve()):
        raise ValueError('fresh dedicated Flet 17 generated records root required')
    before = read_pinned_source()
    rows = generated_failure_rows()
    sys.path.insert(0, str(HERE))
    import builder_probe
    import import_guard
    import launch_true_gui

    previous_fake = builder_probe.FakeDB
    previous_guard = import_guard.apply_critical_guards

    class Failed17FakeDB(previous_fake):
        def get_failed_jobs(self):
            return [dict(row) for row in rows]

    def guarded_install(*args, **kwargs):
        previous_guard(*args, **kwargs)
        sys.addaudithook(source_copy_write_denial)

    previous_bytecode = sys.dont_write_bytecode
    failure = None
    with pinned_win32_sources(
        [SOURCE / name for name in sorted(before)], require_single_link=True
    ):
        if read_pinned_source() != before:
            raise RuntimeError('source changed between preflight and file pin')
        sys.dont_write_bytecode = True
        builder_probe.FakeDB = Failed17FakeDB
        import_guard.apply_critical_guards = guarded_install
        try:
            # Starts the REAL gui.py, guarded through native Flet shutdown.
            launch_true_gui.run(out)
        except Exception as exc:
            failure = {'type': type(exc).__name__, 'message': str(exc)[:240]}
        finally:
            builder_probe.FakeDB = previous_fake
            import_guard.apply_critical_guards = previous_guard
            sys.dont_write_bytecode = previous_bytecode
        # Verify the exact same named source bytes while all 33 OS handles
        # still deny writes/deletes through any file path or hardlink.
        after = read_pinned_source()
    if not out.is_dir():
        raise RuntimeError('actual original GUI did not create owned root')
    receipt = {
        'schema': 1, 'scope': 'REAL original gui.py inline failed tab 17 GENERATED records',
        'source_gui_sha256': EXACT_GUI_SHA, 'source_count': 33,
        'source_unchanged': before == after, 'original_data_cookie_used': False,
        'generated_failure_count': len(rows),
        'fixture_first_work_id': rows[0]['work_id'],
        'fixture_sha256': sha256(json.dumps(rows, ensure_ascii=False,
                                            sort_keys=True).encode('utf8')).hexdigest(),
        'native_gui_main_returned': failure is None,
        'failure': failure, 'formal_pass_added': 0,
        'same_native_gdi_nicegui_parity': False,
        'actual_failed_search_ui_observed': False,
    }
    with (out / 'generated-failed-17-source-guard.json').open('x', encoding='utf8') as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'real_original_gui_returned': failure is None,
                      'source_unchanged': before == after,
                      'generated_failure_count': len(rows),
                      'formal_pass_added': 0}))
    if failure or before != after:
        raise RuntimeError('original full GUI ended in failure or source change')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('usage: check_stage5_original_failed17_native.py FRESH_ARTIFACT_ROOT')
    run(Path(sys.argv[1]))
