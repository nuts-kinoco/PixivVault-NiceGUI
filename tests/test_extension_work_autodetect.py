"""N09 legacy work commands; generated Cookie/GET bytes and owned stores only.

The static child is a routing fixture, not a download acceptance claim. Novel
children use the existing real fixture exporter and verified saved receipts.
"""
from hashlib import sha256
from html import escape
from io import BytesIO
import json
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import urlsplit

import pytest

from pixivvault_nicegui.extension_application import ExtensionApplication
from pixivvault_nicegui.extension_bridge import ExtensionDownload, parse_download
from pixivvault_nicegui.job_service import JobService
from pixivvault_nicegui.managed_novel_jobs import ManagedNovelJobs
from pixivvault_nicegui.media_jobs import MediaJobs
from pixivvault_nicegui.novel_save_presentation import build_novel_ui_fixture
from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient


def packet(body, *, error=False):
    return json.dumps(dict(error=error, body=body)).encode()


class SyntheticGet:
    def __init__(self, metadata="work", *, identity_status=None):
        self.metadata = metadata
        self.identity_status = identity_status
        self.calls = []

    def __call__(self, request, timeout, limit, deadline):
        assert request.get_method() == "GET"
        assert urlsplit(request.full_url).netloc == "www.pixiv.net"
        path = urlsplit(request.full_url).path
        self.calls.append(path)
        if path == "/":
            if self.identity_status is not None:
                raise HTTPError(request.full_url, self.identity_status, "synthetic", {}, BytesIO())
            data = escape(json.dumps({"userData": {"id": "55"}}))
            return f'<meta id="meta-global-data" content="{data}">'.encode()
        if path == "/ajax/user/extra":
            return packet({"userId": "55"})
        assert path == "/ajax/illust/123", "Unexpected synthetic GET route"
        if type(self.metadata) is int:
            raise HTTPError(request.full_url, self.metadata, "synthetic", {}, BytesIO())
        if self.metadata == "timeout":
            raise TimeoutError("synthetic")
        if self.metadata == "api_error":
            return packet({}, error=True)
        if self.metadata == "malformed":
            return b"not-json"
        return packet(dict(id="123", userId="91", title="Synthetic work",
            userName="Synthetic author", pageCount=1, illustType=0, tags=[]))


class Harness:
    def __init__(self, root, *, metadata="work", identity_status=None,
                 owner_type=ExtensionApplication, fail_static_accept=False):
        self.transport = SyntheticGet(metadata, identity_status=identity_status)
        cookie_dir = root / "generated-source"
        cookie_dir.mkdir(exist_ok=True)
        cookie = cookie_dir / "cookies.txt"
        if not cookie.exists():
            cookie.write_text("# Netscape HTTP Cookie File\n"
                ".pixiv.net\tTRUE\t/\tTRUE\t4102444800\tPHPSESSID\t55_fixtureonly\n",
                encoding="utf-8")
        self.reader = LivePixivReadClient(cookie, transport=self.transport)
        self.static = JobService(lambda _job, context: (context.checkpoint() or "fixture receipt"),
                                 root / "static.sqlite3")
        source = build_novel_ui_fixture()
        fetch = source.fetch
        self.novel_fetches = []
        def recorded_fetch(work_id):
            self.novel_fetches.append(work_id)
            return fetch(work_id)
        source.fetch = recorded_fetch
        self.novels = ManagedNovelJobs(root / "novels", source)
        self.static_accepts = []
        self.fail_static_accept = fail_static_accept
        self.services = SimpleNamespace(closing=False, live_client=self.reader,
            novel_jobs=self.novels, novel_fixture_source=source, preferences=None,
            managed_downloads=SimpleNamespace(verified_saved_receipt=lambda _wid:
                ("fixture receipt", {}, "fixture sha", "fixture date")),
            jobs=MediaJobs(self.static, self.novels.jobs))
        async def save(work_id, operation):
            self.static_accepts.append((work_id, operation))
            if self.fail_static_accept:
                raise RuntimeError("synthetic pre-accept failure")
            return self.static.enqueue("pixiv-illust", work_id, operation_id=operation)
        self.services.save_explicit_work = save
        self.services.enqueue_novel_export = self.novels.enqueue
        self.owner = owner_type(self.services, root / "commands")
        self.services.jobs = MediaJobs(self.static, self.novels.jobs, self.owner)
        assert self.transport.calls == []

    def close(self):
        self.owner.close()
        self.static.close()
        self.novels.close()
        self.reader.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def wait(owner, job_id, state):
    with owner.jobs._condition:
        assert owner.jobs._condition.wait_for(lambda: any(
            job.job_id == job_id and job.state in ("done", "error", "stopped")
            for job in owner.snapshot()), 20), owner.snapshot()
    result = next(job for job in owner.snapshot() if job.job_id == job_id)
    assert result.state == state
    return result


