"""Read-only inventory and privacy gate for a Windows onedir distribution.

This does not install, register a URI scheme, start an app or modify a tree.
The caller-supplied source commit is a label, not proof of build provenance.
Pair this gate with a build from a frozen source tree and native acceptance.
It is a bounded known-pattern scan and inventory, not a complete secret audit.
PyInstaller CArchive/PYZ and other non-ZIP embedded formats are not decoded;
every report records that limitation even when the checks performed pass.
"""

from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import zipfile


REQUIRED = ("PixivVault-NiceGUI.exe", "_internal/python313.dll", "_internal/base_library.zip")
FORBIDDEN_PARTS = {".git", ".nicegui", "artifacts", "quarantine", "_llm_keys"}
FORBIDDEN_SUFFIXES = {".sqlite", ".sqlite3", ".db", ".db-wal", ".db-shm", ".log"}
MAX_FILES = 50000
MAX_BYTES = 4 * 1024 ** 3
MAX_ARCHIVE_MEMBER = 32 * 1024 ** 2
MAX_ARCHIVE_BYTES = 512 * 1024 ** 2
MAX_COMPRESSION_RATIO = 1000
_PRIVATE_KEY = re.compile(rb"(?:^|\n)-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----")
_SESSION = re.compile(rb"(?:^|[\r\n;\t ])PHPSESSID[\t=][^\s;\"']{8,}")
_COOKIE_ROW = re.compile(rb"(?:^|\n)[^\r\n\t]{1,256}\t(?:TRUE|FALSE)\t/[^\r\n\t]*\t(?:TRUE|FALSE)\t[0-9]{1,12}\t[^\r\n\t]+\t[^\r\n\t]+")


def _name_problem(name: str) -> str | None:
    normalized = name.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    if (not parts or normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized)
            or any(part in {"..", "."} for part in parts)):
        return "unsafe_relative_path"
    lowered = tuple(part.casefold() for part in parts)
    if any(part in FORBIDDEN_PARTS for part in lowered):
        return "private_directory"
    base = lowered[-1]
    if (base in {"cookies.txt", "cookie.txt", "cookie-snapshot.txt", "cookies.json", ".env"}
            or base.startswith(("cookie-snapshot", ".env."))
            or re.match(r"cookies?\.(?:txt|json)(?:\.|$)", base)
            or base in {"id_rsa", "id_ed25519", "id_ecdsa"}):
        return "credential_file"
    if (Path(base).suffix in FORBIDDEN_SUFFIXES
            or re.search(r"\.(?:db|sqlite3?)(?:-(?:wal|shm|journal))?(?:\.[^.]+)?$", base)
            or re.search(r"\.log(?:\.[^.]+)?$", base)
            or re.fullmatch(r"(?:gui_error_log|crash_log|app-stderr)\.txt(?:\.[^.]+)?", base)):
        return "runtime_data"
    return None


