from dataclasses import replace
from importlib import import_module

import pytest


@pytest.mark.parametrize('method', ['nearest', 'centroid'])
def test_thumb_variants_are_excluded_from_the_original_image_space(visual, generation, method):
    original = sample(visual, generation, 'reference', (1, 0), 'like',
                      variant='saved_original', saved=True)
    expected = visual.fit(generation, [original], method=method)
    for variant in ('thumb', 'thumb_fallback'):
        thumb = sample(visual, generation, 'thumb-only', (0, 1, 0), 'hate', 'style', variant=variant)
        candidate = image(visual, generation, 'candidate', (1, 0), variant=variant)
        assert visual.fit(generation, [original, thumb], method=method) == expected
        assert expected.image_signal(candidate) is None
        base = -0.0
        assert visual.score(base, expected, candidate, current=generation) is base
    assert expected.image_signal(image(visual, generation, 'candidate', (1, 0))) is not None


def test_payload_without_embedding_space_requires_refit(visual, generation):
    original = sample(visual, generation, 'reference', (1, 0), 'like')
    snapshot = visual.fit(generation, [original])
    old = {key: value for key, value in snapshot.to_payload().items() if key != 'embedding_space'}
    assert visual.ModelSnapshot.from_payload(generation, old) is None


@pytest.fixture
def visual():
    return import_module('pixivvault_nicegui.visual_preference')


@pytest.fixture
def generation(visual):
    return visual.Generation('test-store', 1, 'a' * 64, 'v1', 'b' * 64)


def image(visual, generation, work, vector, *, variant='view'):
    key = visual.EmbeddingKey(work, 0, variant, 'c' * 64, generation.model_sha,
                              generation.preprocessing_version)
    return visual.Embedding(key, vector)


def sample(visual, generation, work, vector, judgment='', reason='', *, saved=False,
           bookmarked=False, author='author', variant='view', revision=0):
    return visual.TrainingSample(image(visual, generation, work, vector, variant=variant),
                                 author, judgment, reason, saved, bookmarked, revision)


def test_similar_tags_and_same_author_beta_keep_both_positive_style_groups(visual, generation):
    rows = [sample(visual, generation, str(i), vector, judgment, reason)
            for i, (vector, judgment, reason) in enumerate([
                ((1, .05, 0), 'superlike', ''), ((1, -.05, 0), 'like', ''),
                ((.05, 1, 0), 'like', ''), ((-.05, 1, 0), 'like', ''),
                ((0, .05, 1), 'hate', 'style'), ((.05, 0, 1), 'hate', 'style'),
            ])]
    candidates = [image(visual, generation, 'candidate-' + str(i), vector)
                  for i, vector in enumerate(((1, 0, 0), (0, 1, 0), (0, 0, 1)))]
    base = .5  # Both the tag component and author Beta are equal.
    nearest = visual.fit(generation, rows, method='nearest')
    centroid = visual.fit(generation, rows, method='centroid')

    def scores(model):
        return [visual.score(base, model, candidate, current=generation)
                for candidate in candidates]

    near_scores = scores(nearest)
    centroid_scores = scores(centroid)
    assert min(near_scores[:2]) > base > near_scores[2]
    assert min(near_scores[:2]) > min(centroid_scores[:2]) > centroid_scores[2]
    assert visual.score(.999, nearest, candidates[0], current=generation, alpha=.5) == 1


def test_off_missing_gpu_mismatch_old_and_invalid_never_add(visual, generation):
    class Unmodified(float):
        def __add__(self, other):
            pytest.fail('base score was used in addition')

    base = Unmodified(-0.0)
    ready = image(visual, generation, 'candidate', (1, 0))
    learned = visual.fit(generation, [sample(visual, generation, 'like', (1, 0), 'like')])
    assert learned is not None
    for model, candidate, current, gpu_ok in [
        (None, object(), object(), True),
        (learned, None, generation, True),
        (learned, ready, generation, False),
        (learned, ready, replace(generation, number=2), True),
        (learned, ready, replace(generation, judgment_fingerprint='d' * 64), True),
        (learned, replace(ready, key=replace(ready.key, model_sha='d' * 64)), generation, True),
        (learned, replace(ready, key=replace(ready.key, preprocessing_version='v2')), generation, True),
        (learned, replace(ready, key=replace(ready.key, variant='thumb_fallback')), generation, True),
        (learned, replace(ready, vector=(float('inf'), 1)), generation, True),
        (learned, replace(ready, vector=(1, 0, 0)), generation, True),
    ]:
        assert visual.score(base, model, candidate, current=current, gpu_ok=gpu_ok) is base
    assert visual.score(base, learned, ready, current=generation, alpha=0) is base


