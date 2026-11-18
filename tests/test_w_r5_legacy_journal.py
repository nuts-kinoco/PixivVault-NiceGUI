import hashlib

import pytest

from pixivvault_nicegui import managed_generations as mg
from pixivvault_nicegui.managed_downloads import ManagedDownloadError
from test_v_dl_cap_20261117 import make_root, seed_index
from test_v_dl_integrity_20261117 import entry_for, write_legacy_journal


def test_running_reader_refuses_shortened_legacy_journal(tmp_path):
    root = make_root(tmp_path)
    seed_index(root, 2)
    digest = hashlib.sha256((root / 'active-generations.json').read_bytes()).hexdigest()
    first = entry_for(root, '1000', '1' * 32)
    second = entry_for(root, '1001', '2' * 32)
    write_legacy_journal(root, [first], digest)
    old = (root / mg._JOURNAL).read_bytes()
    write_legacy_journal(root, [first, second], digest)
    reader = mg.ManagedGenerations(root)
    assert '1001' in reader.entries()
    (root / mg._JOURNAL).write_bytes(old)
    with pytest.raises(ManagedDownloadError):
        reader.entries()
