import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import closing, contextmanager
from dataclasses import replace
from importlib import import_module
from pathlib import Path
from threading import Event

import pytest


MODEL = 'a' * 64
REVISION = 'b' * 64


@pytest.fixture
def store_api():
    return import_module('pixivvault_nicegui.visual_preference_store')


def open_store(api, root, *, profile='test-profile', account='test-account', protected=()):
    return api.VisualPreferenceStore(root, profile=profile, account=account,
                                     protected_paths=protected)


def generation(store, *, model=MODEL, revision=REVISION):
    return store.advance_generation(model_sha=model, preprocessing_version='v1',
                                    judgment_fingerprint=revision)


def model(active, vector=(1, 0)):
    visual = import_module('pixivvault_nicegui.visual_preference')
    key = visual.EmbeddingKey('training-work', 0, 'view', 'c' * 64, active.model_sha,
                              active.preprocessing_version)
    sample = visual.TrainingSample(visual.Embedding(key, vector), 'author', 'like')
    return visual.fit(active, [sample])


def test_foreign_and_corrupt_databases_are_refused_without_modification(store_api, tmp_path):
    root = tmp_path / 'owned'
    store = open_store(store_api, root)
    with sqlite3.connect(store.path) as db:
        db.execute('PRAGMA user_version=99')
    unknown = store.path.read_bytes()
    with pytest.raises(store_api.VisualPreferenceStoreError):
        open_store(store_api, root)
    assert store.path.read_bytes() == unknown
    store.path.write_bytes(b'broken database')
    with pytest.raises(store_api.VisualPreferenceStoreError):
        open_store(store_api, root)
    assert store.path.read_bytes() == b'broken database'


def test_replacement_after_exclusive_reservation_is_not_initialized(store_api, tmp_path, monkeypatch):
    foreign = tmp_path / 'foreign.sqlite3'
    with closing(sqlite3.connect(foreign)) as db, db:
        db.execute('CREATE TABLE untouched(x)')
    original = foreign.read_bytes()
    root = tmp_path / 'owned'
    original_open = Path.open

    @contextmanager
    def reserve_then_swap(path, args, kwargs):
        with original_open(path, *args, **kwargs) as stream:
            yield stream
        path.rename(path.with_suffix('.reserved'))
        foreign.rename(path)

    def open_path(path, *args, **kwargs):
        if path.name == 'visual_preference.sqlite3' and args == ('xb',):
            return reserve_then_swap(path, args, kwargs)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', open_path)
    with pytest.raises(store_api.VisualPreferenceStoreError):
        open_store(store_api, root)
    assert (root / 'visual_preference.sqlite3').read_bytes() == original


def test_links_and_replaced_database_never_reach_sqlite(store_api, tmp_path, monkeypatch):
    root = tmp_path / 'owned'
    store = open_store(store_api, root)
    old = generation(store)
    outside = tmp_path / 'outside.sqlite3'
    store.path.rename(outside)
    store.path.hardlink_to(outside)
    original = outside.read_bytes()
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **k: pytest.fail('opened a hardlink'))
    with pytest.raises(store_api.VisualPreferenceStoreError):
        store.publish(old, model(old).to_payload())
    with pytest.raises(store_api.VisualPreferenceStoreError):
        open_store(store_api, root)
    assert outside.read_bytes() == original


def test_root_scope_overlap_and_purge_are_confined(store_api, tmp_path):
    library = tmp_path / 'images'
    library.mkdir()
    learning = tmp_path / 'learning.sqlite3'
    learning.write_bytes(b'untouched')
    for root in (library, library / 'nested', tmp_path, learning):
        with pytest.raises(store_api.VisualPreferenceStoreError):
            open_store(store_api, root, protected=(library, learning))
    unmarked = tmp_path / 'unmarked'
    unmarked.mkdir()
    with pytest.raises(store_api.VisualPreferenceStoreError):
        open_store(store_api, unmarked)
    root = tmp_path / 'owned'
    store = open_store(store_api, root, protected=(library, learning))
    other = open_store(store_api, tmp_path / 'other', account='other-account')
    for scope in ({'account': 'other-account'}, {'profile': 'other-profile'}):
        with pytest.raises(store_api.VisualPreferenceStoreError):
            open_store(store_api, root, **scope)
    marker = (root / store_api.OWNER_MARKER).read_text(encoding='utf-8')
    assert 'test-profile' not in marker and 'test-account' not in marker
    old = generation(store)
    other_old = generation(other)
    assert other_old != old
    assert not other.publish(old, model(old).to_payload())
    store.append_event('presentation', {'items': [{'work_id': 'synthetic', 'base_rank': 1,
                                                 'frame': 'certain', 'image_available': True}]})
    store.append_event('judgment', {'work_id': 'synthetic', 'verdict': 'like', 'revision': 1})
    assert [event.kind for event in store.events()] == ['presentation', 'judgment']
    with sqlite3.connect(store.path) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute('UPDATE ranking_events SET kind = ?', ('changed',))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute('DELETE FROM ranking_events')
    assert store.publish(old, model(old).to_payload())
    assert other.publish(other_old, model(other_old, (0, 1)).to_payload())
    store.purge()
    assert store.events() == ()
    assert store.current_generation() is None
    assert store.load_model(old) is None
    assert not store.publish(old, model(old).to_payload())
    assert other.load_model(other_old) == model(other_old, (0, 1))
    assert learning.read_bytes() == b'untouched'
    assert list(library.iterdir()) == []


