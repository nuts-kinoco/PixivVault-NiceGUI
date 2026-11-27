"""Autouse test isolation and fail-closed sentinel for real Windows LocalAppData.

Prevents any test from touching the real user LocalAppData (including the
Windows-user-wide Pixiv GET stop owner under
``<LocalAppData>\\PixivVault-NiceGUI\\managed-pixiv-get-stop-v1``) without
modifying ``src/``.

Two independent layers are installed:
1. A process-wide ``sys.addaudithook`` sentinel (plus a Win32 ``CreateFileW``
   wrapper on ``managed_network_stop_gate._create_file``) that raises a
   ``BaseException`` subclass before any open / sqlite3.connect / mkdir /
   directory listing / rename / delete / Win32 lock-file open reaches the real
   LocalAppData tree. Using ``BaseException`` prevents broad ``except Exception``
   handlers in production code from swallowing a sentinel trip.
2. An ``autouse`` pytest fixture that redirects Known Folder resolution
   (``_windows_local_app_data``), every direct ``from ... import`` alias in
   ``src/`` (``managed_stop_migration._windows_local_app_data`` and
   ``managed_stop_migration_binding.open_existing_windows_user_stop``), and the
   ``LOCALAPPDATA`` / ``APPDATA`` environment variables into a per-test
   temporary directory under pytest's ``--basetemp``.
"""

from __future__ import annotations

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys
from threading import Lock
from urllib.parse import unquote, urlsplit

import pytest


class RealLocalAppDataAccessError(BaseException):
    """Raised immediately when code tries to touch the real LocalAppData tree."""

    def __init__(self, event: str, location: str):
        self.event = event
        self.location = location
        super().__init__(f"Blocked access to real LocalAppData ({event}: {location})")


