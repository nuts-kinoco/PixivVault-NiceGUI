"""Partial rollback is detectable; restoring the entire root is not.

A prepared next generation is not a committed rollback witness.  A crash at
any commit boundary must retain every committed ID and receipt digest.
"""
import hashlib
import json

import pytest

from pixivvault_nicegui import managed_generations as mg
from pixivvault_nicegui.managed_downloads import ManagedDownloadError, ManagedPixivDownloads
from test_v_dl_cap_20261117 import make_root, publish


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() if p.is_file() else None
            for p in root.rglob('*')}


@pytest.mark.parametrize('point', ['pair', 'candidate'])
def test_uncommitted_fold_is_ignored_after_interruption(tmp_path, monkeypatch, point):
    root = make_root(tmp_path)
    writer = mg.ManagedGenerations(root)
    writer.prepare_for_publication()
    publish(writer, root, '1000')
    publish(writer, root, '1001')
    expected = {k: (v['sha256'], hashlib.sha256((root / v['relative_receipt']).read_bytes()).hexdigest())
                for k, v in writer.entries().items()}
    original = writer._new_journal if point == 'pair' else writer._write_head

    def interrupt(*args, **kwargs):
        if point == 'pair':
            original(*args, **kwargs)
        raise OSError('interrupted before head commit')

    with monkeypatch.context() as patch:
        patch.setattr(writer, '_new_journal' if point == 'pair' else '_write_head', interrupt)
        with pytest.raises(OSError):
            writer._compact(writer._loaded)
    reopened = mg.ManagedGenerations(root)
    assert {k: (v['sha256'], hashlib.sha256((root / v['relative_receipt']).read_bytes()).hexdigest())
            for k, v in reopened.entries().items()} == expected
    abandoned = tuple(reopened.stage_dir.glob('prepared-*'))
    assert abandoned
    reopened._compact(reopened._loaded)
    assert all(not path.exists() for path in abandoned)


@pytest.mark.parametrize('opener', ['generations', 'downloads'])
def test_sequenced_v5_is_the_current_format(tmp_path, opener):
    root = make_root(tmp_path)
    from test_w_fix3_migration import historical_module
    previous = historical_module('efabe62').ManagedGenerations(root)
    previous.prepare_for_publication()
    saved = publish(previous, root, '1000')
    (root / 'incomplete').mkdir()
    before = snapshot(root)
    if opener == 'generations':
        opened = mg.ManagedGenerations(root)
    else:
        opened = ManagedPixivDownloads(root, None, resolve_record=lambda *_: None)._generations
    assert opened.entries()['1000']['sha256'] == saved['sha256']
    assert snapshot(root) == before


@pytest.mark.parametrize('point', ['floor', 'head', 'pair'])
def test_committed_fold_recovers_every_receipt(tmp_path, monkeypatch, point):
    root = make_root(tmp_path)
    writer = mg.ManagedGenerations(root)
    writer.prepare_for_publication()
    publish(writer, root, '1000')
    publish(writer, root, '1001')
    expected = {k: v['sha256'] for k, v in writer.entries().items()}
    replace = mg._replace_retrying
    install = writer._install_prepared

    def interrupted_replace(source, target, patience=5):
        if ((point == 'floor' and target.name == mg._V5_HEAD)
                or (point == 'head' and target == writer.index_path)):
            raise OSError('interrupted after durable commit')
        return replace(source, target, patience)

    def interrupted_pair(*args):
        if point == 'pair':
            raise OSError('interrupted after durable head')
        return install(*args)

    with monkeypatch.context() as patch:
        patch.setattr(mg, '_replace_retrying', interrupted_replace)
        patch.setattr(writer, '_install_prepared', interrupted_pair)
        with pytest.raises(OSError):
            writer._compact(writer._loaded)
    reopened = mg.ManagedGenerations(root)
    entries = reopened.entries()
    assert {k: v['sha256'] for k, v in entries.items()} == expected
    assert all(hashlib.sha256((root / v['relative_receipt']).read_bytes()).hexdigest() == v['sha256']
               for v in entries.values())


