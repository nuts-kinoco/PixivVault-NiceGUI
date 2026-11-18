import json
import os

import pytest

from pixivvault_nicegui import managed_generations as mg
from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads, ManagedDownloadError
from test_v_dl_cap_20261117 import make_root


@pytest.mark.parametrize('entry', ['generations', 'downloads'])
def test_index_replaced_after_precheck_creates_no_directories(tmp_path, monkeypatch, entry):
    root = make_root(tmp_path)
    index = root / 'active-generations.json'
    index.write_text(json.dumps({'version': 5, 'journal': {'id': 'a' * 32, 'generation': 1}}))
    replacement = tmp_path / 'replacement'
    replacement.write_text(json.dumps({'version': 5, 'journal': {'id': 'a' * 32}}))
    original = mg.reject_uncertain_old_v5
    swapped = False
    def check_then_swap(root):
        nonlocal swapped
        original(root)
        if not swapped:
            swapped = True
            os.replace(replacement, index)
    monkeypatch.setattr(mg, 'reject_uncertain_old_v5', check_then_swap)
    before = {p.name for p in root.iterdir()}
    with pytest.raises(ManagedDownloadError):
        if entry == 'generations':
            mg.ManagedGenerations(root)
        else:
            ManagedPixivDownloads(root, object(), resolve_record=lambda *_: None)
    assert {p.name for p in root.iterdir()} == before


@pytest.mark.skipif(os.name != 'nt', reason='Windows sharing contract')
def test_index_guard_blocks_replacement_during_creation(tmp_path):
    root = make_root(tmp_path)
    index = root / 'active-generations.json'
    index.write_text(json.dumps({'version': 5, 'journal': {'id': 'a' * 32, 'generation': 1}}))
    replacement = tmp_path / 'replacement'
    replacement.write_text(json.dumps({'version': 5, 'journal': {'id': 'a' * 32}}))
    with mg._index_creation_guard(root):
        with pytest.raises(PermissionError):
            os.replace(replacement, index)
        assert json.loads(index.read_text())['journal']['generation'] == 1
