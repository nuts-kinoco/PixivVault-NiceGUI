from test_w_fix3_migration import historical_module
from test_v_dl_cap_20261117 import make_root, publish, seed_index
from pixivvault_nicegui import managed_generations as mg
from pixivvault_nicegui.managed_downloads import ManagedDownloadError
from pathlib import Path
import os
from unittest.mock import patch
import pytest

def test_legacy_migration_is_safe_for_existing_interrupted_roots(tmp_path):
    legacy_writer = historical_module('d64879a')
    prior = historical_module('823871e')
    root = make_root(tmp_path)
    seed_index(root, 2)
    idx = root / 'active-generations.json'
    old = idx.read_bytes()
    publish(legacy_writer.ManagedGenerations(root), root, '1000')
    writer = prior.ManagedGenerations(root)
    assert set(writer.entries()) == {'1000','100000','100001'}
    with patch.object(writer, '_append_journal', side_effect=OSError('synthetic interruption after v5 index')):
        try:
            publish(writer, root, '1001')
        except ManagedDownloadError:
            pass
        else:
            raise AssertionError('expected interruption')
    assert not (root / 'active-generations.journal').exists()
    assert (root / mg._V5_COMMITTED).read_bytes().startswith(b'upgrade:')
    with pytest.raises(ManagedDownloadError, match='旧形式'):
        mg.ManagedGenerations(root)
    idx.write_bytes(old)
    try:
        now = set(mg.ManagedGenerations(root).entries())
    except ManagedDownloadError:
        return
    assert now == {'1000','100000','100001'}, f'restored older index hides a committed work: {sorted(now)}'
