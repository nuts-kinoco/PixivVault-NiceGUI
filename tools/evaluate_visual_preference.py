"""Read-only preliminary evaluation of an isolated JSON export.

The export has model_sha and preprocessing_version plus disjoint training and
slates arrays. Each slate is a presented candidate list in original rank order.
Only candidates whose scores change can exchange their original slots.
Rows have work_id, author_id, optional judgment/reason, and optional embedding
metadata (vector, image_sha, model_sha, preprocessing_version, variant, page).
Training rows may include saved, bookmarked and revision. Slate rows need
base_score. The exporter must verify local image/embedding provenance first.
The built-in synthetic export shows the format without accessing local history.

Run from the repo with src on PYTHONPATH:
    python -B tools/evaluate_visual_preference.py --synthetic
    python -B tools/evaluate_visual_preference.py --input <isolated-export.json>
"""
import argparse
import json
from math import log2
import os
from pathlib import Path
from random import Random
from statistics import mean, quantiles

from pixivvault_nicegui.visual_preference import (
    DEFAULT_ALPHA, Embedding, EmbeddingKey, Generation, TrainingSample, blend, fit,
)


_MODES = ('baseline', 'D', 'A', 'A+D')


def _embedding(row):
    if row.get('vector') is None:
        return None
    key = EmbeddingKey(row['work_id'], row.get('page', 0), row['variant'], row['image_sha'],
                       row['model_sha'], row['preprocessing_version'])
    return Embedding(key, tuple(row['vector']))


def _relevance(row):
    judgment = row.get('judgment')
    if judgment == 'superlike':
        return 2
    if judgment == 'like':
        return 1
    if judgment == 'hate' and row.get('reason') == 'style':
        return 0
    return None


def _dcg(relevance):
    return sum((2 ** value - 1) / log2(rank + 2) for rank, value in enumerate(relevance[:10]))


def _ci(values, repetitions, seed):
    if len(values) < 2:
        return None
    random = Random(seed)
    estimates = [mean(random.choices(values, k=len(values))) for _ in range(repetitions)]
    steps = quantiles(estimates, n=1000, method='inclusive')
    return [steps[24], steps[974]]


def _separation(positive, negative):
    total = len(positive) * len(negative)
    wins = sum(1 if like > hate else 0.5 if like == hate else 0
               for like in positive for hate in negative)
    return {'positive': len(positive), 'hate_style': len(negative),
            'auc': wins / total if total else None}


def _measure(slates, model, *, alpha, repetitions, seed):
    metrics = {mode: [] for mode in _MODES}
    contamination = {mode: [] for mode in _MODES}
    top_labelled = {mode: [] for mode in _MODES}
    coverage = dict(presented=0, labelled=0, image_ready=0, usable_image=0, author_ready=0,
                    image_ready_by_variant={})
    positive, negative = [], []
    for slate in slates:
        ranked = {mode: [] for mode in _MODES}
        labels = []
        for row in slate:
            base = row['base_score']
            if type(base) not in (int, float) or not 0 <= base <= 1:
                raise ValueError('base_score must be between zero and one')
            image = _embedding(row)
            label = _relevance(row)
            a = model.image_signal(image) if model is not None else None
            d = model.author_signal(row['author_id']) if model is not None else None
            coverage['presented'] += 1
            coverage['image_ready'] += image is not None
            coverage['usable_image'] += a is not None
            coverage['author_ready'] += d is not None
            if image is not None:
                by_variant = coverage['image_ready_by_variant']
                variant = image.key.variant
                by_variant[variant] = by_variant.get(variant, 0) + 1
            if label is not None:
                coverage['labelled'] += 1
                labels.append(label)
                if a is not None:
                    (positive if label else negative).append(a.value)
            signals = (None, d, a, a if a is not None else d)
            for mode, signal in zip(_MODES, signals):
                ranked[mode].append((blend(base, signal, alpha=alpha), base, label))
        ideal = _dcg(sorted(labels, reverse=True))
        for mode in _MODES:
            entries = ranked[mode]
            changed = iter(sorted((entry for entry in entries if entry[0] != entry[1]),
                                  key=lambda entry: -entry[0]))
            ordered = [next(changed)[2] if corrected != base else label
                       for corrected, base, label in entries]
            if ideal:
                metrics[mode].append(_dcg([label for label in ordered if label is not None]) / ideal)
            if ordered:
                top = ordered[:10]
                contamination[mode].append(top.count(0) / len(top))
                top_labelled[mode].append((len(top) - top.count(None)) / len(top))

    comparison = {}
    for mode in _MODES:
        scores = metrics[mode]
        delta = [score - base for score, base in zip(scores, metrics['baseline'])]
        comparison[mode] = dict(ndcg_at_10=mean(scores) if scores else None,
                                slates=len(scores),
                                paired_delta_ci95=_ci(delta, repetitions, seed),
                                hate_style_top_10=mean(contamination[mode])
                                if contamination[mode] else None,
                                top_10_labelled_rate=mean(top_labelled[mode])
                                if top_labelled[mode] else None)
    coverage['image_rate'] = (coverage['image_ready'] / coverage['presented']
                              if coverage['presented'] else None)
    return comparison, coverage, _separation(positive, negative)


