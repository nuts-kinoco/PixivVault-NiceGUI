from dataclasses import replace
from functools import partial

import pytest

from pixivvault_nicegui import discovery_ranking as ranking, visual_preference as visual
from test_visual_preference_shadow_review import rank_parity_digest


GENERATION = visual.Generation('synthetic', 2, 'a' * 64, 'v1', 'judgments')
POSITIVE = (1, 0, 0)
OTHER_POSITIVE = (0, 1, 0)
NEGATIVE = (0, 0, 1)
NEUTRAL = (0, 0, -1)


def _embedding(work, vector, **changes):
    key = visual.EmbeddingKey(work, 0, 'view', 'b' * 64, GENERATION.model_sha,
                              GENERATION.preprocessing_version)
    return visual.Embedding(replace(key, **changes), vector)


@pytest.fixture
def inputs():
    candidates = [
        {'work_id': work, 'author_id': work, 'tags': [tag]}
        for work, tag in [('40', 'known'), ('10', 'known'), ('30', 'known'),
                          ('20', 'known'), ('80', 'novel'), ('90', 'known')]
    ]
    tags = {'known': {'like_count': 20}}
    authors = {work: {'like_count': 20} for work in ('10', '20', '30', '40', '80')}
    return candidates, tags, authors


def _snapshot(vectors, **options):
    from pixivvault_nicegui.visual_rank_adapter import VisualPreferenceSnapshot

    model = visual.fit(GENERATION, [
        visual.TrainingSample(_embedding('p1', POSITIVE), 'train', 'superlike'),
        visual.TrainingSample(_embedding('p2', OTHER_POSITIVE), 'train', 'like'),
        visual.TrainingSample(_embedding('n1', NEGATIVE), 'train', 'hate', 'style'),
    ])
    assert model is not None
    embeddings = {work: _embedding(work, vector) for work, vector in vectors.items()}
    return VisualPreferenceSnapshot(model, GENERATION, embeddings, **options)


def _ids(ordered):
    return [candidate['work_id'] for candidate in ordered]


def test_only_sure_frame_changes_with_two_liked_styles_and_one_rejected_style(inputs):
    snapshot = _snapshot({'10': NEGATIVE, '20': OTHER_POSITIVE, '30': POSITIVE,
                          '40': NEUTRAL, '80': POSITIVE, '90': NEGATIVE})
    baseline, frames, breakdown = ranking.rank_candidates_with_diagnostics(*inputs)
    assert len({breakdown[work]['score'] for work in ('10', '20', '30', '40')}) == 1
    assert _ids(baseline) == ['80', '10', '90', '20', '30', '40']
    assert frames == {'10': 'sure', '20': 'sure', '30': 'sure', '40': 'sure',
                      '80': 'subject', '90': 'style'}
    result, corrected_frames = ranking.rank_candidates(*inputs, visual_snapshot=snapshot)
    assert _ids(result) == ['80', '30', '90', '20', '40', '10']
    assert corrected_frames == frames


def test_off_on_off_preserves_fixed_tie_break_frames_and_limit(inputs):
    snapshot = _snapshot({'10': NEGATIVE, '20': POSITIVE, '30': POSITIVE, '40': NEUTRAL})
    baseline = ranking.rank_candidates(*inputs, limit=4)
    assert _ids(baseline[0]) == ['80', '10', '90', '20']
    assert ranking.rank_candidates(*inputs, visual_snapshot=None, limit=4) == baseline
    on, frames = ranking.rank_candidates(*inputs, visual_snapshot=snapshot, limit=4)
    assert _ids(on) == ['80', '20', '90', '30']
    assert frames == baseline[1]
    assert ranking.rank_candidates(*inputs, visual_snapshot=None, limit=4) == baseline


def test_alpha_can_change_whether_the_correction_overcomes_the_base_score_gap(inputs):
    candidates, tags, authors = inputs
    authors['20'] = {'like_count': 21}
    baseline = ranking.rank_candidates(candidates, tags, authors)
    assert _ids(baseline[0]) == ['80', '20', '90', '10', '30', '40']
    snapshot = _snapshot({'10': POSITIVE, '20': NEGATIVE}, alpha=0.001)
    assert ranking.rank_candidates(candidates, tags, authors, visual_snapshot=snapshot) == baseline
    ordered, _ = ranking.rank_candidates(
        candidates, tags, authors, visual_snapshot=replace(snapshot, alpha=visual.DEFAULT_ALPHA))
    assert _ids(ordered) == ['80', '10', '90', '20', '30', '40']


def test_clipped_tie_uses_work_id_even_with_different_evidence_counts(inputs):
    candidates, tags, authors = inputs
    authors['20'] = {'like_count': 100}
    baseline, _, breakdown = ranking.rank_candidates_with_diagnostics(candidates, tags, authors)
    assert _ids(baseline) == ['80', '20', '90', '10', '30', '40']
    snapshot = _snapshot({'10': POSITIVE, '20': POSITIVE}, alpha=0.5)
    assert all(visual.score(breakdown[work]['score'], snapshot.model, snapshot.embeddings[work],
                            current=GENERATION, alpha=snapshot.alpha) == 1 for work in ('10', '20'))
    ordered, _ = ranking.rank_candidates(candidates, tags, authors, visual_snapshot=snapshot)
    assert _ids(ordered) == ['80', '10', '90', '20', '30', '40']


