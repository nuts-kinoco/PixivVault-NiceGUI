"""Original recommendation previews retain metadata without implicit GETs.

Only generated records and a newly created SQLite fixture are used here.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from pixivvault_nicegui.recommendation_acquisition import decode, encode, work
from pixivvault_nicegui.recommendation_state import (
    DiscoveryRecommendationSource,
    FrozenRecommendationSource,
    RecommendationController,
    RecommendationGroup,
    RecommendationPreferences,
)
from test_recommendation_acquisition import make
from test_recommendation_state import source_db


THUMB = 'https://i.pximg.net/img-master/recommend-fixture-11_p0.jpg'


def visual_row(work_id='11', *, kind=1, count=3):
    return dict(id=work_id, title='Synthetic recommendation', user_id='91',
                user_name='Synthetic author', thumbnail_url=THUMB,
                page_count=count, illust_type=kind, tags=['synthetic'],
                create_date='2026-01-02T03:04:05+09:00', x_restrict=0)


@pytest.mark.parametrize('kind,count', [(0, 1), (1, 3), (2, 1)])
def test_reader_preview_survives_projection_and_persisted_group_roundtrip(kind, count):
    projected = work(visual_row(kind=kind, count=count))
    restored = decode(json.loads(json.dumps(encode(
        RecommendationGroup('fixture', 'Synthetic group', (projected,)))))).works[0]
    assert getattr(projected, 'thumbnail_url', '') == THUMB
    assert getattr(restored, 'thumbnail_url', '') == THUMB
    assert getattr(restored, 'page_count', None) == count
    assert getattr(restored, 'illust_type', None) == kind
    assert tuple(getattr(restored, 'tags', ())) == ('synthetic',)


def test_old_cache_without_preview_metadata_remains_unknown_without_fetch():
    restored = decode(dict(key='old', title='Old cache', works=[dict(
        work_id='11', title='Old title', author_id='91', author_name='Old author')]))
    item = restored.works[0]
    assert item.work_id == '11'
    assert getattr(item, 'thumbnail_url', '') == ''
    assert getattr(item, 'page_count', None) is None
    assert getattr(item, 'illust_type', None) is None


def test_frozen_superlike_and_carryover_keep_supplied_preview_and_source_bytes(tmp_path):
    path = source_db(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute('ALTER TABLE superlike_info_cache ADD COLUMN thumbnail_url TEXT')
        db.execute('ALTER TABLE superlike_info_cache ADD COLUMN illust_type INTEGER')
        db.execute('ALTER TABLE superlike_info_cache ADD COLUMN page_count INTEGER')
        db.execute('UPDATE superlike_info_cache SET thumbnail_url=?,illust_type=1,page_count=3', (THUMB,))
        payload = [dict(tag='fixture', display='Synthetic group', works=[visual_row()])]
        db.execute("UPDATE recommend_section_carryover SET payload_json=? WHERE section='certain'",
                   (json.dumps(payload),))
    before = (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
    value = FrozenRecommendationSource(path).load()
    for section in ('superlike', 'certain'):
        item = dict(value.sections)[section][0].works[0]
        assert getattr(item, 'thumbnail_url', '') == THUMB
        assert getattr(item, 'page_count', None) == 3
        assert getattr(item, 'illust_type', None) == 1
    assert (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns) == before
    assert all(not path.with_name(path.name + suffix).exists()
               for suffix in ('-wal', '-shm', '-journal'))


def test_discovery_projection_keeps_known_preview_without_resolving_any_work():
    calls = []
    class Discovery:
        def recommendation_snapshot(self):
            calls.append('local snapshot')
            row = visual_row()
            row['work_id'] = row.pop('id')
            row['author_id'] = row.pop('user_id')
            row['author_name'] = row.pop('user_name')
            return dict(works=[row], judged_ids=frozenset({'11'}),
                        superlike_ids=frozenset({'11'}), superlike_order=('11',))
        def get_discovery_work(self, *_args):
            pytest.fail('reading a cached recommendation must not GET metadata')
    item = DiscoveryRecommendationSource(Discovery()).load().sections[0][1][0].works[0]
    assert getattr(item, 'thumbnail_url', '') == THUMB
    assert getattr(item, 'page_count', None) == 3
    assert calls == ['local snapshot']


def test_explicit_update_cache_reopens_with_preview_and_no_metadata_refetch(tmp_path):
    controller, source, fetch = make(tmp_path)
    fetch.detail = lambda work_id: visual_row(work_id)
    root = controller.preferences.root
    try:
        controller.update(controller.snapshot()['revision'])
    finally:
        controller.close()
    fetch.calls.clear()
    reopened = RecommendationController(source, RecommendationPreferences(root), acquisition=fetch)
    try:
        state = reopened.refresh()
        item = state['sections']['superlike'][0].works[0]
        assert getattr(item, 'thumbnail_url', '') == THUMB
        assert getattr(item, 'page_count', None) == 3
        assert fetch.calls == []
    finally:
        reopened.close()
