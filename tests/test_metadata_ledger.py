"""Offline managed-only side-ledger acceptance; no original DB or Pixiv GET."""
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from pixivvault_nicegui.managed_downloads import IDENTITY, MARKER
from pixivvault_nicegui.metadata_ledger import (
    ManagedMetadataLedger, MetadataLedgerError,
)
from pixivvault_nicegui.metadata_policy import (
    FailureEvidence, FailureDecision, MetadataCandidate,
    MetadataFailureClass, classify_failure,
)

FP1, FP2 = 'a' * 64, 'b' * 64
_ACTIVE = {}


def open_ledger(root):
    # A synthetic immutable managed receipt index. M2 never contacts Pixiv.
    active = _ACTIVE.setdefault(root, {'123': FP1})
    return ManagedMetadataLedger(root, active_receipt_sha256=active.get)


def root_with_marker(tmp_path):
    root = tmp_path / 'isolated-managed'
    root.mkdir()
    (root / MARKER).write_text(json.dumps(IDENTITY), encoding='utf-8')
    return root


def candidate():
    return MetadataCandidate('123', '91', 'illust', False, None, 0)


def test_reject_unmarked_or_foreign_storage_without_creating_sqlite(tmp_path):
    root = tmp_path / 'unmarked'
    root.mkdir()
    with pytest.raises(MetadataLedgerError):
        open_ledger(root)
    assert not list(root.glob('*.sqlite3'))
    (root / MARKER).write_text('{}', encoding='utf-8')
    with pytest.raises(MetadataLedgerError):
        open_ledger(root)
    assert not list(root.glob('*.sqlite3'))


def test_off_default_and_revision_cas_persist_across_restart(tmp_path):
    root = root_with_marker(tmp_path)
    ledger = open_ledger(root)
    try:
        assert not ledger.settings().enabled
        assert ledger.offer(candidate(), FP1)
        assert ledger.claim('123', FP1) is None
        enabled = ledger.configure(enabled=True, expected_revision=1)
        assert enabled.enabled and enabled.revision == 2
        with pytest.raises(MetadataLedgerError, match='changed'):
            ledger.configure(enabled=False, expected_revision=1)
        assert ledger.offer(candidate(), FP1) is False
    finally:
        ledger.close()
    restored = open_ledger(root)
    try:
        assert restored.settings().enabled and restored.settings().revision == 2
        assert restored.entry('123').state == 'pending'
        assert restored.configure(enabled=False, expected_revision=2).revision == 3
        assert restored.claim('123', FP1) is None
    finally:
        restored.close()


def test_unversioned_m2_database_reopens_and_preserves_existing_data(tmp_path):
    root = root_with_marker(tmp_path)
    ledger = open_ledger(root)
    ledger.offer(candidate(), FP1)
    ledger.configure(enabled=True, expected_revision=1)
    lease = ledger.claim('123', FP1)
    ledger.complete_success('123', FP1, lease, {'bookmark_count': 0},
                            verified_active_receipt_sha256=FP1)
    ledger.close()
    with sqlite3.connect(root / '.pixivvault-managed-metadata.sqlite3') as db:
        db.execute('PRAGMA user_version=0')  # The committed M2 schema lacked user_version.
    restored = open_ledger(root)
    try:
        assert restored.settings().enabled
        assert restored.verified_overlay('123', FP1) == {'bookmark_count': 0}
        assert restored.entry('123').state == 'done'
    finally:
        restored.close()
    with sqlite3.connect(root / '.pixivvault-managed-metadata.sqlite3') as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1


def test_existing_snapshot_attempt_budget_is_not_reset_on_first_offer(tmp_path):
    ledger = open_ledger(root_with_marker(tmp_path))
    try:
        row = MetadataCandidate('123', '91', 'illust', False, None, 2)
        assert ledger.offer(row, FP1)
        assert ledger.entry('123').attempts == 2
        ledger.configure(enabled=True, expected_revision=1)
        lease = ledger.claim('123', FP1)
        assert ledger.complete_failure(
            '123', FP1, lease, classify_failure(FailureEvidence('http', 404))) == 'gave_up'
        assert ledger.entry('123').attempts == 3
    finally:
        ledger.close()