def test_only_current_explicit_style_hate_is_negative_and_works_are_deduplicated(visual, generation):
    weak = sample(visual, generation, 'weak', (0, 1), saved=True, bookmarked=True)
    strong = sample(visual, generation, 'strong', (1, 0), 'superlike', saved=True)
    superseded = sample(visual, generation, 'changed', (1, 0), 'hate', 'style')
    current = replace(superseded, judgment='like', reason='', revision=1)
    negative = sample(visual, generation, 'hate', (-1, 0), 'hate', 'style', saved=True)
    excluded = [sample(visual, generation, 'ignored-' + kind + reason, (-1, 0), kind, reason)
                for kind, reason in [('skip', ''), ('hate', ''), ('hate', 'genre'), ('opened', '')]]
    excluded.append(sample(visual, generation, 'fallback', (-1, 0), 'hate', 'style',
                           variant='thumb_fallback'))
    rows = [weak, strong, strong, superseded, current, negative, *excluded]
    model = visual.fit(generation, rows)
    assert model == visual.fit(generation, reversed(rows))
    assert model.positive_count == 3
    assert model.negative_count == 1
    assert visual.fit(generation, excluded) is None


def test_author_confidence_uses_distinct_saved_works_and_style_dispersion(visual, generation):
    def saved(work, author, vector):
        return sample(visual, generation, work, vector, saved=True,
                      author=author, variant='saved_original')

    single = saved('single', 'sparse', (1, 0))
    rows = [single, single, saved('a', 'consistent', (1, 0)),
            saved('b', 'consistent', (1, .01)), saved('c', 'varied', (1, 0)),
            saved('d', 'varied', (0, 1)), sample(visual, generation, 'bookmark',
                                                 (1, 0), bookmarked=True, author='unsaved')]
    model = visual.fit(generation, rows)
    assert model.author_signal('missing') is None
    assert model.author_signal('unsaved') is None
    assert model.author_signal('consistent').confidence > model.author_signal('sparse').confidence
    assert model.author_signal('consistent').confidence > model.author_signal('varied').confidence


def test_fitted_snapshot_roundtrips_and_old_payload_cannot_be_retagged(visual, generation):
    api = import_module('pixivvault_nicegui.visual_preference_store')
    model = visual.fit(generation, [sample(visual, generation, 'like', (1, 0), 'like')])
    payload = model.to_payload()
    assert visual.ModelSnapshot.from_payload(generation, payload) == model
    assert visual.ModelSnapshot.from_payload(replace(generation, number=2), payload) is None
    assert visual.ModelSnapshot.from_payload(generation, {**payload, 'dimension': 999}) is None
    assert visual.Generation is api.Generation


def test_alpha_zero_does_not_compute_visual_signal(visual, generation, monkeypatch):
    ready = image(visual, generation, 'candidate', (1, 0))
    learned = visual.fit(generation, [sample(visual, generation, 'like', (1, 0), 'like')])
    monkeypatch.setattr(visual.ModelSnapshot, 'image_signal',
                        lambda *args: pytest.fail('computed a visual score while disabled'))
    assert visual.score(.5, learned, ready, current=generation, alpha=0) == .5


@pytest.mark.parametrize('judgment', ['unknown', 'opened', None, []])
def test_only_an_empty_judgment_can_supply_weak_labels(visual, generation, judgment):
    row = sample(visual, generation, 'saved', (1, 0), judgment,
                 saved=True, bookmarked=True, variant='saved_original')
    assert visual.fit(generation, [row]) is None


def test_negative_and_unknown_saved_works_do_not_enter_author_bank(visual, generation):
    rows = [sample(visual, generation, 'positive', (1, 0), 'like', author='good',
                   saved=True, variant='saved_original'),
            sample(visual, generation, 'negative', (0, 1), 'hate', 'style', author='bad',
                   saved=True, variant='saved_original'),
            sample(visual, generation, 'unknown', (1, 0), 'unknown', author='unknown',
                   saved=True, variant='saved_original')]
    result = visual.fit(generation, rows)
    assert result.positive_count == result.negative_count == 1
    assert result.author_signal('good') is not None
    assert result.author_signal('bad') is None
    assert result.author_signal('unknown') is None


def test_unneeded_or_mismatched_vectors_do_not_discard_the_valid_model(visual, generation):
    good = [sample(visual, generation, f'positive-{i}', vector, 'like')
            for i, vector in enumerate(((1, 0), (0, 1)))]
    irrelevant = sample(visual, generation, 'a-irrelevant', (1, 0, 0), 'skip', saved=True)
    wrong_dim = sample(visual, generation, 'b-wrong', (1, 0, 0), 'like')
    assert visual.fit(generation, [irrelevant, wrong_dim, *good]) == visual.fit(generation, good)
