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


@pytest.mark.parametrize('reader_mode', ['cold', 'warm'])
def test_restoring_only_old_v5_index_does_not_hide_later_save(tmp_path, reader_mode):
    root = make_root(tmp_path)
    writer = mg.ManagedGenerations(root)
    writer.prepare_for_publication()
    idx = root / 'active-generations.json'
    publish(writer, root, '1000')
    saved_idx = idx.read_bytes()
    retired_journal, retired_mark = journal_of(root), mark_of(root)
    writer._compact(writer._loaded)
    publish(writer, root, '1001')
    observer = mg.ManagedGenerations(root)
    assert set(observer.entries()) == {'1000', '1001'}
    assert retired_journal.exists() and retired_mark.exists()
    assert journal_of(root) != retired_journal
    assert (root / mg._V5_COMMITTED).read_bytes() == mg._V5_DONE
    idx.write_bytes(saved_idx)
    try:
        observed = set((observer if reader_mode == 'warm' else mg.ManagedGenerations(root)).entries())
    except ManagedDownloadError:
        return
    print(f'{reader_mode}: before=[1000, 1001], after={sorted(observed)}')
    assert observed == {'1000', '1001'}


def test_fold_head_and_journal_share_monotonic_generation(tmp_path):
    root = make_root(tmp_path)
    writer = mg.ManagedGenerations(root)
    writer.prepare_for_publication()
    publish(writer, root, '1000')
    index_path = root / 'active-generations.json'
    before_bytes = index_path.read_bytes()
    before = json.loads(before_bytes)
    writer._compact(writer._loaded)
    current = json.loads(index_path.read_bytes())
    pointer = current['journal']
    header = json.loads(journal_of(root).read_bytes().splitlines()[0])
    mark = json.loads(mark_of(root).read_bytes())
    head = json.loads((root / mg._V5_HEAD).read_bytes())
    import hashlib
    assert pointer['generation'] == before['journal']['generation'] + 1
    assert pointer['parent'] == hashlib.sha256(before_bytes).hexdigest()
    assert header['generation'] == mark['generation'] == head['generation'] == pointer['generation']
    assert header['parent'] == pointer['parent']
    assert head['digest'] == hashlib.sha256(index_path.read_bytes()).hexdigest()


