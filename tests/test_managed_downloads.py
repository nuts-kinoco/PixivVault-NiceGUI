"""Synthetic, offline original-artwork persistence tests."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
from threading import Event, Thread
import time

import pytest
from PIL import Image

from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.job_service import JobService
from pixivvault_nicegui.job_service import JobCancelled
from pixivvault_nicegui.managed_downloads import ManagedDownloadError, ManagedPixivDownloads


def png() -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (9, 7)).save(stream, format="PNG")
    return stream.getvalue()


def record() -> RemoteBookmark:
    return RemoteBookmark("123", "illustration", "show", "synthetic title", "456", "tester",
                          ("test",), "", "2026-10-23T00:00:00Z", 2, 0, 0)


class Client:
    def __init__(self):
        self.count = 0
        self.urls = 0
        self.identity = 0
        self.payload = png()
        self.after_get = None

    def verify_identity(self):
        self.identity += 1
        return "456"

    def get_artwork_original_pages(self, work_id):
        self.urls += 1
        return ("https://i.pximg.net/a", "https://i.pximg.net/b")

    def get_image_bytes(self, url):
        self.count += 1
        if self.after_get:
            self.after_get(self.count)
        return self.payload


class Context:
    def checkpoint(self):
        pass

    def progress(self, done, total):
        pass


class Job:
    kind = "pixiv-illust"
    target_id = "123"


def create(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    client = Client()
    runner = ManagedPixivDownloads(tmp_path / "managed", client, resolve_record=lambda wid: record())
    return runner, client


def test_original_two_pages_and_reuse(tmp_path):
    runner, client = create(tmp_path)
    relative = runner.run(Job(), Context())
    assert client.count == 2
    entries = runner.saved_receipts()
    assert len(entries) == 1 and entries[0][0] == relative
    assert entries[0][1]["source"] == "pixiv-original"
    assert entries[0][1]["page_count"] == 2
    assert runner.read_saved_page(relative, 0, entries[0][2]) == png()
    assert runner.read_saved_page(relative, 1, entries[0][2]) == png()
    assert runner.run(Job(), Context()) == relative
    assert client.count == 2
    runner.close()


def test_work_lock_is_shared_and_serializes_same_work(tmp_path):
    runner, _ = create(tmp_path)
    entered = Event()
    release = Event()
    finished = Event()
    first_lock = runner.work_lock('123')
    assert first_lock is runner.work_lock('123')

    def publication():
        with first_lock:
            entered.set()
            assert release.wait(3)
        finished.set()

    thread = Thread(target=publication)
    thread.start()
    try:
        assert entered.wait(2)
        # Acceptance must use the identical lock and cannot pass the active
        # publication window for this same work ID.
        assert not runner.work_lock('123').acquire(blocking=False)
        assert runner.work_lock('124').acquire(blocking=False)
        runner.work_lock('124').release()
    finally:
        release.set()
        thread.join(3)
        runner.close()
    assert finished.is_set() and not thread.is_alive()


def test_corruption_cannot_be_read_or_reused(tmp_path):
    runner, client = create(tmp_path)
    relative = runner.run(Job(), Context())
    receipt = runner.saved_receipts()[0]
    file = runner.root / relative.rsplit("/", 1)[0] / "page-1.png"
    file.write_bytes(b"corrupt")
    with pytest.raises(ManagedDownloadError):
        runner.saved_receipts()
    with pytest.raises(ManagedDownloadError):
        runner.read_saved_page(relative, 0, receipt[2])
    with pytest.raises(ManagedDownloadError):
        runner.run(Job(), Context())
    assert client.count == 2
    runner.close()


def test_single_pinned_page_does_not_decode_the_whole_artwork_again(tmp_path, monkeypatch):
    import pixivvault_nicegui.managed_downloads as module
    runner, _ = create(tmp_path)
    try:
        relative = runner.run(Job(), Context())
        saved = runner.saved_receipts()[0]
        original = module._image
        decoded = []
        def observed(payload):
            decoded.append(1)
            return original(payload)
        monkeypatch.setattr(module, "_image", observed)
        assert runner.read_saved_page(relative, 0, saved[2]) == png()
        assert decoded == [], "one thumbnail request must not decode all saved pages"
        # Complete-save / duplicate validation still checks every stored page.
        runner.saved_receipts()
        assert len(decoded) == 2
    finally:
        runner.close()


def test_bad_job_and_record_do_not_access_client(tmp_path):
    runner, client = create(tmp_path)
    with pytest.raises(ManagedDownloadError):
        runner.run(replace(record(), remote_id="999"), Context())
    runner.resolve_record = lambda wid: replace(record(), illustration_type=2)
    with pytest.raises(ManagedDownloadError):
        runner.run(Job(), Context())
    runner.resolve_record = lambda wid: replace(record(), illustration_type=True)
    with pytest.raises(ManagedDownloadError):
        runner.run(Job(), Context())
    assert client.count == client.identity == 0
    runner.close()


def test_marker_bounded_unique_and_bad_json(tmp_path):
    import pixivvault_nicegui.managed_downloads as module
    runner, _ = create(tmp_path)
    root = runner.root
    runner.close()
    marker = root / module.MARKER
    for data in (b' ' * 4097, b'{broken', b'{"version":1,"version":1}',
                 b'[' * 1500 + b'0' + b']' * 1500):
        marker.write_bytes(data)
        with pytest.raises(ManagedDownloadError, match='managed marker rejected'):
            ManagedPixivDownloads(root, Client(), resolve_record=lambda _: record())


def test_receipt_bool_version_and_deep_json_rejected(tmp_path):
    runner, _ = create(tmp_path)
    relative = runner.run(Job(), Context())
    path = runner.root / relative
    source = json.loads(path.read_text('utf-8'))
    for payload in ({**source, 'version': True}, '[' * 1500 + '0' + ']' * 1500):
        path.write_text(json.dumps(payload) if isinstance(payload, dict) else payload, encoding='utf-8')
        with pytest.raises(ManagedDownloadError):
            runner.saved_receipts()
    runner.close()


def test_reparse_flag_and_hardlink_rejected_without_symlink_privileges(tmp_path, monkeypatch):
    import pixivvault_nicegui.managed_downloads as module
    from pixivvault_nicegui.fs_paths import logical_path
    runner, _ = create(tmp_path)
    marker = runner.root / module.MARKER
    original_lstat = Path.lstat

    class StatProxy:
        def __init__(self, actual, **overrides):
            self._actual = actual
            self._overrides = overrides
        def __getattr__(self, name):
            if name in self._overrides:
                return self._overrides[name]
            return getattr(self._actual, name)

    # Linux stat_result has no st_file_attributes: a proxy must return the
    # injected Windows flag without evaluating an absent native attribute.
    from types import SimpleNamespace
    assert StatProxy(SimpleNamespace(), st_file_attributes=0x400).st_file_attributes == 0x400

    def flagged(path):
        info = original_lstat(path)
        if logical_path(path) == marker:
            return StatProxy(info, st_file_attributes=0x400)
        return info

    monkeypatch.setattr(Path, 'lstat', flagged)
    with pytest.raises(ManagedDownloadError, match='reparse'):
        module._read_bounded(marker, 4096)
    monkeypatch.setattr(Path, 'lstat', original_lstat)

    def linked(path):
        info = original_lstat(path)
        return StatProxy(info, st_nlink=2) if logical_path(path) == marker else info

    monkeypatch.setattr(Path, 'lstat', linked)
    with pytest.raises(ManagedDownloadError, match='managed file rejected'):
        module._read_bounded(marker, 4096)
    monkeypatch.setattr(Path, 'lstat', original_lstat)
    runner.close()


def test_foreign_root_and_links_rejected(tmp_path):
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "user-original.jpg").write_bytes(b"original")
    with pytest.raises(ManagedDownloadError):
        ManagedPixivDownloads(foreign, Client(), resolve_record=lambda _: record())
    assert (foreign / "user-original.jpg").read_bytes() == b"original"
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink requires privileges")
    with pytest.raises(ManagedDownloadError):
        ManagedPixivDownloads(link, Client(), resolve_record=lambda _: record())


def test_checkpoint_interrupts_before_receipt(tmp_path):
    runner, client = create(tmp_path)
    class Stop(Exception):
        pass
    class Interrupt(Context):
        calls = 0
        def checkpoint(self):
            self.calls += 1
            if self.calls >= 6:
                raise Stop()
    with pytest.raises(ManagedDownloadError):
        runner.run(Job(), Interrupt())
    assert runner.saved_receipts() == ()
    runner.close()


def test_job_service_synthetic_execution(tmp_path):
    runner, client = create(tmp_path)
    service = JobService(runner.run, storage=runner.storage_path)
    first = service.enqueue("pixiv-illust", "123", "synthetic")
    second = service.enqueue("pixiv-illust", "123", "synthetic")
    assert first.job_id == second.job_id
    limit = time.monotonic() + 5
    while time.monotonic() < limit:
        jobs = service.snapshot()
        if jobs and jobs[0].state in ("done", "error"):
            break
        time.sleep(.02)
    assert service.snapshot()[0].state == "done"
    assert len(runner.saved_receipts()) == 1
    assert client.count == 2
    service.close()
    runner.close()


def test_fsync_failure_does_not_publish(tmp_path, monkeypatch):
    import pixivvault_nicegui.managed_downloads as module
    runner, client = create(tmp_path)
    original = module.os.fsync
    calls = 0

    def fail_after_first(descriptor):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("synthetic fsync failure")
        return original(descriptor)

    monkeypatch.setattr(module.os, "fsync", fail_after_first)
    with pytest.raises(ManagedDownloadError, match="managed download failed"):
        runner.run(Job(), Context())
    assert runner.saved_receipts() == ()
    monkeypatch.setattr(module.os, "fsync", original)
    assert len(runner.saved_receipts()) == 0
    assert runner.run(Job(), Context())
    runner.close()


def test_stopping_job_after_first_get(tmp_path):
    runner, client = create(tmp_path)

    class Interrupted(Context):
        cancel = False
        def checkpoint(self):
            if self.cancel:
                raise JobCancelled("synthetic stop")

    ctx = Interrupted()
    client.after_get = lambda count: setattr(ctx, "cancel", True)
    with pytest.raises(JobCancelled):
        runner.run(Job(), ctx)
    assert runner.saved_receipts() == ()
    assert client.count == 1
    runner.close()


def test_forged_receipt_fields_and_snapshot_change(tmp_path):
    runner, client = create(tmp_path)
    relative = runner.run(Job(), Context())
    _, receipt, digest = runner.saved_receipts()[0]
    receipt_path = runner.root / relative
    original = receipt_path.read_bytes()
    receipt["source"] = "thumbnail"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ManagedDownloadError):
        runner.saved_receipts()
    with pytest.raises(ManagedDownloadError):
        runner.read_saved_page(relative, 0, digest)
    receipt_path.write_bytes(original)
    assert runner.read_saved_page(relative, 0, digest) == png()
    with pytest.raises(ManagedDownloadError):
        runner.read_saved_page(relative, 2, digest)
    runner.close()


def test_missing_receipt_and_duplicate_completed_work_are_errors(tmp_path):
    runner, _ = create(tmp_path)
    relative = runner.run(Job(), Context())
    original = runner.root / relative
    second = runner.outputs / ('work-123-' + 'a' * 32)
    second.mkdir()
    with pytest.raises(ManagedDownloadError):
        runner.saved_receipts()
    second.rmdir()
    import shutil
    shutil.copytree(original.parent, second)
    with pytest.raises(ManagedDownloadError, match='duplicate completed'):
        runner.saved_receipts()
    shutil.rmtree(second)
    original.unlink()
    with pytest.raises(ManagedDownloadError):
        runner.saved_receipts()
    runner.close()


def test_original_formats_limits_identity_and_unchanged_source(tmp_path, monkeypatch):
    import pixivvault_nicegui.managed_downloads as module
    for fmt in ('PNG', 'JPEG', 'WEBP'):
        runner, client = create(tmp_path / fmt)
        stream = io.BytesIO()
        Image.new('RGB', (12, 8)).save(stream, format=fmt)
        source = stream.getvalue()
        digest = hashlib.sha256(source).hexdigest()
        client.payload = source
        relative = runner.run(Job(), Context())
        saved = runner.saved_receipts()[0]
        assert saved[1]['page_count'] == 2
        assert client.identity == client.urls == 1 and client.count == 2
        assert all(item['sha256'] == digest for item in saved[1]['files'])
        assert runner.read_saved_page(relative, 0, saved[2]) == source
        assert hashlib.sha256(source).hexdigest() == digest
        runner.close()
    runner, client = create(tmp_path / 'limit')
    monkeypatch.setattr(module, 'MAX_PAGE_BYTES', len(png()) - 1)
    with pytest.raises(ManagedDownloadError):
        runner.run(Job(), Context())
    assert runner.saved_receipts() == ()
    runner.close()