def auto_command():
    return parse_download({"type": "work", "work_id": "123"})


def test_omitted_media_is_distinct_from_both_explicit_flags_and_null():
    assert auto_command().is_novel is None
    assert parse_download({"type": "work", "work_id": "123", "is_novel": False}).is_novel is False
    assert parse_download({"type": "work", "work_id": "123", "is_novel": True}).is_novel is True
    assert ExtensionDownload("work", "123").is_novel is False  # Existing direct callers.
    with pytest.raises(ValueError):
        parse_download({"type": "work", "work_id": "123", "is_novel": None})


@pytest.mark.parametrize("flag", [False, True])
def test_explicit_media_keeps_canonical_intent_and_does_not_probe_other_namespace(tmp_path, flag):
    with Harness(tmp_path, metadata=404) as env:
        command = parse_download({"type": "work", "work_id": "123", "is_novel": flag})
        job_id = env.owner.enqueue(command)
        parent = wait(env.owner, job_id, "done")
        literal = "true" if flag else "false"
        assert parent.target_id == '{"is_novel":' + literal + ',"type":"work","work_id":"123"}'
        assert env.transport.calls == []
        assert bool(env.novels.current("123", "EPUB")) is flag
        assert bool(env.static_accepts) is (not flag)


@pytest.mark.parametrize("metadata,expected_kind", [("work", "pixiv-illust"), (404, "novel-export")])
def test_legacy_auto_work_uses_only_verified_artwork_or_exact_not_found(tmp_path, metadata, expected_kind):
    with Harness(tmp_path, metadata=metadata) as env:
        job_id = env.owner.enqueue(auto_command())
        parent = wait(env.owner, job_id, "done")
        assert parent.target_id == '{"type":"work","work_id":"123"}'
        assert env.transport.calls == ["/", "/ajax/user/extra", "/ajax/illust/123"]
        children = env.static.snapshot() + env.novels.jobs.snapshot()
        assert len(children) == 1 and children[0].kind == expected_kind
        key = "work:123" if expected_kind == "pixiv-illust" else "novel:123"
        expected_operation = sha256((job_id + ":" + key).encode()).hexdigest()[:32]
        assert env.services.jobs.operation_ids(children[0].job_id) == (expected_operation,)
        if expected_kind == "novel-export":
            saved = env.novels.current("123", "EPUB")
            assert saved is not None and children[0].result == saved.relative_receipt
            assert not env.static_accepts
        else:
            assert env.novel_fetches == []


@pytest.mark.parametrize("metadata", [401, 403, 429, 500, "timeout", "api_error", "malformed"])
def test_authentication_temporary_and_ambiguous_failures_never_fall_back_to_novel(tmp_path, metadata):
    with Harness(tmp_path, metadata=metadata) as env:
        job_id = env.owner.enqueue(auto_command())
        wait(env.owner, job_id, "error")
        assert "/ajax/illust/123" in env.transport.calls
        assert env.static_accepts == [] and env.novel_fetches == []
        assert env.static.snapshot() == () and env.novels.jobs.snapshot() == ()


