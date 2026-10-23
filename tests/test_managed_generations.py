"""Active-generation CAS and legacy v1 durability, entirely synthetic."""
from io import BytesIO
import hashlib
import json
from pathlib import Path
import shutil

from PIL import Image
import pytest

from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.managed_downloads import ManagedDownloadError, ManagedPixivDownloads
from pixivvault_nicegui.managed_generations import ManagedGenerations


class Job:
    kind = 'pixiv-illust'
    target_id = '123'


class Context:
    def checkpoint(self):
        pass

    def progress(self, *_):
        pass


class Client:
    def verify_identity(self):
        return '999'

    def get_artwork_original_pages(self, wid):
        return ('https://i.pximg.net/synthetic/123_p0.png',)

    def get_image_bytes(self, url):
        out = BytesIO()
        Image.new('RGB', (12, 8), (6, 20, 55)).save(out, 'PNG')
        return out.getvalue()


def new_writer(tmp_path):
    rec = RemoteBookmark('123', 'illustration', 'show', 'Synthetic', '24',
                         'Author', (), '', '2026', 1, 0, 0)
    writer = ManagedPixivDownloads(tmp_path / 'managed', Client(), resolve_record=lambda _: rec)
    first = writer.run(Job(), Context())
    relative, receipt, digest = writer.saved_receipts()[0]
    assert first == relative and receipt['version'] == 1
    return writer, relative, digest


def stage_generation(writer, original, name):
    writer._generations.prepare_for_publication()
    folder = writer.generation_outputs / ('work-123-' + name * 32)
    shutil.copytree(writer.root / original.rsplit('/', 1)[0], folder)
    rel = f'generations/{folder.name}/receipt.json'
    digest = hashlib.sha256((folder / 'receipt.json').read_bytes()).hexdigest()
    return rel, digest


def test_legacy_active_until_atomic_new_generation_and_restart(tmp_path):
    writer, original, first_digest = new_writer(tmp_path)
    old_bytes = (writer.root / original).read_bytes()
    new, sha = stage_generation(writer, original, 'a')
    assert writer.saved_receipts()[0][0] == original  # Orphan is inactive.
    assert writer.saved_update_date('123') is None
    entry = writer._generations.publish('123', new, sha, '2026-10-24', first_digest, original)
    assert entry['relative_receipt'] == new
    assert writer.saved_update_date('123') == '2026-10-24'
    assert writer.saved_receipts()[0][0] == new
    assert (writer.root / original).read_bytes() == old_bytes
    reopened = ManagedPixivDownloads(writer.root, Client(), resolve_record=lambda _: None)
    assert reopened.saved_receipts()[0][0] == new
    assert reopened.saved_update_date('123') == '2026-10-24'


def test_cas_rejects_old_snapshot_and_preserves_active(tmp_path):
    writer, previous, prior_sha = new_writer(tmp_path)
    first, first_sha = stage_generation(writer, previous, 'a')
    second, second_sha = stage_generation(writer, previous, 'b')
    writer._generations.publish('123', first, first_sha, '2026-10-24', prior_sha, previous)
    with pytest.raises(ManagedDownloadError, match='compare-and-swap'):
        writer._generations.publish('123', second, second_sha, '2026-10-25', prior_sha, previous)
    assert writer.saved_receipts()[0][0] == first


@pytest.mark.parametrize('version', [0, 1.0, True, '1'])
def test_bad_index_version_is_not_silently_ignored(tmp_path, version):
    writer, previous, old_sha = new_writer(tmp_path)
    target, sha = stage_generation(writer, previous, 'a')
    writer._generations.publish('123', target, sha, '2026-10-24', old_sha, previous)
    index = writer.root / 'active-generations.json'
    data = json.loads(index.read_text())
    data['version'] = version
    index.write_text(json.dumps(data))
    with pytest.raises(ManagedDownloadError):
        writer.saved_receipts()


def test_failed_pointer_write_keeps_legacy_visible(tmp_path, monkeypatch):
    import pixivvault_nicegui.managed_generations as module
    writer, prior, old_sha = new_writer(tmp_path)
    target, sha = stage_generation(writer, prior, 'a')
    original = module._fsync_dir

    def fail(path):
        if path == writer._generations.stage_dir:
            raise OSError('injected staging fsync failure')
        return original(path)

    monkeypatch.setattr(module, '_fsync_dir', fail)
    with pytest.raises(ManagedDownloadError, match='publication failed'):
        writer._generations.publish('123', target, sha, '2026-10-24', old_sha, prior)
    assert writer.saved_receipts()[0][0] == prior
    assert (writer.root / target).exists()


