"""X07/X08 use generated originals and a newly owned managed root only."""
from io import BytesIO
from types import SimpleNamespace
import hashlib
import shutil

from PIL import Image
import pytest

from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.duplicate_inspection import ManagedDuplicateInspection
from pixivvault_nicegui.managed_downloads import ManagedDownloadError, ManagedPixivDownloads


class Context:
    def checkpoint(self):
        pass

    def progress(self, *_):
        pass


class Client:
    def __init__(self):
        self.images = []

    def verify_identity(self):
        return '99'

    def get_artwork_original_pages(self, wid):
        count = 3 if wid == '123' else 1
        return tuple(f'https://i.pximg.net/synthetic/{wid}/{n}.png' for n in range(count))

    def get_image_bytes(self, url):
        self.images.append(url)
        page = int(url.rsplit('/', 1)[-1].split('.')[0])
        stream = BytesIO()
        Image.new('RGB', (16 + page * 2, 12), (30 + page * 20, 20, 10)).save(stream, 'PNG')
        return stream.getvalue()


def record(wid):
    return RemoteBookmark(wid, 'illustration', 'show', 'Synthetic ' + wid,
                          '24' if wid in ('123', '124') else '25',
                          'Synthetic author', (), '', '2026-10-29T00:00:00Z',
                          3 if wid == '123' else 1, 0, 0)


@pytest.fixture
def owned(tmp_path):
    client = Client()
    writer = ManagedPixivDownloads(tmp_path / 'managed', client, resolve_record=record)
    for wid in ('123', '124', '223', '224'):
        writer.run(SimpleNamespace(kind='pixiv-illust', target_id=wid), Context())
    source = ManagedDuplicateInspection(writer)
    try:
        yield writer, source, client
    finally:
        source.close()
        writer.close()


def proof_bytes(writer):
    return {path.relative_to(writer.root).as_posix(): path.read_bytes()
            for folder in (writer.outputs, writer.generation_outputs)
            for path in folder.rglob('*') if path.is_file()}


def plan_for(source, *keys):
    scan = source.scan()
    wanted = set(keys)
    selections = {group.group_id: {item.key for item in group.items if item.key in wanted}
                  for group in scan.groups}
    assert set().union(*selections.values()) == wanted
    return source.plan(scan, selections)


def test_confirm_plan_is_read_only_and_identifies_executable_owned_selection(owned):
    writer, source, client = owned
    before, calls = proof_bytes(writer), tuple(client.images)
    plan = plan_for(source, '123:2')
    assert plan.mutation_allowed
    assert len(plan.items) == 1 and plan.items[0].page == 2
    assert proof_bytes(writer) == before and tuple(client.images) == calls
    assert not (writer.root / 'quarantine').exists()


def test_middle_page_is_quarantined_without_editing_any_original_proof(owned):
    writer, source, client = owned
    before, calls = proof_bytes(writer), tuple(client.images)
    plan = plan_for(source, '123:2')
    item = plan.items[0]
    expected = writer.read_saved_page(item.relative_receipt, 1, item.receipt_sha256)
    result = source.execute(plan, operation_id='a' * 32)
    assert [item.key for item in result.committed] == ['123:2']
    assert result.failed == ()
    assert writer.visible_source_pages('123') == (0, 2)
    assert proof_bytes(writer) == before and tuple(client.images) == calls
    copies = [p for p in (writer.root / 'quarantine' / 'operations').rglob('*')
              if p.is_file() and p.suffix == '.png']
    assert len(copies) == 1 and copies[0].read_bytes() == expected
    with pytest.raises(ManagedDownloadError, match='quarantin|excluded'):
        writer.read_saved_page(item.relative_receipt, 1, item.receipt_sha256)
    assert '123:2' not in {page.key for group in source.scan().groups for page in group.items}


def test_whole_work_can_be_removed_when_another_work_keeps_the_group_alive(owned):
    writer, source, client = owned
    before = proof_bytes(writer)
    result = source.execute(plan_for(source, '123:1', '123:2', '123:3'),
                            operation_id='b' * 32)
    assert len(result.committed) == 3 and not result.failed
    assert writer.visible_source_pages('123') == ()
    assert writer.visible_source_pages('124') == (0,)
    reopened = ManagedPixivDownloads(writer.root, client, resolve_record=record)
    try:
        assert reopened.visible_source_pages('123') == ()
        assert len(reopened.saved_receipts()) == 4
        assert proof_bytes(reopened) == before
    finally:
        reopened.close()


def test_second_client_cannot_use_stale_plan_to_remove_the_last_group_member(owned):
    writer, first, _ = owned
    second = ManagedDuplicateInspection(writer)
    try:
        first_plan = plan_for(first, '124:1')
        second_plan = plan_for(second, '123:1', '123:2', '123:3')
        first.execute(first_plan, operation_id='c' * 32)
        with pytest.raises(ValueError, match='changed|compare-and-swap|snapshot'):
            second.execute(second_plan, operation_id='d' * 32)
        assert writer.visible_source_pages('124') == ()
        assert writer.visible_source_pages('123') == (0, 1, 2)
    finally:
        second.close()


