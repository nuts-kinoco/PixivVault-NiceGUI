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


def test_old_index_with_published_journal_is_rejected(tmp_path):
    root = make_root(tmp_path)
    seed_index(root, 0)
    index = root / 'active-generations.json'
    old_index = index.read_bytes()
    g = mg.ManagedGenerations(root)
    g.prepare_for_publication()
    publish(g, root, '1000')
    index.write_bytes(old_index)
    with pytest.raises(ManagedDownloadError):
        mg.ManagedGenerations(root)