def test_identity_not_found_is_not_artwork_not_found(tmp_path):
    with Harness(tmp_path, identity_status=404) as env:
        job_id = env.owner.enqueue(auto_command())
        wait(env.owner, job_id, "error")
        assert env.transport.calls == ["/"]
        assert env.static_accepts == [] and env.novel_fetches == []
        assert env.static.snapshot() == () and env.novels.jobs.snapshot() == ()


def test_accepted_explicit_false_reopens_with_same_meaning_and_operation(tmp_path):
    with Harness(tmp_path, fail_static_accept=True) as env:
        job_id = env.owner.enqueue(ExtensionDownload("work", "123"))
        before = wait(env.owner, job_id, "error")
        operations = env.owner.operation_ids(job_id)
        assert env.transport.calls == []
    with Harness(tmp_path, metadata=404) as env:
        assert env.owner.operation_ids(job_id) == operations
        assert env.owner.control(job_id, "retry", expected_control_revision=before.control_revision)
        parent = wait(env.owner, job_id, "done")
        assert parent.target_id == before.target_id and parent.attempt == 2
        assert env.transport.calls == [] and env.novel_fetches == []
        assert len(env.static.snapshot()) == 1


def test_auto_choice_survives_failure_before_child_acceptance_and_restart(tmp_path):
    with Harness(tmp_path, fail_static_accept=True) as env:
        job_id = env.owner.enqueue(auto_command())
        before = wait(env.owner, job_id, "error")
        assert env.transport.calls == ["/", "/ajax/user/extra", "/ajax/illust/123"]
        assert env.static.snapshot() == ()
        operations = env.owner.operation_ids(job_id)
    with Harness(tmp_path, metadata=404) as env:
        assert env.owner.operation_ids(job_id) == operations
        assert env.owner.control(job_id, "retry", expected_control_revision=before.control_revision)
        wait(env.owner, job_id, "done")
        assert env.transport.calls == [] and env.novel_fetches == []
        child, = env.static.snapshot()
        expected = sha256((job_id + ":work:123").encode()).hexdigest()[:32]
        assert env.static.operation_ids(child.job_id) == (expected,)


def test_auto_novel_choice_survives_child_acceptance_before_binding_crash(tmp_path):
    class CrashBeforeBinding(ExtensionApplication):
        def _binding(self, parent, key, child=None):
            if child is not None:
                raise RuntimeError("synthetic crash after child acceptance")
            return super()._binding(parent, key, child)

    with Harness(tmp_path, metadata=404, owner_type=CrashBeforeBinding) as env:
        job_id = env.owner.enqueue(auto_command())
        before = wait(env.owner, job_id, "error")
        child, = env.novels.jobs.snapshot()
        with env.novels.jobs._condition:
            assert env.novels.jobs._condition.wait_for(lambda: any(
                row.job_id == child.job_id and row.state == "done"
                for row in env.novels.jobs.snapshot()), 20)
        saved = env.novels.current("123", "EPUB")
        receipt = (env.novels.store.root / saved.relative_receipt).read_bytes()
        expected = sha256((job_id + ":novel:123").encode()).hexdigest()[:32]
        assert env.novels.jobs.operation_ids(child.job_id) == (expected,)
    with Harness(tmp_path, metadata="work") as env:
        assert env.transport.calls == []
        assert env.owner.control(job_id, "retry", expected_control_revision=before.control_revision)
        wait(env.owner, job_id, "done")
        assert env.transport.calls == [] and env.static_accepts == []
        assert env.novels.jobs.snapshot()[0].job_id == child.job_id
        assert env.novels.jobs.snapshot()[0].attempt == 1
        assert len(env.novels.jobs.snapshot()) == 1
        assert env.novels.current("123", "EPUB") == saved
        assert (env.novels.store.root / saved.relative_receipt).read_bytes() == receipt
