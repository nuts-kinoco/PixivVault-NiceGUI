from pathlib import Path

import pytest

from pixivvault_nicegui import managed_generations as mg
from pixivvault_nicegui.managed_downloads import ManagedDownloadError
from test_v_dl_cap_20261117 import journal_of, make_generation, make_root, mark_of, publish, seed_index
from test_w_fix3_migration import historical_module


@pytest.mark.parametrize('writer_format', ['anchored', 'old-v5'])
@pytest.mark.parametrize('reader_mode', ['cold', 'warm'])
def test_same_work_update_is_never_silently_rolled_back(tmp_path, writer_format, reader_mode):
    root = make_root(tmp_path)
    module = mg if writer_format == 'anchored' else historical_module('823871e')
    writer = module.ManagedGenerations(root)
    writer.prepare_for_publication()
    base = publish(writer, root, '1000')
    index = root / 'active-generations.json'
    old_index = index.read_bytes()
    writer._compact(writer._loaded)
    relative, digest = make_generation(root, '1000', suffix='f' * 32)
    writer.publish('1000', relative, digest, '2026-01-03T00:00:00+00:00',
                   base['sha256'], base['relative_receipt'], 'e' * 64)
    if writer_format == 'old-v5':
        with pytest.raises(ManagedDownloadError, match='旧形式'):
            mg.ManagedGenerations(root)
        index.write_bytes(old_index)
        with pytest.raises(ManagedDownloadError, match='旧形式'):
            mg.ManagedGenerations(root)
        return
    observer = mg.ManagedGenerations(root)
    assert observer.current('1000')['relative_receipt'] == relative
    index.write_bytes(old_index)
    with pytest.raises(ManagedDownloadError, match='index rejected'):
        (observer if reader_mode == 'warm' else mg.ManagedGenerations(root)).entries()


@pytest.mark.parametrize('stage', ['new_mark_install', 'head_install'])
def test_prepared_fold_recovers_before_head_commit(tmp_path, monkeypatch, stage):
    root = make_root(tmp_path)
    baseline = seed_index(root, 2)
    writer = mg.ManagedGenerations(root)
    original_index = writer.index_path.read_bytes()
    write = mg._write

    def interrupted_write(path, data):
        if stage == 'new_mark_install' and path.name.startswith('prepared-') and path.name.endswith('.mark'):
            raise OSError('synthetic interruption before commit')
        return write(path, data)

    def interrupted_head(*args):
        raise OSError('synthetic interruption before commit')

    with monkeypatch.context() as patch:
        patch.setattr(mg, '_write', interrupted_write)
        if stage == 'head_install':
            patch.setattr(writer, '_write_head', interrupted_head)
        with pytest.raises(OSError):
            writer._compact(writer._loaded)
    assert writer.index_path.read_bytes() == original_index
    reopened = mg.ManagedGenerations(root)
    assert set(reopened.entries()) == set(baseline)
    reopened._compact(reopened._loaded)
    publish(reopened, root, '1000')
    assert set(mg.ManagedGenerations(root).entries()) == set(baseline) | {'1000'}


def test_warm_reader_rejects_rolled_back_mark_sequence(tmp_path):
    root = make_root(tmp_path)
    writer = mg.ManagedGenerations(root)
    writer.prepare_for_publication()
    publish(writer, root, '1000')
    mark = mark_of(root)
    older_mark = mark.read_bytes()
    publish(writer, root, '1001')
    observer = mg.ManagedGenerations(root)
    assert set(observer.entries()) == {'1000', '1001'}
    assert observer._loaded.mark_records == 2
    mark.write_bytes(older_mark)
    with pytest.raises(ManagedDownloadError, match='index rejected'):
        observer.entries()