def test_windows_native_spelling_of_protected_root_cannot_hide_overlap(store_api, tmp_path):
    native = import_module('pixivvault_nicegui.fs_paths').native_io_path
    library = tmp_path / 'images'
    library.mkdir()
    root = library / 'visual'
    with pytest.raises(store_api.VisualPreferenceStoreError):
        open_store(store_api, root, protected=(native(library),))
    assert not root.exists()


@pytest.mark.parametrize('reason', ['judgment', 'reason_change', 'undo', 'restore', 'reset',
                                    'image_change', 'model_change', 'off', 'account_change'])
def test_invalidation_rejects_late_publish_and_already_loaded_snapshot(store_api, tmp_path, reason):
    root = tmp_path / 'owned'
    store = open_store(store_api, root)
    worker = open_store(store_api, root)
    old = generation(store)
    assert worker.publish(old, model(old).to_payload())
    loaded = worker.load_model(old)
    assert loaded == model(old)
    store.invalidate(reason)
    assert not worker.is_current(old)
    assert worker.load_model(old) is None
    assert not worker.publish(old, model(old, (0, 1)).to_payload())
    new = generation(store, model='c' * 64 if reason == 'model_change' else MODEL)
    assert new.number > old.number
    assert not worker.publish(new, loaded.to_payload())
    assert worker.publish(new, model(new, (0, 1)).to_payload())
    assert worker.load_model(new) == model(new, (0, 1))


def test_invalid_payload_cannot_claim_generation(store_api, tmp_path):
    store = open_store(store_api, tmp_path / 'owned')
    active = generation(store)
    fitted = model(active)
    assert not store.publish(active, {**fitted.to_payload(), 'dimension': 999})
    assert store.load_model(active) is None
    assert store.publish(active, fitted.to_payload())
    assert store.publish(active, fitted.to_payload())
    assert not store.publish(active, model(active, (0, 1)).to_payload())
    assert store.load_model(active) == fitted


def test_embeddings_are_fp32_and_fallback_is_separate(store_api, tmp_path):
    store = open_store(store_api, tmp_path / 'owned')
    active = generation(store)
    key = store_api.EmbeddingKey('work', 0, 'view', 'd' * 64, MODEL, 'v1')
    fallback = replace(key, variant='thumb_fallback', image_sha='e' * 64)
    assert store.put_embedding(active, key, (1.0, 0.5))
    assert store.put_embedding(active, fallback, (0.0, 1.0))
    assert store.get_embedding(active, key) == (1.0, 0.5)
    assert store.get_embedding(active, fallback) == (0.0, 1.0)
    assert store.get_embedding(active, replace(key, image_sha='e' * 64)) is None
    assert store.get_embedding(active, replace(key, model_sha='f' * 64)) is None
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT DISTINCT length(vector) FROM candidate_embeddings').fetchall() == [(8,)]
    with pytest.raises(ValueError):
        store.put_embedding(active, key, (float('nan'),))
    store.invalidate('image_change')
    assert not store.put_embedding(active, key, (0.0, 1.0))
    assert store.get_embedding(active, key) is None
    new = generation(store)
    assert store.get_embedding(new, key) == (1.0, 0.5)
    store.purge()
    assert store.get_embedding(generation(store), key) is None


def test_embedding_blob_affinity_cannot_admit_text(store_api, tmp_path):
    store = open_store(store_api, tmp_path / 'owned')
    active = generation(store)
    key = store_api.EmbeddingKey('work', 0, 'view', 'd' * 64, MODEL, 'v1')
    assert store.put_embedding(active, key, (1.0,))
    with closing(sqlite3.connect(store.path)) as db, db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE candidate_embeddings SET vector='abcd'")
    assert store.get_embedding(active, key) == (1.0,)


def test_unrecognized_file_and_sqlite_sidecar_are_preserved(store_api, tmp_path):
    root = tmp_path / 'owned'
    store = open_store(store_api, root)
    active = generation(store)
    sidecar = Path(str(store.path) + '-journal')
    sidecar.write_bytes(b'foreign journal')
    with pytest.raises(store_api.VisualPreferenceStoreError):
        store.purge()
    assert sidecar.read_bytes() == b'foreign journal'
    sidecar.unlink()
    unrelated = root / 'keep.json'
    unrelated.write_text(json.dumps({'important': True}), encoding='utf-8')
    store.purge()
    assert not store.is_current(active)
    assert json.loads(unrelated.read_text(encoding='utf-8')) == {'important': True}


def test_invalidation_waits_for_live_writer_before_rejecting_stale_work(store_api, tmp_path):
    root = tmp_path / 'owned'
    store = open_store(store_api, root)
    other = open_store(store_api, root)
    active = generation(store)
    has_journal = Event()
    release_writer = Event()

    def worker():
        with store._transaction(write=True) as db:
            db.execute('INSERT INTO preference_models VALUES (?,?)', (active.number, '{}'))
            assert Path(str(store.path) + '-journal').is_file()
            has_journal.set()
            assert release_writer.wait(10)

    with ThreadPoolExecutor(max_workers=2) as threads:
        writing = threads.submit(worker)
        try:
            assert has_journal.wait(5)
            invalidating = threads.submit(other.invalidate, 'undo')
            with pytest.raises(TimeoutError):
                invalidating.result(timeout=0.25)
        finally:
            release_writer.set()
        writing.result()
        invalidating.result()
    assert not store.is_current(active)
    assert not store.publish(active, model(active).to_payload())
