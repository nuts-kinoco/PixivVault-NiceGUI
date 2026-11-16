"""Validate a new CSV actually saved through the owned Windows SaveFileDialog.

Only the fixed, generated three-row format-category fixture is accepted. This
helper never creates the exported file or writes a substitute for the UI.
"""
from __future__ import annotations

import csv
from dataclasses import asdict
from io import StringIO
import os
from pathlib import Path
import stat

from pixivvault_nicegui.failed_records_fixtures import build_failed_records_fixture


COLUMNS = ("work_id", "user_id", "title", "work_type", "error_reason", "failed_at", "retry_count")
MAX_BYTES = 65_536


def fresh_destination(path: Path) -> Path:
    path = Path(path)
    if not path.is_absolute() or path.name != "failed-records-export.csv":
        raise ValueError("explicit native CSV acceptance destination required")
    for parent in (path.parent, *path.parent.parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("CSV acceptance parent must be a regular directory")
    if path.exists() or path.is_symlink():
        raise FileExistsError("native CSV acceptance must not overwrite a file")
    return path


def validate_filtered_bytes(data: bytes) -> dict:
    if not isinstance(data, bytes) or len(data) > MAX_BYTES or not data.startswith(b"\xef\xbb\xbf"):
        raise ValueError("native CSV has invalid encoding or size")
    reader = csv.DictReader(StringIO(data.decode("utf-8-sig"), newline=""), strict=True)
    if reader.fieldnames != list(COLUMNS):
        raise ValueError("native CSV columns differ from the legacy export")
    rows = list(reader)
    expected = []
    for item in build_failed_records_fixture():
        if item.work_id not in {"failure-0005", "failure-0011", "failure-0017"}:
            continue
        row = {key: str(value) for key, value in asdict(item).items()}
        if item.work_id == "failure-0017":
            row["title"] = "'=SUM(1,2)"
        expected.append(row)
    if rows != expected:
        raise ValueError("native CSV does not contain the exact filtered records")
    return {"csv_download_executed": True, "native_csv_exact_fields": True,
            "native_csv_rows": len(rows), "csv_bom": True}


def validate_saved_csv(path: Path) -> dict:
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or getattr(before, "st_file_attributes", 0) & 0x400 or before.st_size > MAX_BYTES):
        raise ValueError("native CSV must be a bounded regular single-link file")
    identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if identity(opened) != identity(before):
            raise ValueError("native CSV changed before reading")
        data = stream.read(MAX_BYTES + 1)
        after = os.fstat(stream.fileno())
    if identity(after) != identity(before) or identity(path.lstat()) != identity(before):
        raise ValueError("native CSV changed during reading")
    return validate_filtered_bytes(data)


def saved_csv_accepted(result: object) -> bool:
    return (isinstance(result, dict) and result.get("passed") is True
            and result.get("csv_download_executed") is True
            and result.get("native_csv_exact_fields") is True
            and type(result.get("native_csv_rows")) is int
            and result["native_csv_rows"] == 3)
