from concurrent.futures import ThreadPoolExecutor


from datetime import datetime, timezone


from pathlib import Path


from threading import Event


import asyncio


import json


import subprocess


import types


import pytest


from pixivvault_nicegui import managed_generations as mg


from pixivvault_nicegui.bookmark_run import VISIBILITY_ROWS


from pixivvault_nicegui.managed_downloads import ManagedDownloadError


from pixivvault_nicegui.managed_network_stop_integration import ManagedNetworkStopIntegration


from pixivvault_nicegui.managed_scheduler import ScheduleError


from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected


from test_v_dl_cap_20261117 import make_root, journal_of, mark_of, publish, seed_index


from test_w_review_bookmark_resume_20261118 import GeneratedReader, services_with_runs


from test_w_review_favourites_20261118 import create_stars


def historical_module(revision):
    path = 'src/pixivvault_nicegui/managed_generations.py'
    code = subprocess.check_output(['git', 'show', f'{revision}:{path}'])
    old = types.ModuleType('pixivvault_nicegui.review_historical_generations')
    old.__file__ = str(Path(path).resolve())
    exec(compile(code, f'{revision}:{path}', 'exec'), old.__dict__)
    return old


def assert_refused_without_writes(root):
    from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads
    def snapshot():
        return {p.relative_to(root): p.read_bytes() if p.is_file() else None for p in root.rglob('*')}
    before = snapshot()
    for opener in (lambda: mg.ManagedGenerations(root),
                   lambda: ManagedPixivDownloads(root, None, resolve_record=lambda *_: None)):
        with pytest.raises(ManagedDownloadError):
            opener()
        assert snapshot() == before


def test_legacy_journal_with_committed_entries_is_refused_without_writes(tmp_path):
    old = historical_module('d64879a')
    root = make_root(tmp_path)
    seed_index(root, 2, version=2)
    publish(old.ManagedGenerations(root), root, '1000')
    assert (root / 'active-generations.journal').exists()
    assert_refused_without_writes(root)


@pytest.mark.parametrize('stage', ['journal_rename', 'index_replace'])
def test_pending_legacy_upgrade_is_refused_without_writes(tmp_path, stage):
    root = make_root(tmp_path)
    seed_index(root, 2, version=2)
    (root / 'generation-incomplete').mkdir()
    (root / mg._V5_COMMITTED).write_bytes(b'upgrade:' + b'a' * 64 + b'\n')
    if stage == 'journal_rename':
        (root / 'active-generations.journal').write_bytes(b'legacy journal\n')
    else:
        (root / 'generation-incomplete' / ('active-' + 'a' * 32 + '.tmp')).write_bytes(b'candidate')
    assert_refused_without_writes(root)


def test_legacy_upgrade_marker_and_journal_are_preserved(tmp_path):
    root = make_root(tmp_path)
    seed_index(root, 2, version=2)
    (root / 'active-generations.journal').write_bytes(b'legacy journal\n')
    (root / mg._V5_COMMITTED).write_bytes(b'upgrade-v3:' + b'a' * 64 + b'\n')
    assert_refused_without_writes(root)
