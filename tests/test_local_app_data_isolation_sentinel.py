"""Synthetic verification of real-LocalAppData sentinel and autouse isolation.

Never reads, writes, or lists any real file under real LocalAppData: every
probe targets a synthetic non-existent child path and must be aborted by the
audit sentinel before the OS call executes.
"""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import sys

import pytest

from conftest import (
    REAL_LOCAL_APP_DATA_ROOTS,
    RealLocalAppDataAccessError,
    capture_real_local_app_data_sentinel,
)
from pixivvault_nicegui import managed_network_stop_gate as stop_gate
from pixivvault_nicegui import managed_network_stop_shared_scope as shared_scope
from pixivvault_nicegui import managed_stop_migration as stop_migration
from pixivvault_nicegui import managed_stop_migration_binding as stop_migration_binding
from pixivvault_nicegui.managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
from pixivvault_nicegui.native_profile import NativeProfileStore


def _synthetic_real_probe() -> Path:
    assert REAL_LOCAL_APP_DATA_ROOTS, "Real LocalAppData root must be discovered"
    return (
        Path(REAL_LOCAL_APP_DATA_ROOTS[0])
        / "PixivVault-NiceGUI"
        / "__synthetic_sentinel_probe_never_created__"
    )


def test_sentinel_blocks_open_sqlite_mkdir_list_rename_and_createfile(tmp_path):
    probe_dir = _synthetic_real_probe()
    probe_file = probe_dir / "probe.txt"
    probe_db = probe_dir / ".pixivvault-user-stop.sqlite3"
    probe_lock = probe_dir / ".pixivvault-user-stop.sqlite3.lock"
    local_src = tmp_path / "src.txt"
    local_src.write_text("ok", encoding="utf-8")

    with capture_real_local_app_data_sentinel() as caught:
        with pytest.raises(RealLocalAppDataAccessError) as exc_open_r:
            open(probe_file, "rb")
        assert exc_open_r.value.event == "open"

        with pytest.raises(RealLocalAppDataAccessError) as exc_open_w:
            open(probe_file, "wb")
        assert exc_open_w.value.event == "open"

        with pytest.raises(RealLocalAppDataAccessError) as exc_sql:
            sqlite3.connect(probe_db)
        assert exc_sql.value.event == "sqlite3.connect"

        with pytest.raises(RealLocalAppDataAccessError) as exc_sql_uri:
            sqlite3.connect(probe_db.as_uri() + "?mode=rw", uri=True)
        assert exc_sql_uri.value.event == "sqlite3.connect"

        with pytest.raises(RealLocalAppDataAccessError) as exc_mkdir:
            os.mkdir(probe_dir)
        assert exc_mkdir.value.event == "os.mkdir"

        with pytest.raises(RealLocalAppDataAccessError) as exc_listdir:
            os.listdir(probe_dir)
        assert exc_listdir.value.event == "os.listdir"

        with pytest.raises(RealLocalAppDataAccessError) as exc_scandir:
            os.scandir(probe_dir)
        assert exc_scandir.value.event == "os.scandir"

        with pytest.raises(RealLocalAppDataAccessError) as exc_rename:
            os.rename(local_src, probe_file)
        assert exc_rename.value.event == "os.rename"

        if os.name == "nt":
            with pytest.raises(RealLocalAppDataAccessError) as exc_cf:
                stop_gate._open_existing_stop_lock(probe_lock)
            assert exc_cf.value.event == "win32.CreateFileW"

    events = [ev for ev, _ in caught]
    assert "open" in events
    assert "sqlite3.connect" in events
    assert "os.mkdir" in events
    assert "os.listdir" in events
    assert "os.scandir" in events
    assert "os.rename" in events
    if os.name == "nt":
        assert "win32.CreateFileW" in events
    assert local_src.is_file()


def test_sentinel_is_not_swallowed_by_migration_binding_except_exception(tmp_path, monkeypatch):
    """If isolation were bypassed, RealLocalAppDataAccessError must escape _association."""
    profile = tmp_path / "profile"
    store = NativeProfileStore(profile)
    store.close()
    scope = ManagedProfileNetworkStopScope(profile)
    base = tmp_path / "save" / "PixivVault-NiceGUI"
    base.mkdir(parents=True)

    probe_file = _synthetic_real_probe() / ".pixivvault-user-stop-owner.v1.json"

    def simulated_bypass():
        open(probe_file, "rb")

    monkeypatch.setattr(shared_scope, "open_existing_windows_user_stop", simulated_bypass)
    with capture_real_local_app_data_sentinel() as caught:
        with pytest.raises(RealLocalAppDataAccessError) as exc:
            stop_migration_binding.verified_storage(scope, base)
    assert exc.value.event == "open"
    assert len(caught) == 1


def test_autouse_isolation_redirects_known_folder_direct_imports_and_env(
    tmp_path, monkeypatch, isolate_real_local_app_data
):
    isolated = isolate_real_local_app_data
    assert shared_scope._windows_local_app_data() == isolated
    assert stop_migration._windows_local_app_data() == isolated
    assert Path(os.environ["LOCALAPPDATA"]) == isolated
    assert Path(os.environ["APPDATA"]) == isolated.parent / "Roaming"

    # Direct-imported open_existing_windows_user_stop uses isolated path by default.
    with pytest.raises(shared_scope.SharedStopOwnerAbsent):
        stop_migration_binding.open_existing_windows_user_stop()

    # Monkeypatching shared_scope must take effect through direct-imported aliases too.
    custom_local = tmp_path / "custom-local"
    custom_local.mkdir()
    monkeypatch.setattr(shared_scope, "_windows_local_app_data", lambda: custom_local)
    assert stop_migration._windows_local_app_data() == custom_local

    sentinel_owner = object()
    monkeypatch.setattr(shared_scope, "open_existing_windows_user_stop", lambda: sentinel_owner)
    assert stop_migration_binding.open_existing_windows_user_stop() is sentinel_owner

    # Ordinary operations inside tmp_path stay unrestricted.
    ok_dir = tmp_path / "allowed"
    ok_dir.mkdir()
    ok_file = ok_dir / "data.txt"
    ok_file.write_text("allowed", encoding="utf-8")
    assert ok_file.read_text(encoding="utf-8") == "allowed"
    conn = sqlite3.connect(ok_dir / "db.sqlite3")
    conn.close()
    assert sorted(os.listdir(ok_dir)) == ["data.txt", "db.sqlite3"]
    if sys.platform == "win32":
        assert str(isolated).lower() not in REAL_LOCAL_APP_DATA_ROOTS
