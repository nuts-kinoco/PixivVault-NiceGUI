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
            with os.scandir(folder) as entries:
                for item in entries:
                    try:
                        item_stat = item.stat(follow_symlinks=False)
                        attributes = getattr(item_stat, 'st_file_attributes', 0)
                        if item.is_symlink() or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                            skipped_reparse += 1
                        elif stat.S_ISDIR(item_stat.st_mode):
                            pending.append(item.path)
                        elif stat.S_ISREG(item_stat.st_mode):
                            # NTFS can return lone UTF-16 code units; SQLite TEXT cannot store them.
                            name = item.path.encode('utf-8', errors='surrogatepass')
                            batch.append((name.lower(), name))
                    except OSError as exc:
                        errors.append({'path': safe_location(item.path), 'errno': exc.errno})
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    output = args.out.resolve()
    if output.drive.upper() != 'S:' or not args.command or args.command[0] != '--' or len(args.command) < 2:
        parser.error('provide a fresh S: directory and -- COMMAND')
    output.mkdir(parents=True)
    temp = output / 'temp'
    temp.mkdir()
    env = dict(os.environ, TEMP=str(temp), TMP=str(temp), TMPDIR=str(temp),
               PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
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
        files, excluded = [], []
        for path in new_files(db):
            group = excluded if path.lower().startswith(OS_PREFIXES) else files
            group.append(safe_location(path))
        counts = {name: db.execute(f'SELECT count(*) FROM {name}').fetchone()[0]
                  for name in ('before', 'after')}
        reparse = (dict(db.execute('SELECT * FROM reparse')) if db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='reparse'").fetchone() else {})
    report = {'times_utc': clock, 'pytest_exit': exit_code, 'files_inventoried': counts,
              'new_files': files, 'excluded_os_files': excluded, 'os_rules': OS_PREFIXES,
              'enumeration_errors': errors, 'reparse_entries_not_followed': reparse,
              'test_output_pattern_matches': matches}
    (output / 'drive-diff.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    phase('finished')
    print(f'pytest exit={exit_code}, new files={len(files)}, OS exclusions={len(excluded)}, '
          f'enumeration errors={sum(map(len, errors.values()))}, redacted test lines={len(matches)}')
    return bool(exit_code or files or matches or any(errors.values()))


if __name__ == '__main__':
    raise SystemExit(main())
