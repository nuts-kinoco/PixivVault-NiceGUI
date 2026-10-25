"""Synthetic M2 persistence faults; no network, database, or real user data."""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
from threading import Barrier, Event
from types import SimpleNamespace
import traceback
import zipfile

from PIL import Image
import pytest

from pixivvault_nicegui import ugoira_storage as subject
from pixivvault_nicegui.media_parity_policy import MediaPayloadRejected, parse_ugoira_manifest
from pixivvault_nicegui.ugoira_archive import inspect_ugoira_archive
from pixivvault_nicegui.ugoira_storage import UgoiraFrameStore, UgoiraStorageError


def _png(size=(4, 3)):
    output = BytesIO()
    with Image.new('RGB', size, (33, 66, 99)) as image:
        image.save(output, format='PNG')
    return output.getvalue()


def _fixture(*, bad_second=False, first_name='000001.png', compression=zipfile.ZIP_DEFLATED,
             force_zip64=False):
    files = [('000001.png', _png((5, 4))), ('000000.png', _png())]
    output = BytesIO()
    with zipfile.ZipFile(output, 'w', compression=compression) as writer:
        # Physical order deliberately differs from playback order.
        for filename, data in reversed(files):
            name = first_name if filename == '000001.png' else filename
            if bad_second and filename == '000000.png':
                data = b'synthetic non-image'
            with writer.open(name, 'w', force_zip64=force_zip64) as stream:
                stream.write(data)
    manifest = parse_ugoira_manifest({
        'originalSrc': 'https://i.pximg.net/img-zip-ugoira/synthetic/123_ugoira.zip',
        'frames': [{'file': '000001.png', 'delay': 50}, {'file': '000000.png', 'delay': 180}],
    }, work_id='123')
    return output.getvalue(), manifest, dict(files)


def _tree_files(folder):
    return {path.relative_to(folder).as_posix(): path.read_bytes()
            for path in folder.rglob('*') if path.is_file()}


