"""Actual Windows junction/hardlink rejection at managed native I/O boundaries."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def run(output):
    from pixivvault_nicegui.fs_paths import native_io_path
    from pixivvault_nicegui.managed_downloads import (
        ManagedDownloadError, ManagedPixivDownloads, _safe_path, _read_bounded, _write)
    if os.name != 'nt':
        raise RuntimeError('actual Windows required')
    output = Path(output).absolute()
    permitted = ROOT / 'artifacts/p3-318-junction-native'
    if output == permitted or not output.is_relative_to(permitted):
        raise ValueError('owned junction acceptance subdirectory required')
    _safe_path(output)
    output.mkdir(parents=True, exist_ok=False)
    files = [ROOT / 'src/pixivvault_nicegui' / name for name in (
        'fs_paths.py', 'managed_downloads.py', 'managed_generations.py', 'managed_quarantine.py')]
    def sources():
        return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    result = dict(success=False, source_before=sources(), rejected={}, forced_cleanup=0)
    target = output / 'target'
    target.mkdir()
    protected = target / 'generated-proof.bin'
    protected.write_bytes(b'generated ownership guard; no real source data\n')
    def proof():
        info = protected.stat()
        return dict(sha256=hashlib.sha256(protected.read_bytes()).hexdigest(),
                    bytes=info.st_size, mtime_ns=info.st_mtime_ns, nlink=info.st_nlink,
                    files=sorted(p.name for p in target.iterdir()))
    before = proof()
    junction = output / 'junction'
    hardlink = output / 'hardlink.bin'
    try:
        created = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(junction), str(target)],
            check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW)
        result['junction_creation_exit'] = created.returncode
        if created.returncode != 0:
            raise AssertionError('owned junction creation failed')
        attributes = junction.lstat().st_file_attributes
        result['junction_attributes'] = attributes
        assert attributes & 0x400 and junction.resolve() == target.resolve()
        checks = (
            ('root', lambda: _safe_path(junction)),
            ('ancestor', lambda: _safe_path(junction / 'new-child')),
            ('read', lambda: _read_bounded(junction / protected.name, 4096)),
            ('write', lambda: _write(junction / 'forbidden.bin', b'never write')),
            ('owner_constructor', lambda: ManagedPixivDownloads(
                junction / 'new-owned', None, resolve_record=lambda _: None)),
        )
        for label, action in checks:
            try:
                action()
            except ManagedDownloadError as error:
                assert 'reparse' in str(error) or 'link rejected' in str(error)
                result['rejected'][label] = type(error).__name__
            else:
                raise AssertionError('managed native boundary accepted ' + label)
        assert proof() == before
        os.link(protected, hardlink)
        result['hardlink_count'] = protected.stat().st_nlink
        assert result['hardlink_count'] == 2
        for label, path in (('hardlink_alias', hardlink), ('hardlink_source', protected)):
            try:
                _read_bounded(native_io_path(path), 4096)
            except ManagedDownloadError as error:
                assert str(error) == 'managed file rejected'
                result['rejected'][label] = type(error).__name__
            else:
                raise AssertionError('hardlink boundary accepted ' + label)
    except Exception as error:
        result['error'] = type(error).__name__ + ': ' + str(error)
    finally:
        if hardlink.exists():
            if not os.path.samefile(hardlink, protected):
                raise AssertionError('owned hardlink identity changed')
            hardlink.unlink()
        if junction.exists():
            attributes = junction.lstat().st_file_attributes
            if not attributes & 0x400 or junction.resolve() != target.resolve():
                raise AssertionError('owned junction identity changed')
            # Remove only this verified junction, never recurse into its target.
            os.rmdir(junction)
        result['protected_before'] = before
        result['protected_after'] = proof()
        result['protected_unchanged'] = before == result['protected_after']
        result['aliases_removed'] = not junction.exists() and not hardlink.exists()
        result['source_after'] = sources()
        result['source_changed'] = result['source_before'] != result['source_after']
        result['success'] = ('error' not in result and len(result['rejected']) == 7
            and result['protected_unchanged'] and result['aliases_removed'] and not result['source_changed'])
        (output / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({k: result[k] for k in (
        'success', 'rejected', 'protected_unchanged', 'aliases_removed', 'source_changed')}))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    raise SystemExit(run(parser.parse_args().output))