def test_active_receipt_corruption_not_reinterpreted_as_empty(tmp_path):
    writer, prior, old_sha = new_writer(tmp_path)
    target, sha = stage_generation(writer, prior, 'a')
    writer._generations.publish('123', target, sha, '2026-10-24', old_sha, prior)
    (writer.root / target).write_bytes(b'corrupt')
    with pytest.raises(ManagedDownloadError):
        writer.saved_receipts()


def test_direct_publish_cannot_activate_malformed_receipt(tmp_path):
    writer, original, old_sha = new_writer(tmp_path)
    target, _ = stage_generation(writer, original, 'c')
    path = writer.root / target
    document = json.loads(path.read_text())
    document.pop('files')
    payload = json.dumps(document).encode('utf-8')
    path.write_bytes(payload)
    with pytest.raises(ManagedDownloadError):
        writer._generations.publish('123', target, hashlib.sha256(payload).hexdigest(),
                                    '2026-10-24', old_sha, original)
    assert writer.saved_receipts()[0][0] == original


def test_traversal_and_duplicated_index_keys_rejected(tmp_path):
    writer, original, sha = new_writer(tmp_path)
    with pytest.raises(ManagedDownloadError):
        writer._generations.publish('123', '../outside/receipt.json', sha,
                                    '2026-10-24', sha, original)
    target, digest = stage_generation(writer, original, 'd')
    writer._generations.publish('123', target, digest, '2026-10-24', sha, original)
    index = writer.root / 'active-generations.json'
    old = index.read_bytes()
    duplicated = old.replace(b'"version":2', b'"version":2,"version":2')
    assert duplicated != old, 'test must actually create a duplicate index key'
    index.write_bytes(duplicated)
    with pytest.raises(ManagedDownloadError):
        writer.saved_receipts()


def test_missing_active_generation_does_not_fall_back_to_legacy(tmp_path):
    writer, original, sha = new_writer(tmp_path)
    target, digest = stage_generation(writer, original, 'e')
    writer._generations.publish('123', target, digest, '2026-10-24', sha, original)
    shutil.rmtree((writer.root / target).parent)
    with pytest.raises(ManagedDownloadError):
        writer.saved_receipts()


def test_idempotent_pointer_replay_refuses_corrupted_image_page(tmp_path):
    writer, previous, prior_sha = new_writer(tmp_path)
    target, digest = stage_generation(writer, previous, 'a')
    writer._generations.publish('123', target, digest, '2026-10-24',
                                prior_sha, previous, 'f' * 64)
    folder = (writer.root / target).parent
    (folder / 'page-1.png').write_bytes(b'corrupt')
    with pytest.raises(ManagedDownloadError, match='generation receipt rejected'):
        writer._generations.publish('123', target, digest, '2026-10-24',
                                    prior_sha, previous, 'f' * 64)


def test_deleted_active_index_does_not_silently_reveal_old_receipt(tmp_path):
    writer, original, sha = new_writer(tmp_path)
    target, digest = stage_generation(writer, original, 'f')
    writer._generations.publish('123', target, digest, '2026-10-24', sha, original)
    assert writer.saved_receipts()[0][0] == target
    (writer.root / 'active-generations.json').unlink()
    with pytest.raises(ManagedDownloadError, match='index missing'):
        writer.saved_receipts()
    with pytest.raises(ManagedDownloadError, match='index missing'):
        ManagedPixivDownloads(writer.root, Client(), resolve_record=lambda _: None)


def test_legacy_only_root_without_index_remains_supported(tmp_path):
    writer, original, _ = new_writer(tmp_path)
    assert not (writer.root / 'active-generations.json').exists()
    reopened = ManagedPixivDownloads(writer.root, Client(), resolve_record=lambda _: None)
    assert reopened.saved_receipts()[0][0] == original


def test_version_one_active_index_remains_readable_and_can_upgrade(tmp_path):
    writer, previous, prior_sha = new_writer(tmp_path)
    first, sha = stage_generation(writer, previous, '1')
    writer._generations.publish('123', first, sha, '2026-10-24', prior_sha, previous)
    path = writer.root / 'active-generations.json'
    previous_index = json.loads(path.read_text())
    previous_index['version'] = 1
    path.write_text(json.dumps(previous_index))
    reopened = ManagedPixivDownloads(writer.root, Client(), resolve_record=lambda _: None)
    assert reopened.saved_receipts()[0][0] == first
    second, next_sha = stage_generation(reopened, previous, '2')
    reopened._generations.publish('123', second, next_sha, '2026-10-25', sha, first,
                                 'f' * 64)
    document = json.loads(path.read_text())
    assert document['version'] == 2
    assert document['entries']['123']['accepted_fingerprint'] == 'f' * 64
