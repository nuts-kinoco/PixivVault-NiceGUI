"""Freeze an exact, independently verifiable Windows packaging source tree.

Never reads the original Flet repository or application data. Build later from
the fresh destination's tools/build_windows.ps1, not from the mutable worktree.
The manifest identifies the uncommitted source bytes, not merely git HEAD.
No existing destination is modified and no failed partial freeze is promoted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess


SOURCE_FILES = ('README.md', 'pyproject.toml',
                'tools/build_windows.ps1', 'tools/native_entry.py',
                'tools/freeze_windows_candidate.py')
SOURCE_ROOT = 'src/pixivvault_nicegui'
ALLOWED_SUFFIXES = frozenset({'.py', '.css', '.js'})
MAX_FILES = 1000
MAX_SOURCE_BYTES = 64 * 1024 * 1024


class FreezeError(ValueError):
    pass


def _metadata(path: Path, *, is_dir=False):
    try:
        value = path.lstat()
    except OSError as exc:
        raise FreezeError('source entry inaccessible') from exc
    if (stat.S_ISLNK(value.st_mode) or
            getattr(value, 'st_file_attributes', 0) & 0x400):
        raise FreezeError('link or reparse entry rejected')
    if is_dir:
        if not stat.S_ISDIR(value.st_mode):
            raise FreezeError('directory expected')
    elif not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
        raise FreezeError('regular single-link file expected')
    return value


def _components_safe(path: Path):
    for component in (path, *path.parents):
        if component.exists() or component.is_symlink():
            _metadata(component, is_dir=True)


def _identity(value):
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def _digest(path: Path) -> tuple[int, str]:
    initial = _metadata(path)
    h = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        if _identity(initial) != _identity(opened):
            raise FreezeError('source changed before read')
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(part)
            size += len(part)
        after = os.fstat(stream.fileno())
    if (_identity(initial) != _identity(after)
            or _identity(initial) != _identity(_metadata(path))
            or size != initial.st_size):
        raise FreezeError('source changed during read')
    return size, h.hexdigest()


def inventory(root: Path) -> list[dict]:
    """Inventory only build inputs, explicitly refusing unknown package files."""
    root = root.absolute()
    _components_safe(root)
    _metadata(root, is_dir=True)
    package = root / SOURCE_ROOT
    _metadata(root / 'src', is_dir=True)
    _metadata(package, is_dir=True)
    _metadata(root / 'tools', is_dir=True)
    paths = [root / relative for relative in SOURCE_FILES]
    for directory, dirs, files in os.walk(package, followlinks=False):
        current = Path(directory)
        _metadata(current, is_dir=True)
        for dirname in list(dirs):
            child = current / dirname
            if dirname == '__pycache__':
                _metadata(child, is_dir=True)
                dirs.remove(dirname)
                continue
            _metadata(child, is_dir=True)
        for filename in files:
            file = current / filename
            if filename.endswith(('.pyc', '.pyo')):
                _metadata(file)
                continue
            if file.suffix.lower() not in ALLOWED_SUFFIXES:
                raise FreezeError('unrecognized package input')
            paths.append(file)
    if len(paths) > MAX_FILES:
        raise FreezeError('source file count limit')
    report = []
    total = 0
    for path in sorted(paths, key=lambda p: p.relative_to(root).as_posix()):
        size, digest = _digest(path)
        total += size
        if total > MAX_SOURCE_BYTES:
            raise FreezeError('source byte count limit')
        report.append({'path': path.relative_to(root).as_posix(),
                       'bytes': size, 'sha256': digest})
    if len({entry['path'] for entry in report}) != len(report):
        raise FreezeError('duplicate input path')
    return report


def _git_label(root: Path) -> dict:
    def read(*args):
        result = subprocess.run(['git', '-C', str(root), *args], check=False,
                                capture_output=True, timeout=8)
        if result.returncode:
            raise FreezeError('git identity unavailable')
        return result.stdout.decode('ascii', errors='strict').strip()
    branch = read('branch', '--show-current')
    head = read('rev-parse', 'HEAD')
    if not branch or len(head) != 40 or any(char not in '0123456789abcdef' for char in head):
        raise FreezeError('git identity invalid')
    return {'branch': branch, 'head': head,
            'note': 'HEAD labels a dirty worktree; every frozen file is individually hashed'}


def _copy_verified(source: Path, target: Path, expected: dict):
    initial = _metadata(source)
    if initial.st_size != expected['bytes']:
        raise FreezeError('source size changed')
    target.parent.mkdir(parents=True, exist_ok=True)
    _components_safe(target.parent)
    with source.open('rb') as stream, target.open('xb') as output:
        opened = os.fstat(stream.fileno())
        if _identity(initial) != _identity(opened):
            raise FreezeError('source identity changed')
        hasher = hashlib.sha256()
        size = 0
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            output.write(part)
            hasher.update(part)
            size += len(part)
        output.flush()
        os.fsync(output.fileno())
        if _identity(initial) != _identity(os.fstat(stream.fileno())):
            raise FreezeError('source changed during copy')
    if _identity(initial) != _identity(_metadata(source)):
        raise FreezeError('source changed after copy')
    if (size, hasher.hexdigest()) != (expected['bytes'], expected['sha256']):
        raise FreezeError('source bytes differ from inventory')
    if _digest(target) != (expected['bytes'], expected['sha256']):
        raise FreezeError('frozen bytes not verified')


def freeze(source_root: Path, destination: Path, *, label: dict | None = None) -> dict:
    source_root, destination = source_root.absolute(), destination.absolute()
    _components_safe(source_root)
    _components_safe(destination.parent)
    if destination.exists() or destination.is_symlink():
        raise FreezeError('destination must be fresh')
    # The only allowable output is the designated artifacts subtree of this
    # same worktree. A function caller cannot silently bypass the CLI gate.
    if not destination.is_relative_to(source_root / 'artifacts') or destination == source_root / 'artifacts':
        raise FreezeError('destination outside artifacts')
    before = inventory(source_root)
    git = _git_label(source_root) if label is None else label
    destination.mkdir()
    try:
        for entry in before:
            _copy_verified(source_root / entry['path'], destination / entry['path'], entry)
        after = inventory(source_root)
        if before != after:
            raise FreezeError('source set or hashes changed during freeze')
        after_copied = inventory(destination)
        if before != after_copied:
            raise FreezeError('frozen candidate differs from source')
        canonical = json.dumps(before, sort_keys=True, separators=(',', ':')).encode('utf-8')
        report = {'format': 1, 'status': 'verified', 'git': git,
                  'source_count': len(before), 'source_bytes': sum(e['bytes'] for e in before),
                  'source_tree_sha256': hashlib.sha256(canonical).hexdigest(),
                  'files': before, 'build_entry': 'tools/build_windows.ps1',
                  'note': 'Source bytes frozen. Native build, deployment, CI and Goal are NOT certified.'}
        with (destination / 'source-manifest.json').open('xb') as output:
            output.write(json.dumps(report, sort_keys=True, indent=2).encode('utf-8') + b'\n')
            output.flush()
            os.fsync(output.fileno())
        return report
    except BaseException:
        # Never remove or reuse a partial source tree. A missing verified
        # manifest deliberately makes the destination ineligible for build.
        raise


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise FreezeError('duplicate manifest field')
        result[key] = value
    return result


def verify_frozen(root: Path) -> dict:
    """Independently recheck a frozen tree before and after its native build."""
    root = root.absolute()
    _components_safe(root)
    manifest = root / 'source-manifest.json'
    size, proof = _digest(manifest)
    if not 0 < size <= 256 * 1024:
        raise FreezeError('manifest size limit')
    try:
        report = json.loads(manifest.read_bytes(), object_pairs_hook=_unique_pairs)
    except FreezeError:
        raise
    except (ValueError, UnicodeError) as exc:
        raise FreezeError('invalid manifest') from exc
    if (type(report) is not dict or report.get('format') != 1 or
            report.get('status') != 'verified' or
            type(report.get('files')) is not list or
            type(report.get('source_count')) is not int or
            type(report.get('source_bytes')) is not int or
            type(report.get('source_tree_sha256')) is not str):
        raise FreezeError('unverified manifest')
    files = inventory(root)
    if (report['files'] != files or report['source_count'] != len(files)
            or report['source_bytes'] != sum(entry['bytes'] for entry in files)):
        raise FreezeError('frozen bytes or file set changed')
    canonical = json.dumps(files, sort_keys=True, separators=(',', ':')).encode('utf-8')
    if report['source_tree_sha256'] != hashlib.sha256(canonical).hexdigest():
        raise FreezeError('frozen tree checksum changed')
    if _digest(manifest) != (size, proof):
        raise FreezeError('manifest changed during verification')
    return {'verified': True, 'source_count': len(files),
            'source_tree_sha256': report['source_tree_sha256'],
            'manifest_sha256': proof}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--output', type=Path, help='Fresh freeze directory')
    mode.add_argument('--verify', type=Path, help='Existing frozen tree')
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    out = (args.output or args.verify).absolute()
    artifact_root = root / 'artifacts'
    if args.output is not None:
        if not out.is_relative_to(artifact_root) or out == artifact_root:
            parser.error('output must be a fresh child of this worktree/artifacts')
        report = freeze(root, out)
        print(json.dumps({'verified': True, 'files': report['source_count'],
                          'tree_sha256': report['source_tree_sha256'],
                          'output': str(out)}, sort_keys=True))
    else:
        # The verifier is itself part of the frozen input. From inside that
        # copied root, '--verify .' verifies the snapshot it belongs to.
        if out != root and (not out.is_relative_to(artifact_root) or out == artifact_root):
            parser.error('verify the frozen source root or a child of artifacts')
        print(json.dumps(dict(verify_frozen(out),output=str(out)), sort_keys=True))


if __name__ == '__main__':
    main()
