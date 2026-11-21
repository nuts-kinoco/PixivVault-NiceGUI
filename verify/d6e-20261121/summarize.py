"""Summarize the recorded D6 trials without running either application."""

import argparse
from collections import Counter
from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3
import statistics

from PIL import Image, ImageChops


ROOT = Path(__file__).resolve().parent


def read(folder, name):
    return json.loads((folder / name).read_text(encoding='utf-8'))


def distribution(values):
    values = sorted(values)
    return {'n': len(values),
            'median_ms': round(statistics.median(values), 3) if values else None,
            'p95_ms': round(values[math.ceil(len(values) * .95) - 1], 3) if values else None}


def viewer(folder):
    def trials(kind):
        return read(folder, f'nice-{kind}.json')['viewer_next']

    jobs = trials('update-jobs')
    sockets = trials('socket-segments')
    return {
        'whole': distribution(trials('samples')),
        'page_label': distribution(row['page_label_ms'] for row in trials('milestones')),
        'update_job': distribution(job['end'] - job['start'] for trial in jobs for job in trial),
        'updates_per_trial': dict(Counter(map(len, jobs))),
        'socket_kinds': dict(Counter(item['kind'] for trial in sockets for item in trial)),
        'task_duration': distribution(row['TaskDuration'] for row in trials('main-thread')),
        'style_duration': distribution(row['RecalcStyleDuration'] for row in trials('main-thread')),
        'dom': distribution(row['elapsed'] for row in trials('dom-segments') if row is not None),
    }


def search(folder):
    job = read(folder, 'nice-update-jobs.json')['library_search']
    server = read(folder, 'nice-server-segments.json')
    events = read(folder, 'nice-dom-segments.json')['library_search']
    timers = server.get('LibraryView._resume_search_capacity.wall_start', [])
    query_starts = server.get('LibraryView.search.wall_start', [])
    matched = 0
    intervals = []
    for trial in events:
        if trial is None:
            continue
        boundary = next((t for t in query_starts if trial['start'] <= t <= trial['changed']), None)
        if boundary is None:
            continue
        intervals.append((trial['start'], boundary))
        matched += any(trial['start'] <= t <= boundary for t in timers)
    return {
        'whole': distribution(read(folder, 'nice-samples.json')['library_search']),
        'observed_debounce_windows': len(intervals),
        'capacity_resumes_in_debounce': matched,
        'updates_per_trial': dict(Counter(map(len, job))),
        'first_ms': read(folder, 'nice-samples.json')['library_search'][0],
    }


def server_network(folder, side):
    filename = 'original/network-count.json' if side == 'original' else 'nice-server-network.json'
    return read(folder, filename)


def paired(folder):
    fixture = read(folder, 'fixture.json')
    payload = {'fixture': fixture, 'errors': read(folder, 'browser-result.json')}
    for side, ledger in (('original', folder / 'original/fixture.sqlite'),
                         ('nice', folder / 'learning-ledger/learning.sqlite3')):
        with closing(sqlite3.connect(ledger.as_uri() + '?mode=ro', uri=True)) as connection:
            judgments = connection.execute('SELECT count(*) FROM discovery_swipe_history').fetchone()[0]
        captures = read(folder, f'{side}-screen-capture-segments.json')
        segments = read(folder, f'{side}-segments.json')
        payload[side] = dict(
            readings=read(folder, f'{side}-summary.json'),
            screenshot={case: distribution(map(sum, rows)) for case, rows in captures.items()},
            paint={case: distribution(row['paint_ms'] for row in rows) for case, rows in segments.items()},
            ready={case: distribution(row['ready_ms'] for row in rows) for case, rows in segments.items()},
            judgments=judgments,
            server_network=server_network(folder, side),
            browser_network=read(folder, f'{side}-browser-network.json'),
            page_errors=read(folder, f'{side}-page-errors.json'),
        )
    return payload


def paired_search(folder):
    return {
        'fixture': read(folder, 'fixture.json'),
        'errors': read(folder, 'browser-result.json'),
        'original': read(folder, 'original-summary.json').get('library_search'),
        'nice': read(folder, 'nice-summary.json').get('library_search'),
        'server_network': {side: server_network(folder, side) for side in ('original', 'nice')},
        'browser_network': {side: read(folder, f'{side}-browser-network.json') for side in ('original', 'nice')},
    }


def layouts(before, after):
    results = {}
    for dimensions in ('1280x800', '1920x1080'):
        filename = f'viewer-{dimensions}.png'
        with Image.open(before / filename) as left, Image.open(after / filename) as right:
            diff = ImageChops.difference(left.convert('RGB'), right.convert('RGB'))
        results[dimensions] = {
            'changed_bbox': diff.getbbox(),
            'changed_pixels': sum(pixel != (0, 0, 0) for pixel in diff.get_flattened_data()),
        }
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', default='final')
    args = parser.parse_args()
    output = {
        'viewer': {key: viewer(ROOT / key) for key in (
            'diag-baseline', 'diag-no-tailwind', 'diag-empty-authors', f'viewer-segments-{args.tag}')},
        'search': {key: search(ROOT / key) for key in (
            'search-before-50-to-1', 'search-after-50-to-1', 'search-before-1-to-1', 'search-after-1-to-1',
            'search-before-1-to-50-retry', 'search-after-1-to-50')},
        'paired': {mode: paired(ROOT / f'paired-{mode}-{args.tag}') for mode in ('included', 'detached')},
        'paired_search_retries': {
            mode: paired_search(ROOT / f'paired-search-{mode}') for mode in ('included', 'detached')
            if (ROOT / f'paired-search-{mode}' / 'browser-result.json').exists()
        },
        'viewer_contract': read(ROOT / f'viewer-contract-{args.tag}', 'viewer-contract.json'),
        'viewer_layouts': layouts(ROOT / 'viewer-contract-before-r2', ROOT / f'viewer-contract-{args.tag}'),
    }
    (ROOT / f'evidence-{args.tag}.json').write_text(json.dumps(output, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
