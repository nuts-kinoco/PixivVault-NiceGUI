import json

import pytest

from pixivvault_nicegui import managed_generations as mg
from pixivvault_nicegui.managed_downloads import ManagedDownloadError
from test_v_dl_cap_20261117 import journal_of, make_generation, make_root, mark_of, publish
from test_w_fix3_migration import historical_module


def test_head_removed_and_old_index_restored_is_refused_with_sequence_guard(tmp_path, monkeypatch):
    old = historical_module('823871e')
    root = make_root(tmp_path)
    writer = old.ManagedGenerations(root)
    writer.prepare_for_publication()

    first = publish(writer, root, '1000')
    writer._compact(writer._loaded)
    idx = root / 'active-generations.json'
    old_index = idx.read_bytes()

    def update(before, suffix):
        relative, digest = make_generation(root, '1000', suffix=suffix * 32)
        return writer.publish('1000', relative, digest, before['update_date'],
                              before['sha256'], before['relative_receipt'], suffix * 64)

    next_saved = update(first, 'a')
    writer._compact(writer._loaded)
    latest = update(next_saved, 'b')

    candidate = mg.ManagedGenerations(root)
    assert candidate.current('1000')['relative_receipt'] == latest['relative_receipt']
    with monkeypatch.context() as patch:
        def append_interruption(*_):
            raise OSError('synthetic interruption after latest anchored index')
        patch.setattr(candidate, '_open_for_append', append_interruption)
        with pytest.raises(ManagedDownloadError, match='publication failed'):
            publish(candidate, root, '1001')

    observer = mg.ManagedGenerations(root)
    assert observer.current('1000')['relative_receipt'] == latest['relative_receipt']
    assert observer.current('1001') is None
    assert (root / mg._V5_SEQUENCE_GUARD).read_bytes() == mg._V5_SEQUENCE_DONE
    assert len(journal_of(root).read_bytes().splitlines()) == 1
    assert json.loads(mark_of(root).read_bytes())['records'] == 0
    (root / mg._V5_HEAD).unlink()
    idx.write_bytes(old_index)
    with pytest.raises(ManagedDownloadError, match='index rejected'):
        recovered = mg.ManagedGenerations(root).current('1000')
        print('observed:', recovered['relative_receipt'], 'latest:', latest['relative_receipt'])
