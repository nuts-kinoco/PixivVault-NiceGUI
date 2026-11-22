from collections import Counter
from dataclasses import replace
from functools import partial
from hashlib import sha256
import json
from random import Random

import pytest

from pixivvault_nicegui import discovery_ranking as ranking, visual_preference as visual


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


def test_neutral_scores_preserve_the_baseline_evidence_tie_break(inputs):
    candidates, tags, authors = inputs
    authors['10'] = {'superlike_count': 10}
    snapshot = _snapshot({'10': NEUTRAL, '20': NEUTRAL, '40': POSITIVE})
    baseline, _, breakdown = ranking.rank_candidates_with_diagnostics(candidates, tags, authors)
    assert breakdown['10']['score'] == breakdown['20']['score']
    assert breakdown['10']['n'] < breakdown['20']['n']
    assert _ids(baseline).index('20') < _ids(baseline).index('10')
    ordered, _ = ranking.rank_candidates(candidates, tags, authors, visual_snapshot=snapshot)
    assert _ids(ordered).index('20') < _ids(ordered).index('10')
    assert _ids(ordered).index('40') < _ids(ordered).index('20')


def test_absent_model_falls_back_with_prepared_embeddings(inputs, monkeypatch):
    snapshot = replace(_snapshot({'10': NEGATIVE, '30': POSITIVE}), model=None)

    def reject_visual_scoring(*args, **kwargs):
        pytest.fail('missing model must not invoke visual scoring')

    monkeypatch.setattr(visual, 'score', reject_visual_scoring)
    assert ranking.rank_candidates(*inputs, visual_snapshot=snapshot) == ranking.rank_candidates(*inputs)


@pytest.mark.parametrize('condition', ['missing', 'stale', 'gpu-failed', 'wrong-model',
                                      'nonfinite', 'zero-alpha', 'wrong-work', 'thumb',
                                      'thumb_fallback', 'wrong-preprocessing', 'wrong-dimension',
                                      'zero-vector'])
def test_ineligible_snapshot_returns_the_complete_original_order(inputs, condition, monkeypatch):
    vector = {'nonfinite': (float('nan'), float('inf'), 0), 'wrong-dimension': (1, 0),
              'zero-vector': (0, 0, 0)}.get(condition, POSITIVE)
    works = () if condition == 'missing' else ('10', '20', '30', '40')
    snapshot = _snapshot(dict.fromkeys(works, vector))
    snapshot = replace(snapshot, **{
        'stale': {'current': replace(GENERATION, number=3)},
        'gpu-failed': {'gpu_ok': False},
        'zero-alpha': {'alpha': 0},
    }.get(condition, {}))
    key_changes = {
        'wrong-model': {'model_sha': 'f' * 64},
        'wrong-work': {'work_id': 'different-work'},
        'thumb': {'variant': 'thumb'},
        'thumb_fallback': {'variant': 'thumb_fallback'},
        'wrong-preprocessing': {'preprocessing_version': 'old'},
    }.get(condition)
    if key_changes:
        snapshot = replace(snapshot, embeddings={
            work: _embedding(work, vector, **key_changes) for work in works})

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


def test_integer_protection_id_matches_the_candidate_string(inputs):
    snapshot = _snapshot({'10': NEGATIVE, '30': POSITIVE}, protected_work_ids=frozenset({10}))
    assert ranking.rank_candidates(*inputs, visual_snapshot=snapshot) == ranking.rank_candidates(*inputs)


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


def test_deferred_tier_one_does_not_take_a_normal_sure_slot():
    candidates = [{'work_id': work, 'author_id': work, 'tags': [tag]}
                  for work, tag in [('10', 'known'), ('20', 'known'), ('30', 'known'),
                                    ('40', 'known'), ('80', 'novel')]]
    tags = {'known': {'like_count': 20}}
    authors = {'10': {'skip_count': 23}, '20': {'hate_count': 1},
               '30': {'skip_count': 25}, '40': {'skip_count': 26}, '80': {'like_count': 20}}
    baseline, frames = ranking.rank_candidates(candidates, tags, authors)
    assert _ids(baseline) == ['80', '10', '30', '40', '20']
    snapshot = _snapshot({'10': NEUTRAL, '20': NEGATIVE, '40': POSITIVE})
    ordered, corrected_frames = ranking.rank_candidates(candidates, tags, authors, visual_snapshot=snapshot)
    assert corrected_frames == frames
    assert _ids(ordered) == ['80', '40', '30', '10', '20']


