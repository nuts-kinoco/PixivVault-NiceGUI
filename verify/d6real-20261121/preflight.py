"""Read-only inventory of the D6 benchmark copy. Never starts a client."""

import argparse
from contextlib import closing
import json
from pathlib import Path, PureWindowsPath
import sqlite3
import sys

WORKTREE = Path(__file__).resolve().parents[2]
SCRATCH = Path("S:/Work/_tmp/PV/d6real")
sys.path.insert(0, str(WORKTREE / "src"))

from pixivvault_nicegui.managed_network_stop_gate import _reject_links
from pixivvault_nicegui.managed_network_stop_history_readonly import (
    read_existing_stop_history_readonly,
)
from pixivvault_nicegui.pixiv_read_client import CookieSourceRejected, _read_cookies

PROFILE = "profile-native"
NATIVE_IMAGES = "native-images"
IMAGE_SUFFIXES = frozenset((".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif"))
LIBRARY_TABLES = ("works", "following_users", "discovery_swipe_history", "discovery_population")


def copied_path(root, path):
    # Check lexical containment before even stat'ing the path.
    if not path.is_absolute() or not path.is_relative_to(root) or ".." in path.parts:
        raise ValueError("path outside measurement copy")
    _reject_links(path)
    return path


def stored_path_kind(root, value):
    if value is None:
        return "unset"
    path = PureWindowsPath(value)
    if not path.is_absolute() or ".." in path.parts:
        return "unsafe"
    return "inside_copy" if path.is_relative_to(PureWindowsPath(root)) else "outside_copy"


def selected_cookie_copy(root, value):
    if not isinstance(value, str):
        return None
    parts = PureWindowsPath(value).parts
    if parts.count("profile-native-credentials") != 1:
        return None
    relative = parts[parts.index("profile-native-credentials"):]
    if ".." in relative or PureWindowsPath(parts[-1]).name != "cookies.txt":
        return None
    return copied_path(root, root.joinpath(*relative))


def open_snapshot(root, path):
    copied_path(root, path)
    for suffix in ("-wal", "-journal", "-shm"):
        if copied_path(root, path.with_name(path.name + suffix)).exists():
            raise ValueError("snapshot has journal sidecars")
    db = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    db.execute("PRAGMA trusted_schema=OFF")
    return closing(db)


def library_inventory(root):
    # Directory extensions are not data counts. Count only rows in the
    # known legacy schema, and image files separately from those rows.
    media = copied_path(root, root / NATIVE_IMAGES)
    if not media.is_dir():
        return {"legacy_db_candidates": 0, "tables": {}, "image_files": 0,
                "unreadable_db": 0, "source_consistency": "unverified"}
    tables = {}
    image_files = unreadable = legacy_dbs = 0
    for path in media.rglob("*"):
        copied_path(root, path)
        if not path.is_file():
            continue
        if path.suffix.lower() in IMAGE_SUFFIXES:
            image_files += 1
        elif path.suffix.lower() in (".sqlite3", ".db"):
            try:
                with open_snapshot(root, path) as db:
                    names = {row[0] for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'")}
                    if {"works", "following_users"} <= names:
                        legacy_dbs += 1
                        for name in LIBRARY_TABLES:
                            if name in names:
                                count, = db.execute(f'SELECT count(*) FROM "{name}"').fetchone()
                                tables[name] = tables.get(name, 0) + count
            except (OSError, sqlite3.Error, ValueError):
                unreadable += 1
    return {"legacy_db_candidates": legacy_dbs, "tables": tables,
            "image_files": image_files, "unreadable_db": unreadable,
            "source_consistency": "unverified"}


def inspect_copy(root):
    root = copied_path(root, root)
    profile = copied_path(root, root / PROFILE)
    gate = read_existing_stop_history_readonly(profile, owner_kind="native")
    with open_snapshot(root, profile / "profile.sqlite3") as db:
        cookie, save, legacy_db, legacy_images = db.execute(
            "SELECT cookie_file,save_parent,library_readonly_db,library_image_root "
            "FROM native_profile_state WHERE id=1"
        ).fetchone()
    stored = {label: stored_path_kind(root, path) for label, path in (
        ("cookie", cookie), ("save", save), ("legacy_db", legacy_db), ("legacy_images", legacy_images))}
    source = selected_cookie_copy(root, cookie)
    cookie_state = "missing"
    if source is not None and source.is_file():
        try:
            _, claimed = _read_cookies(source)
            cookie_state = "parseable_with_session" if claimed is not None else "session_unconfirmed"
        except CookieSourceRejected:
            cookie_state = "invalid"

    return {
        "status": "blocked",
        "pixiv_requests": 0,
        "stored_paths": stored,
        "copied_cookie": cookie_state,
        "online_identity": "unverified",
        "profile_stop": {"state": gate.state, "status_code": gate.status_code,
                         "revision": gate.revision, "reason": gate.reason},
        "windows_user_stop": "unverified_by_this_offline_inventory",
        "legacy_library_match": False,
        "library_inventory": library_inventory(root),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("copy", type=Path, help="backup-copy below the D6 S: scratch directory")
    args = parser.parse_args(argv)
    try:
        copied_path(SCRATCH, args.copy)
        if args.copy == SCRATCH or args.copy.name != "backup-copy":
            raise ValueError("measurement copy required")
        report = inspect_copy(args.copy)
    except Exception:
        # Keep raw exceptions, paths, account IDs and parser data out of evidence.
        report = {"status": "inspection_failed", "pixiv_requests": 0}
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["status"] == "blocked" else 2


if __name__ == "__main__":
    raise SystemExit(main())
