"""Run a command between two C: file inventories; evidence and TEMP/TMP stay on S:."""

import argparse
import json
import os
import sqlite3
import stat
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

if __package__:
    from .p6_secrets import kinds, safe_location
else:
    from p6_secrets import kinds, safe_location


# Only signatures specific to Windows-maintained locations/artifacts. The
# profile, Temp, AppData, ProgramData and Windows Temp are still inventoried.
OS_PREFIXES = ('c:\\windows\\prefetch\\', 'c:\\windows\\system32\\winevt\\logs\\',
               'c:\\windows\\softwaredistribution\\download\\', 'c:\\windows\\logs\\cbs\\')


@contextmanager
def open_snapshot(path):
    connection = sqlite3.connect(path)
    try:
        for name in ('before', 'after'):
            connection.execute(f'CREATE TABLE {name} (path_key BLOB PRIMARY KEY, path BLOB NOT NULL) WITHOUT ROWID')
        yield connection
    finally:
        connection.close()


def snapshot(root, connection, table):
    if table not in {'before', 'after'}:
        raise ValueError('invalid snapshot phase')
    pending, batch, errors = [root], [], []
    skipped_reparse = 0
    while pending:
        folder = pending.pop()
        try:
            native = str(folder)
            if os.name == 'nt' and len(native) >= 240:
                native = '\\\\?\\' + native
            with os.scandir(native) as entries:
                for item in entries:
                    path = os.path.join(folder, item.name)
                    try:
                        item_stat = item.stat(follow_symlinks=False)
                        attributes = getattr(item_stat, 'st_file_attributes', 0)
                        if item.is_symlink() or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                            skipped_reparse += 1
                        elif stat.S_ISDIR(item_stat.st_mode):
                            pending.append(path)
                        elif stat.S_ISREG(item_stat.st_mode):
                            # NTFS can return lone UTF-16 code units; SQLite TEXT cannot store them.
                            name = path.encode('utf-8', errors='surrogatepass')
                            batch.append((name.lower(), name))
                    except OSError as exc:
                        errors.append({'path': safe_location(path), 'errno': exc.errno})
        except OSError as exc:
            errors.append({'path': safe_location(str(folder)), 'errno': exc.errno})
        if len(batch) >= 4096:
            connection.executemany(f'INSERT OR IGNORE INTO {table} VALUES (?, ?)', batch)
            batch.clear()
    connection.executemany(f'INSERT OR IGNORE INTO {table} VALUES (?, ?)', batch)
    connection.commit()
    if skipped_reparse:
        connection.execute('CREATE TABLE IF NOT EXISTS reparse (phase TEXT, entries INTEGER)')
        connection.execute('INSERT INTO reparse VALUES (?, ?)', (table, skipped_reparse))
        connection.commit()
    return errors


def new_files(connection):
    for (path,) in connection.execute('SELECT path FROM after WHERE path_key NOT IN (SELECT path_key FROM before)'):
        yield path.decode('utf-8', errors='surrogatepass')


