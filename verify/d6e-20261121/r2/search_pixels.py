"""Measure one visible author replaced by another, with a screenshot on each trial."""

import argparse
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import measure


def search(page, base, name, repetitions, _cases):
    from PIL import Image
    from io import BytesIO

    assert name == 'nice'
    detached = os.environ['PV_D6_PIXELS'] == 'detached'
    page.set_default_timeout(9000)
    page.get_by_test_id('tab-library').click()
    field = page.locator('input[data-testid=library-search]:visible, [data-testid=library-search] input:visible').first
    cards = page.locator('[data-testid=library-authors-scroll] [data-testid=library-author-open]:visible')

    def result(label):
        try:
            page.wait_for_function('''label => {
                const cards = [...document.querySelectorAll(
                    '[data-testid=library-authors-scroll] [data-testid=library-author-open]')]
                    .filter(e => e.getClientRects().length && getComputedStyle(e).visibility === 'visible');
                return cards.length === 1 && cards[0].getAttribute('aria-label') === label;
            }''', arg=label)
        except Exception:
            state = page.evaluate('''() => ({
                connected: window.socket?.connected, handshake: window.did_handshake,
                cards: [...document.querySelectorAll(
                    '[data-testid=library-authors-scroll] [data-testid=library-author-open]')].length,
            })''')
            (base / 'search-failure.json').write_text(json.dumps({'expected': label, **state}), encoding='utf8')
            raise

    def paint():
        page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')

    page.wait_for_function('''() => document.querySelectorAll(
        '[data-testid=library-authors-scroll] [data-testid=library-author-open]').length === 50''')
    field.fill('Bench Author 199')
    result('Bench Author 199')
    paint()
    samples = []
    for index in range(repetitions):
        label = f'Bench Author {150 + index:03d}'
        started = time.perf_counter_ns()
        field.fill(label)
        result(label)
        paint()
        painted = time.perf_counter_ns()
        screenshot = page.screenshot()
        captured = time.perf_counter_ns()
        assert cards.count() == 1 and cards.first.get_attribute('aria-label') == label
        with Image.open(BytesIO(screenshot)) as image:
            image.verify()
        if index in (0, repetitions - 1):
            (base / f'search-{index + 1:02d}.png').write_bytes(screenshot)
        samples.append({'total_ms': ((painted if detached else captured) - started) / 1e6,
                        'painted_ms': (painted - started) / 1e6,
                        'screenshot_ms': (captured - painted) / 1e6})
    timings = sorted(row['total_ms'] for row in samples)
    output = {'revision': os.environ['PV_D6_CODE_REV'], 'pixel_timing': os.environ['PV_D6_PIXELS'],
              'n': len(samples), 'median_ms': statistics.median(timings),
              'p95_ms': timings[math.ceil(len(timings) * .95) - 1], 'samples': samples}
    (base / 'search-pixels.json').write_text(json.dumps(output, indent=2), encoding='utf8')
    return {}


def run(base, repetitions):
    base.mkdir(parents=True)
    env = measure.isolated_environment(base)
    measure.seed(base)
    measure.measure = search
    command = [sys.executable, '-B', measure.__file__, '--out', str(base), '--serve-nice']
    with measure.server(base, 'nice', command, measure.ROOT, env, measure.NICE_PORT) as url:
        measure.browser_run(base, url, 'nice', env, False, repetitions, ('library_search',))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--repetitions', type=int, default=20, choices=range(20, 31))
    args = parser.parse_args()
    run(args.out.resolve(), args.repetitions)
