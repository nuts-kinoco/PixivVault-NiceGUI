"""Acceptance validator tests: never launch Windows or synthesize a pass flag."""
from pathlib import Path

import pytest

from pixivvault_nicegui.failed_records_fixtures import build_failed_records_fixture
from pixivvault_nicegui.failed_records_state import csv_bytes
from tools.native_failed_csv import (
    fresh_destination, saved_csv_accepted, validate_filtered_bytes, validate_saved_csv,
)


def expected_bytes():
    return csv_bytes([row for row in build_failed_records_fixture()
                      if row.work_id in {"failure-0005", "failure-0011", "failure-0017"}])


def test_saved_native_file_requires_bom_exact_rows_and_fields(tmp_path):
    path = fresh_destination(tmp_path / "failed-records-export.csv")
    path.write_bytes(expected_bytes())
    result = validate_saved_csv(path)
    assert result["native_csv_rows"] == 3
    assert saved_csv_accepted({"passed": True, **result})
    with pytest.raises(FileExistsError):
        fresh_destination(path)


@pytest.mark.parametrize("change", [
    lambda b: b[3:], lambda b: b[:30],
    lambda b: b.replace(b"work_id", b"bad_col", 1),
    lambda b: b.replace(b"failure-0005", b"failure-9999"),
    lambda b: b.replace(b"'=SUM", b"=SUM"),
    lambda b: b + b"extra,row\r\n",
    lambda b: b + b"x" * 65537,
])
def test_incomplete_wrong_or_unsafe_csv_never_passes(change):
    with pytest.raises(ValueError):
        validate_filtered_bytes(change(expected_bytes()))


@pytest.mark.parametrize("result", [None, {}, {"passed": True},
    {"passed": True, "csv_download_executed": False, "native_csv_exact_fields": True, "native_csv_rows": 3},
    {"passed": True, "csv_download_executed": True, "native_csv_exact_fields": False, "native_csv_rows": 3},
    {"passed": True, "csv_download_executed": True, "native_csv_exact_fields": True, "native_csv_rows": 2},
])
def test_native_pass_predicate_requires_actual_saved_csv(result):
    assert not saved_csv_accepted(result)


def test_native_failed_records_requires_explicit_fixture_before_launch(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from tools import check_native_window as checker
    monkeypatch.setattr(checker, "os", SimpleNamespace(name="nt", environ={}))
    called = []
    monkeypatch.setattr(checker.subprocess, "Popen", lambda *a, **kw: called.append(True))
    with pytest.raises(SystemExit):
        checker.main(["--exe", str(tmp_path / "not-launched.exe"), "--inspect-ui", "--inspect-failed-records"])
    assert not called


@pytest.mark.parametrize("budget", ["-1", "121", "nan", "inf"])
def test_dialog_budget_is_bounded(monkeypatch, tmp_path, budget):
    from types import SimpleNamespace
    from tools import check_native_window as checker
    monkeypatch.setattr(checker, "os", SimpleNamespace(name="nt", environ={}))
    with pytest.raises(SystemExit):
        checker.main(["--exe", str(tmp_path / "not-launched.exe"), "--failed-csv-dialog-seconds", budget])