@pytest.mark.parametrize('compression', [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
@pytest.mark.parametrize('force_zip64', [False, True])
def test_save_exact_frames_metadata_and_receipt_after_atomic_publication(
        tmp_path, monkeypatch, compression, force_zip64):
    raw, manifest, files = _fixture(compression=compression, force_zip64=force_zip64)
    store = UgoiraFrameStore(tmp_path / 'isolated')
    assert not store.root.exists()
    publish = subject._publish_directory
    events = []
    verify = subject._verify_contents
    def observed_verify(folder, *args):
        events.append('verify-stage' if folder.parent == store.incomplete else 'verify-published')
        return verify(folder, *args)
    def observed_publish(stage, target):
        assert events == ['verify-stage']
        assert list(store.frames.iterdir()) == []
        assert all((stage / name).read_bytes() == data for name, data in files.items())
        assert (stage / 'receipt.json').is_file()
        assert not (stage / subject.COMPLETION).exists()
        events.append('publish')
        publish(stage, target)
    monkeypatch.setattr(subject, '_verify_contents', observed_verify)
    monkeypatch.setattr(subject, '_publish_directory', observed_publish)
    result = store.save(raw, manifest)
    assert events == ['verify-stage', 'publish', 'verify-published']
    assert result.frame_count == 2
    assert result.duration_ms == 230
    assert result.total_uncompressed_bytes == sum(map(len, files.values()))
    receipt_path = store.root / result.relative_receipt
    receipt = json.loads(receipt_path.read_bytes())
    assert hashlib.sha256(receipt_path.read_bytes()).hexdigest() == result.receipt_sha256
    assert [(item['file'], item['delay'], item['width'], item['height']) for item in receipt['frames']] == [
        ('000001.png', 50, 5, 4), ('000000.png', 180, 4, 3)]
    for item in receipt['frames']:
        assert (receipt_path.parent / item['file']).read_bytes() == files[item['file']]
        assert item['bytes'] == len(files[item['file']])
        assert item['sha256'] == hashlib.sha256(files[item['file']]).hexdigest()
    metadata = (receipt_path.parent / 'ugoira_meta.json').read_bytes()
    assert json.loads(metadata) == {'frames': [{'file': '000001.png', 'delay': 50},
                                              {'file': '000000.png', 'delay': 180}]}
    assert manifest.archive_url.encode() not in receipt_path.read_bytes() + metadata
    assert result.relative_receipt not in repr(result)
    assert result.receipt_sha256 not in repr(result)
    assert list(store.incomplete.iterdir()) == []
    assert set(_tree_files(receipt_path.parent)) == set(files) | {'receipt.json', 'ugoira_meta.json', subject.COMPLETION}


def test_full_validation_finishes_before_any_root_or_frame_write(tmp_path, monkeypatch):
    raw, manifest, _ = _fixture(bad_second=True)
    store = UgoiraFrameStore(tmp_path / 'isolated')
    def forbidden(*args, **kwargs):
        pytest.fail('storage prepared before all frames passed inspection')
    monkeypatch.setattr(store, '_prepare_root', forbidden)
    monkeypatch.setattr(subject, '_write', forbidden)
    with pytest.raises(MediaPayloadRejected):
        store.save(raw, manifest)
    assert not store.root.exists()


@pytest.mark.parametrize('filename', ['../escape.png', '/escape.png', 'C:escape.png', 'CON.png', 'nested/a.png'])
def test_unsafe_archive_names_never_reach_a_writer(tmp_path, filename):
    raw, manifest, _ = _fixture(first_name=filename)
    store = UgoiraFrameStore(tmp_path / 'isolated')
    with pytest.raises(MediaPayloadRejected):
        store.save(raw, manifest)
    assert list(tmp_path.iterdir()) == []


def test_inspection_dto_cannot_replace_raw_archive_validation(tmp_path):
    raw, manifest, _ = _fixture()
    store = UgoiraFrameStore(tmp_path / 'isolated')
    with pytest.raises(MediaPayloadRejected):
        store.save(inspect_ugoira_archive(raw, manifest), manifest)
    assert not store.root.exists()


@pytest.mark.parametrize('foreign', ['empty', 'database', 'wrong-marker', 'foreign-marker'])
def test_existing_unowned_roots_are_preserved(tmp_path, foreign):
    root = tmp_path / 'foreign'
    root.mkdir()
    if foreign == 'database':
        (root / 'pixiv.db').write_bytes(b'synthetic legacy database sentinel')
    elif foreign == 'wrong-marker':
        (root / subject.MARKER).write_bytes(b'synthetic wrong marker')
    elif foreign == 'foreign-marker':
        (root / '.pixivvault-managed-pixiv-downloads.json').write_bytes(b'synthetic other subsystem')
    before = _tree_files(root)
    raw, manifest, _ = _fixture()
    with pytest.raises(UgoiraStorageError):
        UgoiraFrameStore(root).save(raw, manifest)
    assert _tree_files(root) == before
    assert set(path.name for path in root.iterdir()) == set(before)


def test_reopen_and_repeat_keep_old_generations_immutable_and_no_db_is_created(tmp_path):
    root = tmp_path / 'isolated'
    raw, manifest, _ = _fixture()
    first = UgoiraFrameStore(root).save(raw, manifest)
    first_folder = (root / first.relative_receipt).parent
    before = _tree_files(first_folder)
    second = UgoiraFrameStore(root).save(raw, manifest)
    assert second.relative_receipt != first.relative_receipt
    assert _tree_files(first_folder) == before
    assert len(list((root / 'frames').iterdir())) == 2
    assert not list(root.rglob('*.db'))
    assert not list(root.rglob('*.sqlite3'))


@pytest.mark.parametrize('empty_target', [False, True])
def test_atomic_publish_never_replaces_existing_even_empty_directory(tmp_path, monkeypatch, empty_target):
    root = tmp_path / 'isolated'
    store = UgoiraFrameStore(root)
    store._prepare_root()  # Synthetic owned-root setup only.
    token = '1' * 32
    target = store.frames / token
    target.mkdir()
    if not empty_target:
        (target / 'user-sentinel.txt').write_bytes(b'synthetic retained user output')
    before, identity = _tree_files(target), subject._directory_identity(target)
    monkeypatch.setattr(subject, 'uuid4', lambda: SimpleNamespace(hex=token))
    raw, manifest, _ = _fixture()
    with pytest.raises(UgoiraStorageError):
        store.save(raw, manifest)
    assert _tree_files(target) == before
    assert subject._directory_identity(target) == identity
    assert (store.incomplete / token / 'receipt.json').is_file()


@pytest.mark.parametrize('failure_name', ['000000.png', 'ugoira_meta.json', 'receipt.json'])
def test_write_failure_retains_only_incomplete_and_preserves_older_success(tmp_path, monkeypatch, failure_name):
    raw, manifest, _ = _fixture()
    store = UgoiraFrameStore(tmp_path / 'isolated')
    first = store.save(raw, manifest)
    published = _tree_files(store.frames)
    write = subject._write
    def fail_second(path, content):
        if path.name == failure_name:
            raise OSError('synthetic-cookie=secret; private/path')
        write(path, content)
    monkeypatch.setattr(subject, '_write', fail_second)
    with pytest.raises(UgoiraStorageError) as caught:
        store.save(raw, manifest)
    assert str(caught.value) == 'ugoira frame storage failed'
    assert 'synthetic-cookie' not in ''.join(traceback.format_exception(caught.value))
    assert caught.value.__suppress_context__ is True
    assert _tree_files(store.frames) == published
    assert (store.root / first.relative_receipt).is_file()
    pending = list(store.incomplete.iterdir())
    assert len(pending) == 1
    assert (pending[0] / '000001.png').is_file()
    assert not (pending[0] / 'receipt.json').exists()


def test_saved_byte_tampering_blocks_publication(tmp_path, monkeypatch):
    raw, manifest, _ = _fixture()
    store = UgoiraFrameStore(tmp_path / 'isolated')
    write = subject._write
    def corrupt(path, content):
        write(path, content)
        if path.name == '000001.png':
            path.write_bytes(b'synthetic disk corruption')
    monkeypatch.setattr(subject, '_write', corrupt)
    with pytest.raises(UgoiraStorageError):
        store.save(raw, manifest)
    assert list(store.frames.iterdir()) == []
    assert len(list(store.incomplete.iterdir())) == 1


def test_publish_failure_never_returns_complete_or_deletes_staged_evidence(tmp_path, monkeypatch):
    raw, manifest, _ = _fixture()
    store = UgoiraFrameStore(tmp_path / 'isolated')
    def fail(*args):
        raise OSError('synthetic private filename')
    monkeypatch.setattr(subject, '_publish_directory', fail)
    with pytest.raises(UgoiraStorageError):
        store.save(raw, manifest)
    assert list(store.frames.iterdir()) == []
    assert len(list(store.incomplete.glob('*/receipt.json'))) == 1


def test_failure_after_rename_never_returns_success_or_rolls_back_valid_orphan(tmp_path, monkeypatch):
    raw, manifest, _ = _fixture()
    store = UgoiraFrameStore(tmp_path / 'isolated')
    fsync = subject._fsync_dir
    def fail_after_publish(folder):
        if folder == store.frames:
            raise OSError('synthetic directory flush failure')
        fsync(folder)
    monkeypatch.setattr(subject, '_fsync_dir', fail_after_publish)
    with pytest.raises(UgoiraStorageError):
        store.save(raw, manifest)
    assert list(store.incomplete.iterdir()) == []
    assert len(list(store.frames.glob('*/receipt.json'))) == 1


@pytest.mark.parametrize('component', ['root', 'ancestor'])
def test_link_or_junction_chain_rejected_without_writes(tmp_path, monkeypatch, component):
    root = tmp_path / 'isolated'
    blocked = root if component == 'root' else tmp_path
    is_symlink = Path.is_symlink
    monkeypatch.setattr(Path, 'is_symlink', lambda path: path == blocked or is_symlink(path))
    with pytest.raises(UgoiraStorageError):
        UgoiraFrameStore(root)
    assert list(tmp_path.iterdir()) == []


def test_reparse_attribute_rejected_without_requiring_windows_link_privilege(tmp_path, monkeypatch):
    root = tmp_path / 'isolated'
    lstat = Path.lstat
    def reparse(path, *args, **kwargs):
        if path == tmp_path:
            return SimpleNamespace(st_file_attributes=0x400, st_mode=lstat(path).st_mode)
        return lstat(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', reparse)
    with pytest.raises(UgoiraStorageError):
        UgoiraFrameStore(root)
    assert list(tmp_path.iterdir()) == []


def test_pinned_root_identity_change_blocks_reuse_before_any_new_write(tmp_path, monkeypatch):
    raw, manifest, _ = _fixture()
    store = UgoiraFrameStore(tmp_path / 'isolated')
    store.save(raw, manifest)
    before = _tree_files(store.root)
    identity = subject._directory_identity
    def replaced(path):
        device, inode = identity(path)
        return (device, inode + 1) if path == store.root else (device, inode)
    monkeypatch.setattr(subject, '_directory_identity', replaced)
    def forbidden(*args):
        pytest.fail('write attempted after the root identity changed')
    monkeypatch.setattr(subject, '_write', forbidden)
    with pytest.raises(UgoiraStorageError):
        store.save(raw, manifest)
    assert _tree_files(store.root) == before


def test_relative_root_is_rejected():
    with pytest.raises(UgoiraStorageError, match='^ugoira frame storage failed$'):
        UgoiraFrameStore(Path('relative-destination'))


def test_hardlinked_frame_is_rejected_before_publication(tmp_path, monkeypatch):
    raw, manifest, files = _fixture()
    store = UgoiraFrameStore(tmp_path / 'isolated')
    write = subject._write
    alias = tmp_path / 'synthetic-hardlink.png'
    def hardlink(path, content):
        write(path, content)
        if path.name == '000001.png':
            os.link(path, alias)
    monkeypatch.setattr(subject, '_write', hardlink)
    with pytest.raises(UgoiraStorageError):
        store.save(raw, manifest)
    assert list(store.frames.iterdir()) == []
    assert alias.read_bytes() == files['000001.png']


def test_budget_accepts_existing_regular_files_but_rejects_retained_hardlinks(tmp_path):
    raw, manifest, files = _fixture()
    root = tmp_path / 'isolated'
    store = UgoiraFrameStore(root)
    first = store.save(raw, manifest)
    first_folder = (root / first.relative_receipt).parent
    first_before = _tree_files(first_folder)

    # This second save must scan and accept the first generation's ordinary
    # files; a first save alone would not exercise _reserve_budget's file stat.
    second = UgoiraFrameStore(root).save(raw, manifest)
    assert second.relative_receipt != first.relative_receipt
    assert _tree_files(first_folder) == first_before
    second_folder = (root / second.relative_receipt).parent
    retained_frame = second_folder / '000001.png'

    # Add a real sibling hardlink so the retained frame's link count is > 1.
    alias = tmp_path / 'synthetic-retained-hardlink.png'
    os.link(retained_frame, alias)
    before = _tree_files(root)
    assert retained_frame.read_bytes() == files['000001.png']
    assert alias.read_bytes() == files['000001.png']
    assert retained_frame.stat().st_nlink > 1

    with pytest.raises(UgoiraStorageError):
        UgoiraFrameStore(root).save(raw, manifest)

    # Reservation must reject before creating another generation, and neither
    # the existing generation nor the external hardlink may be overwritten.
    assert _tree_files(root) == before
    assert retained_frame.read_bytes() == files['000001.png']
    assert alias.read_bytes() == files['000001.png']
    assert len(list((root / 'frames').iterdir())) == 2
    assert list((root / 'incomplete').iterdir()) == []


def test_exclusive_frame_write_preserves_an_injected_existing_file(tmp_path, monkeypatch):
    raw, manifest, _ = _fixture()
    store = UgoiraFrameStore(tmp_path / 'isolated')
    write = subject._write
    occupied = []
    def collision(path, content):
        if path.name == '000001.png':
            path.write_bytes(b'synthetic existing file sentinel')
            occupied.append(path)
        write(path, content)
    monkeypatch.setattr(subject, '_write', collision)
    with pytest.raises(UgoiraStorageError):
        store.save(raw, manifest)
    assert occupied[0].read_bytes() == b'synthetic existing file sentinel'
    assert list(store.frames.iterdir()) == []


def _planned_bytes(raw, manifest):
    inspected = inspect_ugoira_archive(raw, manifest)
    metadata, receipt = subject._metadata(inspected)
    completion = subject._completion_bytes('0' * 32, hashlib.sha256(receipt).hexdigest())
    return inspected.total_uncompressed_bytes + len(metadata) + len(receipt) + len(completion)


def _published_after_fault(store, monkeypatch, raw, manifest):
    fsync = subject._fsync_dir
    def fail(folder):
        if folder == store.frames:
            raise OSError('synthetic pre-completion flush fault')
        fsync(folder)
    with monkeypatch.context() as changes:
        changes.setattr(subject, '_fsync_dir', fail)
        with pytest.raises(UgoiraStorageError):
            store.save(raw, manifest)
    folder, = store.frames.iterdir()
    return folder, f'frames/{folder.name}/receipt.json'


def test_reconciliation_is_read_only_and_requires_full_completion_proof(tmp_path, monkeypatch):
    raw, manifest, _ = _fixture(force_zip64=True)
    root = tmp_path / 'isolated'
    saved = UgoiraFrameStore(root).save(raw, manifest)
    before = _tree_files(root)
    reopened = UgoiraFrameStore(root)
    def forbidden(*args, **kwargs):
        pytest.fail('reconcile attempted a storage mutation')
    monkeypatch.setattr(reopened, '_prepare_root', forbidden)
    monkeypatch.setattr(subject, '_write', forbidden)
    monkeypatch.setattr(subject, '_publish_directory', forbidden)
    result = reopened.reconcile(saved.relative_receipt, expected_sha256=saved.receipt_sha256)
    assert result.state == 'complete'
    assert result.saved == saved
    assert result.saved.relative_receipt not in repr(result)
    assert _tree_files(root) == before


def test_post_rename_failure_and_old_unsealed_output_are_uncommitted(tmp_path, monkeypatch):
    raw, manifest, _ = _fixture()
    store = UgoiraFrameStore(tmp_path / 'isolated')
    folder, relative = _published_after_fault(store, monkeypatch, raw, manifest)
    assert (folder / 'receipt.json').is_file()
    assert not (folder / subject.COMPLETION).exists()
    before = _tree_files(store.root)
    result = UgoiraFrameStore(store.root).reconcile(relative)
    assert result.state == 'uncommitted' and result.saved is None
    assert _tree_files(store.root) == before


def test_missing_generation_never_becomes_complete(tmp_path):
    store = UgoiraFrameStore(tmp_path / 'isolated')
    store._prepare_root()
    before = _tree_files(store.root)
    result = store.reconcile('frames/' + 'a' * 32 + '/receipt.json')
    assert result.state == 'missing' and result.saved is None
    assert _tree_files(store.root) == before


@pytest.mark.parametrize('relative', ['../receipt.json', '/receipt.json', 'frames/' + 'a' * 32,
                                      'frames/../receipt.json', 'frames/' + 'a' * 32 + '/receipt.json/extra'])
def test_reconcile_rejects_folder_presence_or_unsafe_receipt_reference(tmp_path, relative):
    store = UgoiraFrameStore(tmp_path / 'isolated')
    store._prepare_root()
    before = _tree_files(store.root)
    with pytest.raises(UgoiraStorageError):
        store.reconcile(relative)
    assert _tree_files(store.root) == before


@pytest.mark.parametrize('damage', ['frame', 'receipt', 'metadata', 'proof', 'extra-file', 'expected-hash'])
def test_reconciliation_rejects_corrupt_or_mismatched_evidence(tmp_path, damage):
    raw, manifest, _ = _fixture()
    store = UgoiraFrameStore(tmp_path / 'isolated')
    saved = store.save(raw, manifest)
    folder = (store.root / saved.relative_receipt).parent
    expected = saved.receipt_sha256
    if damage == 'frame':
        (folder / '000001.png').write_bytes(b'synthetic corrupt frame')
    elif damage == 'receipt':
        (folder / 'receipt.json').write_bytes(b'{"kind":"synthetic corruption"}')
    elif damage == 'metadata':
        (folder / 'ugoira_meta.json').write_bytes(b'{"frames":[]}')
    elif damage == 'proof':
        (folder / subject.COMPLETION).write_bytes(b'{"version":1}')
    elif damage == 'extra-file':
        (folder / 'unexpected.txt').write_bytes(b'synthetic extra')
    else:
        expected = '0' * 64
    before = _tree_files(store.root)
    with pytest.raises(UgoiraStorageError):
        store.reconcile(saved.relative_receipt, expected_sha256=expected)
    assert _tree_files(store.root) == before


@pytest.mark.parametrize('damage', ['dimensions', 'frame-path', 'size', 'delay', 'duplicate-key'])
def test_reconciliation_revalidates_even_self_consistent_receipt_and_proof(tmp_path, damage):
    raw, manifest, _ = _fixture()
    store = UgoiraFrameStore(tmp_path / 'isolated')
    saved = store.save(raw, manifest)
    folder = (store.root / saved.relative_receipt).parent
    receipt = json.loads((folder / 'receipt.json').read_bytes())
    if damage == 'dimensions':
        receipt['frames'][0]['width'] = 1  # Bounded but false image dimensions.
    elif damage == 'frame-path':
        receipt['frames'][0]['file'] = '../unsafe.png'
    elif damage == 'size':
        receipt['frames'][0]['bytes'] = subject.MAX_SINGLE_FRAME_BYTES + 1
    elif damage == 'delay':
        receipt['frames'][0]['delay'] = True
    payload = (json.dumps(receipt, ensure_ascii=True, separators=(',', ':')) + '\n').encode()
    if damage == 'duplicate-key':
        payload = payload.replace(b'{"version":1,', b'{"version":1,"version":1,', 1)
    digest = hashlib.sha256(payload).hexdigest()
    (folder / 'receipt.json').write_bytes(payload)
    (folder / subject.COMPLETION).write_bytes(subject._completion_bytes(folder.name, digest))
    with pytest.raises(UgoiraStorageError):
        store.reconcile(saved.relative_receipt)


def test_completion_write_failure_does_not_authorize_existing_published_folder(tmp_path, monkeypatch):
    raw, manifest, _ = _fixture()
    store = UgoiraFrameStore(tmp_path / 'isolated')
    write = subject._write
    def fail(path, payload):
        if path.name == subject.COMPLETION:
            raise OSError('synthetic completion write fault')
        write(path, payload)
    monkeypatch.setattr(subject, '_write', fail)
    with pytest.raises(UgoiraStorageError):
        store.save(raw, manifest)
    folder, = store.frames.iterdir()
    result = store.reconcile(f'frames/{folder.name}/receipt.json')
    assert result.state == 'uncommitted' and result.saved is None


def test_partial_completion_write_is_never_accepted(tmp_path, monkeypatch):
    raw, manifest, _ = _fixture()
    store = UgoiraFrameStore(tmp_path / 'isolated')
    write = subject._write
    def fail(path, payload):
        if path.name == subject.COMPLETION:
            path.write_bytes(payload[:len(payload) // 2])
            raise OSError('synthetic partial completion fault')
        write(path, payload)
    monkeypatch.setattr(subject, '_write', fail)
    with pytest.raises(UgoiraStorageError):
        store.save(raw, manifest)
    folder, = store.frames.iterdir()
    before = _tree_files(store.root)
    with pytest.raises(UgoiraStorageError):
        store.reconcile(f'frames/{folder.name}/receipt.json')
    assert _tree_files(store.root) == before


def test_uncertain_failure_after_complete_proof_can_only_be_resolved_by_verification(tmp_path, monkeypatch):
    raw, manifest, _ = _fixture()
    store = UgoiraFrameStore(tmp_path / 'isolated')
    fsync = subject._fsync_dir
    def fail(folder):
        if folder.parent == store.frames and (folder / subject.COMPLETION).exists():
            raise OSError('synthetic acknowledgement boundary fault')
        fsync(folder)
    monkeypatch.setattr(subject, '_fsync_dir', fail)
    with pytest.raises(UgoiraStorageError):
        store.save(raw, manifest)
    folder, = store.frames.iterdir()
    before = _tree_files(store.root)
    result = UgoiraFrameStore(store.root).reconcile(f'frames/{folder.name}/receipt.json')
    assert result.state == 'complete' and result.saved is not None
    assert _tree_files(store.root) == before


def test_reconcile_is_bounded_before_large_receipt_or_completion_read(tmp_path, monkeypatch):
    raw, manifest, _ = _fixture()
    store = UgoiraFrameStore(tmp_path / 'isolated')
    saved = store.save(raw, manifest)
    monkeypatch.setattr(subject, 'MAX_RECEIPT_BYTES', 1)
    with pytest.raises(UgoiraStorageError):
        store.reconcile(saved.relative_receipt)
    monkeypatch.setattr(subject, 'MAX_RECEIPT_BYTES', 2 * 1024 * 1024)
    monkeypatch.setattr(subject, 'MAX_COMPLETION_BYTES', 1)
    with pytest.raises(UgoiraStorageError):
        store.reconcile(saved.relative_receipt)


@pytest.mark.parametrize('budget', ['generations', 'files', 'bytes'])
def test_full_save_reservation_fits_exact_budget_and_rejects_one_more_save(tmp_path, monkeypatch, budget):
    raw, manifest, _ = _fixture()
    limits = {'generations': ('MAX_RETAINED_GENERATIONS', 1),
              'files': ('MAX_RETAINED_FILES', len(manifest.frames) + 3),
              'bytes': ('MAX_RETAINED_BYTES', _planned_bytes(raw, manifest))}
    name, amount = limits[budget]
    monkeypatch.setattr(subject, name, amount)
    store = UgoiraFrameStore(tmp_path / 'isolated')
    saved = store.save(raw, manifest)
    assert store.reconcile(saved.relative_receipt).state == 'complete'
    before = _tree_files(store.root)
    with pytest.raises(UgoiraStorageError):
        UgoiraFrameStore(store.root).save(raw, manifest)
    assert _tree_files(store.root) == before
    assert list(store.incomplete.iterdir()) == []


@pytest.mark.parametrize('budget', ['generations', 'files', 'bytes'])
def test_reservation_rejects_before_stage_or_payload_when_budget_is_short(tmp_path, monkeypatch, budget):
    raw, manifest, _ = _fixture()
    limits = {'generations': ('MAX_RETAINED_GENERATIONS', 0),
              'files': ('MAX_RETAINED_FILES', len(manifest.frames) + 2),
              'bytes': ('MAX_RETAINED_BYTES', _planned_bytes(raw, manifest) - 1)}
    name, amount = limits[budget]
    monkeypatch.setattr(subject, name, amount)
    store = UgoiraFrameStore(tmp_path / 'isolated')
    write = subject._write
    def guard(path, payload):
        if path.name != subject.MARKER:
            pytest.fail('payload written before quota reservation passed')
        write(path, payload)
    monkeypatch.setattr(subject, '_write', guard)
    with pytest.raises(UgoiraStorageError):
        store.save(raw, manifest)
    assert list(store.frames.iterdir()) == []
    assert list(store.incomplete.iterdir()) == []


def test_repeated_failed_empty_stages_eventually_refuse_without_deletion(tmp_path, monkeypatch):
    raw, manifest, _ = _fixture()
    monkeypatch.setattr(subject, 'MAX_RETAINED_GENERATIONS', 2)
    store = UgoiraFrameStore(tmp_path / 'isolated')
    write = subject._write
    def fail(path, payload):
        if path.name.endswith('.png'):
            raise OSError('synthetic first-frame failure')
        write(path, payload)
    monkeypatch.setattr(subject, '_write', fail)
    for _ in range(2):
        with pytest.raises(UgoiraStorageError):
            store.save(raw, manifest)
    folders = {path.name for path in store.incomplete.iterdir()}
    assert len(folders) == 2
    with pytest.raises(UgoiraStorageError):
        UgoiraFrameStore(store.root).save(raw, manifest)
    assert {path.name for path in store.incomplete.iterdir()} == folders
    assert list(store.frames.iterdir()) == []


def test_retained_partial_bytes_count_towards_next_reservation(tmp_path, monkeypatch):
    raw, manifest, _ = _fixture()
    store = UgoiraFrameStore(tmp_path / 'isolated')
    store._prepare_root()
    pending = store.incomplete / ('a' * 32)
    pending.mkdir()
    sentinel = pending / 'synthetic-partial.bin'
    sentinel.write_bytes(b'12345678')
    monkeypatch.setattr(subject, 'MAX_RETAINED_BYTES', _planned_bytes(raw, manifest) + 7)
    before = _tree_files(store.root)
    with pytest.raises(UgoiraStorageError):
        store.save(raw, manifest)
    assert sentinel.read_bytes() == b'12345678'
    # Root preparation already bootstraps the lock; quota refusal changes no
    # bytes, including the fixed owner byte outside payload accounting.
    assert _tree_files(store.root) == before
    monkeypatch.setattr(subject, 'MAX_RETAINED_BYTES', _planned_bytes(raw, manifest) + 8)
    assert store.save(raw, manifest).frame_count == 2
    assert sentinel.read_bytes() == b'12345678'


def test_uncommitted_post_rename_orphan_cannot_escape_generation_budget(tmp_path, monkeypatch):
    raw, manifest, _ = _fixture()
    store = UgoiraFrameStore(tmp_path / 'isolated')
    folder, _ = _published_after_fault(store, monkeypatch, raw, manifest)
    monkeypatch.setattr(subject, 'MAX_RETAINED_GENERATIONS', 1)
    before = _tree_files(folder)
    with pytest.raises(UgoiraStorageError):
        UgoiraFrameStore(store.root).save(raw, manifest)
    assert _tree_files(folder) == before
    assert list(store.incomplete.iterdir()) == []


def test_process_owner_lock_prevents_a_second_instance_from_reserving_same_budget(tmp_path, monkeypatch):
    raw, manifest, _ = _fixture()
    store = UgoiraFrameStore(tmp_path / 'isolated')
    store._prepare_root()
    monkeypatch.setattr(subject, 'MAX_RETAINED_GENERATIONS', 1)
    other = UgoiraFrameStore(store.root)
    reserve = store._reserve_budget
    def attempted_concurrent_reservation(*args):
        with pytest.raises(UgoiraStorageError):
            other.save(raw, manifest)
        reserve(*args)
    monkeypatch.setattr(store, '_reserve_budget', attempted_concurrent_reservation)
    assert store.save(raw, manifest).frame_count == 2
    assert len(list(store.frames.iterdir())) == 1
    assert list(store.incomplete.iterdir()) == []


def test_resource_budget_constants_are_finite():
    assert 0 < subject.MAX_RETAINED_GENERATIONS <= 128
    assert 0 < subject.MAX_RETAINED_FILES <= 20_000
    assert 0 < subject.MAX_RETAINED_BYTES <= 1024 * 1024 * 1024


def test_empty_retained_files_count_towards_next_reservation(tmp_path, monkeypatch):
    raw, manifest, _ = _fixture()
    store = UgoiraFrameStore(tmp_path / 'isolated')
    store._prepare_root()
    pending = store.incomplete / ('b' * 32)
    pending.mkdir()
    (pending / 'first.partial').write_bytes(b'')
    (pending / 'second.partial').write_bytes(b'')
    planned_files = len(manifest.frames) + 3
    monkeypatch.setattr(subject, 'MAX_RETAINED_FILES', planned_files + 1)
    with pytest.raises(UgoiraStorageError):
        store.save(raw, manifest)
    assert set(path.name for path in pending.iterdir()) == {'first.partial', 'second.partial'}
    assert list(store.frames.iterdir()) == []
    monkeypatch.setattr(subject, 'MAX_RETAINED_FILES', planned_files + 2)
    assert store.save(raw, manifest).frame_count == 2
    assert set(path.name for path in pending.iterdir()) == {'first.partial', 'second.partial'}


def test_completion_proof_is_bound_to_its_generation_not_just_matching_frame_bytes(tmp_path):
    raw, manifest, _ = _fixture()
    store = UgoiraFrameStore(tmp_path / 'isolated')
    first = store.save(raw, manifest)
    second = store.save(raw, manifest)
    assert first.receipt_sha256 == second.receipt_sha256
    first_folder = (store.root / first.relative_receipt).parent
    second_folder = (store.root / second.relative_receipt).parent
    (second_folder / subject.COMPLETION).write_bytes((first_folder / subject.COMPLETION).read_bytes())
    with pytest.raises(UgoiraStorageError):
        store.reconcile(second.relative_receipt)
    assert store.reconcile(first.relative_receipt).state == 'complete'


def test_owner_initialization_waits_for_os_lock_and_writes_only_once(tmp_path, monkeypatch):
    root = tmp_path / 'private-owner'
    root.mkdir()
    acquire, write = subject._acquire_owner_lock, os.write
    held = False
    writes = []
    def observed_acquire(descriptor):
        nonlocal held
        acquire(descriptor)
        held = True
    def observed_write(descriptor, payload):
        assert held, 'owner byte written before acquiring the OS lock'
        writes.append(payload)
        return write(descriptor, payload)
    monkeypatch.setattr(subject, '_acquire_owner_lock', observed_acquire)
    monkeypatch.setattr(subject.os, 'write', observed_write)
    for _ in range(2):
        with subject._owner_lock(root):
            pass
        held = False
    assert writes == [b'0']
    assert (root / subject._OWNER).read_bytes() == b'0'


def test_owner_lock_is_explicitly_released_on_normal_and_exceptional_exit(tmp_path, monkeypatch):
    root = tmp_path / 'private-owner'
    root.mkdir()
    release, calls = subject._release_owner_lock, []
    def observed_release(descriptor):
        calls.append(os.fstat(descriptor).st_size)
        return release(descriptor)
    monkeypatch.setattr(subject, '_release_owner_lock', observed_release)

    with subject._owner_lock(root):
        pass
    with pytest.raises(RuntimeError, match='synthetic body failure'):
        with subject._owner_lock(root):
            raise RuntimeError('synthetic body failure')

    assert calls == [1, 1]
    # A third acquisition proves neither exit path left the byte-range lock held.
    with subject._owner_lock(root):
        pass
    assert (root / subject._OWNER).read_bytes() == b'0'


def test_denied_os_lock_never_initializes_the_empty_owner_file(tmp_path, monkeypatch):
    root = tmp_path / 'private-owner'
    root.mkdir()
    def denied(descriptor):
        raise OSError('synthetic denied byte-range lock')
    monkeypatch.setattr(subject, '_acquire_owner_lock', denied)
    with pytest.raises(OSError):
        with subject._owner_lock(root):
            pytest.fail('failed acquisition yielded ownership')
    assert (root / subject._OWNER).read_bytes() == b''


def test_stale_empty_observation_is_rechecked_under_ownership(tmp_path, monkeypatch):
    root = tmp_path / 'private-owner'
    root.mkdir()
    owner = root / subject._OWNER
    acquire = subject._acquire_owner_lock
    def prior_owner_finished(descriptor):
        assert owner.read_bytes() == b''
        # Inject the state left by another initializer between our first
        # fstat and acquisition. Its one valid byte must not be appended to.
        owner.write_bytes(b'0')
        acquire(descriptor)
    def forbidden(*args):
        pytest.fail('stale EOF triggered a second initialization write')
    monkeypatch.setattr(subject, '_acquire_owner_lock', prior_owner_finished)
    monkeypatch.setattr(subject.os, 'write', forbidden)
    with subject._owner_lock(root):
        pass
    assert owner.read_bytes() == b'0'


def test_two_initializers_cannot_append_or_corrupt_the_owner_byte(tmp_path, monkeypatch):
    root = tmp_path / 'private-owner'
    root.mkdir()
    barrier, release = Barrier(2), Event()
    acquire = subject._acquire_owner_lock
    def simultaneous(descriptor):
        barrier.wait(timeout=8)
        acquire(descriptor)
    monkeypatch.setattr(subject, '_acquire_owner_lock', simultaneous)
    def initialize():
        try:
            with subject._owner_lock(root):
                assert release.wait(8), 'test did not release lock holder'
            return 'owned'
        except (OSError, UgoiraStorageError):
            return 'busy'
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(initialize) for _ in range(2)]
        try:
            done, _ = wait(futures, timeout=8, return_when=FIRST_COMPLETED)
            assert len(done) == 1
            assert next(iter(done)).result() == 'busy'
        finally:
            release.set()
        outcomes = [future.result(timeout=8) for future in futures]
    assert sorted(outcomes) == ['busy', 'owned']
    assert (root / subject._OWNER).read_bytes() == b'0'
    assert (root / subject._OWNER).stat().st_size == 1


def test_failed_initialization_leaves_empty_lock_for_writer_only_recovery(tmp_path, monkeypatch):
    root = tmp_path / 'private-owner'
    root.mkdir()
    def fault(*args):
        raise OSError('synthetic initializer write fault')
    with monkeypatch.context() as changes:
        changes.setattr(subject.os, 'write', fault)
        with pytest.raises(OSError):
            with subject._owner_lock(root):
                pytest.fail('failed initialization yielded ownership')
    assert (root / subject._OWNER).read_bytes() == b''
    with pytest.raises(UgoiraStorageError):
        with subject._owner_lock(root, create=False):
            pytest.fail('reader repaired an uninitialized owner file')
    assert (root / subject._OWNER).read_bytes() == b''
    with subject._owner_lock(root):
        pass
    assert (root / subject._OWNER).read_bytes() == b'0'


@pytest.mark.parametrize('payload', [b'00', b'X'])
def test_owner_corruption_is_preserved_and_never_automatically_repaired(tmp_path, payload):
    root = tmp_path / 'private-owner'
    root.mkdir()
    owner = root / subject._OWNER
    owner.write_bytes(payload)
    for create in (False, True):
        with pytest.raises(UgoiraStorageError):
            with subject._owner_lock(root, create=create):
                pytest.fail('corrupt owner file yielded ownership')
        assert owner.read_bytes() == payload


@pytest.mark.parametrize('empty_owner', [False, True])
def test_reconcile_never_creates_or_initializes_missing_coordination(tmp_path, empty_owner):
    root = tmp_path / 'private-owner'
    root.mkdir()
    (root / subject.MARKER).write_bytes(subject._MARKER_BYTES)
    (root / 'frames').mkdir()
    (root / 'incomplete').mkdir()
    if empty_owner:
        (root / subject._OWNER).write_bytes(b'')
    before = _tree_files(root)
    with pytest.raises(UgoiraStorageError):
        UgoiraFrameStore(root).reconcile('frames/' + 'a' * 32 + '/receipt.json')
    assert _tree_files(root) == before


def test_reconcile_opens_existing_owner_without_write_create_append_or_truncate(tmp_path, monkeypatch):
    raw, manifest, _ = _fixture()
    store = UgoiraFrameStore(tmp_path / 'isolated')
    saved = store.save(raw, manifest)
    opened = []
    open_descriptor = os.open
    def observed(path, flags, *args, **kwargs):
        if Path(path) == store.root / subject._OWNER:
            opened.append(flags)
            assert not flags & (os.O_CREAT | os.O_APPEND | os.O_TRUNC | os.O_WRONLY | os.O_RDWR)
        return open_descriptor(path, flags, *args, **kwargs)
    monkeypatch.setattr(subject.os, 'open', observed)
    assert UgoiraFrameStore(store.root).reconcile(saved.relative_receipt).state == 'complete'
    assert len(opened) == 1



def test_reconcile_transient_io_is_distinct_from_corruption(tmp_path, monkeypatch):
    raw, manifest, _ = _fixture()
    store = UgoiraFrameStore(tmp_path / 'isolated')
    saved = store.save(raw, manifest)
    read_bounded = subject._read_bounded

    def temporary_io(path, limit):
        if Path(path).name == 'receipt.json':
            raise PermissionError('synthetic temporary sharing violation')
        return read_bounded(path, limit)

    monkeypatch.setattr(subject, '_read_bounded', temporary_io)
    with pytest.raises(subject.UgoiraStorageIOError) as caught:
        store.reconcile(saved.relative_receipt)
    assert 'synthetic temporary sharing violation' not in str(caught.value)

def test_reconcile_excludes_a_different_store_save_through_entire_verification(tmp_path, monkeypatch):
    raw, manifest, _ = _fixture()
    writer = UgoiraFrameStore(tmp_path / 'isolated')
    saved = writer.save(raw, manifest)
    reader = UgoiraFrameStore(writer.root)
    before = _tree_files(writer.root)
    verify = subject._verify_contents
    attempts = []
    def concurrent_save(folder, *args):
        if folder.parent == writer.frames and len(args) == 5:
            attempts.append('save-refused')
            with pytest.raises(UgoiraStorageError):
                writer.save(raw, manifest)
        return verify(folder, *args)
    monkeypatch.setattr(subject, '_verify_contents', concurrent_save)
    assert reader.reconcile(saved.relative_receipt).state == 'complete'
    assert attempts == ['save-refused']
    assert _tree_files(writer.root) == before


def test_save_excludes_reconcile_before_completion_proof_is_written(tmp_path, monkeypatch):
    raw, manifest, _ = _fixture()
    writer = UgoiraFrameStore(tmp_path / 'isolated')
    reader = UgoiraFrameStore(writer.root)
    write = subject._write
    attempts = []
    def concurrent_read(path, payload):
        if path.name == subject.COMPLETION:
            relative = f'frames/{path.parent.name}/receipt.json'
            with pytest.raises(UgoiraStorageError):
                reader.reconcile(relative)
            attempts.append('read-refused')
        write(path, payload)
    monkeypatch.setattr(subject, '_write', concurrent_read)
    saved = writer.save(raw, manifest)
    assert attempts == ['read-refused']
    assert reader.reconcile(saved.relative_receipt).state == 'complete'


def test_save_excludes_reconcile_even_after_proof_before_final_flush(tmp_path, monkeypatch):
    raw, manifest, _ = _fixture()
    writer = UgoiraFrameStore(tmp_path / 'isolated')
    reader = UgoiraFrameStore(writer.root)
    fsync = subject._fsync_dir
    attempts = []
    def concurrent_read(folder):
        if folder.parent == writer.frames and (folder / subject.COMPLETION).exists():
            relative = f'frames/{folder.name}/receipt.json'
            with pytest.raises(UgoiraStorageError):
                reader.reconcile(relative)
            attempts.append('read-refused')
        fsync(folder)
    monkeypatch.setattr(subject, '_fsync_dir', concurrent_read)
    saved = writer.save(raw, manifest)
    assert attempts == ['read-refused']
    assert reader.reconcile(saved.relative_receipt).state == 'complete'
