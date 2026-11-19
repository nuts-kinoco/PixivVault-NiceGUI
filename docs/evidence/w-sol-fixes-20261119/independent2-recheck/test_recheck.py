import os

import pytest

from pixivvault_nicegui import managed_generations as mg
from pixivvault_nicegui.managed_downloads import ManagedDownloadError
from test_v_dl_cap_20261117 import make_root, journal_of, mark_of, publish


def test_live_reader_recovers_after_pair_is_restored(tmp_path):
    root = make_root(tmp_path)
    writer = mg.ManagedGenerations(root)
    writer.prepare_for_publication()
    publish(writer, root, '1000')
    reader = mg.ManagedGenerations(root)
    journal, mark = journal_of(root), mark_of(root)
    earlier = journal.read_bytes(), mark.read_bytes()

    publish(writer, root, '1001')
    latest = journal.read_bytes(), mark.read_bytes()
    assert set(reader.entries()) == {'1000', '1001'}

    journal.write_bytes(earlier[0])
    mark.write_bytes(earlier[1])
    for _ in range(2):
        with pytest.raises(ManagedDownloadError, match='active generation index rejected'):
            reader.entries()
    with pytest.raises(ManagedDownloadError, match='active generation index rejected'):
        publish(writer, root, '1002')

    journal.write_bytes(latest[0])
    mark.write_bytes(latest[1])
    assert set(reader.entries()) == {'1000', '1001'}
    publish(writer, root, '1002')
    assert set(reader.entries()) == {'1000', '1001', '1002'}
