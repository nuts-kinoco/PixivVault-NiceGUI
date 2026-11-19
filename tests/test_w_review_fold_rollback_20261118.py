from datetime import datetime, timezone
import asyncio
import json
import sqlite3

import pytest

from pixivvault_nicegui import managed_generations as mg
from pixivvault_nicegui.bookmark_run import VISIBILITY_ROWS
from pixivvault_nicegui.managed_downloads import ManagedDownloadError
from pixivvault_nicegui.managed_root_writer import writer_fence
from test_v_dl_cap_20261117 import make_root, mark_of, publish, seed_index
from test_w_review_favourites_20261118 import create_stars
from test_w_review_bookmark_resume_20261118 import services_with_runs


def test_second_rollback_after_retired_journal_pruning(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    seed_index(root, 0)
    index = root / 'active-generations.json'
    old = index.read_bytes()
    monkeypatch.setattr(mg, '_COMPACT_RECORDS', 1)
    monkeypatch.setattr(mg, '_JOURNAL_RETAIN_SECONDS', -1)
    writer = mg.ManagedGenerations(root)
    publish(writer, root, '1000')    # threshold folds work into the base
    assert set(writer.entries()) == {'1000'}
    with writer_fence(root), writer._lock:
        writer._compact(writer._checked()[0])    # same index rewrite/pruning used by production fold
    marks = [json.loads(path.read_bytes())['records'] for path in root.glob('active-generations-*.mark')]
    assert marks and set(marks) == {0}
    index.write_bytes(old)
    try:
        reopened = mg.ManagedGenerations(root)
    except ManagedDownloadError:
        return
    assert set(reopened.entries()) == {'1000'}


