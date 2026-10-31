"""Fail-closed scope of the actual P3-395 Flet category observations."""
from __future__ import annotations

import json
import shutil

import pytest

from tools import check_stage5_failed17_other_categories_gui as audit


def copy_real_observations(tmp_path):
    out = tmp_path / 'p395-copied-observations'
    out.mkdir()
    for filename in audit.FROZEN_SHA:
        shutil.copy2(audit.FLET_RUN / filename, out / filename)
    return out


def test_real_original_flet_six_category_states_match_generated_fixture():
    assert audit.verify_original_categories() == audit.prior.fixture_fingerprint()


@pytest.mark.parametrize('filename,field,forged', [
    ('uia-not-found3.json', 'phase', 'all17-restored'),
    ('uia-permission-age3.json', 'accessibility_tree', '3件を再試行'),
    ('uia-other2.json', 'accessibility_tree', '2件を再試行 fake-rows'),
    ('actual-original-full-gui-ready.json', 'app_pid', -1),
])
def test_original_p395_raw_evidence_mutation_rejected(tmp_path, filename, field, forged):
    root = copy_real_observations(tmp_path)
    p = root / filename
    value = json.loads(p.read_text(encoding='utf8'))
    value[field] = forged
    p.write_text(json.dumps(value), encoding='utf8')
    with pytest.raises(RuntimeError, match='original'):
        audit.verify_original_categories(root)


def test_self_consistent_forged_hardware_owner_rejected(tmp_path):
    root = copy_real_observations(tmp_path)
    for path in root.glob('uia-*.json'):
        value = json.loads(path.read_text(encoding='utf8'))
        value['window']['id'] = 44444444
        value['accessibility_tree'] += '\n 9999: Text "synthesized false original"'
        path.write_text(json.dumps(value), encoding='utf8')
    with pytest.raises(RuntimeError, match='original'):
        audit.verify_original_categories(root)


def test_missing_p395_state_rejected(tmp_path):
    root = copy_real_observations(tmp_path)
    (root / 'uia-format3.json').unlink()
    with pytest.raises(RuntimeError, match='original'):
        audit.verify_original_categories(root)
