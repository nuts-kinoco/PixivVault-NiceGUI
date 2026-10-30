"""Failure/replay/update boundaries not covered by a successful quarantine alone."""
from io import BytesIO
import shutil
from types import SimpleNamespace

import pytest
from PIL import Image

from pixivvault_nicegui.local_media_adapter import MediaUnavailable
from pixivvault_nicegui.managed_download_catalog import build_managed_library
from pixivvault_nicegui.managed_download_requests import ManagedAuthorIntent
from pixivvault_nicegui.managed_downloads import ManagedDownloadError, ManagedPageSelectionChanged
from test_managed_quarantine import Context, owned, plan_for, proof_bytes, record


def active(writer):
    return writer.verified_saved_receipt('123')


def promote_v1(writer):
    old = active(writer)
    token = writer._generations.work_state_token('123')
    writer._generations.prepare_for_publication()
    target = writer.generation_outputs / ('work-123-' + 'a' * 32)
    shutil.copytree((writer.root / old[0]).parent, target)
    relative = target.relative_to(writer.root).as_posix() + '/receipt.json'
    result = writer._generations.publish('123', relative, old[2], '2026-10-29', old[2], old[0],
                                         expected_work_state_token=token)
    return old, relative, token, result


def revision(*_):
    return {'author_id': '24', 'page_count': 3, 'illustration_type': 0,
            'update_date': '2026-10-30'}


def update_intent(writer):
    relative, _, sha, *_ = active(writer)
    return ManagedAuthorIntent(record('123'), 'id', '2026-10-30', sha, 'updated', relative)


def test_all_copy_failures_change_no_index_and_report_no_success(owned, monkeypatch):
    writer, source, remote = owned
    plan = plan_for(source, '123:2', '223:1')
    before, calls = proof_bytes(writer), tuple(remote.images)
    state = writer._generations.state_snapshot()
    def fail(*_):
        raise OSError('synthetic full destination')
    monkeypatch.setattr(writer.quarantine, '_copy_page', fail)
    result = source.execute(plan, operation_id='b' * 32)
    assert result.committed == () and {p.key for p in result.failed} == {'123:2', '223:1'}
    assert writer._generations.state_snapshot() == state
    assert writer.visible_source_pages('123') == (0, 1, 2)
    assert not list(writer.quarantine.operations.iterdir())
    assert proof_bytes(writer) == before and tuple(remote.images) == calls


def test_exception_after_index_replace_reports_committed_success_with_warning(owned, monkeypatch):
    writer, source, _ = owned
    plan = plan_for(source, '123:2')
    original = writer._generations._commit_packet
    def published_then_failed(packet):
        original(packet)
        if packet.get('quarantine_operations'):
            raise OSError('synthetic directory fsync failure after replace')
    monkeypatch.setattr(writer._generations, '_commit_packet', published_then_failed)
    result = source.execute(plan, operation_id='c' * 32)
    assert [p.key for p in result.committed] == ['123:2']
    assert result.failed == () and result.cleanup_warning
    assert writer.visible_source_pages('123') == (0, 2)
    replay = source.execute(plan, operation_id='c' * 32)
    assert replay.committed == result.committed and replay.failed == ()


@pytest.mark.parametrize('damaged', ['manifest', 'source-receipt'])
def test_warm_visibility_cache_revalidates_changed_quarantine_evidence(owned, damaged):
    writer, source, _ = owned
    result = source.execute(plan_for(source, '123:2'), operation_id='d' * 32)
    _, catalog, _ = build_managed_library(writer)
    try:
        catalog.get('123', 0, 'original')
        assert catalog.peek('123', 0, 'original') is not None
        path = (writer.quarantine.operations / result.operation_id / 'manifest.json'
                if damaged == 'manifest' else writer.root / result.committed[0].relative_receipt)
        path.write_bytes(b'{"corrupt":true}')
        with pytest.raises(MediaUnavailable):
            catalog.peek('123', 0, 'original')
        with pytest.raises(ManagedDownloadError):
            writer.saved_receipts()
    finally:
        catalog.close()