def test_batch_reports_only_durable_success_when_one_copy_fails(owned, monkeypatch):
    writer, source, client = owned
    before, calls = proof_bytes(writer), tuple(client.images)
    plan = plan_for(source, '123:2', '223:1')
    original_copy = writer.quarantine._copy_page

    def fail_one(item, destination):
        if item.work_id == '223':
            raise OSError('synthetic copy failure')
        return original_copy(item, destination)

    monkeypatch.setattr(writer.quarantine, '_copy_page', fail_one)
    result = source.execute(plan, operation_id='e' * 32)
    assert [item.key for item in result.committed] == ['123:2']
    assert [item.key for item in result.failed] == ['223:1']
    assert writer.visible_source_pages('123') == (0, 2)
    assert writer.visible_source_pages('223') == (0,)
    assert proof_bytes(writer) == before and tuple(client.images) == calls


def test_same_operation_replay_uses_committed_result_without_duplicate_copies(owned):
    writer, source, _ = owned
    plan = plan_for(source, '123:2')
    first = source.execute(plan, operation_id='f' * 32)
    files = {p.relative_to(writer.root): p.read_bytes()
             for p in (writer.root / 'quarantine').rglob('*') if p.is_file()}
    second = source.execute(plan, operation_id='f' * 32)
    assert first.committed == second.committed and first.failed == second.failed
    assert files == {p.relative_to(writer.root): p.read_bytes()
                     for p in (writer.root / 'quarantine').rglob('*') if p.is_file()}


def test_precommit_failure_does_not_publish_exclusion(owned, monkeypatch):
    writer, source, _ = owned
    before = proof_bytes(writer)
    plan = plan_for(source, '123:2')
    writer._generations.prepare_for_publication()

    def fail_commit(*_args, **_kwargs):
        raise OSError('synthetic index publication failure')

    monkeypatch.setattr(writer._generations, '_commit_packet', fail_commit)
    with pytest.raises((OSError, ManagedDownloadError)):
        source.execute(plan, operation_id='1' * 32)
    assert writer.visible_source_pages('123') == (0, 1, 2)
    assert proof_bytes(writer) == before


def test_missing_index_after_v1_quarantine_never_reveals_originals(owned):
    writer, source, client = owned
    source.execute(plan_for(source, '123:2'), operation_id='2' * 32)
    assert not any(writer.generation_outputs.iterdir())
    (writer.root / 'active-generations.json').unlink()
    with pytest.raises(ManagedDownloadError, match='index missing'):
        writer.saved_receipts()
    with pytest.raises(ManagedDownloadError, match='index missing'):
        ManagedPixivDownloads(writer.root, client, resolve_record=record)


def test_download_publish_cas_includes_quarantine_state_even_when_receipt_is_unchanged(owned):
    writer, source, _ = owned
    before = writer._generations.work_state_token('123')
    relative, receipt, old_sha, _, _ = writer.verified_saved_receipt('123')
    source.execute(plan_for(source, '123:2'), operation_id='3' * 32)
    folder = writer.generation_outputs / ('work-123-' + '4' * 32)
    shutil.copytree((writer.root / relative).parent, folder)
    target = f'generations/{folder.name}/receipt.json'
    digest = hashlib.sha256((folder / 'receipt.json').read_bytes()).hexdigest()
    with pytest.raises(ManagedDownloadError, match='compare-and-swap'):
        writer._generations.publish('123', target, digest, '2026-10-29', old_sha, relative,
                                    expected_work_state_token=before)
    assert writer.verified_saved_receipt('123')[0] == relative
    assert writer.visible_source_pages('123') == (0, 2)


def test_quarantined_page_cannot_be_republished_by_individual_save(owned):
    writer, source, client = owned
    source.execute(plan_for(source, '123:2'), operation_id='5' * 32)
    calls = tuple(client.images)
    with pytest.raises(ManagedDownloadError, match='quarantin|excluded'):
        writer.save_viewer_page('123', 1, 3, '24', lambda: pytest.fail('must not fetch metadata'))
    assert tuple(client.images) == calls
    assert not (writer.root / 'viewer-pages').exists()


def test_whole_work_retry_does_not_retrieve_or_restore_quarantined_pages(owned):
    writer, source, client = owned
    source.execute(plan_for(source, '123:1', '123:2', '123:3'), operation_id='6' * 32)
    calls = tuple(client.images)
    writer.run(SimpleNamespace(kind='pixiv-illust', target_id='123'), Context())
    assert tuple(client.images) == calls
    assert writer.visible_source_pages('123') == ()


def test_explicit_recovery_restores_only_the_expected_committed_exclusion(owned):
    writer, source, client = owned
    before, calls = proof_bytes(writer), tuple(client.images)
    result = source.execute(plan_for(source, '123:2'), operation_id='7' * 32)
    writer.quarantine.restore(result.operation_id, expected_revision=result.state_revision)
    assert writer.visible_source_pages('123') == (0, 1, 2)
    assert proof_bytes(writer) == before and tuple(client.images) == calls
    with pytest.raises(ManagedDownloadError, match='compare-and-swap|changed'):
        writer.quarantine.restore(result.operation_id, expected_revision=result.state_revision)
