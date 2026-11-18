import json

import pytest

from pixivvault_nicegui import managed_generations as mg
from pixivvault_nicegui.managed_downloads import ManagedDownloadError
from test_v_dl_cap_20261117 import journal_of, make_root, mark_of, publish


@pytest.mark.parametrize('reader_mode', ['cold', 'warm'])
def test_rollback_of_same_generation_journal_and_mark_is_rejected(tmp_path, reader_mode):
    root = make_root(tmp_path)
    writer = mg.ManagedGenerations(root)
    writer.prepare_for_publication()
    publish(writer, root, '1000')
    journal, mark = journal_of(root), mark_of(root)
    oldest_journal, oldest_mark = journal.read_bytes(), mark.read_bytes()
    head_path = root / mg._V5_HEAD
    head_before = head_path.read_bytes()
    index_before = writer.index_path.read_bytes()
    publish(writer, root, '1001')
    assert writer.index_path.read_bytes() == index_before
    assert json.loads(mark.read_bytes())['records'] == 2
    assert json.loads(oldest_mark)['records'] == 1
    observer = mg.ManagedGenerations(root)
    assert set(observer.entries()) == {'1000', '1001'}
    newest_head = head_path.read_bytes()
    journal.write_bytes(oldest_journal)
    mark.write_bytes(oldest_mark)
    assert head_path.read_bytes() == newest_head
    try:
        records = (observer if reader_mode == 'warm' else mg.ManagedGenerations(root)).entries()
    except ManagedDownloadError:
        return
    print('head changed on publish:', newest_head != head_before, 'visible after rollback:', sorted(records))
    pytest.fail(f'rolled-back pair accepted as {sorted(records)}')


def test_interrupted_sequence_witness_is_repaired_on_idempotent_retry(tmp_path, monkeypatch):
    from test_v_dl_cap_20261117 import make_generation
    root = make_root(tmp_path)
    writer = mg.ManagedGenerations(root)
    writer.prepare_for_publication()
    publish(writer, root, '1000')
    journal, mark = journal_of(root), mark_of(root)
    old_journal, old_mark = journal.read_bytes(), mark.read_bytes()
    relative, digest = make_generation(root, '1001')
    with monkeypatch.context() as patch:
        patch.setattr(writer, '_write_head', lambda *_: (_ for _ in ()).throw(OSError('head unavailable')))
        with pytest.raises(ManagedDownloadError, match='publication failed'):
            writer.publish('1001', relative, digest, '2026-01-01T00:00:00+00:00', None)
    reopened = mg.ManagedGenerations(root)
    assert set(reopened.entries()) == {'1000', '1001'}
    reopened.publish('1001', relative, digest, '2026-01-01T00:00:00+00:00', None)
    journal.write_bytes(old_journal)
    mark.write_bytes(old_mark)
    with pytest.raises(ManagedDownloadError, match='index rejected'):
        mg.ManagedGenerations(root)


@pytest.mark.parametrize('reader_mode', ['cold', 'warm'])
def test_mark_ahead_of_rolled_back_head_is_rejected(tmp_path, reader_mode):
    root = make_root(tmp_path)
    writer = mg.ManagedGenerations(root)
    writer.prepare_for_publication()
    publish(writer, root, '1000')
    head = root / mg._V5_HEAD
    old_head = head.read_bytes()
    publish(writer, root, '1001')
    observer = mg.ManagedGenerations(root)
    assert set(observer.entries()) == {'1000', '1001'}
    head.write_bytes(old_head)
    with pytest.raises(ManagedDownloadError, match='index rejected'):
        (observer if reader_mode == 'warm' else mg.ManagedGenerations(root)).entries()