@pytest.mark.parametrize('reader_mode', ['cold', 'warm'])
def test_floor_refuses_index_and_head_restored_together(tmp_path, reader_mode):
    root = make_root(tmp_path)
    writer = mg.ManagedGenerations(root)
    writer.prepare_for_publication()
    publish(writer, root, '1000')
    index = root / 'active-generations.json'
    head = root / mg._V5_HEAD
    old_index, old_head = index.read_bytes(), head.read_bytes()
    writer._compact(writer._loaded)
    publish(writer, root, '1001')
    observer = mg.ManagedGenerations(root)
    index.write_bytes(old_index)
    head.write_bytes(old_head)
    with pytest.raises(ManagedDownloadError):
        (observer if reader_mode == 'warm' else mg.ManagedGenerations(root)).entries()


@pytest.mark.parametrize('reader_mode', ['cold', 'warm'])
def test_mark_only_rollback_is_rejected(tmp_path, reader_mode):
    from test_v_dl_cap_20261117 import mark_of
    root = make_root(tmp_path)
    writer = mg.ManagedGenerations(root)
    writer.prepare_for_publication()
    publish(writer, root, '1000')
    mark = mark_of(root)
    old_mark = mark.read_bytes()
    publish(writer, root, '1001')
    observer = mg.ManagedGenerations(root)
    mark.write_bytes(old_mark)
    with pytest.raises(ManagedDownloadError):
        (observer if reader_mode == 'warm' else mg.ManagedGenerations(root)).entries()


@pytest.mark.parametrize('indexed', [False, True])
def test_real_save_receipt_records_committed_generation(tmp_path, indexed):
    from pixivvault_nicegui.managed_download_requests import ManagedAuthorIntent
    from test_managed_downloads import Client, Context, Job, record

    class RevisionClient(Client):
        def get_artwork_update_revision(self, work_id, author_id):
            return {'author_id': author_id, 'page_count': 2, 'illustration_type': 0,
                    'update_date': '2026-10-29T00:00:00Z'}

    class IdentifiedJob(Job):
        job_id = '1' * 32

    owner = ManagedPixivDownloads(tmp_path / 'managed', RevisionClient(), resolve_record=lambda _: record())
    intent = (ManagedAuthorIntent(record(), 'follow', '2026-10-29T00:00:00Z', None, 'new')
              if indexed else None)
    relative = owner.run(IdentifiedJob(), Context(), intent=intent)
    receipt = json.loads((owner.root / relative).read_bytes())
    index = json.loads((owner.root / 'active-generations.json').read_bytes())
    assert type(receipt['generation']) is int
    assert receipt['generation'] == index['journal']['generation']


@pytest.mark.parametrize('legacy', ['journal', 'upgrade'])
@pytest.mark.parametrize('opener', ['generations', 'downloads'])
def test_legacy_control_files_are_rejected_before_directory_creation(tmp_path, legacy, opener):
    root = make_root(tmp_path)
    writer = mg.ManagedGenerations(root)
    writer.prepare_for_publication()
    if legacy == 'journal':
        (root / 'active-generations.journal').write_bytes(b'legacy\n')
    else:
        (root / mg._V5_COMMITTED).write_bytes(b'upgrade:' + b'a' * 64 + b'\n')
    (root / 'generation-incomplete').rmdir()
    before = snapshot(root)
    with pytest.raises(ManagedDownloadError):
        if opener == 'generations':
            mg.ManagedGenerations(root)
        else:
            ManagedPixivDownloads(root, None, resolve_record=lambda *_: None)
    assert snapshot(root) == before


@pytest.mark.parametrize('opener', ['generations', 'downloads'])
def test_nonempty_uninitialized_root_is_not_modified(tmp_path, opener):
    root = make_root(tmp_path)
    (root / 'unrelated.txt').write_text('do not change')
    before = snapshot(root)
    with pytest.raises(ManagedDownloadError):
        if opener == 'generations':
            mg.ManagedGenerations(root)
        else:
            ManagedPixivDownloads(root, None, resolve_record=lambda *_: None)
    assert snapshot(root) == before


