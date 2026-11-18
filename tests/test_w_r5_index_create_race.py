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
    assert swapped
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


@pytest.mark.parametrize('entry', ['generations', 'downloads'])
@pytest.mark.parametrize('packet', [
    {'version': 5, 'journal': {'id': 'a' * 32, 'generation': 1}},
    {'version': 5, 'entries': {}, 'exclusions': {}, 'quarantine_operations': {},
     'journal': {'id': 'a' * 32, 'generation': 1, 'parent': '0' * 64}},
])
def test_invalid_existing_index_creates_no_directories(tmp_path, entry, packet):
    root = make_root(tmp_path)
    (root / 'active-generations.json').write_text(json.dumps(packet))
    before = {p.name for p in root.iterdir()}
    with pytest.raises(ManagedDownloadError):
        if entry == 'generations':
            mg.ManagedGenerations(root)
        else:
            ManagedPixivDownloads(root, object(), resolve_record=lambda *_: None)
    assert {p.name for p in root.iterdir()} == before


@pytest.mark.skipif(os.name != 'nt', reason='Windows name reservation')
@pytest.mark.parametrize('entry', ['generations', 'downloads'])
def test_absent_index_name_is_reserved_during_directory_creation(tmp_path, monkeypatch, entry):
    from pathlib import Path
    from pixivvault_nicegui.fs_paths import native_io_path
    root = make_root(tmp_path)
    replacement = tmp_path / 'old-index'
    replacement.write_text(json.dumps({'version': 5, 'journal': {'id': 'a' * 32}}))
    original = Path.mkdir
    attempted = []
    def racing_mkdir(path, *args, **kwargs):
        if path.parent == native_io_path(root) and path.name == 'outputs' and not attempted:
            try:
                os.replace(replacement, root / 'active-generations.json')
            except PermissionError:
                attempted.append('blocked')
            else:
                attempted.append('replaced')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'mkdir', racing_mkdir)
    if entry == 'generations':
        mg.ManagedGenerations(root)
    else:
        ManagedPixivDownloads(root, object(), resolve_record=lambda *_: None)
    assert attempted == ['blocked']
    assert not (root / 'active-generations.json').exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows name reservation')
@pytest.mark.parametrize('entry', ['generations', 'downloads'])
def test_old_index_wins_before_reservation_creates_no_directories(tmp_path, monkeypatch, entry):
    root = make_root(tmp_path)
    index = root / 'active-generations.json'
    original = mg.reject_uncertain_old_v5
    injected = False
    def check_then_insert(root_arg):
        nonlocal injected
        original(root_arg)
        if not injected:
            injected = True
            index.write_text(json.dumps({'version': 5, 'journal': {'id': 'a' * 32}}))
    monkeypatch.setattr(mg, 'reject_uncertain_old_v5', check_then_insert)
    with pytest.raises(ManagedDownloadError):
        if entry == 'generations':
            mg.ManagedGenerations(root)
        else:
            ManagedPixivDownloads(root, object(), resolve_record=lambda *_: None)
    assert injected
    assert not (root / 'outputs').exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows name reservation')
def test_absent_index_reservation_is_removed_after_process_exit(tmp_path):
    import subprocess
    import sys
    root = make_root(tmp_path)
    code = '''
import os, sys
from pathlib import Path
from pixivvault_nicegui import managed_generations as mg
root = Path(sys.argv[1])
with mg._index_creation_guard(root) as reserved:
    if not reserved or not (root / 'active-generations.json').exists():
        os._exit(3)
    os._exit(0)
'''
    result = subprocess.run([sys.executable, '-B', '-c', code, str(root)], timeout=30)
    assert result.returncode == 0
    assert not (root / 'active-generations.json').exists()


@pytest.mark.parametrize('entry', ['generations', 'downloads'])
def test_missing_index_with_existing_generations_creates_no_directories(tmp_path, entry):
    from test_v_dl_cap_20261117 import make_generation
    root = make_root(tmp_path)
    make_generation(root, '1000')
    before = {p.name for p in root.iterdir()}
    with pytest.raises(ManagedDownloadError):
        if entry == 'generations':
            mg.ManagedGenerations(root)
        else:
            ManagedPixivDownloads(root, object(), resolve_record=lambda *_: None)
    assert {p.name for p in root.iterdir()} == before


@pytest.mark.parametrize('entry', ['generations', 'downloads'])
def test_existing_empty_index_is_rejected_without_creating_directories(tmp_path, entry):
    root = make_root(tmp_path)
    (root / 'active-generations.json').write_bytes(b'')
    before = {p.name for p in root.iterdir()}
    with pytest.raises(ManagedDownloadError):
        if entry == 'generations':
            mg.ManagedGenerations(root)
        else:
            ManagedPixivDownloads(root, object(), resolve_record=lambda *_: None)
    assert {p.name for p in root.iterdir()} == before
