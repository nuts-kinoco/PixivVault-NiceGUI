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


def test_legacy_journal_migration_interrupted_before_append_does_not_lose_prior_commits(tmp_path, monkeypatch):
    old = historical_module('d64879a')
    root = make_root(tmp_path)
    existing = seed_index(root, 2)
    idx = root / 'active-generations.json'
    saved_idx = idx.read_bytes()
    publish(old.ManagedGenerations(root), root, '1000')
    legacy = root / 'active-generations.journal'
    assert legacy.exists()
    writer = mg.ManagedGenerations(root)
    assert set(writer.entries()) == set(existing) | {'1000'}

    def interruption(*_):
        raise OSError('synthetic interruption before the first v5 append')

    monkeypatch.setattr(writer, '_append_journal', interruption)
    with pytest.raises(ManagedDownloadError, match='publication failed'):
        publish(writer, root, '1001')
    assert json.loads(idx.read_bytes())['version'] == 5
    assert not legacy.exists()
    assert (root / mg._V5_COMMITTED).read_bytes() == mg._V5_DONE
    assert set(mg.ManagedGenerations(root).entries()) == set(existing) | {'1000'}
    idx.write_bytes(saved_idx)
    try:
        observed = set(mg.ManagedGenerations(root).entries())
    except ManagedDownloadError:
        return
    print(f'migrated legacy entry: before=[1000, 100000, 100001], after={sorted(observed)}')
    assert observed == set(existing) | {'1000'}


@pytest.mark.parametrize('stage', ['journal_rename', 'index_replace'])
def test_pending_upgrade_recovers_when_interrupted_before_v5_index(tmp_path, monkeypatch, stage):
    root = make_root(tmp_path)
    old_entries = seed_index(root, 2)
    idx = root / 'active-generations.json'
    saved_idx = idx.read_bytes()
    writer = mg.ManagedGenerations(root)
    original = mg._replace_retrying
    marker_path = root / mg._V5_COMMITTED

    def interrupt_before_replace(source, target, patience=5):
        if ((stage == 'index_replace' and target == idx)
                or (stage == 'journal_rename' and target.name.endswith('.journal'))):
            raise OSError('synthetic interruption during upgrade')
        return original(source, target, patience)

    with monkeypatch.context() as context:
        context.setattr(mg, '_replace_retrying', interrupt_before_replace)
        with pytest.raises(ManagedDownloadError, match='publication failed'):
            publish(writer, root, '1000')
    assert idx.read_bytes() == saved_idx
    assert marker_path.read_bytes().startswith(b'upgrade:')
    reopened = mg.ManagedGenerations(root)
    assert set(reopened.entries()) == set(old_entries)
    publish(reopened, root, '1000')
    assert set(mg.ManagedGenerations(root).entries()) == set(old_entries) | {'1000'}
    assert marker_path.read_bytes() == mg._V5_DONE
    idx.write_bytes(saved_idx)
    with pytest.raises(ManagedDownloadError, match='index rejected'):
        mg.ManagedGenerations(root)


def test_upgrade_marker_failure_preserves_legacy_journal(tmp_path, monkeypatch):
    old = historical_module('d64879a')
    root = make_root(tmp_path)
    baseline = seed_index(root, 2)
    idx = root / 'active-generations.json'
    old_index = idx.read_bytes()
    publish(old.ManagedGenerations(root), root, '1000')
    writer = mg.ManagedGenerations(root)
    original = writer._write_v5_marker

    def fail_finalization(data):
        if data == mg._V5_DONE:
            raise OSError('marker finalization interrupted')
        return original(data)

    with monkeypatch.context() as patch:
        patch.setattr(writer, '_write_v5_marker', fail_finalization)
        with pytest.raises(ManagedDownloadError):
            publish(writer, root, '1001')
    assert (root / 'active-generations.journal').exists()
    new_index = idx.read_bytes()
    assert set(mg.ManagedGenerations(root).entries()) == set(baseline) | {'1000'}
    idx.write_bytes(old_index)
    with pytest.raises(ManagedDownloadError, match='index rejected'):
        mg.ManagedGenerations(root)
    idx.write_bytes(new_index)
    reopened = mg.ManagedGenerations(root)
    assert set(reopened.entries()) == set(baseline) | {'1000'}
    publish(reopened, root, '1001')
    assert set(mg.ManagedGenerations(root).entries()) == set(baseline) | {'1000', '1001'}
