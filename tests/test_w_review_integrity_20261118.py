import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from nicegui import Client, ui
from pixivvault_nicegui import managed_generations as mg
from pixivvault_nicegui.favourites import FavouriteBook
from pixivvault_nicegui.following_sync import FollowingSyncStore, FollowingSyncError
from pixivvault_nicegui.managed_downloads import ManagedDownloadError
from pixivvault_nicegui.managed_scheduler import ManagedScheduler
from pixivvault_nicegui.ui.scheduler_view import SchedulerView
from test_v_dl_cap_20261117 import make_root, journal_of, mark_of, publish, seed_index
from test_v_dl_priority_20261117 import sync
from test_t_dl_engine_20261117 import env


@pytest.mark.parametrize('damage', ['missing', 'wrong_chain'])
def test_live_reader_and_writer_reject_mark_damage(tmp_path, damage):
    root = make_root(tmp_path)
    g = mg.ManagedGenerations(root)
    g.prepare_for_publication()
    publish(g, root, '1000')
    mark = mark_of(root)
    if damage == 'missing':
        mark.unlink()
    else:
        record = json.loads(mark.read_bytes())
        record['chain'] = '0' * 64
        mark.write_bytes(json.dumps(record).encode())
    with pytest.raises(ManagedDownloadError):
        g.entries()
    with pytest.raises(ManagedDownloadError):
        publish(g, root, '1001')


def test_live_reader_rejects_corrupted_prefix_after_append(tmp_path):
    root = make_root(tmp_path)
    writer = mg.ManagedGenerations(root)
    writer.prepare_for_publication()
    publish(writer, root, '1000')
    cached = mg.ManagedGenerations(root)
    journal = journal_of(root)
    publish(writer, root, '1001')
    with journal.open('r+b') as stream:
        stream.write(b'!')
    with pytest.raises(ManagedDownloadError):
        cached.entries()