def parity_cases():
    rand = Random(20261122)
    tags = ['shape', 'terrain', 'warm', 'blue', 'unknown']
    authors = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h']
    for case in range(192):
        works = [
            {'work_id': str(100 + i), 'author_id': rand.choice(authors),
             'tags': rand.sample(tags, rand.randrange(4)),
             'source': 'related_author' if i % 5 == 0 else 'search'}
            for i in range(case % 39)
        ]
        rand.shuffle(works)
        prefs = {
            tag: {'like_count': rand.randrange(8, 22), 'skip_count': rand.randrange(1, 12),
                  'seed_saved': rand.randrange(2, 9)}
            for tag in tags[:-1]
        } if case % 8 else {}
        author_prefs = {
            'a': {'like_count': 100},
            'b': {'like_count': 80},
            'c': {'like_count': 16, 'hate_count': 1},
            'd': {'hate_count': 1},
            'e': {'like_count': 10, 'hate_count': 2},
            'f': {'seed_following': 2, 'seed_bookmarked': 4},
        }
        settings = {'discovery_ratio_subject': case % 55, 'discovery_ratio_style': case % 18}
        # The full history puts a at its quota outside cooldown, and b in cooldown.
        history = ('a',) * 5 + ('history',) * 21 + ('b', 'history', 'history')
        yield (works, prefs, author_prefs), dict(
            get_setting=settings.get, limit=(None, 0, 7, 18)[case % 4],
            total_works=case * 3 or None, recent_author_ids=((), history)[case % 2],
        )


def parity_outputs(rank):
    results = []
    for args, options in parity_cases():
        ordered, frames = rank(*args, **options)[:2]
        results.append((_ids(ordered), frames))
    return results


def parity_digest(results):
    return sha256(json.dumps(results, sort_keys=True).encode('ascii')).hexdigest()


def test_explicit_off_matches_the_frozen_192_case_baseline_and_observer_rank(monkeypatch):
    def reject_visual_scoring(*args, **kwargs):
        pytest.fail('OFF must not invoke visual scoring')

    monkeypatch.setattr(visual, 'score', reject_visual_scoring)
    # Derived by running parity_outputs against discovery_ranking.py from 254b70e.
    expected = 'b358bc68cb0bc246d0bdb154e294a66b61c7af6141fa308f4a22400ba6fafca7'
    baseline_route = parity_outputs(ranking.rank_candidates)
    assert len(baseline_route) == 192
    assert parity_digest(baseline_route) == expected
    assert parity_outputs(partial(ranking.rank_candidates, visual_snapshot=None)) == baseline_route
    frame_counts = Counter()
    reject_tiers = Counter()
    author_reorderings = Counter()
    original_quota = ranking.apply_author_quota

    def observe_quota(candidates, *, recent_author_ids, cooldown):
        options = dict(recent_author_ids=recent_author_ids, cooldown=cooldown)
        result = original_quota(candidates, **options)
        if result != original_quota(candidates, **options, ratio=1):
            author_reorderings['quota'] += 1
        if result != original_quota(candidates, recent_author_ids=recent_author_ids, cooldown=0):
            author_reorderings['cooldown'] += 1
        return result

    def observe(*args, **kwargs):
        ordered, frames, breakdown = ranking.rank_candidates_with_diagnostics(*args, **kwargs)
        frame_counts.update(frames.values())
        reject_tiers.update(row['author_reject_tier'] for row in breakdown.values())
        return ordered, frames

    monkeypatch.setattr(ranking, 'apply_author_quota', observe_quota)
    assert parity_outputs(observe) == baseline_route
    assert all(frame_counts[key] > 0 for key in ('sure', 'subject', 'style')), frame_counts
    assert all(reject_tiers[key] > 0 for key in (0, 1, 2)), reject_tiers
    assert all(author_reorderings[key] > 0 for key in ('quota', 'cooldown')), author_reorderings
    print(f'192 cases: frames={dict(frame_counts)}, reject_tiers={dict(reject_tiers)}, '
          f'author_reorderings={dict(author_reorderings)}')