def test_exact_generation_success_can_overlay_zero_without_receipt_mutation(tmp_path):
    root = root_with_marker(tmp_path)
    marker_before = (root / MARKER).read_bytes()
    ledger = open_ledger(root)
    try:
        ledger.offer(candidate(), FP1)
        ledger.configure(enabled=True, expected_revision=1)
        lease = ledger.claim('123', FP1)
        assert lease is not None
        with pytest.raises(MetadataLedgerError, match='generation changed'):
            ledger.complete_success('123', FP1, lease, {'bookmark_count': 0},
                                    verified_active_receipt_sha256=FP2)
        assert ledger.entry('123').state == 'in_flight'
        ledger.complete_success('123', FP1, lease, {'bookmark_count': 0, 'view_count': 4},
                                verified_active_receipt_sha256=FP1)
        assert ledger.verified_overlay('123', FP1) == {'bookmark_count': 0, 'view_count': 4}
        assert ledger.verified_overlay('123', FP2) is None
        assert ledger.claim('123', FP1) is None
    finally:
        ledger.close()
    assert (root / MARKER).read_bytes() == marker_before
    assert not list(root.rglob('receipt.json'))


def test_unknown_or_global_faults_never_increment_or_implicitly_resume(tmp_path):
    root = root_with_marker(tmp_path)
    ledger = open_ledger(root)
    try:
        ledger.offer(candidate(), FP1)
        ledger.configure(enabled=True, expected_revision=1)
        lease = ledger.claim('123', FP1)
        assert ledger.complete_failure(
            '123', FP1, lease, classify_failure(FailureEvidence('http', 429))) == 'blocked'
        assert ledger.entry('123').attempts == 0
        assert ledger.claim('123', FP1) is None
        assert not ledger.offer(candidate(), FP1)
        _ACTIVE[root]['123'] = FP2
        ledger.offer(candidate(), FP2)  # A *different* immutable generation.
        lease = ledger.claim('123', FP2)
        assert ledger.complete_failure(
            '123', FP2, lease, classify_failure(FailureEvidence('unknown'))) == 'blocked'
        assert ledger.entry('123').attempts == 0
        assert ledger.claim('123', FP2) is None
    finally:
        ledger.close()


def test_work_specific_failures_reach_three_and_never_hot_retry(tmp_path):
    root = root_with_marker(tmp_path)
    ledger = open_ledger(root)
    try:
        ledger.offer(candidate(), FP1)
        ledger.configure(enabled=True, expected_revision=1)
        for retry in range(1, 4):
            lease = ledger.claim('123', FP1)
            assert lease is not None
            outcome = ledger.complete_failure(
                '123', FP1, lease, classify_failure(FailureEvidence('http', 404)))
            assert ledger.entry('123').attempts == retry
            assert outcome == ('gave_up' if retry == 3 else 'pending')
        assert ledger.claim('123', FP1) is None
        assert not ledger.offer(candidate(), FP1)
    finally:
        ledger.close()


def test_inflight_is_ambiguous_on_restart_and_requires_explicit_ack(tmp_path):
    root = root_with_marker(tmp_path)
    ledger = open_ledger(root)
    ledger.offer(candidate(), FP1)
    ledger.configure(enabled=True, expected_revision=1)
    old_lease = ledger.claim('123', FP1)
    ledger.close()
    restored = open_ledger(root)
    try:
        assert restored.claim('123', FP1) is None
        assert restored.entry('123').state == 'in_flight'
        assert not hasattr(restored.entry('123'), 'lease_id')
        with pytest.raises(MetadataLedgerError, match='no longer current'):
            restored.complete_success('123', FP1, old_lease, {'bookmark_count': 5},
                                      verified_active_receipt_sha256=FP1)
        _ACTIVE[root]['123'] = FP2
        with pytest.raises(MetadataLedgerError, match='in flight'):
            restored.offer(candidate(), FP2)
        restored.acknowledge_interrupted('123')
        assert restored.entry('123').state == 'unknown'
        assert restored.claim('123', FP1) is None
        with pytest.raises(MetadataLedgerError):
            restored.complete_success('123', FP1, old_lease, {'bookmark_count': 5},
                                      verified_active_receipt_sha256=FP1)
        assert restored.offer(candidate(), FP2)
        assert restored.claim('123', FP2) is not None
    finally:
        restored.close()


def test_receipt_generation_isolated_and_corrupt_overlay_refused(tmp_path):
    ledger = open_ledger(root_with_marker(tmp_path))
    try:
        ledger.offer(candidate(), FP1)
        ledger.configure(enabled=True, expected_revision=1)
        lease = ledger.claim('123', FP1)
        with pytest.raises(MetadataLedgerError):
            ledger.complete_success('123', FP1, lease, {'bookmark_count': -1},
                                    verified_active_receipt_sha256=FP1)
        with pytest.raises(MetadataLedgerError):
            ledger.complete_success('123', FP1, lease, {'bookmark_count': 1, 'url': 'x'},
                                    verified_active_receipt_sha256=FP1)
        ledger.complete_success('123', FP1, lease, {'bookmark_count': 2},
                                verified_active_receipt_sha256=FP1)
        ledger._db.execute('UPDATE metadata SET json_body=? WHERE work_id=?',
                           (b'{"bookmark_count":999}', '123'))
        with pytest.raises(MetadataLedgerError, match='integrity'):
            ledger.verified_overlay('123', FP1)
    finally:
        ledger.close()


