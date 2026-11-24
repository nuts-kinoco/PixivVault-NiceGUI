"""Run the product UI against generated CoS data on one fixed loopback port."""
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src'), str(ROOT / 'tests')]
TEMP_ROOT = Path(r'S:\Work\_tmp\PV\cosprep-20261124')
PORTS = range(22701, 22710)


def fixture_paths(base):
    from pixivvault_nicegui.managed_network_stop_gate import _reject_links

    _reject_links(base)
    if not base.is_absolute() or base != base.resolve() or base.parent.parent != TEMP_ROOT:
        raise ValueError('Expected an owned S: fixture directory')
    if base.name not in {str(port) for port in PORTS}:
        raise ValueError('Unexpected fixture port directory')
    if Path.cwd() != base or Path(os.environ['TEMP']) != base / 'tmp':
        raise ValueError('Fixture workdir and temp must be isolated')


def seed(base, port, p5):
    comparison = base / 'seed-source'
    comparison.mkdir()
    shape = {22704: 'empty', 22705: 'long'}.get(port, 'normal')
    p5.seed_profile(base, comparison, shape, tone_mode=port == 22708)
    if port != 22708:
        return
    from PIL import Image

    images = base / 'lib' / 'fake-images'
    with sqlite3.connect(base / 'lib' / 'synthetic-legacy.sqlite') as db:
        for work_id in range(110, 118):
            author = str(22 + work_id % 4)
            title = f'Synthetic history {work_id}'
            when = f'2026-10-{work_id - 100:02}T01:00:00'
            db.execute(
                "INSERT INTO works (work_id,title,page_count,tags,user_id,is_deleted,last_backup,"
                "content_type,work_meta) VALUES (?,?,1,'[]',?,0,?,'illust','{}')",
                (str(work_id), title, author, when),
            )
            folder = images / f'Synthetic backup {author}({author})'
            folder.mkdir(exist_ok=True)
            Image.new('RGB', (64, 48), (work_id, 42, 90)).save(folder / f'{title}({work_id})_1.png')


def install_fixture(base, port, stack):
    from tools import p5_design_evidence as p5, ui_smoke_browser as smoke
    from tools import ui_tone_evidence, uiux_fixture
    from pixivvault_nicegui import bootstrap
    from pixivvault_nicegui.managed_network_stop_shared_scope import _INFLIGHT_BYTES
    from pixivvault_nicegui.preferences import PreferencesStore
    from pixivvault_nicegui.ui.preferences_view import PreferencesView

    seed(base, port, p5)
    original_reader = smoke.make_stopped_reader
    original_register = bootstrap.register_pages

    class Fixture(p5.P5Fixture):
        def get_ranking(self, *args, **kwargs):
            if port in (22701, 22702, 22703):
                # Reuse the real ranking GET through the shared owner and generated transport.
                return self.reader.get_ranking(*args, **kwargs)
            return super().get_ranking(*args, **kwargs)

        def personal_search(self, *args, **kwargs):
            if port == 22702:
                from pixivvault_nicegui.external_vault import VaultError
                raise VaultError('pixiv-outcome-uncertain')
            return super().personal_search(*args, **kwargs)

    fixture = Fixture(base)
    stack.enter_context(patch.object(p5, 'P5Fixture', lambda _base: fixture))

    def reader(base, fake, **_kwargs):
        actual, scope = original_reader(
            base, fake, stop_status=429 if port == 22701 else None,
            transport_body=b'bad synthetic response' if port == 22703 else b'{}',
        )
        if port == 22702:
            scope.gate.inflight_path.write_bytes(_INFLIGHT_BYTES)
        fixture.reader = actual
        return actual, scope

    def register(**kwargs):
        kwargs['startup_tab'] = {22701: 'ranking', 22702: 'personal', 22703: 'ranking',
                                 22704: 'failed', 22705: 'failed', 22706: 'download',
                                 22707: 'download', 22708: 'download', 22709: 'queue'}[port]
        prefs = PreferencesStore(kwargs['preferences_root'] / 'preferences.sqlite3')
        try:
            prefs.set_setting('meta_backfill_enabled', '0')
        finally:
            prefs.close()
        original_register(**kwargs)
        if port in (22701, 22702):
            ui_tone_evidence.register_recovery_pages(base)

    stack.enter_context(patch.object(smoke, 'make_stopped_reader', reader))
    stack.enter_context(patch.object(bootstrap, 'register_pages', register))
    if port == 22707:
        save = PreferencesView.save

        def fail_store(view):
            with patch.object(view.store, 'update', side_effect=OSError('synthetic write failure')):
                return save(view)

        stack.enter_context(patch.object(PreferencesView, 'save', fail_store))
    if port in (22702, 22703, 22704, 22705, 22708, 22709):
        os.environ['PV_UI_TONE_PERSONAL_FIXTURE'] = '1'
    if port == 22706:
        os.environ['PV_UIUX_STATE'] = 'error'
        return lambda nonce: uiux_fixture.serve_port(port, nonce)
    configure = {22708: configure_history, 22709: lambda services: configure_daily(services, fixture)}.get(port)
    return lambda nonce: p5.serve_port(port, nonce, fixed_queue=port != 22709, configure_services=configure)


