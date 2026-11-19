"""WU-4: 画面の状態部品。元 unified_download.py と同じ入力で同じ出力になること。"""
import importlib.util
from datetime import datetime

import pytest

from pixivvault_nicegui import unified_download_state as new

ORIGINAL = 'S:/Projects/PixivVault/unified_download.py'


@pytest.fixture(scope='module')
def old():
    spec = importlib.util.spec_from_file_location('original_udl', ORIGINAL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AUTHORS = [
    {'user_id': '3', 'name': 'Bob', 'last_downloaded': '2026-01-02T03:04:05', 'is_favorite': 0, 'follow_order': 2},
    {'user_id': '10', 'name': 'alice', 'last_downloaded': None, 'is_favorite': 1, 'follow_order': 1},
    {'user_id': '2', 'name': '１２３', 'last_downloaded': '2026-05-01T00:00:00', 'is_favorite': 1, 'follow_order': 3},
    {'user_id': '7', 'name': 'carol', 'last_downloaded': '', 'is_favorite': 0, 'follow_order': 0},
]
COUNTS = {'3': 5, '10': 9, '2': 1}


@pytest.mark.parametrize('key', list(new.SORT_KEYS) + [None, 'bogus', 'id'])
@pytest.mark.parametrize('reverse', [False, True])
def test_sort_five_choices_both_directions_match_the_original(old, key, reverse):
    assert new.sort_authors(AUTHORS, COUNTS, key, reverse) == old.sort_authors(AUTHORS, COUNTS, key, reverse)


def test_sort_labels_are_the_five_original_choices():
    assert tuple(new.SORT_LABELS) == new.SORT_KEYS and len(new.SORT_KEYS) == 5
    assert new.SORT_LABELS['favorite'] == '☆を先に'


def test_favorites_first_and_unknown_last_download_is_oldest():
    assert [a['user_id'] for a in new.sort_authors(AUTHORS, COUNTS, 'favorite')][:2] == ['10', '2']
    assert new.sort_authors(AUTHORS, COUNTS, 'last_downloaded')[0]['user_id'] in ('10', '7')


@pytest.mark.parametrize('text', ['123', '１２３', ' 45 ', '', None, 'a1', '1 2', '０'])
def test_id_query_matches_the_original(old, text):
    assert new.is_id_query(text) == old.is_id_query(text)
    assert new.normalize_query(text) == old.normalize_query(text)


def test_full_width_digits_are_an_id_and_a_digit_name_stays_selectable():
    assert new.is_id_query('１２３') is True
    shown = new.filter_authors(AUTHORS, '123')
    assert [a['user_id'] for a in shown] == ['2']


@pytest.mark.parametrize('query', ['', 'ali', 'BOB', '10', '２', 'zzz'])
def test_filter_matches_the_original_and_never_touches_targets(old, query):
    targets = new.add_targets_bulk([], AUTHORS)
    before = list(targets)
    assert new.filter_authors(AUTHORS, query) == old.filter_authors(AUTHORS, query)
    assert targets == before


def test_targets_never_stack_twice_and_the_first_origin_wins(old):
    t, added = new.add_target([], {'user_id': '5', 'name': 'x'}, 'id')
    assert added
    t, added = new.add_target(t, {'user_id': '5', 'name': 'x'}, 'follow')
    assert not added and t == [{'user_id': '5', 'name': 'x', 'origin': 'id'}]
    assert new.add_target(t, {'user_id': ''}, 'id')[1] is False
    assert new.summarize_targets(t) == old.summarize_targets(t) == {'total': 1, 'id': 1, 'follow': 0}
    assert new.remove_target(t, '5') == [] and new.remove_targets_bulk(t, [5]) == []
    assert new.find_author(AUTHORS, 7)['name'] == 'carol' and new.find_author(AUTHORS, 99) is None


@pytest.mark.parametrize('value', [None, '', '2026-01-02T03:04:05', '2026-01-02T03:04:05+00:00',
                                   'garbage-value-that-is-long'])
def test_format_last_downloaded_matches_the_original(old, value):
    assert new.format_last_downloaded(value) == old.format_last_downloaded(value)
    assert new.format_last_downloaded(None) == '－'


@pytest.mark.parametrize('args', [
    ('1', ['1', '2'], 0, 0, 0, None, False),
    ('1', ['1', '2'], 1, 0, 0, None, False),
    ('2', ['1', '2'], 1, 3, 10, '2', False),
    ('2', ['1', '2'], 1, 0, 0, '2', False),
    ('9', ['1', '2'], 1, 0, 0, None, False),
    ('2', ['1', '2'], 0, 0, 0, None, True),
])
def test_target_status_matches_the_original(old, args):
    assert new.target_status(*args) == old.target_status(*args)


T0 = datetime(2026, 1, 1, 12)


@pytest.mark.parametrize('args', [
    (None, 0, 3, 10.0), (T0, 0, 3, 0), (T0, 1, 3, 60.0), (T0, 3, 3, 90.0),
    (T0, 0, 1, 30.0, 5, 20), (T0, 0, 3, 30.0, 5, 20), (T0, 0, 1, 30.0, 1, 20),
])
def test_estimate_completion_matches_the_original(old, args):
    assert new.estimate_completion(*args) == old.estimate_completion(*args)


def test_a_single_target_gets_an_eta_from_work_progress():
    text, eta = new.estimate_completion(T0, 0, 1, 30.0, 11, 100)
    assert text == '30秒 経過' and eta is not None and eta.endswith('見込み')


def test_run_progress_folds_engine_events_into_chip_states():
    progress = new.RunProgress(['1', '2', '3'])
    progress.apply({'event': 'run_start', 'total': 3, 'totals': {}})
    assert progress.chip('1') == ('waiting', None, None)
    progress.apply({'event': 'author_start', 'author_id': '1', 'totals': {}})
    progress.apply({'event': 'author_listed', 'author_id': '1', 'counters': {'n_new': 4}, 'totals': {}})
    progress.apply({'event': 'work_progress', 'author_id': '1', 'done': 1, 'queued': 4, 'totals': {}})
    assert progress.chip('1') == ('in_progress', 2, 4)
    progress.apply({'event': 'author_done', 'author_id': '1', 'totals': {'n_new': 4}})
    progress.apply({'event': 'author_failed', 'author_id': '2', 'totals': {'n_new': 4}})
    assert progress.chip('1')[0] == progress.chip('2')[0] == 'done' and progress.chip('3')[0] == 'waiting'
    assert progress.counts == {'n_new': 4}
    progress.apply({'event': 'run_paused', 'reason': 'network_stopped', 'totals': {}})
    assert (progress.state, progress.pause_reason) == ('paused', 'network_stopped')
    progress.apply({'event': 'run_end', 'outcome': 'done', 'elapsed': 12.0, 'totals': {}})
    assert progress.chip('3')[0] == 'done' and progress.elapsed == 12.0


def test_end_messages_say_stopped_not_failed():
    assert new.end_message('stopped') == '停止しました。'
    assert new.end_message('done') == '完了しました。'
    assert new.end_message('failed', 'RunFailed') == '失敗しました: RunFailed'
