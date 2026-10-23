"""Owned WebView2 acceptance for synthetic download persistence and restart.

All disk inspection is read-only. The only mutations happen through the owned
native application's download/queue UI. Never invoke this against a live DB.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sqlite3
import stat
import struct
import zlib


MARKER = ".pixivvault-download-sandbox.json"
MARKER_CONTENT = {"application": "pixivvault-nicegui", "purpose": "synthetic-downloads", "version": 1}
APPLICATION_ID = 0x50564A31
WORK_PAGES = {"demo-0003": 3, "demo-0004": 1}
# The checked synthetic fixture has aspect ratios 1.5 and 0.8, respectively.
# Pin dimensions as well as each pixel, so a different-size image cannot pass.
WORK_DIMENSIONS = {"demo-0003": (640, 427), "demo-0004": (512, 640)}
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAX_PNG_BYTES = 20 * 1024 * 1024


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _safe_chain(path: Path) -> None:
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError("synthetic download path link rejected")
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("synthetic download path reparse rejected")


def _regular(path: Path, maximum: int) -> bytes:
    _safe_chain(path)
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or not 0 < info.st_size <= maximum):
        raise ValueError("synthetic download regular file rejected")
    return path.read_bytes()


def verify_marker(root: Path) -> None:
    root = Path(root)
    if not root.is_absolute() or root.parent == root:
        raise ValueError("synthetic download root must be an absolute subdirectory")
    _safe_chain(root)
    if not root.is_dir():
        raise ValueError("synthetic download marker root missing")
    try:
        marker = json.loads(_regular(root / MARKER, 4096).decode("utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise ValueError("synthetic download marker invalid") from exc
    if marker != MARKER_CONTENT:
        raise ValueError("synthetic download marker invalid")


def preflight_download_root(root: Path, *, restart: bool) -> dict | None:
    """Initial run requires a nonexistent path; restart must be a known completed run."""
    root = Path(root)
    if not root.is_absolute() or root.parent == root:
        raise ValueError("download sandbox must be a dedicated absolute directory")
    _safe_chain(root)
    if not restart:
        if root.exists() or root.is_symlink():
            raise ValueError("initial download sandbox must not exist")
        if not root.parent.is_dir():
            raise ValueError("download sandbox parent must already exist")
        return None
    verify_marker(root)
    return verify_native_jobs(root, remaining_ids=frozenset(WORK_PAGES))


def _pixel_check(payload: bytes, work_id: str, page: int) -> tuple[int, int]:
    """Independently decode every pixel of the deterministic offline PNG."""
    _assert(payload.startswith(PNG_MAGIC), "saved page is not PNG")
    pos, chunks = len(PNG_MAGIC), []
    while pos + 12 <= len(payload):
        length = int.from_bytes(payload[pos:pos + 4], "big")
        _assert(length <= MAX_PNG_BYTES and pos + 12 + length <= len(payload),
                "saved PNG chunk length invalid")
        name = payload[pos + 4:pos + 8]
        data = payload[pos + 8:pos + 8 + length]
        crc = int.from_bytes(payload[pos + 8 + length:pos + 12 + length], "big")
        _assert(zlib.crc32(name + data) & 0xFFFFFFFF == crc, "saved PNG CRC mismatch")
        chunks.append((name, data))
        pos += 12 + length
        if name == b"IEND":
            break
    _assert(pos == len(payload) and len(chunks) == 3
            and [name for name, _ in chunks] == [b"IHDR", b"IDAT", b"IEND"]
            and chunks[-1][1] == b"", "unexpected saved PNG structure")
    header = chunks[0][1]
    _assert(len(header) == 13, "saved PNG header invalid")
    width, height, depth, mode, compression, filtering, interlace = struct.unpack(">IIBBBBB", header)
    _assert((width, height) == WORK_DIMENSIONS[work_id]
            and (depth, mode, compression, filtering, interlace) == (8, 2, 0, 0, 0),
            "saved PNG dimensions or format invalid")
    raw_length = height * (1 + width * 3)
    inflater = zlib.decompressobj()
    raw = inflater.decompress(chunks[1][1], raw_length + 1)
    _assert(len(raw) == raw_length and inflater.eof and not inflater.unused_data
            and not inflater.unconsumed_tail, "saved PNG pixels invalid")
    seed = hashlib.sha256(f"{work_id}:{page}:view".encode("ascii")).digest()
    stride = 1 + width * 3
    for y in range(height):
        row = raw[y * stride:(y + 1) * stride]
        _assert(row[0] == 0, "saved PNG scanline filter invalid")
        expected = bytearray()
        for x in range(width):
            band = (x * 5 // width + y * 3 // height) % 8
            expected.extend(((seed[band] + x // 8) & 255,
                             (seed[band + 8] + y // 8) & 255, seed[band + 16]))
        _assert(row[1:] == expected, "saved PNG pixel differs from synthetic artwork")
    return width, height


def verify_native_jobs(root: Path, *, remaining_ids: frozenset[str]) -> dict:
    """Inspect persisted SQLite + all physical receipt pages (including deleted history)."""
    verify_marker(root)
    root = Path(root)
    output = root / "outputs"
    _safe_chain(output)
    _assert(output.is_dir(), "synthetic outputs missing")
    database = root / "jobs.sqlite3"
    _regular(database, 32 * 1024 * 1024)
    _safe_chain(database)
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=2)
    try:
        _assert(connection.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
                and connection.execute("PRAGMA user_version").fetchone()[0] == 1,
                "download database identity invalid")
        tables = {r[0] for r in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        _assert(tables == {"jobs", "operations"}, "download database tables invalid")
        jobs = [json.loads(row[0]) for row in connection.execute("SELECT payload FROM jobs ORDER BY ordinal")]
    finally:
        connection.close()
    _assert(len(jobs) == len(remaining_ids)
            and {j.get("target_id") for j in jobs} == remaining_ids,
            "download history count or targets invalid")
    receipts = {}
    for job in jobs:
        work_id = job["target_id"]
        _assert(job.get("kind") == "demo" and job.get("state") == "done"
                and job.get("attempt") == 1 and job.get("total") == WORK_PAGES[work_id]
                and job.get("done") == WORK_PAGES[work_id]
                and re.fullmatch(r"[0-9a-f]{32}", job.get("job_id", "")) is not None,
                "download history not completed exactly once")
        relative = job.get("result")
        _assert(type(relative) is str and re.fullmatch(
            r"outputs/run-[0-9a-f]{32}/receipt[.]json", relative) is not None,
            "download history receipt path invalid")
        receipts[work_id] = relative

    # Deleted history retains output files; each must still have its own exact receipt.
    directories = list(output.iterdir())
    _assert(len(directories) == len(WORK_PAGES), "unexpected synthetic output directories")
    manifests = {}
    found = set()
    for directory in directories:
        _safe_chain(directory)
        _assert(directory.is_dir() and re.fullmatch(r"run-[0-9a-f]{32}", directory.name) is not None,
                "synthetic output directory invalid")
        receipt_bytes = _regular(directory / "receipt.json", 1024 * 1024)
        receipt = json.loads(receipt_bytes)
        work_id = receipt.get("work_id")
        _assert(work_id in WORK_PAGES and work_id not in found
                and receipt.get("version") == 1 and receipt.get("data") == "synthetic"
                and receipt.get("page_count") == WORK_PAGES[work_id]
                and type(receipt.get("files")) is list
                and len(receipt["files"]) == WORK_PAGES[work_id],
                "synthetic receipt contents invalid")
        found.add(work_id)
        _assert(len(list(directory.iterdir())) == 1 + WORK_PAGES[work_id],
                "unexpected synthetic output files")
        file_digests = []
        for index, entry in enumerate(receipt["files"]):
            name = f"{work_id}_p{index + 1}.png"
            _assert(type(entry) is dict and entry.get("name") == name
                    and type(entry.get("bytes")) is int and 0 < entry["bytes"] <= MAX_PNG_BYTES
                    and type(entry.get("sha256")) is str
                    and re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is not None,
                    "synthetic receipt page fields invalid")
            payload = _regular(directory / name, MAX_PNG_BYTES)
            digest = hashlib.sha256(payload).hexdigest()
            _assert(len(payload) == entry["bytes"] and digest == entry["sha256"],
                    "synthetic saved page hash invalid")
            width, height = _pixel_check(payload, work_id, index)
            file_digests.append({"sha256": digest, "pixels": [width, height]})
        relative = (directory / "receipt.json").relative_to(root).as_posix()
        if work_id in receipts:
            _assert(receipts[work_id] == relative, "completed job receipt mismatch")
        manifests[work_id] = {"receipt": relative,
                              "sha256": hashlib.sha256(receipt_bytes).hexdigest(),
                              "pages": file_digests}
    _assert(found == set(WORK_PAGES), "synthetic receipt page set incomplete")
    return {"passed": True, "history": len(jobs), "receipts": manifests,
            "validated_page_count": sum(len(v["pages"]) for v in manifests.values())}


def jobs_ui_accepted(inspection: dict | None, *, restart: bool) -> bool:
    if not isinstance(inspection, dict) or not inspection.get("passed"):
        return False
    jobs = inspection.get("download_jobs")
    return (isinstance(jobs, dict) and jobs.get("passed") is True
            and jobs.get("mode") == ("restart" if restart else "initial")
            and jobs.get("disk_verified") is True
            and jobs.get("history_verified") is True
            and jobs.get("search_retained") is True
            and jobs.get("validated_page_count") == 4
            and jobs.get("history_before") == 2
            and jobs.get("history_after") == (1 if restart else 2)
            and (not restart or (jobs.get("no_auto_execution") is True
                                 and jobs.get("delete_kept_files") is True))
            and isinstance(jobs.get("receipts"), dict)
            and set(jobs["receipts"]) == set(WORK_PAGES))


def inspect_download_jobs(page, screenshot: Path, guard, root: Path, *, restart: bool) -> dict:
    """Drive the real owned WebView; no test-only product switches or delays."""
    from playwright.sync_api import expect

    run = guard.run
    run(page.get_by_test_id("tab-download").click)
    download = page.get_by_test_id("download-view")
    run(expect(download).to_be_visible)
    run(expect(download).to_have_attribute, "data-active", "true")
    search = download.get_by_test_id("download-search")
    if not restart:
        for work_id in WORK_PAGES:
            run(search.fill, work_id)
            run(expect(download.get_by_test_id("download-count")).to_have_text, "1 / 1 件表示")
            run(download.locator(f'[data-work-id="{work_id}"]').get_by_role("checkbox").click)
        run(expect(download.get_by_test_id("download-selection")).to_have_text, "対象 2 件")
        run(download.get_by_test_id("download-submit").click)
        run(expect(download.get_by_test_id("download-notice")).to_have_text,
            "2件をキューへ追加しました")
        run(expect(download.get_by_test_id("download-selection")).to_have_text, "対象 0 件")
    else:
        # A restart must never auto-enqueue or silently execute another attempt.
        run(search.fill, "demo-0004")
        run(expect(download.get_by_test_id("download-selection")).to_have_text, "対象 0 件")

    run(page.get_by_test_id("tab-queue").click)
    queue = page.get_by_test_id("queue-view")
    run(expect(queue).to_be_visible)
    run(expect(queue.get_by_test_id("queue-count")).to_have_text, "2件")
    queue_search = queue.get_by_test_id("queue-search")
    for work_id, pages in WORK_PAGES.items():
        run(queue_search.fill, work_id)
        row = queue.get_by_test_id("job-row")
        run(expect(row).to_have_count, 1)
        run(expect(row).to_have_attribute, "data-state", "done")
        run(expect(row.get_by_test_id("job-progress")).to_have_text, f"{pages}/{pages}")
    run(queue_search.fill, "demo-0003")
    disk_before = verify_native_jobs(root, remaining_ids=frozenset(WORK_PAGES))
    if restart:
        row = queue.get_by_test_id("job-row")
        run(row.get_by_test_id("job-delete").click)
        run(page.get_by_test_id("job-delete-confirm").click)
        run(expect(row).to_have_count, 0)
        run(queue_search.fill, "")
        run(expect(queue.get_by_test_id("queue-count")).to_have_text, "1件")
        disk_after = verify_native_jobs(root, remaining_ids=frozenset({"demo-0004"}))
        _assert(disk_before["receipts"] == disk_after["receipts"],
                "history deletion changed saved output files")
    else:
        run(queue_search.fill, "")
        disk_after = disk_before
    run(page.get_by_test_id("tab-download").click)
    run(expect(download).to_be_visible)
    run(expect(search).to_have_value, "demo-0004")
    run(expect(download.get_by_test_id("download-count")).to_have_text, "1 / 1 件表示")
    image = screenshot.with_name(screenshot.stem + (
        "-download-jobs-restart.png" if restart else "-download-jobs.png"))
    run(page.screenshot, path=str(image), full_page=False)
    return {"passed": True, "mode": "restart" if restart else "initial",
            "history_verified": True, "disk_verified": True,
            "search_retained": True, "validated_page_count": disk_before["validated_page_count"],
            "history_before": disk_before["history"], "history_after": disk_after["history"],
            "no_auto_execution": restart,
            "delete_kept_files": restart,
            "receipts": disk_after["receipts"], "screenshot": str(image)}