def test_fold_recovers_committed_head_if_index_replace_is_interrupted(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    writer = mg.ManagedGenerations(root)
    writer.prepare_for_publication()
    publish(writer, root, '1000')
    old_index = (root / 'active-generations.json').read_bytes()
    replace = mg._replace_retrying

    def interrupt(source, target, patience=5):
        if target == writer.index_path:
            raise OSError('synthetic interruption after committing head')
        return replace(source, target, patience)

    with monkeypatch.context() as patch:
        patch.setattr(mg, '_replace_retrying', interrupt)
        with pytest.raises(OSError):
            writer._compact(writer._loaded)
    assert (root / 'active-generations.json').read_bytes() == old_index
    recovered = mg.ManagedGenerations(root)
    assert set(recovered.entries()) == {'1000'}
    assert (root / 'active-generations.json').read_bytes() != old_index
    publish(recovered, root, '1001')
    assert set(mg.ManagedGenerations(root).entries()) == {'1000', '1001'}


def test_fold_without_head_is_rejected(tmp_path):
    root = make_root(tmp_path)
    writer = mg.ManagedGenerations(root)
    writer.prepare_for_publication()
    (root / mg._V5_HEAD).unlink()
    with pytest.raises(ManagedDownloadError, match='index rejected'):
        mg.ManagedGenerations(root)


def test_old_v5_is_refused_before_next_publication(tmp_path):
    from test_w_fix3_migration import historical_module
    old = historical_module('823871e')
    root = make_root(tmp_path)
    prior = old.ManagedGenerations(root)
    prior.prepare_for_publication()
    publish(prior, root, '1000')
    index = root / 'active-generations.json'
    stale = index.read_bytes()
    prior._compact(prior._loaded)
    publish(prior, root, '1001')
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(ManagedDownloadError, match='旧形式'):
        mg.ManagedGenerations(root)
    assert {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()} == before
    index.write_bytes(stale)
    with pytest.raises(ManagedDownloadError, match='旧形式'):
        mg.ManagedGenerations(root)


@pytest.mark.parametrize('reader_mode', ['cold', 'warm'])
def test_already_rolled_back_sequenced_v5_rejects_missing_published_work(tmp_path, reader_mode):
    root = make_root(tmp_path)
    writer = mg.ManagedGenerations(root)
    writer.prepare_for_publication()
    publish(writer, root, '1000')
    index = root / 'active-generations.json'
    stale = index.read_bytes()
    writer._compact(writer._loaded)
    publish(writer, root, '1001')
    observer = mg.ManagedGenerations(root)
    assert set(observer.entries()) == {'1000', '1001'}
    index.write_bytes(stale)
    with pytest.raises(ManagedDownloadError, match='index rejected'):
        (observer if reader_mode == 'warm' else mg.ManagedGenerations(root)).entries()


def test_warm_reader_recovers_pending_head(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    writer = mg.ManagedGenerations(root)
    writer.prepare_for_publication()
    publish(writer, root, '1000')
    observer = mg.ManagedGenerations(root)
    replace = mg._replace_retrying
    def interrupt(source, target, patience=5):
        if target == writer.index_path:
            raise OSError('index temporarily held')
        return replace(source, target, patience)
    with monkeypatch.context() as patch:
        patch.setattr(mg, '_replace_retrying', interrupt)
        with pytest.raises(OSError):
            writer._compact(writer._loaded)
    assert set(observer.entries()) == {'1000'}
    assert observer.capacity()['entries'] == 1


def test_old_v5_with_uncommitted_empty_journal_is_refused(tmp_path):
    from test_w_fix3_migration import historical_module
    old = historical_module('823871e')
    root = make_root(tmp_path)
    writer = old.ManagedGenerations(root)
    writer.prepare_for_publication()
    publish(writer, root, '1000')
    jid = 'a' * 32
    header = mg._canonical({'id': jid, 'kind': mg._JOURNAL_KIND, 'version': 2}) + b'\n'
    (root / (mg._JOURNAL_FILE % jid)).write_bytes(header)
    with pytest.raises(ManagedDownloadError, match='旧形式'):
        mg.ManagedGenerations(root)


def test_visibility_cache_tracks_head_and_still_serves_visible_pages(tmp_path):
    root = make_root(tmp_path)
    writer = mg.ManagedGenerations(root)
    writer.prepare_for_publication()
    publish(writer, root, '1000')
    writer.assert_page_visible('1000', 0)
    writer.assert_page_visible('1000', 0)
    (root / mg._V5_HEAD).unlink()
    with pytest.raises(ManagedDownloadError, match='index rejected'):
        writer.assert_page_visible('1000', 0)


def test_pending_recovery_does_not_wait_behind_writer_fence(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor, wait
    from threading import Event
    from pixivvault_nicegui.managed_root_writer import writer_fence
    root = make_root(tmp_path)
    writer = mg.ManagedGenerations(root)
    writer.prepare_for_publication()
    publish(writer, root, '1000')
    observer = mg.ManagedGenerations(root)
    before = observer.state_snapshot()
    replace = mg._replace_retrying
    def interrupt(source, target, patience=5):
        if target == writer.index_path:
            raise OSError('index temporarily held')
        return replace(source, target, patience)
    with monkeypatch.context() as patch:
        patch.setattr(mg, '_replace_retrying', interrupt)
        with pytest.raises(OSError):
            writer._compact(writer._loaded)
    held, release = Event(), Event()
    def hold_fence():
        with writer_fence(root):
            held.set()
            assert release.wait(8)
    with ThreadPoolExecutor(max_workers=2) as pool:
        holder = pool.submit(hold_fence)
        assert held.wait(8)
        reader = pool.submit(observer.state_snapshot)
        try:
            assert wait((reader,), timeout=0.5).done, 'reader blocked behind writer fence'
            during = reader.result()
            assert dict(during['entries']) == dict(before['entries'])
            assert during['revision'] == before['revision']
            assert during['exclusions'] == before['exclusions']
            assert during['operations'] == before['operations']
        finally:
            release.set()
        holder.result(timeout=8)
    assert set(observer.entries()) == {'1000'}


def test_unrelated_append_does_not_invalidate_page_visibility_mid_check(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    writer = mg.ManagedGenerations(root)
    writer.prepare_for_publication()
    publish(writer, root, '1000')
    original = writer._file_identity
    calls = 0
    def append_between_checks(path):
        nonlocal calls
        if path == writer.index_path:
            calls += 1
            if calls == 2:
                publish(writer, root, '1001')
        return original(path)
    monkeypatch.setattr(writer, '_file_identity', append_between_checks)
    writer.assert_page_visible('1000', 0)
    assert calls >= 2
