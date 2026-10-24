"""M4 settings-only UI/service acceptance; never opens real Pixiv or legacy DB."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.metadata_ledger import MetadataLedgerError
from pixivvault_nicegui.metadata_ledger import MetadataProgress
from pixivvault_nicegui.metadata_policy import MetadataCandidate
from pixivvault_nicegui.metadata_policy import FailureEvidence, classify_failure
from pixivvault_nicegui.metadata_transport import MetadataFetchResult
from pixivvault_nicegui.metadata_runner import MetadataRunSummary
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.metadata_view import MetadataView
from pixivvault_nicegui.ui.shell import AVAILABLE_TABS, NAVIGATION


class OfflineSource:
    def __init__(self):
        self.calls = []
        self.closed = False

    def __getattr__(self, name):
        if name.startswith('get_') or name in ('verify_identity', 'require_identity'):
            def forbidden(*args, **kwargs):
                self.calls.append(name)
                raise AssertionError('metadata settings must not make Pixiv GETs')
            return forbidden
        raise AttributeError(name)

    def close(self):
        self.closed = True


def managed_app(tmp_path):
    source = OfflineSource()
    services = ApplicationServices(
        live_client=source, pixiv_download_root=tmp_path / 'managed')
    return services, source


def test_metadata_tab_contract_is_explicit_and_not_a_general_synthetic_tab():
    assert 'metadata' in AVAILABLE_TABS
    assert ('metadata', 'メタデータ補完') in NAVIGATION


def test_metadata_settings_open_is_explicit_off_and_never_fetches(tmp_path):
    services, source = managed_app(tmp_path)
    sidecar = tmp_path / 'managed' / '.pixivvault-managed-metadata.sqlite3'
    try:
        assert not sidecar.exists()
        assert services.managed_metadata is None
        assert services.managed_metadata_settings() is None
        assert services.managed_metadata_progress() is None
        assert not sidecar.exists()  # state inspection must never provision
        ledger = services.enable_managed_metadata()
        assert ledger is services.enable_managed_metadata()
        assert sidecar.is_file()
        assert ledger.settings().enabled is False
        assert ledger.settings().revision == 1
        assert services.managed_metadata_progress() == MetadataProgress()
        assert source.calls == []
        saved = services.configure_managed_metadata(enabled=True, expected_revision=1)
        assert saved.enabled and saved.revision == 2
        assert source.calls == []  # ON only persists a flag; no network worker
        with pytest.raises(MetadataLedgerError, match='changed'):
            services.configure_managed_metadata(enabled=False, expected_revision=1)
        assert services.managed_metadata_settings().enabled is True
    finally:
        services.close()
    assert source.closed
    assert services.managed_metadata is None


def test_metadata_settings_persist_but_do_not_auto_start_after_restart(tmp_path):
    app, source = managed_app(tmp_path)
    app.enable_managed_metadata()
    app.configure_managed_metadata(enabled=True, expected_revision=1)
    app.close()

    second, source2 = managed_app(tmp_path)
    try:
        assert second.managed_metadata is None
        assert second.managed_metadata_settings() is None
        assert source2.calls == []
        restored = second.enable_managed_metadata()
        assert restored.settings().enabled is True
        assert restored.settings().revision == 2
        assert source2.calls == []
    finally:
        second.close()


def test_metadata_pointer_is_immutable_receipt_sha_not_job_fingerprint(tmp_path, monkeypatch):
    app, source = managed_app(tmp_path)
    try:
        receipt_sha, job_fingerprint = 'a' * 64, 'b' * 64
        monkeypatch.setattr(
            app.managed_downloads, 'verified_saved_receipt',
            lambda work_id: ('outputs/synthetic/receipt.json', {'work_id': work_id},
                             receipt_sha, None, job_fingerprint))
        ledger = app.enable_managed_metadata()
        work = MetadataCandidate('123', '91', 'illust', False, None, 0)
        assert ledger.offer(work, receipt_sha)
        assert app.managed_metadata_progress() == MetadataProgress(pending=1)
        with pytest.raises(MetadataLedgerError, match='not active'):
            ledger.offer(work, job_fingerprint)
        ledger.configure(enabled=True, expected_revision=1)
        lease = ledger.claim('123', receipt_sha)
        assert app.managed_metadata_progress() == MetadataProgress(in_flight=1)
        ledger.complete_failure('123', receipt_sha, lease,
                                classify_failure(FailureEvidence('http', 429)))
        counts = app.managed_metadata_progress()
        assert counts == MetadataProgress(blocked=1)
        assert counts.total == 1
        assert source.calls == []
    finally:
        app.close()


def test_one_work_refuses_stale_ui_revision_before_auth_get(tmp_path, monkeypatch):
    app, source = managed_app(tmp_path)
    try:
        _set_synthetic_active_receipt(monkeypatch, app)
        app.enable_managed_metadata()
        app.configure_managed_metadata(enabled=True, expected_revision=1)
        t = MetadataFakeTransport()
        summary = app.run_managed_metadata_one('123', transport=t, expected_revision=1)
        assert summary.stopped == 'settings_changed'
        assert t.calls == []
        assert app.managed_metadata_entry('123') is None
        assert source.calls == []
    finally:
        app.close()


@pytest.mark.asyncio
async def test_view_one_work_requires_explicit_click_and_never_auto_get(tmp_path, monkeypatch):
    app, source = managed_app(tmp_path)
    client = Client(ui.page('/_metadata_explicit_one_work'))
    called = []
    try:
        app.enable_managed_metadata()
        app.configure_managed_metadata(enabled=True, expected_revision=1)

        def fake_run(work_id, *, expected_revision=None):
            called.append((work_id, expected_revision))
            return MetadataRunSummary(target=1, filled=1)

        monkeypatch.setattr(app, 'run_managed_metadata_one', fake_run)
        with client:
            view = MetadataView(app)
        assert not called
        await view.open()
        await view.refresh()
        assert not called and not source.calls
        view.work_id.set_value('123')
        await view.run_one()
        assert called == [('123', 2)]
        assert '完了 1' in view.run_status.text
        assert not source.calls
    finally:
        client.delete()
        app.close()


def test_metadata_settings_refuse_synthetic_mode_and_closed_service(tmp_path):
    synthetic = ApplicationServices()
    try:
        with pytest.raises(RuntimeError, match='managed Pixiv'):
            synthetic.enable_managed_metadata()
    finally:
        synthetic.close()
    app, _ = managed_app(tmp_path)
    app.enable_managed_metadata()
    app.close()
    with pytest.raises(RuntimeError, match='closing'):
        app.enable_managed_metadata()
    with pytest.raises(RuntimeError, match='closing'):
        app.managed_metadata_settings()
    with pytest.raises(RuntimeError, match='not open'):
        app.configure_managed_metadata(enabled=True, expected_revision=1)


def _set_synthetic_active_receipt(monkeypatch, app, digest='a' * 64):
    monkeypatch.setattr(app.managed_downloads, 'verified_saved_receipt',
                        lambda wid: ('outputs/mock/receipt.json', {'work_id': wid, 'author_id': '91'},
                                     digest, None, 'b' * 64))


class MetadataFakeTransport:
    def __init__(self, *, started=None, release=None):
        self.calls = []
        self.started = started
        self.release = release

    def verify_identity(self):
        self.calls.append('identity')
        return '12'

    def fetch_detail(self, work_id, expected_author_id):
        self.calls.append('detail')
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            assert self.release.wait(8), 'synthetic GET gate must be released'
        return MetadataFetchResult(work_id, expected_author_id,
                                   {'bookmark_count': 0, 'meta_source': 'illust_detail'}, False)


def test_managed_one_work_requires_explicit_enabled_ledger(tmp_path, monkeypatch):
    app, source = managed_app(tmp_path)
    try:
        _set_synthetic_active_receipt(monkeypatch, app)
        t = MetadataFakeTransport()
        with pytest.raises(RuntimeError, match='unavailable'):
            app.run_managed_metadata_one('123', transport=t)
        ledger = app.enable_managed_metadata()
        summary = app.run_managed_metadata_one('123', transport=t)
        assert summary.stopped == 'disabled'
        assert t.calls == [] and source.calls == []
        assert ledger.entry('123') is None
        with pytest.raises(ValueError):
            app.run_managed_metadata_one('not-an-id', transport=t)
    finally:
        app.close()


def test_managed_one_work_fills_only_verified_saved_receipt(tmp_path, monkeypatch):
    app, source = managed_app(tmp_path)
    try:
        _set_synthetic_active_receipt(monkeypatch, app)
        ledger = app.enable_managed_metadata()
        app.configure_managed_metadata(enabled=True, expected_revision=1)
        t = MetadataFakeTransport()
        summary = app.run_managed_metadata_one('123', transport=t)
        assert summary.target == 1 and summary.filled == 1
        assert t.calls == ['identity', 'detail']
        assert ledger.verified_overlay('123', 'a' * 64)['bookmark_count'] == 0
        assert app.run_managed_metadata_one('123', transport=t).skipped == 1
        assert t.calls == ['identity', 'detail', 'identity']
        assert source.calls == []
    finally:
        app.close()


def test_managed_download_priority_returns_without_auth_or_ledger_claim(tmp_path, monkeypatch):
    app, source = managed_app(tmp_path)
    try:
        _set_synthetic_active_receipt(monkeypatch, app)
        ledger = app.enable_managed_metadata()
        app.configure_managed_metadata(enabled=True, expected_revision=1)
        t = MetadataFakeTransport()
        original = app.jobs.snapshot
        monkeypatch.setattr(app.jobs, 'snapshot', lambda: (SimpleNamespace(state='queued'),))
        summary = app.run_managed_metadata_one('123', transport=t)
        assert summary.stopped == 'download_busy'
        assert t.calls == [] and ledger.entry('123') is None
        monkeypatch.setattr(app.jobs, 'snapshot', original)
        assert source.calls == []
    finally:
        app.close()


def test_managed_metadata_get_is_exclusive_and_close_joins_get(tmp_path, monkeypatch):
    app, source = managed_app(tmp_path)
    release, started = Event(), Event()
    try:
        _set_synthetic_active_receipt(monkeypatch, app)
        app.enable_managed_metadata()
        app.configure_managed_metadata(enabled=True, expected_revision=1)
        t = MetadataFakeTransport(started=started, release=release)
        with ThreadPoolExecutor(max_workers=2) as pool:
            running = pool.submit(app.run_managed_metadata_one, '123', transport=t)
            assert started.wait(5)
            assert not app._managed_enqueue_lock.acquire(blocking=False)
            assert app.run_managed_metadata_one('123', transport=t).stopped == 'already_running'
            assert app.stop_managed_metadata() is True
            closing = pool.submit(app.close)
            assert not closing.done(), 'close must wait for accepted GET to drain'
            release.set()
            closing.result(timeout=8)
            assert running.result(timeout=8).filled in (0, 1)
        assert app.closed and source.closed
        assert t.calls == ['identity', 'detail']
    finally:
        release.set()
        app.close()


def test_stop_during_run_admission_is_not_cleared_before_auth(tmp_path, monkeypatch):
    app, source = managed_app(tmp_path)
    started, release = Event(), Event()
    try:
        _set_synthetic_active_receipt(monkeypatch, app)
        ledger = app.enable_managed_metadata()
        app.configure_managed_metadata(enabled=True, expected_revision=1)
        original_settings = ledger.settings

        def slow_settings():
            started.set()
            assert release.wait(8)
            return original_settings()

        monkeypatch.setattr(ledger, 'settings', slow_settings)
        t = MetadataFakeTransport()
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(app.run_managed_metadata_one, '123', transport=t)
            assert started.wait(5)
            assert app.stop_managed_metadata()
            release.set()
            summary = running.result(timeout=8)
        assert summary.stopped == 'cancelled'
        assert t.calls == []
        assert app.managed_metadata_entry('123') is None
        assert source.calls == []
        monkeypatch.setattr(ledger, 'settings', original_settings)
        assert app.run_managed_metadata_one('123', transport=t).filled == 1
    finally:
        release.set()
        app.close()


def test_recovery_is_explicit_revisioned_and_does_not_fetch(tmp_path, monkeypatch):
    app, source = managed_app(tmp_path)
    try:
        _set_synthetic_active_receipt(monkeypatch, app)
        ledger = app.enable_managed_metadata()
        ledger.offer(MetadataCandidate('123', '91', 'illust', False, None, 0), 'a' * 64)
        app.configure_managed_metadata(enabled=True, expected_revision=1)
        lease = ledger.claim('123', 'a' * 64)
        ledger.complete_failure('123', 'a' * 64, lease,
                                classify_failure(FailureEvidence('http', 429)))
        assert app.managed_metadata_entry('123').state == 'blocked'
        with pytest.raises(MetadataLedgerError, match='changed'):
            app.resume_managed_metadata_blocked('123', expected_revision=1)
        assert app.managed_metadata_entry('123').state == 'blocked'
        app.resume_managed_metadata_blocked('123', expected_revision=2)
        assert app.managed_metadata_entry('123').state == 'pending'
        assert app.managed_metadata_settings().revision == 3
        assert source.calls == []
        with pytest.raises(MetadataLedgerError):
            app.resume_managed_metadata_blocked('123', expected_revision=3)
    finally:
        app.close()


def test_interrupted_recovery_never_acks_current_epoch_or_auto_replays(tmp_path, monkeypatch):
    app, source = managed_app(tmp_path)
    _set_synthetic_active_receipt(monkeypatch, app)
    ledger = app.enable_managed_metadata()
    ledger.offer(MetadataCandidate('123', '91', 'illust', False, None, 0), 'a' * 64)
    app.configure_managed_metadata(enabled=True, expected_revision=1)
    ledger.claim('123', 'a' * 64)
    with pytest.raises(MetadataLedgerError, match='live'):
        app.acknowledge_managed_metadata_interrupted('123')
    app.close()
    assert source.calls == []

    reopened, source2 = managed_app(tmp_path)
    try:
        _set_synthetic_active_receipt(monkeypatch, reopened)
        side = reopened.enable_managed_metadata()
        assert side.entry('123').state == 'in_flight'
        assert side.claim('123', 'a' * 64) is None
        reopened.acknowledge_managed_metadata_interrupted('123')
        assert reopened.managed_metadata_entry('123').state == 'unknown'
        assert side.claim('123', 'a' * 64) is None
        with pytest.raises(MetadataLedgerError, match='changed'):
            reopened.resume_managed_metadata_interrupted('123', expected_revision=1)
        reopened.resume_managed_metadata_interrupted('123', expected_revision=2)
        assert side.entry('123').state == 'pending'
        assert reopened.managed_metadata_settings().revision == 3
        assert source2.calls == []
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_metadata_recovery_ui_requires_explicit_button_and_shows_local_state(tmp_path, monkeypatch):
    app, source = managed_app(tmp_path)
    client = Client(ui.page('/_metadata_recovery_actions'))
    try:
        _set_synthetic_active_receipt(monkeypatch, app)
        ledger = app.enable_managed_metadata()
        ledger.offer(MetadataCandidate('123', '91', 'illust', False, None, 0), 'a' * 64)
        app.configure_managed_metadata(enabled=True, expected_revision=1)
        lease = ledger.claim('123', 'a' * 64)
        ledger.complete_failure('123', 'a' * 64, lease,
                                classify_failure(FailureEvidence('http', 429)))
        with client:
            view = MetadataView(app)
        assert view.revision is None
        await view.open()  # This is an explicit action; it doesn't fetch Pixiv.
        assert view.revision == 2
        view.work_id.set_value('123')
        await view.inspect()
        assert 'blocked' in view.entry_status.text
        await view.resume_blocked()
        assert app.managed_metadata_entry('123').state == 'pending'
        assert view.revision == 3
        assert '自動取得は開始しません' in view.entry_status.text
        assert source.calls == []
    finally:
        client.delete()
        app.close()


@pytest.mark.asyncio
async def test_metadata_view_dirty_form_keeps_revision_until_explicit_discard(tmp_path):
    services, source = managed_app(tmp_path)
    try:
        ledger = services.enable_managed_metadata()
        view = object.__new__(MetadataView)
        view.services = services
        view.closed = False
        view._busy = False
        view.revision = None
        view._settings_dirty = False
        view._loading_settings = False

        class Widget:
            def __init__(self):
                self.value = None

            def set_value(self, value):
                self.value = value
                view._mark_settings_dirty()

            def set_text(self, value):
                self.value = value

        view.enabled = Widget()
        view.status = Widget()
        view.progress_label = Widget()
        view._load(ledger.settings())
        assert view.revision == 1
        assert view.enabled.value is False
        assert not view._settings_dirty
        view.enabled.set_value(True)
        assert view._settings_dirty
        services.configure_managed_metadata(enabled=True, expected_revision=1)
        view._load(ledger.settings())  # Timer refresh must not steal CAS token.
        assert view.revision == 1 and view.enabled.value is True
        with pytest.raises(MetadataLedgerError, match='changed'):
            services.configure_managed_metadata(enabled=False, expected_revision=view.revision)
        await view.discard_changes()
        assert not view._settings_dirty
        assert view.revision == 2 and view.enabled.value is True
        assert source.calls == []
        view._load_progress(MetadataProgress(done=2, blocked=1, unknown=1))
        assert '完了 2' in view.progress_label.value
        assert '保留 1' in view.progress_label.value
        assert '中断不明 1' in view.progress_label.value
    finally:
        services.close()
