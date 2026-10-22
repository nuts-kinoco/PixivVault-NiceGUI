"""ビルドを走らせず、配布入口の契約を検証する。"""

import sys
from pathlib import Path

from tools import native_entry


def test_entry_appends_native_and_forwards_check(monkeypatch):
    import pixivvault_nicegui.bootstrap as bootstrap

    called = []
    monkeypatch.setattr(native_entry.multiprocessing, "freeze_support", lambda: called.append("freeze"))
    monkeypatch.setattr(bootstrap, "main", lambda args: called.append(list(args)) or 0)
    monkeypatch.setattr(sys, "argv", ["app.exe", "--check", "--port", "8091"])
    assert native_entry.main() == 0
    assert called == ["freeze", ["--check", "--port", "8091", "--native"]]


def test_entry_does_not_duplicate_native(monkeypatch):
    import pixivvault_nicegui.bootstrap as bootstrap

    called = []
    monkeypatch.setattr(native_entry.multiprocessing, "freeze_support", lambda: None)
    monkeypatch.setattr(bootstrap, "main", lambda args: called.append(args) or 0)
    monkeypatch.setattr(sys, "argv", ["app.exe", "--native", "--check"])
    assert native_entry.main() == 0
    assert called == [["--native", "--check"]]


def test_explicit_check_output_writes_only_requested_path(monkeypatch, tmp_path):
    import pixivvault_nicegui.bootstrap as bootstrap

    monkeypatch.setattr(native_entry.multiprocessing, "freeze_support", lambda: None)
    target = tmp_path / "packaging-check.json"
    monkeypatch.setattr(sys, "argv", ["app.exe", "--check", "--check-output", str(target)])
    monkeypatch.setattr(sys, "stdout", None)
    called = []
    monkeypatch.setattr(bootstrap, "main", lambda args: called.append(args) or print('{"mode":"demo"}') or 0)
    assert native_entry.main() == 0
    assert target.read_text(encoding="utf-8").strip() == '{"mode":"demo"}'
    assert called == [["--check", "--native"]]


def test_windowed_check_without_output_does_not_write(monkeypatch, tmp_path):
    import pixivvault_nicegui.bootstrap as bootstrap

    monkeypatch.setattr(native_entry.multiprocessing, "freeze_support", lambda: None)
    monkeypatch.setattr(sys, "argv", ["app.exe", "--check"])
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(bootstrap, "main", lambda args: print('{"mode":"demo"}') or 0)
    assert native_entry.main() == 0
    assert list(tmp_path.iterdir()) == []


def test_check_output_rejects_relative_path(monkeypatch):
    import pytest

    monkeypatch.setattr(native_entry.multiprocessing, "freeze_support", lambda: None)
    monkeypatch.setattr(sys, "argv", ["app.exe", "--check", "--check-output", "relative.json"])
    with pytest.raises(SystemExit, match="絶対パス"):
        native_entry.main()


def test_script_is_scoped_to_dedicated_output_and_bundles_metadata():
    path = Path(__file__).resolve().parents[1] / "tools" / "build_windows.ps1"
    # Windows PowerShell 5.1 interprets non-BOM UTF-8 as the system codepage.
    # The Japanese build script previously failed parsing before any statement ran.
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    script = path.read_text(encoding="utf-8-sig")
    assert "artifacts\\windows-packaging" in script
    assert "--onedir" in script and "--noconsole" in script
    assert "--copy-metadata" in script and "--collect-all" in script
    assert "'--collect-data', 'pixivvault_nicegui'" in script
    assert "main.spec" not in script
    assert "$packageArgs" in script and "$args =" not in script