def evaluate(data, *, alpha=DEFAULT_ALPHA, bootstrap_samples=1000, seed=0):
    """Compare A, D and A-first fallback on the same held-out labelled works."""
    if (type(alpha) not in (int, float) or not 0 <= alpha <= 1
            or type(bootstrap_samples) is not int or bootstrap_samples < 2):
        raise ValueError('invalid alpha or bootstrap sample count')
    training, slates = data['training'], data['slates']
    held_out = [row['work_id'] for slate in slates for row in slate]
    if set(held_out) & {row['work_id'] for row in training}:
        raise ValueError('training and evaluation work IDs overlap')
    if len(set(held_out)) != len(held_out):
        raise ValueError('evaluation slates must contain distinct works')
    generation = Generation('offline-evaluation', 1, data['model_sha'],
                            data['preprocessing_version'], '0' * 64)
    latest = {}
    for row in training:
        work, revision = row['work_id'], row.get('revision', 0)
        if type(revision) is not int or revision < 0:
            raise ValueError('revision must be a nonnegative integer')
        latest[work] = max(latest.get(work, 0), revision)
    samples = []
    for row in training:
        if row.get('revision', 0) != latest[row['work_id']]:
            continue
        image = _embedding(row)
        if image is not None:
            samples.append(TrainingSample(image, row['author_id'], row.get('judgment', ''),
                                          row.get('reason', ''), row.get('saved', False),
                                          row.get('bookmarked', False), row.get('revision', 0)))
    summaries = {}
    for method in ('nearest', 'centroid'):
        comparison, coverage, separation = _measure(
            slates, fit(generation, samples, method=method), alpha=alpha,
            repetitions=bootstrap_samples, seed=seed)
        summaries[method] = dict(comparison=comparison, coverage=coverage, separation=separation)
    primary = summaries['nearest']
    return dict(**primary, method_comparison={method: dict(A=report['comparison']['A'],
                                                          separation=report['separation'])
                                             for method, report in summaries.items()},
                alpha=alpha, alpha_basis='provisional; no empirical calibration',
                ndcg_basis='labelled candidates in ranked order',
                evaluation='preliminary; labelled available images may be selection-biased')


def synthetic_input():
    model_sha = 'a' * 64

    def row(work, author, vector, judgment='', reason='', *, saved=False, base=None):
        result = dict(work_id=work, author_id=author, judgment=judgment, reason=reason, saved=saved)
        if vector is not None:
            result.update(vector=vector, image_sha='b' * 64, model_sha=model_sha,
                          preprocessing_version='v1', variant='saved_original' if saved else 'view')
        if base is not None:
            result['base_score'] = base
        return result

    training = [row('train-like1', 'a', [1, 0, 0], 'superlike', saved=True),
                row('train-like2', 'b', [0, 1, 0], 'like', saved=True),
                row('train-hate', 'c', [0, 0, 1], 'hate', 'style'),
                row('train-a', 'a', [1, .01, 0], saved=True)]
    slates = []
    for index in range(3):
        slates.append([
            row(f'test-{index}-hate', 'c', [0, 0, 1], 'hate', 'style', base=.503),
            row(f'test-{index}-like', 'b', [0, 1, 0], 'like', base=.502),
            row(f'test-{index}-super', 'a', None, 'superlike', base=.500),
            row(f'test-{index}-unknown', 'new', None, base=.48),
        ])
    return dict(model_sha=model_sha, preprocessing_version='v1', training=training, slates=slates)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--synthetic', action='store_true')
    source.add_argument('--input', type=Path, help='isolated JSON export, opened read-only')
    parser.add_argument('--alpha', type=float, default=DEFAULT_ALPHA)
    parser.add_argument('--bootstrap-samples', type=int, default=1000)
    args = parser.parse_args(argv)
    if args.synthetic:
        data = synthetic_input()
    else:
        path = args.input.resolve(strict=True)
        if os.name == 'nt' and path.drive.upper() != 'S:':
            parser.error('the isolated export must be on S:')
        data = json.loads(path.read_text(encoding='utf-8'))
    report = evaluate(data, alpha=args.alpha, bootstrap_samples=args.bootstrap_samples)
    report['source'] = 'synthetic' if args.synthetic else 'isolated_json_export'
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
