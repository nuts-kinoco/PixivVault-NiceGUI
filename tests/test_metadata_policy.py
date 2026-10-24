"""Pure Phase10 contract tests. No credentials, network, or original DB."""
from dataclasses import FrozenInstanceError
import pytest

from pixivvault_nicegui.metadata_policy import (
    FailureEvidence,
    MetadataCandidate,
    MetadataFailureClass,
    classify_failure,
    select_candidates,
)


@pytest.mark.parametrize('status,kind,increment,stop', [
    (404, MetadataFailureClass.WORK, True, False),
    (401, MetadataFailureClass.GLOBAL, False, True),
    (403, MetadataFailureClass.GLOBAL, False, True),
    (429, MetadataFailureClass.GLOBAL, False, True),
    (500, MetadataFailureClass.GLOBAL, False, True),
    (503, MetadataFailureClass.GLOBAL, False, True),
    (599, MetadataFailureClass.GLOBAL, False, True),
    (400, MetadataFailureClass.UNKNOWN, False, False),
    (499, MetadataFailureClass.UNKNOWN, False, False),
    (410, MetadataFailureClass.UNKNOWN, False, False),
])
def test_http_codes_follow_source_failure_boundary(status, kind, increment, stop):
    result = classify_failure(FailureEvidence('http', status))
    assert result.classification is kind
    assert result.increment_attempts is increment
    assert result.stop_run is stop


@pytest.mark.parametrize('reason', ['authentication', 'transport', 'timeout'])
def test_system_failure_stops_without_consuming_attempts(reason):
    result = classify_failure(FailureEvidence(reason), consecutive_unknown=4)
    assert result.classification is MetadataFailureClass.GLOBAL
    assert not result.increment_attempts and result.stop_run
    assert result.consecutive_unknown == 0


def test_missing_bookmark_count_is_work_specific_and_zero_is_not_missing():
    result = classify_failure(FailureEvidence('missing_bookmark_count'), consecutive_unknown=4)
    assert result.increment_attempts and not result.stop_run
    assert result.consecutive_unknown == 0
    assert not MetadataCandidate('123', '91', 'illust', False, 0, 0).needs_backfill


def test_unknown_streak_stops_at_five_but_never_consumes_work_attempts():
    for streak in range(5):
        result = classify_failure(FailureEvidence('unknown'), consecutive_unknown=streak)
        assert not result.increment_attempts
        assert result.consecutive_unknown == streak + 1
        assert result.stop_run is (streak == 4)
    reset = classify_failure(FailureEvidence('http', 404), consecutive_unknown=4)
    assert reset.consecutive_unknown == 0 and reset.increment_attempts


@pytest.mark.parametrize('evidence', [
    FailureEvidence('http', 418),
    FailureEvidence('unknown'),
])
def test_ambiguous_errors_cannot_silently_exhaust_meta_attempts(evidence):
    decision = classify_failure(evidence)
    assert decision.classification == MetadataFailureClass.UNKNOWN
    assert not decision.increment_attempts


@pytest.mark.parametrize('reason,status', [
    ('http', None), ('http', True), ('http', 99), ('http', 600),
    ('unknown', 404), ('missing_bookmark_count', 200), ('invalid', None),
])
def test_untrusted_failure_evidence_is_rejected(reason, status):
    with pytest.raises(ValueError):
        FailureEvidence(reason, status)


@pytest.mark.parametrize('streak', [-1, True, '4'])
def test_invalid_unknown_streak_rejected(streak):
    with pytest.raises(ValueError):
        classify_failure(FailureEvidence('unknown'), consecutive_unknown=streak)


def test_candidates_match_original_null_bookmark_not_timestamp_criteria():
    rows = (
        MetadataCandidate('123', '91', 'illust', False, None, 0, '2026-10-24'),
        MetadataCandidate('124', '91', 'illust', False, None, 3),
        MetadataCandidate('125', '91', 'novel', False, None, 0),
        MetadataCandidate('126', '91', 'illust', True, None, 0),
        MetadataCandidate('127', '91', 'illust', False, 0, 0),
        MetadataCandidate('128', '91', 'illust', False, None, 2),
    )
    assert tuple(r.work_id for r in select_candidates(rows)) == ('123', '128')


def test_candidate_order_matches_sqlite_attempts_user_work():
    rows = (
        MetadataCandidate('300', '92', 'illust', False, None, 1),
        MetadataCandidate('500', '91', 'illust', False, None, 1),
        MetadataCandidate('401', '91', 'illust', False, None, 0),
        MetadataCandidate('402', '91', 'illust', False, None, 1),
    )
    assert tuple(row.work_id for row in select_candidates(rows)) == (
        '401', '402', '500', '300')


def test_original_sqlite_text_columns_have_lexical_id_order():
    # Original database.py:267 work_id TEXT, :278 user_id TEXT;
    # source query :1660 ORDER BY attempts,user_id,work_id.
    rows = (
        MetadataCandidate('9', '9', 'illust', False, None, 0),
        MetadataCandidate('10', '10', 'illust', False, None, 0),
        MetadataCandidate('11', '10', 'illust', False, None, 0),
    )
    assert tuple((r.user_id, r.work_id) for r in select_candidates(rows)) == (
        ('10', '10'), ('10', '11'), ('9', '9'))


def test_empty_snapshot_stays_empty_and_does_not_mutate_input():
    assert select_candidates(()) == ()
    rows = (MetadataCandidate('123', '91', 'illust', False, None, 2),)
    assert select_candidates(rows) == rows
    assert rows[0].meta_attempts == 2


def test_metadata_records_are_frozen_and_invalid_evidence_rejected():
    item = MetadataCandidate('123', '91', 'illust', False, None, 0)
    with pytest.raises(FrozenInstanceError):
        item.meta_attempts = 3
    with pytest.raises(FrozenInstanceError):
        FailureEvidence('unknown').reason = 'http'
    with pytest.raises(TypeError):
        classify_failure({'reason': 'unknown'})


@pytest.mark.parametrize('kwargs', [
    {'work_id': 'invalid'}, {'user_id': ''}, {'is_deleted': 0},
    {'meta_attempts': True}, {'meta_attempts': -1}, {'bookmark_count': False},
    {'bookmark_count': -1}, {'meta_fetched_at': 123},
])
def test_malformed_candidate_cannot_enter_backfill(kwargs):
    defaults = dict(work_id='123', user_id='91', content_type='illust',
                    is_deleted=False, bookmark_count=None, meta_attempts=0)
    defaults.update(kwargs)
    with pytest.raises(ValueError):
        MetadataCandidate(**defaults)


def test_mutable_candidate_list_rejected_to_avoid_mid_scan_reselection():
    candidate = MetadataCandidate('123', '91', 'illust', False, None, 0)
    with pytest.raises(ValueError):
        select_candidates([candidate])


def test_duplicate_candidate_ids_rejected_before_any_runner_can_double_fetch():
    row = MetadataCandidate('123', '91', 'illust', False, None, 0)
    with pytest.raises(ValueError, match='duplicate'):
        select_candidates((row, row))
