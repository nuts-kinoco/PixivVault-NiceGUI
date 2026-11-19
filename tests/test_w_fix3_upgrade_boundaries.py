from pathlib import Path


import pytest


from pixivvault_nicegui import managed_generations as mg


from pixivvault_nicegui.managed_downloads import ManagedDownloadError


from test_v_dl_cap_20261117 import journal_of, make_generation, make_root, mark_of, publish, seed_index


from test_w_fix3_migration import historical_module


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
