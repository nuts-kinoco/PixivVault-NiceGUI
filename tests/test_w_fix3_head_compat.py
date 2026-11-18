"""An older unsequenced head cannot prove the latest committed append."""
import json

import pytest

from pixivvault_nicegui import managed_generations as mg
from pixivvault_nicegui.managed_downloads import LegacyV5Uncertain, ManagedDownloadError, ManagedPixivDownloads
from test_v_dl_cap_20261117 import journal_of, make_root, mark_of, publish
from test_w_fix3_migration import historical_module


def snapshot(root):
    return {p.relative_to(root): p.read_bytes() if p.is_file() else None for p in root.rglob('*')}


@pytest.mark.parametrize('opener', ['generations', 'downloads'])
def test_unsequenced_head_and_rolled_back_pair_are_refused_without_writes(tmp_path, opener):
    root = make_root(tmp_path)
    original = historical_module('f90e671').ManagedGenerations(root)
    original.prepare_for_publication()
    publish(original, root, '1000')
    assert 'records' not in json.loads((root / mg._V5_HEAD).read_bytes())
    journal, mark = journal_of(root), mark_of(root)
    old_journal, old_mark = journal.read_bytes(), mark.read_bytes()
    publish(original, root, '1001')
    journal.write_bytes(old_journal)
    mark.write_bytes(old_mark)
    before = snapshot(root)
    with pytest.raises(LegacyV5Uncertain):
        if opener == 'generations':
            mg.ManagedGenerations(root)
        else:
            ManagedPixivDownloads(root, None, resolve_record=lambda *_: None)
    assert snapshot(root) == before


@pytest.mark.parametrize('reader_mode', ['cold', 'warm'])
def test_current_head_cannot_lose_its_sequence(tmp_path, reader_mode):
    root = make_root(tmp_path)
    writer = mg.ManagedGenerations(root)
    publish(writer, root, '1000')
    publish(writer, root, '1001')
    observer = mg.ManagedGenerations(root)
    head = root / mg._V5_HEAD
    old_head = json.loads(head.read_bytes())
    del old_head['records'], old_head['chain']
    head.write_text(json.dumps(old_head))
    before = snapshot(root)
    with pytest.raises(ManagedDownloadError):
        (observer if reader_mode == 'warm' else mg.ManagedGenerations(root)).entries()
    assert snapshot(root) == before