def _query_windows_known_local_app_data() -> str | None:
    """Resolve FOLDERID_LocalAppData string only; never touches the filesystem."""
    if sys.platform != "win32":
        return None

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    # FOLDERID_LocalAppData = F1B32785-6FBA-4FCF-9D55-7B8E7F157091
    guid = _GUID(
        0xF1B32785,
        0x6FBA,
        0x4FCF,
        (ctypes.c_ubyte * 8)(0x9D, 0x55, 0x7B, 0x8E, 0x7F, 0x15, 0x70, 0x91),
    )
    value = ctypes.c_wchar_p()
    shell = ctypes.windll.shell32
    shell.SHGetKnownFolderPath.argtypes = [
        ctypes.POINTER(_GUID),
        wintypes.DWORD,
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_wchar_p),
    ]
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    hr = shell.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(value))
    if hr != 0 or not value.value:
        return None
    try:
        return str(value.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(ctypes.cast(value, ctypes.c_void_p))


def _strip_win32_device_prefix(text: str) -> str:
    if text.startswith("\\\\?\\UNC\\") or text.startswith("\\\\.\\UNC\\"):
        return "\\\\" + text[8:]
    if text.startswith("\\\\?\\") or text.startswith("\\\\.\\"):
        return text[4:]
    return text


def _extract_sqlite_path(text: str) -> str | None:
    if not text or text == ":memory:":
        return None
    if text.startswith("file:"):
        parts = urlsplit(text)
        raw_path = unquote(parts.path)
        if not raw_path or raw_path == ":memory:":
            return None
        if len(raw_path) > 2 and raw_path[0] == "/" and raw_path[2] == ":":
            raw_path = raw_path[1:]
        elif parts.netloc and parts.netloc.lower() != "localhost":
            raw_path = f"//{parts.netloc}{raw_path}"
        return raw_path
    return text


def _normalize_path_pure(raw, *, sqlite_uri: bool = False) -> str | None:
    """Pure string normalization; never calls stat/lstat/realpath on disk."""
    if not isinstance(raw, (str, bytes, os.PathLike)):
        return None
    try:
        text = os.fsdecode(raw)
    except (TypeError, ValueError):
        return None
    if sqlite_uri:
        text = _extract_sqlite_path(text)
        if text is None:
            return None
    if not text:
        return None
    text = _strip_win32_device_prefix(text)
    return os.path.normcase(os.path.normpath(os.path.abspath(text)))


def _discover_real_local_app_data_roots() -> tuple[str, ...]:
    candidates = []
    known = _query_windows_known_local_app_data()
    if known:
        candidates.append(known)
    env_local = os.environ.get("LOCALAPPDATA")
    if env_local:
        candidates.append(env_local)
    roots: list[str] = []
    for candidate in candidates:
        norm = _normalize_path_pure(candidate)
        if norm and norm not in roots:
            roots.append(norm)
    return tuple(roots)


_ORIGINAL_ENV_LOCAL_APP_DATA: str | None = os.environ.get("LOCALAPPDATA")
REAL_LOCAL_APP_DATA_ROOTS: tuple[str, ...] = _discover_real_local_app_data_roots()

_SENTINEL_LOCK = Lock()
_UNEXPECTED_VIOLATIONS: list[tuple[str, str]] = []
_CAPTURED_VIOLATIONS: list[tuple[str, str]] | None = None
_HOOK_INSTALLED = False


def _match_real_local_app_data(raw, *, sqlite_uri: bool = False) -> str | None:
    norm = _normalize_path_pure(raw, sqlite_uri=sqlite_uri)
    if norm is None:
        return None
    for root in REAL_LOCAL_APP_DATA_ROOTS:
        if norm == root:
            return "<REAL_LOCALAPPDATA>"
        prefix = root if root.endswith(os.sep) else root + os.sep
        if norm.startswith(prefix):
            rel = norm[len(prefix):]
            return f"<REAL_LOCALAPPDATA>{os.sep}{rel}"
    return None


def _trip_sentinel(event: str, location: str) -> None:
    with _SENTINEL_LOCK:
        if _CAPTURED_VIOLATIONS is not None:
            _CAPTURED_VIOLATIONS.append((event, location))
        else:
            _UNEXPECTED_VIOLATIONS.append((event, location))
    raise RealLocalAppDataAccessError(event, location)


def _check_candidate(event: str, raw, *, sqlite_uri: bool = False) -> None:
    matched = _match_real_local_app_data(raw, sqlite_uri=sqlite_uri)
    if matched is not None:
        _trip_sentinel(event, matched)


_SINGLE_PATH_EVENTS = frozenset({
    "open",
    "os.mkdir",
    "os.listdir",
    "os.scandir",
    "os.remove",
    "os.rmdir",
    "os.truncate",
    "os.chmod",
    "os.chown",
    "os.utime",
    "os.chflags",
    "tempfile.mkstemp",
    "tempfile.mkdtemp",
    "glob.glob",
    "pathlib.Path.glob",
    "pathlib.Path.rglob",
    "shutil.rmtree",
})

_PAIR_PATH_EVENTS = frozenset({
    "os.rename",
    "os.link",
    "os.symlink",
    "shutil.copyfile",
    "shutil.copymode",
    "shutil.copystat",
    "shutil.copytree",
    "shutil.move",
})


def _audit_hook(event: str, args: tuple) -> None:
    if not REAL_LOCAL_APP_DATA_ROOTS:
        return
    if event == "sqlite3.connect":
        if args:
            _check_candidate(event, args[0], sqlite_uri=True)
        return
    if event in _SINGLE_PATH_EVENTS:
        if args:
            _check_candidate(event, args[0])
        return
    if event in _PAIR_PATH_EVENTS:
        for candidate in args[:2]:
            _check_candidate(event, candidate)
        return


def _install_audit_sentinel() -> None:
    global _HOOK_INSTALLED
    if not _HOOK_INSTALLED:
        sys.addaudithook(_audit_hook)
        _HOOK_INSTALLED = True


@contextmanager
def capture_real_local_app_data_sentinel():
    """Context manager for synthetic tests verifying the sentinel itself."""
    global _CAPTURED_VIOLATIONS
    captured: list[tuple[str, str]] = []
    with _SENTINEL_LOCK:
        previous = _CAPTURED_VIOLATIONS
        _CAPTURED_VIOLATIONS = captured
    try:
        yield captured
    finally:
        with _SENTINEL_LOCK:
            _CAPTURED_VIOLATIONS = previous


_install_audit_sentinel()


@pytest.fixture(autouse=True)
def isolate_real_local_app_data(tmp_path_factory, monkeypatch):
    """Redirect Known Folder, direct-imported stop functions, and AppData env."""
    from pixivvault_nicegui import managed_network_stop_gate as stop_gate
    from pixivvault_nicegui import managed_network_stop_shared_scope as shared_scope
    from pixivvault_nicegui import managed_stop_migration as stop_migration
    from pixivvault_nicegui import managed_stop_migration_binding as stop_migration_binding

    base = tmp_path_factory.mktemp("isolated-appdata")
    isolated_local = base / "Local"
    isolated_roaming = base / "Roaming"
    isolated_local.mkdir(parents=True, exist_ok=True)
    isolated_roaming.mkdir(parents=True, exist_ok=True)

    # 1. Redirect Known Folder API resolution to per-test isolated LocalAppData.
    monkeypatch.setattr(shared_scope, "_windows_local_app_data", lambda: isolated_local)

    # 2. Rebind direct `from ... import` aliases in src/ to dynamic module lookups
    #    so both default isolation and per-test monkeypatches on `shared_scope` take effect.
    monkeypatch.setattr(
        stop_migration,
        "_windows_local_app_data",
        lambda: shared_scope._windows_local_app_data(),
    )
    monkeypatch.setattr(
        stop_migration_binding,
        "open_existing_windows_user_stop",
        lambda: shared_scope.open_existing_windows_user_stop(),
    )

    # 3. Guard Win32 CreateFileW calls that bypass Python's `open` audit event.
    if os.name == "nt" and hasattr(stop_gate, "_create_file"):
        real_create_file = stop_gate._create_file

        def guarded_create_file(path, *args, **kwargs):
            _check_candidate("win32.CreateFileW", path)
            return real_create_file(path, *args, **kwargs)

        monkeypatch.setattr(stop_gate, "_create_file", guarded_create_file)

    # 4. Redirect LOCALAPPDATA / APPDATA env vars used by native_startup,
    #    browser_cookie_source, and desktop_integration.
    #    Preserve read-only Playwright browser binary lookup via pure path join
    #    (without touching or listing real LocalAppData).
    if _ORIGINAL_ENV_LOCAL_APP_DATA and not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        monkeypatch.setenv(
            "PLAYWRIGHT_BROWSERS_PATH",
            os.path.join(_ORIGINAL_ENV_LOCAL_APP_DATA, "ms-playwright"),
        )
    monkeypatch.setenv("LOCALAPPDATA", str(isolated_local))
    monkeypatch.setenv("APPDATA", str(isolated_roaming))

    with _SENTINEL_LOCK:
        _UNEXPECTED_VIOLATIONS.clear()

    try:
        yield isolated_local
    finally:
        with _SENTINEL_LOCK:
            unexpected = list(_UNEXPECTED_VIOLATIONS)
            _UNEXPECTED_VIOLATIONS.clear()
        if unexpected:
            details = ", ".join(f"{ev}({loc})" for ev, loc in unexpected)
            pytest.fail(f"Test attempted to access real LocalAppData: {details}")
