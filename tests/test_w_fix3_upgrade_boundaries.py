from pathlib import Path


import pytest


from pixivvault_nicegui import managed_generations as mg


from pixivvault_nicegui.managed_downloads import ManagedDownloadError


from test_v_dl_cap_20261117 import journal_of, make_generation, make_root, mark_of, publish, seed_index


from test_w_fix3_migration import historical_module


@pytest.mark.parametrize('stage', ['new_mark_install', 'head_install'])
def test_new_migration_recovers_before_head_commit(tmp_path, monkeypatch, stage):
    root = make_root(tmp_path)
    baseline = seed_index(root, 2)
    writer = mg.ManagedGenerations(root)
    index = writer.index_path
    original_index = index.read_bytes()
    replace = mg._replace_retrying

    def interrupted(source, target, patience=5):
        if ((stage == 'new_mark_install' and target.name.endswith('.mark'))
                or (stage == 'head_install' and target.name == mg._V5_HEAD)):
            raise OSError('synthetic interruption before commit')
        return replace(source, target, patience)

    with monkeypatch.context() as patch:
        patch.setattr(mg, '_replace_retrying', interrupted)
        with pytest.raises(ManagedDownloadError, match='publication failed'):
            publish(writer, root, '1000')
    assert index.read_bytes() == original_index
    assert not (root / mg._V5_HEAD).exists()
    assert list(root.glob('active-generations-*.journal'))
    reopened = mg.ManagedGenerations(root)
    assert set(reopened.entries()) == set(baseline)
    publish(reopened, root, '1000')
    assert set(mg.ManagedGenerations(root).entries()) == set(baseline) | {'1000'}
