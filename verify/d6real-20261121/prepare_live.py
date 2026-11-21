"""Prepare a fresh, S:-isolated ranking trial from the verified backup copy."""

from contextlib import closing
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import sys

from preflight import SCRATCH, WORKTREE, copied_path, open_snapshot, selected_cookie_copy
from live_gate import Ledger

SOURCE = SCRATCH / "run-01" / "backup-copy"
BACKUP = Path("S:/Projects/PV-DEV-20261117/backup/realprofile-20261121-r6")
RUNNER = Path("S:/Work/PV/d6-perf-20261120/verify/d6d-20261120/runner.py")
MEDIA = (("images", "artwork-originals"), ("novel", "novels"), ("ugoira", "ugoira"))


def selected_media(storage):
    return tuple((kind, storage / "PixivVault-NiceGUI" / name) for kind, name in MEDIA)


def prepare(root, prior_attempts=0):
    from pixivvault_nicegui.managed_network_stop_history_readonly import _check_marker
    from pixivvault_nicegui.managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
    from pixivvault_nicegui.managed_network_stop_startup import open_or_initialize_windows_user_stop
    from pixivvault_nicegui.native_profile import NativeProfileStore
    from pixivvault_nicegui.test_owner_runtime import isolated_test_workspace

    copied_path(SCRATCH, root)
    src = copied_path(SCRATCH, SOURCE)
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
    with open_snapshot(root, profile / "profile.sqlite3") as db:
        selected, old_save, db_path, images_path = db.execute(
            "SELECT cookie_file,save_parent,library_readonly_db,library_image_root "
            "FROM native_profile_state WHERE id=1").fetchone()
    if old_save is None or db_path is not None or images_path is not None:
        raise ValueError("copied profile layout differs")
    source_cookie = selected_cookie_copy(src, selected)
    cookie = root / "credentials" / "cookies.txt"
    cookie.parent.mkdir()
    shutil.copyfile(source_cookie, cookie)
    shutil.copyfile(source_cookie, original / "cookies.txt")
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
    for base in (BACKUP / "native-images", src / "native-images", storage):
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
        local.require_allowed()
        shared = open_or_initialize_windows_user_stop(
            selected_roots=selected_media(storage), profile_scope=local)
        state = shared.gate.snapshot()
        if state.blocked:
            return {"status": "stopped", "http_code": state.status_code, "sources": 4}
        shared.require_allowed()

    # Use the matching copied legacy-schema discovery database for the Flet
    # read-only ranking DB queries and baseline local learning context.
    candidates = []
    for candidate in storage.rglob("*.sqlite3"):
        with open_snapshot(root, candidate) as db:
            names = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if {"works", "following_users", "discovery_population"} <= names:
                candidates.append(candidate)
    if len(candidates) != 1:
        raise ValueError("copied Flet database not uniquely identified")
    shutil.copyfile(candidates[0], original / "ranking.sqlite3")
    spec = importlib.util.spec_from_file_location("existing_d6d", RUNNER)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    runner.ROOT = WORKTREE
    revision = runner.original_copy(original)
    Ledger.create(root, prior_attempts=prior_attempts)
    return {"status": "ready", "shared_stop": "open", "sources": 4,
            "legacy_local_gate_pairs": 0, "owner_setup": "ordinary_isolated_startup",
            "original_revision": revision, "prior_attempts": prior_attempts, "live_requests": 0}


def main():
    root = Path(sys.argv[1])
    try:
        prior_attempts = int(sys.argv[2]) if len(sys.argv) == 3 else 0
        result = prepare(root, prior_attempts)
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