def test_ledger_exclusive_process_owner_and_closed_guards(tmp_path):
    root = root_with_marker(tmp_path)
    ledger = open_ledger(root)
    try:
        with pytest.raises((OSError, MetadataLedgerError)):
            open_ledger(root)
    finally:
        ledger.close()
    with pytest.raises(MetadataLedgerError, match='closed'):
        ledger.settings()
    recovered = open_ledger(root)
    recovered.close()


def test_stale_offer_never_overwrites_newer_finished_generation(tmp_path):
    root = root_with_marker(tmp_path)
    ledger = open_ledger(root)
    try:
        ledger.offer(candidate(), FP1)
        _ACTIVE[root]['123'] = FP2
        assert ledger.offer(candidate(), FP2)
        ledger.configure(enabled=True, expected_revision=1)
        lease = ledger.claim('123', FP2)
        ledger.complete_success('123', FP2, lease, {'bookmark_count': 8},
                                verified_active_receipt_sha256=FP2)
        with pytest.raises(MetadataLedgerError, match='not active'):
            ledger.offer(candidate(), FP1)
        assert ledger.verified_overlay('123', FP2) == {'bookmark_count': 8}
        # Even a corrupt external pointer rollback must not resurrect an
        # older generation and reset its work-specific attempt counters.
        _ACTIVE[root]['123'] = FP1
        with pytest.raises(MetadataLedgerError, match='superseded'):
            ledger.offer(candidate(), FP1)
        assert ledger.entry('123').base_receipt_sha256 == FP2
    finally:
        ledger.close()


def test_forged_failure_flags_cannot_change_attempt_policy(tmp_path):
    root = root_with_marker(tmp_path)
    ledger = open_ledger(root)
    try:
        ledger.offer(candidate(), FP1)
        ledger.configure(enabled=True, expected_revision=1)
        lease = ledger.claim('123', FP1)
        ledger.complete_failure(
            '123', FP1, lease,
            FailureDecision(MetadataFailureClass.WORK, False, False, 0))
        assert ledger.entry('123').attempts == 1
        lease = ledger.claim('123', FP1)
        ledger.complete_failure(
            '123', FP1, lease,
            FailureDecision(MetadataFailureClass.GLOBAL, True, False, 0))
        assert ledger.entry('123').attempts == 1
        assert ledger.entry('123').state == 'blocked'
        with pytest.raises(MetadataLedgerError, match='changed'):
            ledger.resume_blocked('123', expected_revision=1)
        ledger.resume_blocked('123', expected_revision=2)
        assert ledger.settings().revision == 3
        assert ledger.entry('123').state == 'pending'
        assert ledger.claim('123', FP1) is not None
    finally:
        ledger.close()


def test_disable_fences_live_capability_and_no_resume_on_reenable(tmp_path):
    root = root_with_marker(tmp_path)
    ledger = open_ledger(root)
    try:
        ledger.offer(candidate(), FP1)
        ledger.configure(enabled=True, expected_revision=1)
        lease = ledger.claim('123', FP1)
        ledger.configure(enabled=False, expected_revision=2)
        with pytest.raises(MetadataLedgerError, match='no longer current'):
            ledger.complete_success('123', FP1, lease, {'bookmark_count': 3},
                                    verified_active_receipt_sha256=FP1)
        assert ledger.entry('123').state == 'unknown'
        ledger.configure(enabled=True, expected_revision=3)
        assert ledger.claim('123', FP1) is None
    finally:
        ledger.close()


def test_active_receipt_changed_after_claim_refuses_late_success(tmp_path):
    root = root_with_marker(tmp_path)
    ledger = open_ledger(root)
    try:
        ledger.offer(candidate(), FP1)
        ledger.configure(enabled=True, expected_revision=1)
        lease = ledger.claim('123', FP1)
        _ACTIVE[root]['123'] = FP2
        with pytest.raises(MetadataLedgerError, match='generation changed'):
            ledger.complete_success('123', FP1, lease, {'bookmark_count': 3},
                                    verified_active_receipt_sha256=FP1)
        assert ledger.entry('123').state == 'in_flight'
    finally:
        ledger.close()


def test_two_threads_cannot_both_claim_one_pending_work(tmp_path):
    root = root_with_marker(tmp_path)
    ledger = open_ledger(root)
    try:
        ledger.offer(candidate(), FP1)
        ledger.configure(enabled=True, expected_revision=1)
        with ThreadPoolExecutor(max_workers=2) as pool:
            leases = list(pool.map(lambda _: ledger.claim('123', FP1), range(2)))
        assert len([lease for lease in leases if lease is not None]) == 1
    finally:
        ledger.close()