@pytest.mark.parametrize('condition', ['missing', 'stale', 'gpu-failed', 'wrong-model',
                                      'nonfinite', 'zero-alpha', 'wrong-work'])
def test_ineligible_snapshot_returns_the_complete_original_order(inputs, condition, monkeypatch):
    snapshot = _snapshot({} if condition == 'missing' else dict.fromkeys(
        ('10', '20', '30', '40'), (float('nan'), float('inf'), 0) if condition == 'nonfinite' else POSITIVE))
    if condition in {'stale', 'gpu-failed', 'zero-alpha'}:
        snapshot = replace(snapshot, **{
            'stale': {'current': replace(GENERATION, number=3)},
            'gpu-failed': {'gpu_ok': False},
            'zero-alpha': {'alpha': 0},
        }[condition])
    if condition in {'wrong-model', 'wrong-work'}:
        embeddings = {work: visual.Embedding(
            replace(embedding.key, **({'model_sha': 'f' * 64} if condition == 'wrong-model'
                                      else {'work_id': 'different-work'})), embedding.vector)
            for work, embedding in snapshot.embeddings.items()}
        snapshot = replace(snapshot, embeddings=embeddings)

    original = visual.blend

    def blend(base, signal, *, alpha):
        assert signal is None, 'invalid visual evidence must not reach the score addition'
        return original(base, signal, alpha=alpha)

    monkeypatch.setattr(visual, 'blend', blend)
    assert ranking.rank_candidates(*inputs, visual_snapshot=snapshot) == ranking.rank_candidates(*inputs)


def test_protected_and_missing_slots_hold_with_partial_coverage(inputs):
    snapshot = _snapshot({'10': NEGATIVE, '20': NEGATIVE, '30': POSITIVE,
                          '80': NEGATIVE, '90': POSITIVE}, protected_work_ids=frozenset({'20'}))
    ordered, frames = ranking.rank_candidates(*inputs, visual_snapshot=snapshot)
    assert _ids(ordered) == ['80', '30', '90', '20', '10', '40']
    assert frames == ranking.rank_candidates(*inputs)[1]


def test_first_sure_card_stays_even_when_its_visual_score_is_lowest(inputs):
    candidates, tags, authors = inputs
    sure = [work for work in candidates if work['work_id'] not in ('80', '90')]
    snapshot = _snapshot({'10': NEGATIVE, '20': NEGATIVE, '30': OTHER_POSITIVE, '40': POSITIVE})
    ordered, frames = ranking.rank_candidates(sure, tags, authors, visual_snapshot=snapshot)
    assert _ids(ordered) == ['10', '40', '30', '20']
    assert set(frames.values()) == {'sure'}


def test_author_cooldown_cannot_move_exploration_slots(inputs, monkeypatch):
    candidates, tags, authors = inputs
    next(work for work in candidates if work['work_id'] == '30')['author_id'] = '80'
    baseline = ranking.rank_candidates(candidates, tags, authors)
    assert _ids(baseline[0]) == ['80', '10', '90', '20', '30', '40']
    snapshot = _snapshot({'10': NEGATIVE, '30': POSITIVE})
    finalized = []
    original = ranking.finalize_display_order

    def finish(*args, **kwargs):
        result = original(*args, **kwargs)
        finalized.append(_ids(result))
        return result

    monkeypatch.setattr(ranking, 'finalize_display_order', finish)
    assert ranking.rank_candidates(candidates, tags, authors, visual_snapshot=snapshot) == baseline
    assert len(finalized) == 2
    assert finalized[1].index('90') == 1  # The proposed swap would move a style card up.


def test_confirmed_author_rejection_still_defers_a_strong_visual_match(inputs):
    candidates, tags, authors = inputs
    candidates.append({'work_id': '00', 'author_id': 'rejected', 'tags': ['known']})
    authors['rejected'] = {'like_count': 100, 'hate_count': 2}
    snapshot = _snapshot({'00': POSITIVE, '10': NEGATIVE, '30': OTHER_POSITIVE})
    ordered, frames = ranking.rank_candidates(candidates, tags, authors, visual_snapshot=snapshot)
    assert frames['00'] == 'sure'
    assert _ids(ordered) == ['80', '30', '90', '20', '10', '40', '00']


def test_explicit_off_matches_the_frozen_192_case_baseline_and_observer_rank(monkeypatch):
    def reject_visual_scoring(*args, **kwargs):
        pytest.fail('OFF must not invoke visual scoring')

    monkeypatch.setattr(visual, 'score', reject_visual_scoring)
    expected = '2a262d5b30ff47e52d2312bf777413bf8e30972b992942809d63dae746c05fd0'
    assert rank_parity_digest(partial(ranking.rank_candidates, visual_snapshot=None)) == expected
    assert rank_parity_digest(ranking.rank_candidates) == expected
    assert rank_parity_digest(ranking.rank_candidates_with_diagnostics) == expected
