"""Capture the full-size synthetic library after the viewer and fonts settle."""

import argparse
from pathlib import Path
import sys

from reconnect_contract import measure, stable_screenshots


def capture(page, base, name, _repetitions, _cases):
    from playwright.sync_api import expect

    assert name == 'nice'
    page.get_by_test_id('tab-library').click()
    page.get_by_role('button', name='Bench Author 000', exact=True).click()
    page.locator('[data-work-id="100000"][data-page-no="0"]').first.click()
    viewer = page.get_by_test_id('library-viewer')
    expect(viewer).to_be_focused()
    image = viewer.get_by_test_id('library-viewer-image')
    expect(image).to_have_attribute('aria-busy', 'false')
    expect(image.locator('img[data-pv-displayed=true]')).to_be_visible()
    return stable_screenshots(page, base)


def run(base):
    base.mkdir(parents=True)
    env = measure.isolated_environment(base)
    measure.seed(base)
    measure.measure = capture
    command = [sys.executable, '-B', measure.__file__, '--out', str(base), '--serve-nice']
    with measure.server(base, 'nice', command, measure.ROOT, env, measure.NICE_PORT) as url:
        measure.browser_run(base, url, 'nice', env, False, 20, ())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    run(parser.parse_args().out.resolve())
