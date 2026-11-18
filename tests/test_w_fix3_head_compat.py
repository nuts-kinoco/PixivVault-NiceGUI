import json


import pytest


from pixivvault_nicegui import managed_generations as mg


from pixivvault_nicegui.managed_downloads import ManagedDownloadError


from test_v_dl_cap_20261117 import journal_of, make_root, mark_of, publish, seed_index


from test_w_fix3_migration import historical_module


@pytest.mark.parametrize('reader_mode', ['cold', 'warm'])
def test_compat_head_cannot_erase_sequence_of_current_generation(tmp_path, reader_mode):
    older = historical_module('f90e671')
    root = make_root(tmp_path)
    original = older.ManagedGenerations(root)
    original.prepare_for_publication()
    publish(original, root, '1000')
    head = root / mg._V5_HEAD
    old_head = head.read_bytes()
    assert 'records' not in json.loads(old_head)
    journal, mark = journal_of(root), mark_of(root)
    old_journal, old_mark = journal.read_bytes(), mark.read_bytes()
    writer = mg.ManagedGenerations(root)
    publish(writer, root, '1001')
    observer = mg.ManagedGenerations(root)
    assert json.loads(head.read_bytes())['records'] == 2
    assert set(observer.entries()) == {'1000', '1001'}
    head.write_bytes(old_head)
    reader = observer if reader_mode == 'warm' else None
    with pytest.raises(ManagedDownloadError, match='index rejected'):
        (reader if reader is not None else mg.ManagedGenerations(root)).entries()