def _linked(metadata) -> bool:
    return bool(stat.S_ISLNK(metadata.st_mode) or
                getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


class _MaterialScanner:
    """Incremental recognition of known private material, not a universal DLP proof."""
    def __init__(self):
        self.tail = b""
        self.cookie_header = False
        self.cookie_row = False
        self.problem = None

    def feed(self, chunk):
        data = self.tail + chunk
        self.cookie_header |= b"# Netscape HTTP Cookie File" in data
        self.cookie_row |= _COOKIE_ROW.search(data) is not None
        if _PRIVATE_KEY.search(data):
            self.problem = "private_key_material"
        if _SESSION.search(data) or (self.cookie_header and self.cookie_row):
            self.problem = "session_cookie_material"
        self.tail = data[-4096:]


def _inspect_archive(path, relative, reject):
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > MAX_FILES or sum(item.file_size for item in members) > MAX_ARCHIVE_BYTES:
                reject("archive_entry_or_size_limit", relative)
                return
            read_total = 0
            for member in members:
                problem = _name_problem(member.filename)
                mode = member.external_attr >> 16
                if problem or stat.S_ISLNK(mode) or member.flag_bits & 1:
                    reject("archive_" + (problem or "uninspectable_entry"), relative)
                    return
                if member.is_dir():
                    continue
                if (member.file_size > MAX_ARCHIVE_MEMBER
                        or member.file_size > max(1, member.compress_size) * MAX_COMPRESSION_RATIO):
                    reject("archive_expansion_limit", relative)
                    return
                scanner = _MaterialScanner()
                read_member = 0
                member_data = bytearray()
                with archive.open(member) as stream:
                    for chunk in iter(lambda: stream.read(65536), b""):
                        if read_member == 0 and chunk.startswith(b"PK\x03\x04"):
                            reject("archive_nested_container_uninspected", relative)
                            return
                        read_member += len(chunk)
                        read_total += len(chunk)
                        if read_member > MAX_ARCHIVE_MEMBER or read_total > MAX_ARCHIVE_BYTES:
                            reject("archive_expansion_limit", relative)
                            return
                        member_data.extend(chunk)
                        scanner.feed(chunk)
                if read_member != member.file_size:
                    reject("archive_size_mismatch", relative)
                    return
                if scanner.problem:
                    reject("archive_" + scanner.problem, relative)
                    return
                if zipfile.is_zipfile(BytesIO(member_data)):
                    # ZIP readers accept an executable/prefix before the local
                    # headers too. Never label an opaque nested ZIP as scanned.
                    reject("archive_nested_container_uninspected", relative)
                    return
    except (OSError, RuntimeError, zipfile.BadZipFile, NotImplementedError):
        reject("invalid_or_uninspectable_archive", relative)


def inspect_release(root: Path, *, source_commit: str = "", expected: dict | None = None) -> dict:
    root = Path(root)
    report = {"format": 1, "source_commit": source_commit, "passed": False,
              "file_count": 0, "total_bytes": 0, "tree_sha256": "", "files": [], "violations": [],
              "scan_scope": "file names, raw known patterns, bounded ZIP members, SHA-256 inventory",
              "complete_secret_audit": False,
              "uninspected_embedded_formats": ["PyInstaller CArchive/PYZ", "other non-ZIP containers"]}
    def reject(code, relative="."):
        report["violations"].append({"code": code, "path": relative})
    if source_commit and not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        reject("invalid_source_commit")
        return report
    try:
        for component in (root, *root.absolute().parents):
            if _linked(component.lstat()):
                reject("linked_root")
                return report
        if not root.is_dir():
            reject("missing_directory")
            return report
    except OSError:
        reject("unreadable_root")
        return report
    root = root.resolve()
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError:
            reject("unreadable_directory", directory.relative_to(root).as_posix())
            continue
        for item in entries:
            relative = item.relative_to(root).as_posix()
            try:
                metadata = item.lstat()
                if _linked(metadata):
                    reject("linked_entry", relative)
                    continue
                issue = _name_problem(relative)
                if issue:
                    reject(issue, relative)
                    if item.is_dir():
                        continue
                if stat.S_ISDIR(metadata.st_mode):
                    pending.append(item)
                    continue
                if not stat.S_ISREG(metadata.st_mode):
                    reject("non_regular_entry", relative)
                    continue
                if metadata.st_nlink != 1:
                    reject("hard_linked_entry", relative)
                    continue
                if len(report["files"]) >= MAX_FILES or report["total_bytes"] + metadata.st_size > MAX_BYTES:
                    reject("inventory_limit", relative)
                    pending.clear()
                    break
                digest = hashlib.sha256()
                prefix = b""
                scanner = _MaterialScanner()
                with item.open("rb") as stream:
                    opened = os.fstat(stream.fileno())
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                        scanner.feed(chunk)
                        if len(prefix) < 1024 * 1024:
                            prefix += chunk[:1024 * 1024 - len(prefix)]
                    after_read = os.fstat(stream.fileno())
                final = item.lstat()
                identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
                if not identity(metadata) == identity(opened) == identity(after_read) == identity(final):
                    reject("changed_during_inspection", relative)
                    continue
                if scanner.problem:
                    reject(scanner.problem, relative)
                if relative == REQUIRED[0] and not prefix.startswith(b"MZ"):
                    reject("invalid_windows_executable", relative)
                if (item.suffix.casefold() == ".zip" or prefix.startswith(b"PK\x03\x04")
                        or zipfile.is_zipfile(item)):
                    _inspect_archive(item, relative, reject)
                    after_archive = item.lstat()
                    if identity(after_archive) != identity(final):
                        reject("changed_during_archive_inspection", relative)
                report["files"].append({"path": relative, "bytes": metadata.st_size, "sha256": digest.hexdigest()})
                report["total_bytes"] += metadata.st_size
            except OSError:
                reject("unreadable_entry", relative)
    report["files"].sort(key=lambda item: item["path"])
    inventory = {entry["path"]: entry for entry in report["files"]}
    for needed in REQUIRED:
        if needed not in inventory:
            reject("required_file_missing", needed)
    report["file_count"] = len(inventory)
    canonical = json.dumps(report["files"], sort_keys=True, separators=(",", ":")).encode("utf-8")
    report["tree_sha256"] = hashlib.sha256(canonical).hexdigest()
    if expected is not None:
        if (not isinstance(expected, dict) or expected.get("format") != 1 or expected.get("passed") is not True
                or expected.get("source_commit") != source_commit or expected.get("files") != report["files"]
                or expected.get("tree_sha256") != report["tree_sha256"]):
            reject("manifest_mismatch")
    report["passed"] = not report["violations"]
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source-commit", default="")
    parser.add_argument("--expected", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    root, output = args.root.resolve(), args.output.resolve()
    if output == root or root in output.parents:
        parser.error("output must be outside the inspected distribution")
    expected = None
    if args.expected:
        try:
            if args.expected.stat().st_size > 20 * 1024 * 1024:
                raise ValueError()
            expected = json.loads(args.expected.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            parser.error("expected manifest is unreadable or invalid")
    report = inspect_release(args.root, source_commit=args.source_commit, expected=expected)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=True, indent=2)
    print(json.dumps({"passed": report["passed"], "file_count": report["file_count"],
                      "total_bytes": report["total_bytes"], "violation_count": len(report["violations"])}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
