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
from account_test_support import observed_scope
from pixivvault_nicegui import managed_generations as mg
from pixivvault_nicegui.favourites import FavouriteBook
from pixivvault_nicegui.following_sync import FollowingSyncStore, FollowingSyncError
from pixivvault_nicegui.managed_downloads import ManagedDownloadError
from pixivvault_nicegui.managed_scheduler import ManagedScheduler
from pixivvault_nicegui.ui.scheduler_view import SchedulerView
from test_v_dl_cap_20261117 import make_root, journal_of, mark_of, publish, seed_index
from test_v_dl_priority_20261117 import ACCOUNT, sync
from test_t_dl_engine_20261117 import env


def create_stars(tmp_path):
    root = tmp_path / 'managed'
    root.mkdir()
    following = FollowingSyncStore(tmp_path / 'follow')
    ledger = ManagedScheduler(root)
    sync(following, 3)
    book = FavouriteBook(following, scheduler=ledger)
    ledger.attach_favourites(book)
    return following, ledger, book


@pytest.mark.asyncio
async def test_nonfollowed_manual_star_is_not_silently_discarded(tmp_path):
    following, ledger, book = create_stars(tmp_path)
    book.set('1000', True, account_id=ACCOUNT)
    services = SimpleNamespace(managed_scheduler=ledger, live_client=observed_scope(ACCOUNT).reader,
                               managed_schedule_runner=SimpleNamespace(settings_changed=lambda: None))
    client = Client(ui.page('/sol-form-unknown-id'))
    try:
        with client:
            view = SchedulerView(services)
        view._load(ledger)
        view.authors.set_value('1000, 9999')
        view._mark_settings_dirty()
        await view.save()
        print(f'saved field={view.authors.value!r}; action={view.action_notice.text!r}; status={view.status.text!r}')
        assert '9999' in view.authors.value or '9999' in view.action_notice.text or '9999' in view.status.text
    finally:
        client.delete()
        ledger.close()
        following.close()




