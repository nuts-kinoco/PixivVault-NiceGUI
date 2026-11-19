"""Report credential locations without printing the line, value, or a value hash."""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path


PATTERNS = {
    'cookie': re.compile(
        rb'''(?i)\bPHPSESSID(?:["']?\s*[:=]\s*["']?|\t)([\w.%+/-]{8,})'''),
    'token': re.compile(
        rb'''(?ix)(?:\bbearer\s+([\w.~+/-]{12,})|'''
        rb'''\b(?:(?:access|refresh|csrf|session|oauth|api|app)[_ -]?token|api[_ -]?key|'''
        rb'''client[_ -]?secret|secret[_ -]?key)'''
        rb'''["']?\s*[:=]\s*["']([\w.~+/=-]{8,})["']|'''
        rb'''\b((?:gh[pousr]_|github_pat_|sk-(?:proj-)?)\w[\w-]{15,}|eyJ[\w-]+\.eyJ[\w-]+\.[\w-]+))'''),
    'development-password': re.compile(
        rb'''(?ix)\b(?:[\w-]*(?:dev|gate|premium)[_-]?[\w-]*(?:password|passwd|passphrase)|password)'''
        rb'''["']?\s*[:=]\s*["']([^"'\s]{5,})["']'''),
}


def kinds(line, known_values=()):
    found = {kind for kind, pattern in PATTERNS.items() if pattern.search(line)}
    if any(secret in line for secret in known_values):
        found.add('development-password')
    return sorted(found)


def safe_location(location, known_values=()):
    if kinds(location.encode('utf-8', errors='replace'), known_values):
        return '[credential-bearing location redacted]'
    return location


def find(data, location, known_values=()):
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        data = data.decode('utf-16', errors='replace').encode('utf-8')
    elif b'\x00' in data[:4096]:
        # Also scan printable runs inside binary blobs without emitting their contents.
        data = data.replace(b'\x00', b' ')
    for line_number, line in enumerate(data.splitlines(), 1):
        for kind in kinds(line, known_values):
            yield {'location': safe_location(location, known_values), 'line': line_number, 'kind': kind}


def git_find(repo, known_values=(), counts=None):
    # Includes loose/unreachable commits, reflogs and all refs/worktrees sharing this store.
    objects = subprocess.check_output(
        ['git', 'cat-file', '--batch-all-objects', '--batch-check=%(objectname) %(objecttype)'], cwd=repo)
    with subprocess.Popen(['git', 'cat-file', '--batch'], cwd=repo, stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE) as git:
        for row in objects.splitlines():
            oid, object_type = row.split()
            if object_type not in (b'blob', b'commit', b'tag'):
                continue
            label = object_type.decode()
            if counts is not None:
                counts[label] = counts.get(label, 0) + 1
            git.stdin.write(oid + b'\n')
            git.stdin.flush()
            header = git.stdout.readline().split()
            if len(header) != 3 or header[:2] != [oid, object_type]:
                raise RuntimeError('Git object changed during scan')
            size = int(header[2])
            data = git.stdout.read(size)
            if len(data) != size or git.stdout.read(1) != b'\n':
                raise RuntimeError('truncated Git object')
            yield from find(data, f'git:{label}:{oid.decode()}', known_values)
        git.stdin.close()
        if git.wait():
            raise RuntimeError('git cat-file failed')


def artifact_files(roots, errors):
    for root in roots:
        if root.is_file():
            yield root
            continue
        def failed(exc):
            errors.append({'location': safe_location(str(exc.filename)), 'errno': exc.errno})

        for folder, dirs, files in os.walk(root, followlinks=False, onerror=failed):
            dirs[:] = [name for name in dirs if name not in {'.git', '.venv', 'node_modules', '__pycache__'}
                       and not Path(folder, name).is_junction()]
            for name in files:
                path = Path(folder, name)
                if name.casefold() in ('cookies.txt', 'generated-cookie.txt') or path.is_symlink():
                    continue  # Cookie stores are inputs, never evidence.
                filename = name.casefold()
                if path.suffix.lower() in {'.log', '.out', '.err'} or (
                    ('status' in filename or 'evidence' in str(path).casefold())
                    and path.suffix.lower() in {'.txt', '.md', '.json', '.jsonl', '.csv', '.xml', '.html'}
                ) or 'pytest' in filename:
                    yield path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path.cwd())
    parser.add_argument('--artifact', type=Path, action='append', default=[])
    parser.add_argument('--known-values-file', type=Path, help='Optional isolated list, one value per line')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.out.resolve().drive.upper() != 'S:':
        parser.error('output must be on S:')
    known = tuple(line for line in args.known_values_file.read_bytes().splitlines() if line) if args.known_values_file else ()
    roots = [args.repo, *args.artifact]
    if missing := [root for root in roots if not root.exists()]:
        parser.error(f'{len(missing)} scan roots do not exist')
    counts, errors = {}, []
    findings = list(git_find(args.repo, known, counts))
    paths = sorted({path.resolve() for path in artifact_files(roots, errors)} - {args.out.resolve()})
    for path in paths:
        try:
            findings.extend(find(path.read_bytes(), str(path), known))
        except OSError as exc:
            errors.append({'location': safe_location(str(path), known), 'errno': exc.errno})
    args.out.write_text(json.dumps({'git_objects': counts, 'artifact_files': len(paths), 'findings': findings,
                                    'artifact_read_errors': errors}, indent=2), encoding='utf-8')
    print(f'artifact files={len(paths)}, credential pattern matches={len(findings)}')
    return bool(findings or errors)


if __name__ == '__main__':
    raise SystemExit(main())
