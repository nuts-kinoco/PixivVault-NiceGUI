import json
from importlib import import_module

import pytest


@pytest.fixture
def evaluator():
    return import_module('tools.evaluate_visual_preference')


def test_synthetic_evaluation_compares_same_labeled_slates_and_reports_coverage(evaluator):
    result = evaluator.evaluate(evaluator.synthetic_input(), bootstrap_samples=100)
    assert set(result['comparison']) == {'baseline', 'D', 'A', 'A+D'}
    assert result['coverage']['image_ready'] < result['coverage']['presented']
    assert result['coverage']['labelled'] < result['coverage']['presented']
    assert result['comparison']['A']['ndcg_at_10'] > result['comparison']['baseline']['ndcg_at_10']
    assert result['comparison']['A+D']['ndcg_at_10'] >= result['comparison']['A']['ndcg_at_10']
    assert len(result['comparison']['A']['paired_delta_ci95']) == 2
    assert result['separation']['auc'] > .5
    assert set(result['method_comparison']) == {'centroid', 'nearest'}


def test_read_only_input_and_no_work_leakage(evaluator, tmp_path, capsys):
    data = evaluator.synthetic_input()
    path = tmp_path / 'export.json'
    original = json.dumps(data).encode()
    path.write_bytes(original)
    assert evaluator.main(['--input', str(path), '--bootstrap-samples', '100']) == 0
    assert json.loads(capsys.readouterr().out)['coverage']['presented'] > 0
    assert path.read_bytes() == original
    data['training'][0]['work_id'] = data['slates'][0][0]['work_id']
    with pytest.raises(ValueError, match='overlap'):
        evaluator.evaluate(data)


def test_missing_and_unjudged_rows_do_not_become_negative_labels(evaluator):
    data = evaluator.synthetic_input()
    data['slates'] = [[{'work_id': 'new-work', 'author_id': 'unknown', 'base_score': .9,
                        'judgment': 'skip'}]]
    result = evaluator.evaluate(data)
    assert result['coverage']['image_ready'] == 0
    assert result['coverage']['labelled'] == 0
    assert result['comparison']['A']['ndcg_at_10'] is None
    assert result['comparison']['A']['paired_delta_ci95'] is None
    assert result['separation']['auc'] is None


def test_uses_recorded_rank_and_counts_top_ten_slots_with_unjudged_cards(evaluator):
    data = evaluator.synthetic_input()
    neutral = [{'work_id': f'neutral-{i}', 'author_id': 'unknown', 'base_score': .9}
               for i in range(9)]
    like = {'work_id': 'new-like', 'author_id': 'unknown', 'base_score': .9, 'judgment': 'like'}
    hate = {'work_id': 'new-hate', 'author_id': 'unknown', 'base_score': .1,
            'judgment': 'hate', 'reason': 'style'}
    data['slates'] = [[*neutral, hate, like]]
    result = evaluator.evaluate(data, alpha=0)
    for mode in result['comparison'].values():
        assert mode['ndcg_at_10'] == pytest.approx(1 / 1.584962500721156)
        assert mode['hate_style_top_10'] == .1


def test_missing_current_image_does_not_revive_superseded_training_label(evaluator):
    data = evaluator.synthetic_input()
    like = data['training'][0]
    data['training'] = [like, {'work_id': like['work_id'], 'author_id': like['author_id'],
                              'judgment': 'skip', 'revision': 1}]
    result = evaluator.evaluate(data)
    assert result['coverage']['usable_image'] == 0
    assert result['separation']['auc'] is None


def test_single_correction_cannot_reorder_unadjusted_cards(evaluator):
    data = evaluator.synthetic_input()
    unlabelled = {**data['slates'][0][1], 'judgment': '', 'base_score': .5}
    data['slates'] = [[
        {'work_id': 'fixed-hate', 'author_id': 'unknown', 'judgment': 'hate',
         'reason': 'style', 'base_score': .1},
        {'work_id': 'fixed-like', 'author_id': 'unknown', 'judgment': 'like', 'base_score': .9},
        unlabelled,
    ]]
    report = evaluator.evaluate(data)['comparison']
    assert report['baseline']['ndcg_at_10'] == pytest.approx(1 / 1.584962500721156)
    assert report['A']['ndcg_at_10'] == report['baseline']['ndcg_at_10']
    assert report['A+D']['ndcg_at_10'] == report['baseline']['ndcg_at_10']
