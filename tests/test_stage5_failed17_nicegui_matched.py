"""Cross-app generated fixture identity is necessary, never full GUI parity."""
from __future__ import annotations

from hashlib import sha256
import json
import shutil

import pytest

from tools import check_stage5_failed17_nicegui_matched as matched
from tools import check_stage5_original_failed17_native as original


FLET_FILES = (
    'actual-original-full-gui-ready.json',
    'generated-failed-17-source-guard.json',
    'filtered-one/original-full-failed-native.json',
    'filtered-one/original-full-failed-native.png',
    'restored-seventeen/original-full-failed-native.json',
    'restored-seventeen/original-full-failed-native.png',
    'uia-initial17.json',
    'uia-filtered-one.json',
    'uia-no-match.json',
    'uia-restored17.json',
)


def copy_flet_evidence(root):
    root.mkdir(parents=True, exist_ok=True)
    for rel in FLET_FILES:
        destination = root / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(matched.FLET_RUN / rel, destination)


def test_same_17_generated_failure_records_have_identical_fixture_sha():
    rows = original.generated_failure_rows()
    original_sha = sha256(
        json.dumps(rows, ensure_ascii=False, sort_keys=True).encode('utf8')).hexdigest()
    assert len(rows) == 17
    assert matched.fixture_fingerprint() == original_sha


def test_real_original_flet_native_capture_metadata_must_be_verified():
    """RED if checker only trusts a self-stated GUI-start success JSON."""
    assert matched.verify_flet_real_evidence() == matched.fixture_fingerprint()


def test_old_native_images_without_ui_a_state_receipts_are_not_four_operations():
    """P2 RED: two photos alone do not prove 17->1->none->17 actions."""
    legacy = matched.ROOT / 'artifacts/p3-389-real-flet-failed17-r2'
    with pytest.raises(RuntimeError, match='original real Flet'):
        matched.verify_flet_real_evidence(legacy)


def test_missing_real_flet_native_capture_cannot_be_counted(tmp_path):
    root = tmp_path / 'saved-flet'
    root.mkdir()
    shutil.copy2(
        matched.FLET_RUN / 'generated-failed-17-source-guard.json',
        root / 'generated-failed-17-source-guard.json',
    )
    with pytest.raises(RuntimeError, match='original real Flet'):
        matched.verify_flet_real_evidence(root)


def test_tampered_original_flet_native_png_rejected_before_gui(tmp_path):
    root = tmp_path / 'saved-flet'
    copy_flet_evidence(root)
    (root / 'filtered-one/original-full-failed-native.png').write_bytes(b'fake-image')
    with pytest.raises(RuntimeError, match='original real Flet'):
        matched.verify_flet_real_evidence(root)


def test_identical_native_capture_with_self_consistent_rewritten_sha_is_rejected(tmp_path):
    """P2 RED: two copies of one picture do not prove 1-row then 17-row UI."""
    root = tmp_path / 'flet-duplicated'
    copy_flet_evidence(root)
    image = (root / 'filtered-one/original-full-failed-native.png').read_bytes()
    (root / 'restored-seventeen/original-full-failed-native.png').write_bytes(image)
    altered = root / 'restored-seventeen/original-full-failed-native.json'
    metadata = json.loads(altered.read_text(encoding='utf8'))
    metadata['image_sha256'] = sha256(image).hexdigest()
    altered.write_text(json.dumps(metadata), encoding='utf8')
    uia = root / 'uia-restored17.json'
    state = json.loads(uia.read_text(encoding='utf8'))
    state['native_capture_sha256'] = sha256(image).hexdigest()
    uia.write_text(json.dumps(state), encoding='utf8')
    with pytest.raises(RuntimeError, match='original real Flet'):
        matched.verify_flet_real_evidence(root)


def test_forged_restored_uia_state_is_not_equivalent(tmp_path):
    root = tmp_path / 'forged-state'
    copy_flet_evidence(root)
    restored = root / 'uia-restored17.json'
    state = json.loads(restored.read_text(encoding='utf8'))
    state['ui_tree'] = json.loads(
        (root / 'uia-filtered-one.json').read_text(encoding='utf8'))['ui_tree']
    restored.write_text(json.dumps(state), encoding='utf8')
    with pytest.raises(RuntimeError, match='original real Flet'):
        matched.verify_flet_real_evidence(root)


@pytest.mark.parametrize('field,value', [
    ('native_gui_main_returned', False),
    ('source_unchanged', False),
    ('generated_failure_count', 0),
    ('fixture_sha256', '0' * 64),
])
def test_unverified_original_flet_evidence_denies_nicegui_run_before_launch(
    tmp_path, monkeypatch, field, value
):
    flet_dir = tmp_path / 'flet-evidence'
    copy_flet_evidence(flet_dir)
    receipt = json.loads(
        (matched.FLET_RUN / 'generated-failed-17-source-guard.json').read_text(encoding='utf8'))
    receipt[field] = value
    (flet_dir / 'generated-failed-17-source-guard.json').write_text(
        json.dumps(receipt), encoding='utf8')
    monkeypatch.setattr(matched, 'FLET_RUN', flet_dir)
    output = matched.ROOT / 'artifacts' / 'p3-invalid-never-run'
    # The checker refuses before creating any test directory or process.
    with pytest.raises(RuntimeError, match='original real Flet'):
        matched.run(output)
    assert not output.exists()
