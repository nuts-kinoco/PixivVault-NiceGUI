"""Isolated freeze test: no native build, source data, registry or network."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import freeze_windows_candidate as freeze


def candidate(tmp_path):
    root = tmp_path / 'candidate'
    (root / 'src/pixivvault_nicegui').mkdir(parents=True)
    (root / 'src/pixivvault_nicegui/__pycache__').mkdir()
    (root / 'tools').mkdir()
    (root / 'artifacts').mkdir()
    files = {
        'README.md': b'# Synthetic\n',
        'pyproject.toml': b'[project]\nname="synthetic"\n',
        'tools/build_windows.ps1': b'Write-Host frozen\n',
        'tools/native_entry.py': b'raise SystemExit(0)\n',
        'tools/freeze_windows_candidate.py': b'# synthetic verifier\n',
        'src/pixivvault_nicegui/__init__.py': b'# synthetic\n',
        'src/pixivvault_nicegui/ui.js': b'const state = 1;\n',
    }
    for relative, value in files.items():
        (root / relative).write_bytes(value)
    (root / 'src/pixivvault_nicegui/__pycache__/__init__.pyc').write_bytes(b'ignored')
    return root, files


def label():
    return {'branch': 'synthetic', 'head': 'f'*40,
            'note': 'synthetic uncommitted data'}


def test_freeze_copies_only_whitelisted_files_and_checks_tree(tmp_path):
    root, files = candidate(tmp_path)
    dest = root / 'artifacts' / 'fresh'
    result = freeze.freeze(root, dest, label=label())
    assert result['status'] == 'verified'
    assert result['source_count'] == len(files)
    assert result['git'] == label()
    actual = json.loads((dest / 'source-manifest.json').read_text())
    assert actual == result
    assert {row['path'] for row in result['files']} == set(files)
    assert not (dest / 'src/pixivvault_nicegui/__pycache__').exists()
    assert freeze.inventory(root) == freeze.inventory(dest) == result['files']
    checked = freeze.verify_frozen(dest)
    assert checked['verified'] and checked['source_count'] == len(files)
    assert checked['source_tree_sha256'] == result['source_tree_sha256']
    for relative, expected in files.items():
        assert (dest / relative).read_bytes() == expected
    canonical = json.dumps(result['files'],sort_keys=True,separators=(',',':')).encode()
    assert result['source_tree_sha256'] == hashlib.sha256(canonical).hexdigest()


def test_existing_destination_not_reused_or_changed(tmp_path):
    root, _ = candidate(tmp_path)
    dest = root / 'artifacts' / 'occupied'
    dest.mkdir()
    (dest / 'foreign.bin').write_bytes(b'keep')
    with pytest.raises(freeze.FreezeError, match='fresh'):
        freeze.freeze(root,dest,label=label())
    assert (dest / 'foreign.bin').read_bytes() == b'keep'


def test_destination_outside_owned_artifacts_rejected_before_write(tmp_path):
    root, _ = candidate(tmp_path)
    outside = tmp_path / 'other'
    with pytest.raises(freeze.FreezeError,match='outside artifacts'):
        freeze.freeze(root,outside,label=label())
    assert not outside.exists()


def test_changed_live_source_fails_without_success_manifest(tmp_path,monkeypatch):
    root, _ = candidate(tmp_path)
    original = freeze._copy_verified
    flipped = False

    def copy_then_mutate(source,target,expected):
        nonlocal flipped
        original(source,target,expected)
        if not flipped:
            flipped = True
            (root / 'src/pixivvault_nicegui/ui.js').write_bytes(b'changed during freeze\n')

    monkeypatch.setattr(freeze,'_copy_verified',copy_then_mutate)
    dest=root / 'artifacts' / 'changed'
    with pytest.raises(freeze.FreezeError,match='changed|differ'):
        freeze.freeze(root,dest,label=label())
    assert dest.is_dir() and not (dest / 'source-manifest.json').exists()
    with pytest.raises(freeze.FreezeError,match='fresh'):
        freeze.freeze(root,dest,label=label())


def test_unknown_binary_in_source_rejected_before_copy(tmp_path):
    root, _ = candidate(tmp_path)
    (root / 'src/pixivvault_nicegui/raw.sqlite3').write_bytes(b'private data')
    dest = root / 'artifacts' / 'blocked'
    with pytest.raises(freeze.FreezeError,match='unrecognized package'):
        freeze.freeze(root,dest,label=label())
    assert not dest.exists()


def test_reparse_attribute_on_package_file_rejected(tmp_path,monkeypatch):
    root, _ = candidate(tmp_path)
    target = root / 'src/pixivvault_nicegui/__init__.py'
    actual = Path.lstat

    def marked(self,*args,**kwargs):
        info=actual(self,*args,**kwargs)
        if self == target:
            return SimpleNamespace(**{key:getattr(info,key) for key in
                ('st_mode','st_nlink','st_dev','st_ino','st_size','st_mtime_ns')},
                st_file_attributes=0x400)
        return info

    monkeypatch.setattr(Path,'lstat',marked)
    with pytest.raises(freeze.FreezeError,match='reparse'):
        freeze.freeze(root,root/'artifacts'/'linked',label=label())
    assert not (root/'artifacts'/'linked').exists()


def test_modified_frozen_file_is_not_approved(tmp_path):
    root, _ = candidate(tmp_path)
    dest = root / 'artifacts' / 'frozen'
    freeze.freeze(root,dest,label=label())
    (dest/'tools/native_entry.py').write_bytes(b'changed after freeze\n')
    with pytest.raises(freeze.FreezeError, match='changed'):
        freeze.verify_frozen(dest)


def test_duplicate_manifest_json_field_rejected(tmp_path):
    root, _ = candidate(tmp_path)
    dest = root / 'artifacts' / 'frozen'
    freeze.freeze(root,dest,label=label())
    (dest/'source-manifest.json').write_bytes(b'{"format":1,"format":1}')
    with pytest.raises(freeze.FreezeError, match='duplicate'):
        freeze.verify_frozen(dest)