def configure_history(services):
    from tools.p5_design_evidence import LEARNING_FIXTURE_URL

    discovery = services.discovery

    def seed_history():
        db = discovery._database()
        for index in range(110, 118):
            entry = dict(work_id=str(index), title=f'Synthetic history {index}', author_id=str(22 + index % 4),
                         author_name='Synthetic history author', tags=['a'], source='following',
                         thumbnail_url=LEARNING_FIXTURE_URL)
            with db._transaction() as conn:
                conn.execute(
                    'INSERT OR IGNORE INTO managed_discovery_candidate VALUES (?, ?)',
                    (entry['work_id'], json.dumps(entry)),
                )
            discovery._accept_metadata(entry)
            for _ in range(1 + index % 3):
                db.record_discovery_open(entry['work_id'], LEARNING_FIXTURE_URL)

    discovery._submit(seed_history).result(timeout=10)

def configure_daily(services, fixture):
    from tools.p5_design_evidence import RANKING_FIXTURE_URL

    class RecommendationFixture:
        def search(self, tag, page, mode):
            rows = [dict(id='501', title='Synthetic recommendation', user_id='2001',
                         user_name='Synthetic author', page_count=1, illust_type=0,
                         tags=[tag], thumbnail_url=RANKING_FIXTURE_URL)]
            return dict(works=rows if page == 1 else [], total=len(rows))

        def author(self, author_id, limit, excluded_ids):
            return ()

        def detail(self, work_id):
            return None

    controller = services.recommendations
    controller.acquisition.source = RecommendationFixture()
    services.live_client.get_image_bytes = fixture.get_image_bytes
    revision, _, _ = controller.preferences.snapshot()
    controller.preferences.store_acquisition(
        dict(cache={}, boosts={}, added=[dict(tag='synthetic', display='Synthetic tag')]), revision,
    )
    configure_queue(services)

def configure_queue(services):
    """Seed real job state and use a local-only runner for queue controls."""
    from time import sleep

    def run(job, context):
        if job.target_id == '402':
            raise OSError('synthetic job failure')
        total = 30 if job.target_id == '404' else 1
        for step in range(1, total + 1):
            context.checkpoint()
            if total > 1:
                sleep(0.6)
            context.progress(step, total)
        return 'synthetic-queue-receipt'

    jobs = services.static_jobs
    jobs._runner = run
    jobs.enqueue('pixiv-illust', '401', 'Synthetic completed job')
    jobs.enqueue('pixiv-illust', '402', 'Synthetic failing job')
    paused = jobs.enqueue('pixiv-illust', '403', 'Synthetic paused job')
    jobs.control(paused.job_id, 'pause')
    running = jobs.enqueue('pixiv-illust', '404', 'Synthetic resumable job')
    jobs.control(running.job_id, 'pause')


def forbid_side_effects(event, args):
    if event in {'subprocess.Popen', 'os.startfile', 'os.startfile/2', 'os.system'}:
        raise PermissionError('CoS fixtures cannot launch external processes')

def main():
    from contextlib import ExitStack
    from pixivvault_nicegui.test_owner_runtime import isolated_test_workspace
    from tools import check_library_calendar_slider_owned as boundary

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('port', type=int, choices=PORTS)
    parser.add_argument('base', type=Path)
    args = parser.parse_args()
    fixture_paths(args.base)
    if args.base.name != str(args.port):
        parser.error('Port and fixture directory disagree')
    from p6_c_write_audit import write_destinations

    def audit(event, values):
        forbid_side_effects(event, values)
        for path in write_destinations(event, values):
            if isinstance(path, (str, bytes, os.PathLike)):
                # Owned stores use extended-length Windows paths for writes.
                canonical = os.path.realpath(os.fsdecode(path))
                if canonical.startswith('\\\\?\\') and canonical[5:7] == ':\\':
                    canonical = canonical[4:]
                destination = Path(canonical)
                if not destination.is_relative_to(args.base):
                    raise PermissionError('CoS fixture write escaped its workspace')

    sys.addaudithook(audit)
    network = boundary.deny_external_connections()
    with isolated_test_workspace(args.base), ExitStack() as stack:
        stack.enter_context(patch.object(boundary, 'deny_external_connections', lambda: network))
        serve = install_fixture(args.base, args.port, stack)
        serve((args.base / 'control.txt').read_text(encoding='ascii').strip())


if __name__ == '__main__':
    main()