def test_damaged_quarantine_copy_cannot_restore_the_page(owned):
    writer, source, _ = owned
    result = source.execute(plan_for(source, '123:2'), operation_id='e' * 32)
    copy = next((writer.quarantine.operations / result.operation_id).glob('*.png'))
    copy.write_bytes(b'corrupt retained copy')
    with pytest.raises(ManagedDownloadError):
        writer.quarantine.restore(result.operation_id, expected_revision=result.state_revision)
    assert writer.visible_source_pages('123') == (0, 2)
    assert copy.read_bytes() == b'corrupt retained copy'


def test_same_generation_publish_replay_preserves_a_later_quarantine(owned):
    writer, source, _ = owned
    old, relative, token, expected = promote_v1(writer)
    source.execute(plan_for(source, '123:2'), operation_id='f' * 32)
    before = proof_bytes(writer)
    replay = writer._generations.publish('123', relative, old[2], '2026-10-29', old[2], old[0],
                                         expected_work_state_token=token)
    assert replay == expected
    assert writer.visible_source_pages('123') == (0, 2)
    assert proof_bytes(writer) == before


def test_author_update_retains_excluded_original_without_fetching_it(owned, monkeypatch):
    writer, source, remote = owned
    promote_v1(writer)
    source.execute(plan_for(source, '123:2'), operation_id='1' * 32)
    old = active(writer)
    retained = writer._read_saved_page_proof(old[0], 1, old[2])
    before = proof_bytes(writer)
    _, catalog, _ = build_managed_library(writer)
    pin = catalog.source_page_identity('123', 1)
    calls = len(remote.images)
    original = remote.get_image_bytes
    def changed(url):
        original(url)
        out = BytesIO()
        Image.new('RGB', (22, 12), (80, 180, 40)).save(out, 'PNG')
        return out.getvalue()
    monkeypatch.setattr(remote, 'get_artwork_update_revision', revision, raising=False)
    monkeypatch.setattr(remote, 'get_image_bytes', changed)
    try:
        result = writer.run(SimpleNamespace(kind='pixiv-illust', target_id='123', job_id='2' * 32),
                            Context(), intent=update_intent(writer))
        current = active(writer)
        assert current[0] == result and current[0] != old[0]
        assert remote.images[calls:] == [
            'https://i.pximg.net/synthetic/123/0.png',
            'https://i.pximg.net/synthetic/123/2.png',
        ]
        assert current[1]['page_count'] == 3
        assert writer._read_saved_page_proof(current[0], 1, current[2]) == retained
        assert writer.visible_source_pages('123') == (0, 2)
        assert {path: proof_bytes(writer)[path] for path in before} == before
        with pytest.raises(ManagedPageSelectionChanged, match='snapshot changed'):
            writer.save_pinned_page(pin)
    finally:
        catalog.close()


def test_quarantine_committed_during_update_blocks_final_generation_publication(owned, monkeypatch):
    writer, source, remote = owned
    promote_v1(writer)
    intent = update_intent(writer)
    plan = plan_for(source, '123:2')
    before = active(writer)
    generation_dirs = set(writer.generation_outputs.iterdir())
    original = remote.get_image_bytes
    committed = []
    def changed(url):
        data = original(url)
        if not committed:
            committed.append(source.execute(plan, operation_id='3' * 32))
        return data
    monkeypatch.setattr(remote, 'get_artwork_update_revision', revision, raising=False)
    monkeypatch.setattr(remote, 'get_image_bytes', changed)
    with pytest.raises(ManagedDownloadError, match='compare-and-swap'):
        writer.run(SimpleNamespace(kind='pixiv-illust', target_id='123', job_id='4' * 32),
                   Context(), intent=intent)
    assert len(committed) == 1 and len(committed[0].committed) == 1
    assert active(writer) == before
    assert set(writer.generation_outputs.iterdir()) == generation_dirs
    assert writer.visible_source_pages('123') == (0, 2)