def exclusions(path):
    if path is None:
        return []
    rows = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(rows, list):
        raise ValueError('exclusions must be a list')
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {'prefix', 'reason', 'evidence'}
                or any(not isinstance(value, str) or not value.strip() for value in row.values())
                or not row['prefix'].lower().startswith('c:\\') or not row['prefix'].endswith('\\')
                or row['prefix'].count('\\') < 3 or '..' in Path(row['prefix']).parts):
            raise ValueError('each exclusion requires a C: subdirectory, reason and evidence')
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--exclusions', type=Path, help='JSON list of evidenced OS and other-application directories')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    output = args.out.resolve()
    if output.drive.upper() != 'S:' or not args.command or args.command[0] != '--' or len(args.command) < 2:
        parser.error('provide a fresh S: directory and -- COMMAND')
    rules = exclusions(args.exclusions)
    output.mkdir(parents=True)
    temp = output / 'temp'
    temp.mkdir()
    profile = output / 'test-profile'
    local, roaming = profile / 'AppData' / 'Local', profile / 'AppData' / 'Roaming'
    local.mkdir(parents=True)
    roaming.mkdir(parents=True)
    env = dict(os.environ, TEMP=str(temp), TMP=str(temp), TMPDIR=str(temp),
               PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1',
               HOME=str(profile), USERPROFILE=str(profile), LOCALAPPDATA=str(local), APPDATA=str(roaming),
               P6_C_AUDIT_OUT=str(output / 'python-write-audit.json'))
    def now():
        return datetime.now(timezone.utc).isoformat()

    def phase(state):
        (output / 'run-status.json').write_text(json.dumps({'wrapper_pid': os.getpid(), 'phase': state,
                                                            'utc': now()}), encoding='utf-8')

    with open_snapshot(output / 'snapshot.sqlite3') as db:
        phase('before')
        clock = {'before_start': now()}
        errors = {'before': snapshot(Path('C:/'), db, 'before')}
        clock['command_start'] = now()
        matches = []
        with (output / 'pytest.log').open('wb') as log, subprocess.Popen(
            args.command[1:], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env
        ) as child:
            phase('command')
            (output / 'owned-test.json').write_text(json.dumps({'wrapper_pid': os.getpid(), 'test_pid': child.pid}),
                                                   encoding='utf-8')
            for number, line in enumerate(child.stdout, 1):
                if found := kinds(line):
                    matches.extend(dict(location='pytest.log', line=number, kind=kind) for kind in found)
                    log.write(b'[credential pattern redacted]\n')
                else:
                    log.write(line)
            exit_code = child.wait()
        clock['after_start'] = now()
        phase('after')
        errors['after'] = snapshot(Path('C:/'), db, 'after')
        clock['after_end'] = now()
        files, excluded, other = [], [], []
        for path in new_files(db):
            if path.lower().startswith(OS_PREFIXES):
                excluded.append(safe_location(path))
                continue
            rule = next((index for index, row in enumerate(rules)
                         if path.lower().startswith(row['prefix'].lower())), None)
            if rule is None:
                files.append(safe_location(path))
            else:
                other.append({'path': safe_location(path), 'rule': rule})
        counts = {name: db.execute(f'SELECT count(*) FROM {name}').fetchone()[0]
                  for name in ('before', 'after')}
        reparse = (dict(db.execute('SELECT * FROM reparse')) if db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='reparse'").fetchone() else {})
    before_errors = {(row['path'], row['errno']) for row in errors['before']}
    after_errors = {(row['path'], row['errno']) for row in errors['after']}
    audit_path = output / 'python-write-audit.json'
    audit = json.loads(audit_path.read_text(encoding='utf-8')) if audit_path.is_file() else None
    audit_missing = 'p6_c_write_audit' in args.command[1:] and audit is None
    report = {'times_utc': clock, 'pytest_exit': exit_code, 'files_inventoried': counts,
              'new_files': files, 'excluded_os_files': excluded, 'os_rules': OS_PREFIXES,
              'excluded_by_rule': other, 'additional_rules': rules,
              'enumeration_errors': errors, 'reparse_entries_not_followed': reparse,
              'stable_enumeration_errors': len(before_errors & after_errors),
              'after_only_enumeration_errors': len(after_errors - before_errors),
              'python_write_audit': audit,
              'python_write_audit_missing': audit_missing,
              'test_output_pattern_matches': matches}
    (output / 'drive-diff.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    phase('finished')
    print(f'pytest exit={exit_code}, unmatched new files={len(files)}, exclusions={len(excluded) + len(other)}, '
          f'enumeration errors={sum(map(len, errors.values()))}, redacted test lines={len(matches)}')
    return bool(exit_code or files or matches or any(errors.values()) or audit_missing or audit and audit['blocked'])


if __name__ == '__main__':
    raise SystemExit(main())
