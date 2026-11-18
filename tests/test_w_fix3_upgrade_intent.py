import json


import pytest


from pixivvault_nicegui import managed_generations as mg


from pixivvault_nicegui.managed_downloads import ManagedDownloadError


from test_v_dl_cap_20261117 import journal_of, make_root, mark_of, publish, seed_index


from test_w_fix3_migration import historical_module


@pytest.mark.parametrize('stage', ['no_journal', 'torn_stage'])
def test_prepared_fold_is_recoverable_before_a_complete_journal(tmp_path, monkeypatch, stage):
    root = make_root(tmp_path)
    baseline = seed_index(root, 2)
    writer = mg.ManagedGenerations(root)
    original_index = writer.index_path.read_bytes()
    original = mg._write

    def interrupted(path, data):
        if path.name.startswith('prepared-') and path.name.endswith('.journal'):
            if stage == 'torn_stage':
                original(path, data[:13])
            raise OSError('synthetic interrupted journal write')
        return original(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(mg, '_write', interrupted)
        with pytest.raises(OSError):
            writer._compact(writer._loaded)
    assert writer.index_path.read_bytes() == original_index
    reopened = mg.ManagedGenerations(root)
    assert set(reopened.entries()) == set(baseline)
    reopened._compact(reopened._loaded)
    publish(reopened, root, '1000')
    assert set(mg.ManagedGenerations(root).entries()) == set(baseline) | {'1000'}
