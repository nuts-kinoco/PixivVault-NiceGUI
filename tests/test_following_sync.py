"""S03 contract tests: synthetic readers and owned temporary databases only."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, RLock
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import sqlite3

import pytest

import pixivvault_nicegui.following_sync as following
from pixivvault_nicegui.following_sync import (
    FollowingSyncStore, FollowingSyncError, FollowingSyncConflict,
    FollowingSyncCancelled, FollowingSyncTimedOut,
)
from pixivvault_nicegui.pixiv_read_client import (
    LivePixivReadClient, InvalidPayload, TransportFailed, _validate_destination,
)
from pixivvault_nicegui.preferences import RequestPreferences
from pixivvault_nicegui.request_policy import RequestRetryPolicy
from pixivvault_nicegui.fs_paths import logical_path


def author(uid="41", name="Synthetic author", account="synthetic_author",
           profile_img="https://i.pximg.net/user-profile/offline/avatar.png"):
    return dict(user_id=uid, name=name, account=account, profile_img=profile_img)


class Reader:
    def __init__(self, public=(), private=(), account_id="9001", hook=None):
        self.public, self.private = tuple(public), tuple(private)
        self.account_id, self.hook, self.calls = account_id, hook, []

    def require_identity(self):
        return self.account_id

    def get_following_users_detailed(self, account_id, rest_type="show"):
        assert account_id == self.account_id
        self.calls.append(rest_type)
        if self.hook is not None:
            self.hook(rest_type)
        return self.public if rest_type == "show" else self.private


@pytest.fixture
def store(tmp_path):
    result = FollowingSyncStore(tmp_path / "owned-following")
    try:
        yield result
    finally:
        result.close()


def seed(store):
    return store.sync(Reader(public=(author(),)))


def test_full_sync_both_routes_order_metadata_and_restart(tmp_path):
    root = tmp_path / "owned-following"
    first = FollowingSyncStore(root)
    reader = Reader(public=(author("41"), author("42", name="Second")),
                    private=(author("43", name="Private"), author("41")))
    try:
        empty = first.snapshot("9001")
        assert empty.revision == 0 and empty.last_synced_at is None
        assert empty.authors == () and reader.calls == []
        saved = first.sync(reader, expected_revision=0)
        assert reader.calls == ["show", "hide"]
        assert saved.account_id == "9001" and saved.revision == 1
        assert saved.last_synced_at is not None
        assert [row.user_id for row in saved.following] == ["41", "42", "43"]
        assert [row.follow_order for row in saved.following] == [0, 1, 2]
        assert [row.restrict for row in saved.following] == [1, 0, 1]
        assert saved.following[0].account == "synthetic_author"
        assert saved.following[0].profile_img == author()["profile_img"]
    finally:
        first.close()
    reopened = FollowingSyncStore(root)
    try:
        assert reopened.snapshot("9001") == saved
    finally:
        reopened.close()


def test_private_failure_keeps_prior_snapshot_and_no_automatic_retry(store):
    before = seed(store)
    attempts = []
    policy = RequestRetryPolicy(
        lambda: RequestPreferences(0.3, 3, 3.0),
        sleep=lambda _: pytest.fail("S03 must not retry a failed GET"),
    )
    def hook(rest):
        if rest == "hide":
            def fail():
                attempts.append(rest)
                raise TransportFailed()
            policy.execute(fail, deadline=following.time.monotonic() + 10)
    reader = Reader(public=(author("42"),), hook=hook)
    with pytest.raises(FollowingSyncError):
        store.sync(reader)
    assert reader.calls == ["show", "hide"] and attempts == ["hide"]
    assert store.snapshot("9001") == before


def test_cancel_between_routes_prevents_private_get_and_commit(store):
    before = seed(store)
    cancelled = Event()
    reader = Reader(public=(author("42"),), hook=lambda _: cancelled.set())
    with pytest.raises(FollowingSyncCancelled):
        store.sync(reader, cancelled=cancelled.is_set)
    assert reader.calls == ["show"]
    assert store.snapshot("9001") == before


def test_expired_operation_does_not_publish_completed_response(store, monkeypatch):
    before = seed(store)
    clock = [100.0]
    monkeypatch.setattr(following.time, "monotonic", lambda: clock[0])
    def expire(rest):
        if rest == "hide":
            clock[0] = 111.0
    with pytest.raises(FollowingSyncTimedOut):
        store.sync(Reader(public=(author("42"),), hook=expire), timeout=10)
    assert store.snapshot("9001") == before


def test_identity_change_after_fetch_is_rejected_without_mixing_accounts(store):
    before = seed(store)
    reader = Reader(public=(author("42"),))
    reader.hook = lambda rest: setattr(reader, "account_id", "9002") if rest == "hide" else None
    with pytest.raises(FollowingSyncError):
        store.sync(reader)
    assert store.snapshot("9001") == before
    assert store.snapshot("9002").last_synced_at is None
    assert store.snapshot("9002").authors == ()


def test_account_snapshots_are_separate_and_unsynced_is_not_empty_success(store):
    first = seed(store)
    second = store.sync(Reader(private=(author("72"),), account_id="9002"))
    assert store.snapshot("9001") == first
    assert [r.user_id for r in second.following] == ["72"]
    assert second.account_id == "9002"
    unknown = store.snapshot("9003")
    assert unknown.last_synced_at is None and unknown.authors == ()
    cleared = store.sync(Reader(account_id="9002"), expected_revision=second.revision)
    assert cleared.last_synced_at is not None and cleared.following == ()
    assert [r.user_id for r in cleared.authors] == ["72"]
    assert not cleared.authors[0].is_following
    assert store.snapshot("9001") == first


def test_slow_concurrent_sync_cannot_overwrite_newer_commit(store):
    seed(store)
    entered, release = Event(), Event()
    def block(rest):
        if rest == "hide":
            entered.set()
            assert release.wait(5)
    slow = Reader(public=(author("42"),), hook=block)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(store.sync, slow)
        try:
            assert entered.wait(5)
            winner = store.sync(Reader(public=(author("43"),)))
        finally:
            release.set()
        with pytest.raises(FollowingSyncConflict):
            future.result(timeout=5)
    assert store.snapshot("9001") == winner
    assert [r.user_id for r in winner.following] == ["43"]


def test_local_follow_during_sync_fences_the_stale_remote_snapshot(store):
    seed(store)
    entered, release = Event(), Event()
    def block(rest):
        if rest == "hide":
            entered.set()
            assert release.wait(5)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(store.sync, Reader(public=(author("42"),), hook=block))
        try:
            assert entered.wait(5)
            local = store.record_followed("9001", "43", "Local acknowledgement")
        finally:
            release.set()
        with pytest.raises(FollowingSyncConflict):
            future.result(timeout=5)
    assert store.snapshot("9001") == local
    assert {r.user_id for r in local.following} == {"41", "43"}


def test_close_while_fetching_prevents_late_publish_and_reopens_old_snapshot(tmp_path):
    root = tmp_path / "owned-following"
    store = FollowingSyncStore(root)
    before = seed(store)
    entered, release = Event(), Event()
    def block(rest):
        if rest == "hide":
            entered.set()
            assert release.wait(5)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(store.sync, Reader(public=(author("42"),), hook=block))
            try:
                assert entered.wait(5)
                store.close()
            finally:
                release.set()
            with pytest.raises(FollowingSyncError):
                future.result(timeout=5)
    finally:
        store.close()
    with pytest.raises(FollowingSyncError):
        store.snapshot("9001")
    reopened = FollowingSyncStore(root)
    try:
        assert reopened.snapshot("9001") == before
    finally:
        reopened.close()


def test_follow_and_unfollow_keep_management_columns_and_enforce_cas(store):
    before = seed(store)
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE following_authors SET is_favorite=1,is_zipped=1,last_downloaded=? "
            "WHERE account_id=? AND user_id=?", ("2026-01-02T03:04:05Z", "9001", "41"))
    stopped = store.record_unfollowed("9001", "41", expected_revision=before.revision)
    assert stopped.following == ()
    retained = stopped.authors[0]
    assert retained.is_favorite and retained.is_zipped
    assert retained.last_downloaded == "2026-01-02T03:04:05Z"
    with pytest.raises(FollowingSyncConflict):
        store.record_followed("9001", "41", expected_revision=before.revision)
    restored = store.record_followed("9001", "41", expected_revision=stopped.revision)
    assert restored.following[0].is_favorite and restored.following[0].is_zipped
    assert restored.following[0].last_downloaded == retained.last_downloaded
    assert restored.following[0].account == retained.account
    assert restored.last_synced_at == before.last_synced_at
    assert store.record_unfollowed("9001", "9999") == restored
    synced = store.sync(Reader(public=(author("41", name="Updated"),)))
    assert synced.following[0].is_favorite and synced.following[0].is_zipped
    assert synced.following[0].last_downloaded == retained.last_downloaded


def test_sql_failure_mid_upsert_rolls_back_authors_revision_and_sync_time(store):
    before = seed(store)
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "CREATE TRIGGER injected_follow_failure BEFORE INSERT ON following_authors "
            "WHEN NEW.user_id='43' BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(FollowingSyncError):
        store.sync(Reader(public=(author("42"), author("43"))))
    assert store.snapshot("9001") == before


@pytest.mark.parametrize("url", [
    "file:///private.png", "http://i.pximg.net/avatar.png",
    "https://example.invalid/avatar.png", "https://u:p@i.pximg.net/avatar.png",
])
def test_untrusted_profile_url_never_replaces_persisted_snapshot(store, url):
    before = seed(store)
    with pytest.raises(FollowingSyncError):
        store.sync(Reader(public=(author(profile_img=url),)))
    assert store.snapshot("9001") == before


def test_unmarked_existing_root_is_rejected_before_sqlite_connect(tmp_path, monkeypatch):
    root = tmp_path / "original-like"
    root.mkdir()
    legacy = root / "original.sqlite3"
    legacy.write_bytes(b"owned test sentinel")
    before = legacy.read_bytes()
    def forbidden(*args, **kwargs):
        pytest.fail("an unmarked root must be rejected before any SQLite connection")
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    with pytest.raises(FollowingSyncError):
        FollowingSyncStore(root)
    assert legacy.read_bytes() == before
    assert [p.name for p in root.iterdir()] == ["original.sqlite3"]


def test_windows_reparse_component_is_rejected_using_lstat(tmp_path, monkeypatch):
    root = tmp_path / "junction-like"
    root.mkdir()
    original = Path.lstat
    def fake(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        if logical_path(path) == logical_path(root):
            return SimpleNamespace(st_mode=value.st_mode, st_nlink=value.st_nlink,
                                   st_file_attributes=0x400)
        return value
    monkeypatch.setattr(Path, "lstat", fake)
    with pytest.raises(FollowingSyncError):
        FollowingSyncStore(root)
    assert list(root.iterdir()) == []


def detailed_reader(profile_img="https://i.pximg.net/user-profile/offline/avatar.png"):
    reader = object.__new__(LivePixivReadClient)
    reader._closed = False
    reader._verified_identity = "9001"
    reader._identity_lock = RLock()
    seen = []
    def response(kind, url, *, deadline):
        _validate_destination(url, kind)
        seen.append(url)
        if int(parse_qs(urlsplit(url).query)["offset"][0]):
            return {"users": []}
        return {"users": [dict(userId="41", userName="Synthetic author",
                              userAccount="synthetic_author", profileImageUrl=profile_img)]}
    reader._json = response
    return reader, seen


def test_detailed_reader_keeps_old_dto_compatible_and_adds_safe_profile_fields():
    reader, seen = detailed_reader()
    assert reader.get_following_users("9001", "show") == (
        {"user_id": "41", "name": "Synthetic author"},)
    detailed = reader.get_following_users_detailed("9001", "hide")
    assert detailed == (author(),)
    assert len(seen) == 4
    assert all("/ajax/user/9001/following?" in url for url in seen)


def test_detailed_reader_rejects_profile_url_outside_existing_cdn_policy():
    reader, _ = detailed_reader("https://example.invalid/avatar.png")
    with pytest.raises(InvalidPayload):
        reader.get_following_users_detailed("9001")


def test_sync_deadline_reaches_http_transport_and_scope_does_not_leak():
    from http.cookiejar import CookieJar
    import time
    from pixivvault_nicegui.pixiv_read_client import read_deadline_scope
    reader = object.__new__(LivePixivReadClient)
    reader._closed = False
    reader._cookies = CookieJar()
    seen = []
    def transport(request, timeout, limit, deadline):
        assert request.get_method() == "GET"
        seen.append((timeout, deadline))
        return b"{}"
    reader._transport = transport
    url = "https://www.pixiv.net/ajax/user/9001/following?offset=0&limit=24&rest=show&lang=ja"
    short = time.monotonic() + 5
    long = time.monotonic() + 120
    with read_deadline_scope(short):
        with read_deadline_scope(long):
            assert reader._get("following", url, deadline=long) == b"{}"
    assert seen[0][1] == short
    assert 0 < seen[0][0] <= 5
    assert reader._get("following", url, deadline=long) == b"{}"
    assert seen[1][1] > short
    assert 0 < seen[1][0] <= 10.0


def test_expired_read_scope_performs_no_transport_call():
    import time
    from pixivvault_nicegui.pixiv_read_client import ReadDeadlineExceeded, read_deadline_scope
    called = []
    with pytest.raises(ReadDeadlineExceeded):
        with read_deadline_scope(time.monotonic() - 1):
            called.append("must not enter expired operation")
    assert called == []
