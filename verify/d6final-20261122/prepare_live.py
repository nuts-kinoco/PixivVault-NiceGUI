"""Prepare a fresh, S:-isolated ranking trial from the verified backup copy."""

from contextlib import closing
import argparse
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import sys

from preflight import SCRATCH, WORKTREE, copied_path, open_snapshot, selected_cookie_copy
from live_gate import Ledger, ROUND_THREE_PRIOR, RUN_CAPS, require_quiet_window

BACKUP = Path("S:/Projects/PV-DEV-20261117/backup/realprofile-20261122-r8")
RUNNER = WORKTREE / "verify" / "d6e-20261121" / "measure.py"
MEDIA = (("images", "artwork-originals"), ("novel", "novels"), ("ugoira", "ugoira"))
FAKE_COOKIE = (
    b"# Netscape HTTP Cookie File\n"
    b".pixiv.net\tTRUE\t/\tTRUE\t4102444800\tPHPSESSID\t55_synthetic_only\n"
)


def selected_media(storage):
    return tuple((kind, storage / "PixivVault-NiceGUI" / name) for kind, name in MEDIA)


def prepare(root, prior_attempts=0, *, fake=False, owner_open_at=None):
    from pixivvault_nicegui.managed_network_stop_history_readonly import (
        _check_marker, read_existing_stop_history_readonly,
    )
    from pixivvault_nicegui.managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
    from pixivvault_nicegui.managed_network_stop_startup import open_or_initialize_windows_user_stop
    from pixivvault_nicegui.native_profile import NativeProfileStore
    from pixivvault_nicegui.test_owner_runtime import isolated_test_workspace

    if not fake:
        if (owner_open_at is None or owner_open_at.utcoffset() is None
                or owner_open_at.astimezone().date() != datetime.now().date()
                or owner_open_at > datetime.now().astimezone() or prior_attempts not in RUN_CAPS):
            raise ValueError("current operator confirmation and shared count required")
        if prior_attempts == ROUND_THREE_PRIOR:
            require_quiet_window()
    copied_path(SCRATCH, root)
    if root == SCRATCH:
        raise ValueError("use a dedicated run directory")
    src = copied_path(BACKUP, BACKUP)
    root.mkdir(exist_ok=False)
    (root / ".pixivvault-isolated-test-workspace").write_bytes(
        b"PixivVault isolated source test workspace v1\n")
    profile = root / "profile"
    storage = root / "storage"
    original = root / "original"
    original.mkdir()
    paths = (src / "profile-native", src / "native-images")
    for old, new in zip(paths, (profile, storage)):
        for entry in old.rglob("*"):
            copied_path(src, entry)
        shutil.copytree(old, new)
    if prior_attempts == ROUND_THREE_PRIOR:
        observation = read_existing_stop_history_readonly(profile, owner_kind="native")
        evidence = {"observed_at": datetime.now().astimezone().isoformat(),
                    "state": observation.state, "status": observation.status_code,
                    "revision": observation.revision,
                    "blocked": {"observed_unblocked": 0, "observed_stopped": 1}.get(observation.state)}
        (root / "prepare-profile-stop.json").write_text(json.dumps(evidence), encoding="ascii")
        if observation.state != "observed_unblocked":
            return {"status": observation.state, "scope": "profile", "http_code": observation.status_code}
    with open_snapshot(root, profile / "profile.sqlite3") as db:
        selected, old_save, db_path, images_path = db.execute(
            "SELECT cookie_file,save_parent,library_readonly_db,library_image_root "
            "FROM native_profile_state WHERE id=1").fetchone()
    if old_save is None or db_path is not None or images_path is not None:
        raise ValueError("copied profile layout differs")
    cookie = root / "credentials" / "cookies.txt"
    if fake:
        (root / "fake-wire-only").write_bytes(b"D6 fake wire v1\n")
    # The copied settings still point at the original machine paths. Rebase
    # just the two references before any product code inspects them.
    with closing(sqlite3.connect(profile / "profile.sqlite3")) as db:
        changed = db.execute("UPDATE native_profile_state SET cookie_file=?,save_parent=? WHERE id=1",
                             (str(cookie), str(storage))).rowcount
        if changed != 1:
            raise ValueError("copied profile missing")
        db.commit()
    # The ordinary startup path handles a current-format media root with
    # neither legacy gate file. Match the untouched backup to the clone and
    # refuse any local STOP or uncertain/incomplete history in this setup.
    for base in (src / "native-images", storage):
        for kind, media in selected_media(base):
            copied_path(base, media)
            _check_marker(media, kind)
            gate = media / ".pixivvault-network-stop.sqlite3"
            if any(gate.with_name(gate.name + suffix).exists()
                   for suffix in ("", ".lock", "-wal", "-shm", "-journal")):
                raise ValueError("legacy gate history requires formal migration")
    with isolated_test_workspace(root):
        NativeProfileStore.peek(profile)
        local = ManagedProfileNetworkStopScope(profile)
        state = local.gate.snapshot()
        if state.blocked:
            return {"status": "stopped", "http_code": state.status_code, "scope": "profile"}
        local.require_allowed()
        shared = open_or_initialize_windows_user_stop(
            selected_roots=selected_media(storage), profile_scope=local)
        state = shared.gate.snapshot()
        if state.blocked:
            return {"status": "stopped", "http_code": state.status_code, "scope": "shared"}
        shared.require_allowed()

    # Use the matching copied legacy-schema discovery database for the Flet
    # read-only ranking DB queries and baseline local learning context.
    source_db = copied_path(root, storage / "PixivVault-NiceGUI/discovery/learning.sqlite3")
    if not source_db.is_file():
        raise ValueError("discovery database missing")
    for suffix in ("-wal", "-shm", "-journal"):
        copied_path(root, source_db.with_name(source_db.name + suffix))
    with closing(sqlite3.connect(source_db.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA trusted_schema=OFF")
        names = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        tables = ("works", "following_users", "discovery_population")
        if not set(tables) <= names:
            raise ValueError("copied discovery database schema differs")
        with closing(sqlite3.connect(original / "ranking.sqlite3")) as target:
            db.backup(target, pages=0)
            if target.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ValueError("copied Flet database integrity error")
            counts = {name: target.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
                      for name in tables}
    spec = importlib.util.spec_from_file_location("existing_d6d", RUNNER)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    revision = runner.original_copy(original)
    cookie.parent.mkdir()
    if fake:
        cookie.write_bytes(FAKE_COOKIE)
    else:
        source = selected_cookie_copy(src, selected)
        if source is None or not source.is_file():
            raise ValueError("backup copy does not contain the selected credential file")
        shutil.copyfile(source, cookie)
        (root / "normal-owner-open.json").write_text(
            json.dumps({"owner_open_at": owner_open_at.isoformat()}), encoding="ascii")
    shutil.copyfile(cookie, original / "cookies.txt")
    Ledger.create(root, prior_attempts=prior_attempts)
    return {"status": "ready", "shared_stop": "open", "sources": 4,
            "legacy_local_gate_pairs": 0, "owner_setup": "ordinary_isolated_startup",
            "original_revision": revision, "prior_attempts": prior_attempts, "live_requests": 0,
            "discovery_counts": counts}


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("root", type=Path)
    parser.add_argument("prior_attempts", type=int, nargs="?", default=0)
    parser.add_argument("--fake", action="store_true")
    parser.add_argument("--owner-open-at", type=datetime.fromisoformat)
    args = parser.parse_args()
    if args.fake and args.prior_attempts:
        parser.error("fake runs have a separate request ledger")
    try:
        result = prepare(args.root, args.prior_attempts, fake=args.fake,
                         owner_open_at=args.owner_open_at)
    except Exception as error:
        reason = getattr(error, "reason", None)
        # Only reviewed fixed product codes can be written to evidence.
        permitted = {"source_unverified", "source_busy", "history_unverified", "target_exists"}
        result = {"status": "failed", "classification": type(error).__name__,
                  "reason": reason if isinstance(reason, str) and reason in permitted else None}
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