def test_duplicate_key_marker_is_rejected(tmp_path):
    root = root_with_marker(tmp_path)
    payload = json.dumps(IDENTITY)
    (root / MARKER).write_text(
        payload.replace('{"application":', '{"application": "foreign", "application":'),
        encoding='utf-8',
    )
    with pytest.raises(MetadataLedgerError, match='marker'):
        open_ledger(root)


def test_schema_corruption_is_rejected_without_silent_repair(tmp_path):
    root = root_with_marker(tmp_path)
    path = root / '.pixivvault-managed-metadata.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA user_version=1')
        db.execute('CREATE TABLE metadata(work_id TEXT PRIMARY KEY, secret TEXT)')
    with sqlite3.connect(path) as db:
        before = (db.execute('PRAGMA user_version').fetchone(),
                  db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall())
    with pytest.raises(MetadataLedgerError, match='schema rejected'):
        open_ledger(root)
    with sqlite3.connect(path) as db:
        after = (db.execute('PRAGMA user_version').fetchone(),
                 db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall())
    assert after == before


def test_active_pointer_callback_never_runs_under_ledger_lock(tmp_path):
    root = root_with_marker(tmp_path)
    holder = {}
    observations = []

    def active(work_id):
        ledger = holder.get('ledger')
        if ledger is not None:
            observations.append(ledger._lock._is_owned())
        return _ACTIVE.setdefault(root, {'123': FP1}).get(work_id)

    ledger = ManagedMetadataLedger(root, active_receipt_sha256=active)
    holder['ledger'] = ledger
    try:
        assert ledger.offer(candidate(), FP1)
        ledger.configure(enabled=True, expected_revision=1)
        lease = ledger.claim('123', FP1)
        ledger.complete_success('123', FP1, lease, {'bookmark_count': 0},
                                verified_active_receipt_sha256=FP1)
        assert observations and not any(observations)
    finally:
        ledger.close()


def test_active_pointer_failure_is_sanitized(tmp_path):
    root = root_with_marker(tmp_path)
    secret_path = 'C:\\private\\cookie.txt'

    def broken(_work_id):
        raise OSError('failed reading ' + secret_path)

    ledger = ManagedMetadataLedger(root, active_receipt_sha256=broken)
    try:
        with pytest.raises(MetadataLedgerError, match='active managed receipt unavailable') as error:
            ledger.offer(candidate(), FP1)
        assert secret_path not in str(error.value)
    finally:
        ledger.close()


def test_superseded_generations_stay_retired_after_restart(tmp_path):
    root = root_with_marker(tmp_path)
    ledger = open_ledger(root)
    try:
        assert ledger.offer(candidate(), FP1)
        _ACTIVE[root]['123'] = FP2
        assert ledger.offer(candidate(), FP2)
    finally:
        ledger.close()
    reopened = open_ledger(root)
    try:
        _ACTIVE[root]['123'] = FP1
        with pytest.raises(MetadataLedgerError, match='superseded'):
            reopened.offer(candidate(), FP1)
        assert reopened.entry('123').base_receipt_sha256 == FP2
    finally:
        reopened.close()


def test_changed_generation_can_explicitly_retire_current_stale_lease(tmp_path):
    root = root_with_marker(tmp_path)
    ledger = open_ledger(root)
    try:
        ledger.offer(candidate(), FP1)
        ledger.configure(enabled=True, expected_revision=1)
        lease = ledger.claim('123', FP1)
        _ACTIVE[root]['123'] = FP2
        with pytest.raises(MetadataLedgerError, match='generation changed'):
            ledger.complete_success('123', FP1, lease, {'bookmark_count': 4},
                                    verified_active_receipt_sha256=FP1)
        ledger.discard_stale_result('123', FP1, lease)
        assert ledger.entry('123').state == 'unknown'
        assert ledger.offer(candidate(), FP2)
        assert ledger.claim('123', FP2) is not None
    finally:
        ledger.close()


def test_interrupted_same_generation_requires_explicit_revisioned_resume(tmp_path):
    root = root_with_marker(tmp_path)
    ledger = open_ledger(root)
    ledger.offer(candidate(), FP1)
    ledger.configure(enabled=True, expected_revision=1)
    ledger.claim('123', FP1)
    ledger.close()
    restored = open_ledger(root)
    try:
        restored.acknowledge_interrupted('123')
        assert restored.entry('123').state == 'unknown'
        assert restored.claim('123', FP1) is None
        with pytest.raises(MetadataLedgerError, match='changed'):
            restored.resume_interrupted('123', expected_revision=1)
        assert restored.resume_interrupted('123', expected_revision=2) is None
        assert restored.settings().revision == 3
        assert restored.claim('123', FP1) is not None
    finally:
        restored.close()
