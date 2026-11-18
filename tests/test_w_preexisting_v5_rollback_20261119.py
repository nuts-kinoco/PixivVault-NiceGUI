import pytest

from pixivvault_nicegui import managed_generations as mg
from pixivvault_nicegui.managed_downloads import ManagedDownloadError
from test_v_dl_cap_20261117 import make_root, publish, seed_index


@pytest.mark.parametrize('old_count', [0, 2])
def test_old_compacted_v5_without_new_marker_refuses_index_rollback(tmp_path, monkeypatch, old_count):
    root = make_root(tmp_path)
    seed_index(root, old_count)
    index = root / 'active-generations.json'
    old_index = index.read_bytes()
    monkeypatch.setattr(mg, '_COMPACT_RECORDS', 1)
    monkeypatch.setattr(mg, '_JOURNAL_RETAIN_SECONDS', -1)
    writer = mg.ManagedGenerations(root)
    publish(writer, root, '1000')
    writer._compact(writer._loaded)
    # The released candidate before this fix had no persistent v5 marker.
    (root / mg._V5_COMMITTED).unlink()
    index.write_bytes(old_index)
    with pytest.raises(ManagedDownloadError):
        mg.ManagedGenerations(root)


def test_old_index_with_unresolved_journal_stage_and_orphan_is_refused(tmp_path):
    root = make_root(tmp_path)
    seed_index(root, 0)
    from test_v_dl_cap_20261117 import make_generation
    make_generation(root, '1000')
    stage = root / 'generation-incomplete'
    stage.mkdir(exist_ok=True)
    (stage / 'journal-unresolved.tmp').write_bytes(b'partial')
    with pytest.raises(ManagedDownloadError):
        mg.ManagedGenerations(root)
