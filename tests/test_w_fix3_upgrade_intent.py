import json


import pytest


from pixivvault_nicegui import managed_generations as mg


from pixivvault_nicegui.managed_downloads import ManagedDownloadError


from test_v_dl_cap_20261117 import journal_of, make_root, mark_of, publish, seed_index


from test_w_fix3_migration import historical_module


@pytest.mark.parametrize('stage', ['no_journal', 'torn_stage'])
def test_upgrade_intent_is_recoverable_before_any_complete_journal(tmp_path, monkeypatch, stage):
    root = make_root(tmp_path)
    baseline = seed_index(root, 2)
    writer = mg.ManagedGenerations(root)
    index = writer.index_path
    original_index = index.read_bytes()
    original = mg._write

    def interrupted(path, data):
        if path.name.startswith('journal-'):
            if stage == 'torn_stage':
                original(path, data[:13])
            raise OSError('synthetic interrupted journal write')
        return original(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(mg, '_write', interrupted)
        with pytest.raises(ManagedDownloadError, match='publication failed'):
            publish(writer, root, '1000')
    assert index.read_bytes() == original_index
    assert not list(root.glob('active-generations-*.journal'))
    assert not (root / mg._V5_HEAD).exists()
    assert (root / mg._V5_COMMITTED).read_bytes().startswith(b'upgrade')
    reopened = mg.ManagedGenerations(root)
    assert set(reopened.entries()) == set(baseline)
    publish(reopened, root, '1000')
    assert set(mg.ManagedGenerations(root).entries()) == set(baseline) | {'1000'}
