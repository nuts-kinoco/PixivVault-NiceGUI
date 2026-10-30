"""A concurrent quarantine commit must not publish a mixed catalog snapshot."""
import pytest

from pixivvault_nicegui.managed_downloads import ManagedDownloadError
from test_managed_quarantine import owned, plan_for, proof_bytes


def test_catalog_read_retries_an_intervening_real_quarantine_commit(owned, monkeypatch):
    writer, source, remote = owned
    plan = plan_for(source, '123:2')
    original_proofs, gets = proof_bytes(writer), tuple(remote.images)
    read_receipts = writer.saved_receipts
    committed = []

    def read_then_commit(*, verify_pages=True):
        rows = read_receipts(verify_pages=verify_pages)
        if not committed:
            # Set the guard before execute, which also refreshes its own scan.
            committed.append(True)
            result = source.execute(plan, operation_id='e' * 32)
            assert len(result.committed) == 1 and not result.failed
        return rows

    monkeypatch.setattr(writer, 'saved_receipts', read_then_commit)
    snapshot = writer.catalog_snapshot()
    assert committed == [True]
    assert snapshot.source_pages['123'] == (0, 2)
    assert snapshot.work_tokens['123'] == writer._generations.work_state_token('123')
    assert snapshot.revision == writer._generations.state_snapshot()['revision']
    assert proof_bytes(writer) == original_proofs and tuple(remote.images) == gets


def test_continuous_revision_changes_fail_closed_after_bounded_observation(owned, monkeypatch):
    writer, _, _ = owned
    state = writer._generations.state_snapshot()
    reads = []

    def changed_snapshot():
        reads.append(True)
        if len(reads) > 10:
            pytest.fail('catalog observation retries must be bounded')
        return dict(state, revision=f'{len(reads):064x}')

    monkeypatch.setattr(writer._generations, 'state_snapshot', changed_snapshot)
    with pytest.raises(ManagedDownloadError, match='managed catalog snapshot changed'):
        writer.catalog_snapshot()
    assert 2 <= len(reads) <= 10
