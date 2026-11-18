import pytest

from pixivvault_nicegui import managed_generations as mg
from pixivvault_nicegui.managed_downloads import ManagedDownloadError
from test_v_dl_cap_20261117 import make_root, publish


@pytest.mark.parametrize('reader_mode', ['cold', 'warm'])
def test_index_and_head_rollback_with_newer_journal_is_refused(tmp_path, reader_mode):
    root = make_root(tmp_path)
    writer = mg.ManagedGenerations(root)
    writer.prepare_for_publication()
    publish(writer, root, '1000')
    old_index = writer.index_path.read_bytes()
    head = root / mg._V5_HEAD
    old_head = head.read_bytes()
    writer._compact(writer._loaded)
    publish(writer, root, '1001')
    reader = mg.ManagedGenerations(root)
    assert set(reader.entries()) == {'1000', '1001'}
    writer.index_path.write_bytes(old_index)
    head.write_bytes(old_head)
    with pytest.raises(ManagedDownloadError):
        (reader if reader_mode == 'warm' else mg.ManagedGenerations(root)).entries()


def test_cached_page_rejects_newer_mark(tmp_path):
    import json
    root = make_root(tmp_path)
    writer = mg.ManagedGenerations(root)
    writer.prepare_for_publication()
    publish(writer, root, '1000')
    writer.assert_page_visible('1000', 0)
    generation = writer._loaded.generation
    jid = 'f' * 32
    (root / f'active-generations-{jid}.mark').write_text(json.dumps(
        {'id': jid, 'generation': generation + 1, 'records': 0, 'chain': 'a' * 64}))
    with pytest.raises(ManagedDownloadError):
        writer.assert_page_visible('1000', 0)
