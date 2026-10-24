"""M4 settings-only UI/service acceptance; never opens real Pixiv or legacy DB."""
from pathlib import Path

import pytest

from pixivvault_nicegui.metadata_ledger import MetadataLedgerError
from pixivvault_nicegui.metadata_ledger import MetadataProgress
from pixivvault_nicegui.metadata_policy import MetadataCandidate
from pixivvault_nicegui.metadata_policy import FailureEvidence, classify_failure
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
