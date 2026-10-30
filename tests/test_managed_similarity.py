"""Owned GPU embedding ledger, immutable legacy overlay and resume invariants."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import sqlite3
import struct
from types import SimpleNamespace
from threading import RLock

import pytest

from pixivvault_nicegui.egara_inference import EGARA_BB_SHA256, STYLE, COMPOSITION, MODEL
from pixivvault_nicegui.managed_similarity import (
    CombinedSimilaritySource, ManagedSimilarityError, ManagedSimilarityIndex, MARKER,
)


def vector(index=0):
    values = [0.] * 1024
    values[index] = 1.
    return struct.pack('<1024f', *values)


def embeddings(style=0, composition=0):
    return {STYLE: vector(style), COMPOSITION: vector(composition)}


def digest(value):
    return sha256(value).hexdigest()


def frozen_db(tmp_path):
    root = tmp_path / 'frozen'
    root.mkdir()
    path = root / 'legacy.sqlite3'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE work_embeddings(work_id TEXT,kind TEXT,model TEXT,page INTEGER,dim INTEGER,vec BLOB,PRIMARY KEY(work_id,kind,model,page))')
        for work_id, page, comp, style in [('1', 1, 0, 1), ('2', 2, 1, 2)]:
            for kind, index in ((STYLE, style), (COMPOSITION, comp)):
                conn.execute('INSERT INTO work_embeddings VALUES(?,?,?,?,?,?)',
                             (work_id, kind, MODEL, page, 1024, vector(index)))
    return path


@pytest.fixture
def store(tmp_path):
    owned = tmp_path / 'own'
    owned.mkdir()
    index = ManagedSimilarityIndex(owned / 'embeddings')
    try:
        yield index
    finally:
        index.close()


def test_resumable_atomic_two_kind_record_and_no_overwrite(store):
    image_a, image_b = digest(b'image a'), digest(b'image b')
    receipt = digest(b'receipt')
    assert store.commit('11', 3, image_a, receipt, embeddings()) is True
    assert store.commit('11', 3, image_a, receipt, embeddings(style=1)) is False
    assert store.source_status('11', 3, image_a, receipt) is True
    assert store.source_status('11', 3, image_b, receipt) is False
    assert store.commit('11', 3, image_b, receipt, embeddings(style=1)) is True
    snap = store.snapshot_rows()
    assert snap[STYLE] == (('11', 3, 1024, vector(1)),)
    assert snap[COMPOSITION] == (('11', 3, 1024, vector(0)),)
    assert store.path.stat().st_size > 0
    assert (store.root / MARKER).is_file()


def test_atomic_rollback_on_midway_failure(store):
    store._conn.executescript('''
        CREATE TRIGGER fail_comp BEFORE INSERT ON work_embeddings
        WHEN NEW.kind='composition'
        BEGIN SELECT RAISE(ABORT,'synthetic failure'); END;
    ''')
    with pytest.raises(sqlite3.Error):
        store.commit('11', 1, digest(b'image'), digest(b'receipt'), embeddings())
    assert store.snapshot_rows() == {STYLE: (), COMPOSITION: ()}
    assert not store.source_status('11', 1, digest(b'image'), digest(b'receipt'))


def test_foreign_root_corrupt_signature_and_symlink_rejected(tmp_path, store):
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    with pytest.raises(ManagedSimilarityError):
        ManagedSimilarityIndex(foreign)
    with pytest.raises(ManagedSimilarityError):
        store.commit('not-id', 1, digest(b'image'), digest(b'receipt'), embeddings())
    with pytest.raises(ManagedSimilarityError):
        store.commit('12', 1, digest(b'image'), digest(b'receipt'), {STYLE: vector()})
    with pytest.raises(ManagedSimilarityError):
        store.commit('12', 1, digest(b'image'), digest(b'receipt'),
                     {STYLE: vector(), COMPOSITION: b'bad'})
    with pytest.raises(ManagedSimilarityError):
        store.commit('12', 1, digest(b'image'), digest(b'receipt'),
                     embeddings(), model_sha256=digest(b'other model'))
    store.close()
    with pytest.raises(ManagedSimilarityError):
        store.snapshot_rows()


def test_legacy_overlay_returns_cross_source_matches_and_preserves_original(tmp_path, store):
    old = frozen_db(tmp_path)
    before = old.read_bytes(), old.stat().st_mtime_ns
    receipt = digest(b'receipt')
    store.commit('3', 1, digest(b'new image'), receipt, embeddings(style=1, composition=0))
    store.commit('1', 2, digest(b'page two'), receipt, embeddings(style=1, composition=0))
    joined = CombinedSimilaritySource(old, store)
    try:
        result = joined.search('1', 1)
        assert result['status'] == 'ok' and result['kind'] == 'composition'
        assert result['pool_size'] == 4 and result['excluded_same_work'] == 1
        assert result['results'][0] == (('3', 1), 1.)
        assert all(item[0][0] != '1' for item in result['results'])
        chosen, author = joined.search_author(('1', '2'), '1')
        assert chosen == '1' and author['status'] == 'ok'
    finally:
        joined.close()
    assert (old.read_bytes(), old.stat().st_mtime_ns) == before
    assert all(not Path(str(old) + suffix).exists() for suffix in ('-wal','-shm','-journal'))


def test_managed_only_query_and_missing_embedding_status(store):
    store.commit('11', 1, digest(b'image 11'), digest(b'receipt'), embeddings())
    source = CombinedSimilaritySource(None, store)
    try:
        assert source.search('11')['status'] == 'ok'
        assert source.search('12')['status'] == 'no_query_embedding'
        assert source.search_author(['11'], '11')[0] == '11'
    finally:
        source.close()


class Writer:
    def __init__(self, content=b'generated image bytes'):
        self.content = content
        self.revision = digest(b'receipt')
        self.visible = True
        self.calls = []

    def work_lock(self, wid):
        return RLock()

    def verified_saved_receipt(self, wid, *, verify_pages=True):
        self.calls.append('verified')
        if wid != '11':
            return None
        receipt = {'work_id': '11', 'page_count': 1,
                   'files': [{'sha256': digest(self.content)}]}
        return 'generated/receipt.json', receipt, self.revision, None, None

    def read_saved_page(self, relative, page, expected):
        self.calls.append('read')
        assert relative == 'generated/receipt.json' and page == 0 and expected == self.revision
        return self.content

    def assert_page_visible(self, wid, page):
        self.calls.append('visible')
        if not self.visible:
            raise ValueError('quarantined')


class Inference:
    model_sha256 = EGARA_BB_SHA256
    def __init__(self):
        self.calls = 0
    def infer(self, data):
        self.calls += 1
        return embeddings()


def test_verified_receipt_resumable_and_current_visible_only(store):
    writer, inference = Writer(), Inference()
    assert store.analyze_saved_page(writer, inference, '11', 0) is True
    assert inference.calls == 1
    assert store.analyze_saved_page(writer, inference, '11', 0) is False
    assert inference.calls == 1
    writer.content = b'different saved data'
    writer.visible = False
    with pytest.raises(ValueError, match='quarantined'):
        store.analyze_saved_page(writer, inference, '11', 0)
    assert inference.calls == 1  # current receipt/ledger rejects quarantined page before I/O
    assert store.source_status('11', 1, digest(b'generated image bytes'), writer.revision)


def test_changed_receipt_during_inference_writes_nothing(store):
    writer = Writer()
    inference = Inference()
    def changed(data):
        writer.revision = digest(b'new receipt')
        return embeddings()
    inference.infer = changed
    with pytest.raises(ManagedSimilarityError, match='保存世代'):
        store.analyze_saved_page(writer, inference, '11', 0)
    assert store.snapshot_rows() == {STYLE: (), COMPOSITION: ()}
