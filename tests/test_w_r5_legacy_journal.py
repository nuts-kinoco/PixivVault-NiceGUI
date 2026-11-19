import hashlib

import pytest

from pixivvault_nicegui import managed_generations as mg
from pixivvault_nicegui.managed_downloads import ManagedDownloadError
from test_v_dl_cap_20261117 import make_root, seed_index
from test_v_dl_integrity_20261117 import entry_for, write_legacy_journal


def test_running_and_new_readers_refuse_legacy_journal_without_writes(tmp_path):
    root = make_root(tmp_path)
    seed_index(root, 2)
    digest = hashlib.sha256((root / 'active-generations.json').read_bytes()).hexdigest()
    first = entry_for(root, '1000', '1' * 32)
    second = entry_for(root, '1001', '2' * 32)
    reader = mg.ManagedGenerations(root)
    write_legacy_journal(root, [first, second], digest)
    before = {p.relative_to(root): p.read_bytes() if p.is_file() else None for p in root.rglob('*')}
    for action in (reader.entries, lambda: mg.ManagedGenerations(root)):
        with pytest.raises(ManagedDownloadError):
            action()
        assert {p.relative_to(root): p.read_bytes() if p.is_file() else None for p in root.rglob('*')} == before