@pytest.mark.parametrize('opener', ['generations', 'downloads'])
def test_existing_index_with_missing_directory_is_not_modified(tmp_path, opener):
    root = make_root(tmp_path)
    writer = mg.ManagedGenerations(root)
    writer.prepare_for_publication()
    (root / 'generation-incomplete').rmdir()
    before = snapshot(root)
    with pytest.raises(ManagedDownloadError):
        if opener == 'generations':
            mg.ManagedGenerations(root)
        else:
            ManagedPixivDownloads(root, None, resolve_record=lambda *_: None)
    assert snapshot(root) == before


@pytest.mark.parametrize('warm', [False, True])
def test_deleted_empty_committed_index_is_rejected_without_writes(tmp_path, warm):
    owner = ManagedPixivDownloads(tmp_path / 'managed', None, resolve_record=lambda _: None)
    root = owner.root
    (root / 'active-generations.json').unlink()
    before = snapshot(root)
    with pytest.raises(ManagedDownloadError):
        (owner._generations if warm else mg.ManagedGenerations(root)).entries()
    assert snapshot(root) == before


def test_generations_initialization_is_reopenable_by_downloads_without_writes(tmp_path):
    root = make_root(tmp_path)
    mg.ManagedGenerations(root)
    before = snapshot(root)
    reopened = ManagedPixivDownloads(root, None, resolve_record=lambda _: None)
    assert reopened._generations.entries() == {}
    assert snapshot(root) == before


def test_interrupted_initial_preparation_reopens_empty_root(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(mg.ManagedGenerations, '_write_head',
                      lambda *a, **k: (_ for _ in ()).throw(OSError('interrupted')))
        with pytest.raises(ManagedDownloadError):
            mg.ManagedGenerations(root)
    reopened = mg.ManagedGenerations(root)
    assert reopened.entries() == {}
    assert (root / 'active-generations.json').exists()


def test_committed_append_then_journal_rollback_is_rejected(tmp_path, monkeypatch):
    from test_v_dl_cap_20261117 import journal_of, make_generation
    root = make_root(tmp_path)
    writer = mg.ManagedGenerations(root)
    publish(writer, root, '1000')
    journal = journal_of(root)
    old = journal.read_bytes()
    relative, digest = make_generation(root, '1001')
    with monkeypatch.context() as patch:
        patch.setattr(writer, '_write_head', lambda *a, **k: (_ for _ in ()).throw(OSError('interrupted')))
        with pytest.raises(ManagedDownloadError):
            writer.publish('1001', relative, digest, '2026-01-01T00:00:00+00:00', None)
    journal.write_bytes(old)
    before = snapshot(root)
    with pytest.raises(ManagedDownloadError):
        mg.ManagedGenerations(root)
    assert snapshot(root) == before


def test_prepared_append_is_invisible_and_retryable(tmp_path, monkeypatch):
    from test_v_dl_cap_20261117 import make_generation
    root = make_root(tmp_path)
    writer = mg.ManagedGenerations(root)
    publish(writer, root, '1000')
    relative, digest = make_generation(root, '1001')
    replace = mg._replace_retrying

    def interrupt(source, target, patience=5):
        if target.name.startswith('append-') and target.name.endswith('.prepared'):
            if json.loads(source.read_bytes()).get('phase') == 'committed':
                raise OSError('interrupted before append commit')
        return replace(source, target, patience)

    with monkeypatch.context() as patch:
        patch.setattr(mg, '_replace_retrying', interrupt)
        with pytest.raises(ManagedDownloadError):
            writer.publish('1001', relative, digest, '2026-01-01T00:00:00+00:00', None)
    reopened = mg.ManagedGenerations(root)
    assert set(reopened.entries()) == {'1000'}
    reopened.publish('1001', relative, digest, '2026-01-01T00:00:00+00:00', None)
    assert set(mg.ManagedGenerations(root).entries()) == {'1000', '1001'}
