"""A02-only owned lifecycle around the existing author fixture and Edge checker.

Each viewport gets a fresh in-memory fixture. Normal nonce shutdown is attempted
once; no terminate/kill fallback or connection to an existing browser is used.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ''):
    sys.path.insert(0, str(ROOT))

from tools.check_library_calendar_slider_owned import (  # noqa: E402
    OwnedTree, deny_external_connections, helper_hashes as calendar_helper_hashes,
    owned_output, write_new,
)
from tools.run_browser_checks import (  # noqa: E402
    child_environment, free_port, port_released, source_hashes, wait_for_health,
)

HOST = '127.0.0.1'
SCOPE = 'A02 cached author revisit only'
OWNER_FILE = 'author-run-owner.json'
VIEWPORTS = ((1280, 720), (1920, 1080))


def helper_hashes():
    result = calendar_helper_hashes()
    for name in (Path(__file__).name, 'serve_viewer_author_fixture.py',
                 'check_viewer_zoom_pan_browser.py'):
        result[f'tools/{name}'] = hashlib.sha256((ROOT / 'tools' / name).read_bytes()).hexdigest()
    return result


def serve(port, output, nonce):
    output = owned_output(output)
    owner = json.loads((output / OWNER_FILE).read_text(encoding='utf-8'))
    if (owner.get('scope') != SCOPE or not secrets.compare_digest(
            owner.get('nonce_sha256', ''), hashlib.sha256(nonce.encode()).hexdigest())):
        raise ValueError('author fixture ownership mismatch')
    network = deny_external_connections()
    from fastapi import Header, HTTPException
    from tools import serve_viewer_author_fixture as fixture

    def install(app, registry, reader):
        if type(reader) is not fixture.FixtureReader or reader.author_count != 100:
            raise RuntimeError('expected the in-memory 100-work author fixture')
        write_new(output / 'fixture-images.json', reader.image_fixture_manifest)
        stopping = False

        def auth(value):
            if not secrets.compare_digest(value or '', nonce):
                raise HTTPException(status_code=403)

        @app.get('/healthz')
        def health():
            return {'status': 'ok', 'mode': 'demo', 'data': 'synthetic'}

        @app.get('/_author_fixture/owner', include_in_schema=False)
        def identity(x_acceptance: str | None = Header(default=None)):
            auth(x_acceptance)
            return {'pid': os.getpid(), 'data': 'synthetic',
                    'network_reader': False, 'author_count': reader.author_count}

        @app.post('/_author_fixture/shutdown', include_in_schema=False)
        async def shutdown(x_acceptance: str | None = Header(default=None)):
            nonlocal stopping
            auth(x_acceptance)
            if stopping:
                raise HTTPException(status_code=409)
            stopping = True
            app.shutdown()
            return {'stopping': True}

        async def close_registry():
            completed, close_error = False, None
            try:
                await asyncio.to_thread(registry.close)
                completed = True
            except BaseException as exc:
                close_error = type(exc).__name__
                raise
            finally:
                write_new(output / 'fixture-image-events.json', reader.image_observation())
                write_new(output / 'server-lifecycle.json', {
                    'pid': os.getpid(), 'data': 'synthetic',
                    'shutdown_requested': stopping, 'registry_close_completed': completed,
                    'close_error': close_error, 'denied_external_connections': network['denied'],
                })

        app.on_shutdown(close_registry)

    sys.argv = [str(ROOT / 'tools' / 'serve_viewer_author_fixture.py'),
                '--port', str(port), '--author-count', '100']
    fixture.main(acceptance_hooks=install)
    return 0


def run_viewport(output, port, size, timeout, native):
    output.mkdir()
    temporary = output / 'tmp'
    temporary.mkdir()
    nonce = secrets.token_urlsafe(32)
    write_new(output / OWNER_FILE, {
        'runner_pid': os.getpid(), 'scope': SCOPE,
        'nonce_sha256': hashlib.sha256(nonce.encode()).hexdigest(),
    })
    env = child_environment()
    env.update(PYTHONDONTWRITEBYTECODE='1', TEMP=str(temporary), TMP=str(temporary))
    url = f'http://{HOST}:{port}'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    processes, trees, reports = {}, {}, {}
    failures, browser_result, lifecycle = [], None, None
    endpoint_owner = None
    shutdown = {'attempted': False, 'acknowledged': False}

    def fail(label):
        if label not in failures:
            failures.append(label)

    def request(path, *, post=False):
        req = urllib.request.Request(url + path, method='POST' if post else 'GET',
                                     headers={'X-Acceptance': nonce}, data=b'' if post else None)
        with opener.open(req, timeout=5) as response:
            if response.status != 200:
                raise RuntimeError('non-200 fixture endpoint')
            return json.loads(response.read(65536))

    with (output / 'server.log').open('xb') as server_log, (output / 'checker.log').open('xb') as checker_log:
        try:
            processes['server'] = subprocess.Popen(
                [sys.executable, '-B', str(Path(__file__).resolve()),
                 '--serve', str(port), '--nonce', nonce, '--output', str(output)],
                cwd=str(output), env=env, stdin=subprocess.DEVNULL,
                stdout=server_log, stderr=subprocess.STDOUT)
            trees['server'] = OwnedTree(processes['server'], native)
            wait_for_health(url, processes['server'], expected_data='synthetic')
            identity = request('/_author_fixture/owner')
            if (type(identity) is not dict or set(identity) != {
                    'pid', 'data', 'network_reader', 'author_count'}
                    or identity['data'] != 'synthetic' or identity['network_reader'] is not False
                    or identity['author_count'] != 100):
                raise RuntimeError('unexpected author fixture identity')
            endpoint_owner = trees['server'].observed_endpoint(identity['pid'])
            if endpoint_owner is None:
                raise RuntimeError('endpoint PID/birth is not inside the owned tree')
            processes['checker'] = subprocess.Popen(
                [sys.executable, '-B', str(ROOT / 'tools' / 'check_viewer_zoom_pan_browser.py'),
                 '--author-revisit', '--url', url, '--output', str(output / 'ui'),
                 '--width', str(size[0]), '--height', str(size[1])],
                cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
                stdout=checker_log, stderr=subprocess.STDOUT)
            trees['checker'] = OwnedTree(processes['checker'], native)
            deadline = time.monotonic() + timeout
            while processes['checker'].poll() is None and time.monotonic() < deadline:
                if processes['server'].poll() is not None:
                    fail('server_exited_during_browser_operations')
                    break
                time.sleep(0.1)
            if processes['checker'].poll() is None:
                fail('browser_checker_not_finished')
            elif processes['checker'].returncode != 0:
                fail('browser_checker_failed')
            result_path = output / 'ui' / 'result.json'
            if result_path.is_file() and processes['checker'].poll() is not None:
                browser_result = json.loads(result_path.read_text(encoding='utf-8'))
            if not (isinstance(browser_result, dict)
                    and browser_result.get('success') is True and browser_result.get('scope') == SCOPE
                    and browser_result.get('viewport') == list(size)
                    and browser_result.get('author_revisit', {}).get('expected_author_count') == 100
                    and not browser_result.get('console_errors')
                    and not browser_result.get('page_errors')
                    and not browser_result.get('external_requests')):
                fail('author_revisit_operations_not_accepted')
        except Exception as exc:
            fail(type(exc).__name__ + ': runner_failed')
        finally:
            tree = trees.get('server')
            if tree is not None:
                try:
                    if endpoint_owner is not None and tree.observed_endpoint(endpoint_owner['pid']) == endpoint_owner:
                        shutdown['attempted'] = True
                        shutdown['endpoint_owner_verified'] = True
                        shutdown['acknowledged'] = request('/_author_fixture/shutdown', post=True) == {'stopping': True}
                    else:
                        fail('server_endpoint_owner_missing_before_normal_shutdown')
                except Exception as exc:
                    fail(type(exc).__name__ + ': normal_shutdown_unconfirmed')
            # Never use Popen context-manager exit or subprocess.run timeout;
            # neither the checker nor the fixture has a forced-stop fallback.
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if all(process.poll() is not None for process in processes.values()):
                    for tree in trees.values():
                        tree.sample()
                    if all(tree.last_living == [] for tree in trees.values()):
                        break
                time.sleep(0.1)
            reports = {name: tree.finish() for name, tree in trees.items()}
            if set(reports) != {'server', 'checker'}:
                fail('both_owned_roots_not_observed')
            for name, report in reports.items():
                if (report['exit_code'] != 0 or report['living'] != []
                        or report['errors'] or not report['observed']):
                    fail(name + '_owned_exit_not_proven')
            if not shutdown['attempted'] or not shutdown['acknowledged']:
                fail('normal_shutdown_not_acknowledged')

    try:
        released = port_released(port)
    except Exception as exc:
        released = False
        fail(type(exc).__name__ + ': port_observation_failed')
    if not released:
        fail('app_port_not_released')
    try:
        lifecycle = json.loads((output / 'server-lifecycle.json').read_text(encoding='utf-8'))
    except Exception as exc:
        fail(type(exc).__name__ + ': registry_close_evidence_invalid')
    if not (isinstance(lifecycle, dict) and endpoint_owner is not None
            and lifecycle.get('pid') == endpoint_owner['pid'] and lifecycle.get('data') == 'synthetic'
            and lifecycle.get('shutdown_requested') is True
            and lifecycle.get('registry_close_completed') is True and lifecycle.get('close_error') is None
            and lifecycle.get('denied_external_connections') == 0):
        fail('registry_close_or_network_gate_failed')
    manifest = image_reads = None
    target_trace = (browser_result or {}).get('target_image_network')
    try:
        manifest = json.loads((output / 'fixture-images.json').read_text(encoding='utf-8'))
        image_reads = json.loads((output / 'fixture-image-events.json').read_text(encoding='utf-8'))
        entries = manifest['entries']
        expected = {(row['work_id'], row['page_no']): row for row in entries}
        assert manifest['schema'] == 'a02-prepared-raw-v1' and manifest['count'] == len(entries) == 103
        assert set(expected) == {(str(number), 0) for number in range(501, 604)}
        assert all(row['variant'] == 'view' and row['bytes'] > 0 for row in entries)
        assert manifest['bytes'] == sum(row['bytes'] for row in entries) <= manifest['limit_bytes'] == 32 * 1024 * 1024
        assert manifest['aggregate_sha256'] == hashlib.sha256(json.dumps(
            entries, sort_keys=True, separators=(',', ':')).encode('ascii')).hexdigest()
        assert image_reads['dropped'] == image_reads['failed'] == image_reads['active'] == 0
        assert image_reads['started'] == image_reads['finished'] > 0
        assert sum(event['event'] == 'end' for event in image_reads['events']) == image_reads['finished']
        for event in image_reads['events']:
            if event['event'] == 'end':
                row = expected[(event['work_id'], event['page_no'])]
                assert event['source'] == 'prepared' and event['error_class'] is None
                assert event['bytes'] == row['bytes'] and event['sha256'] == row['sha256']
        assert isinstance(target_trace, dict) and target_trace['dropped'] == 0
    except Exception as exc:
        fail(type(exc).__name__ + ': prepared_fixture_or_observation_unverified')
    result = {
        'fixture_images': manifest, 'fixture_image_reads': image_reads,
        'target_image_network': target_trace, 'cold_performance_claim': False,
        'success': not failures, 'viewport': list(size), 'scope': SCOPE, 'port': port,
        'browser_result': 'ui/result.json', 'normal_shutdown': shutdown, 'processes': reports,
        'endpoint_owner': endpoint_owner, 'server_lifecycle': lifecycle,
        'port_released': released, 'forced_cleanup': 0, 'failures': failures,
    }
    write_new(output / 'owned-result.json', result)
    return result


def run(output, timeout):
    from tools import check_native_window as native

    output = owned_output(output)
    base = (ROOT / 'artifacts' / 'p3-318-author-revisit').resolve()
    if output == base or not output.is_relative_to(base):
        raise ValueError('output must be a fresh run below artifacts/p3-318-author-revisit')
    output.mkdir(parents=True, exist_ok=False)
    before, helpers_before = source_hashes(), helper_hashes()
    port, runs, failures = free_port(), [], []
    try:
        for size in VIEWPORTS:
            result = run_viewport(output / f'{size[0]}x{size[1]}', port, size, timeout, native)
            runs.append(result)
            if not result['success']:
                failures.append('viewport_failed; remaining_operations_not_started')
                break
    except Exception as exc:
        failures.append(type(exc).__name__ + ': runner_failed')
    try:
        after, helpers_after = source_hashes(), helper_hashes()
    except Exception as exc:
        after, helpers_after = {}, {}
        failures.append(type(exc).__name__ + ': final_source_observation_failed')
    if before != after or helpers_before != helpers_after:
        failures.append('source_or_checker_changed')
    success = not failures and len(runs) == 2 and all(run['success'] for run in runs)
    result = {
        'success': success, 'scope': SCOPE, 'data': 'synthetic', 'channel': 'msedge',
        'expected_viewports': [list(size) for size in VIEWPORTS], 'runs': runs,
        'source_before': before, 'source_after': after, 'source_changed': before != after,
        'helpers_before': helpers_before, 'helpers_after': helpers_after,
        'helpers_changed': helpers_before != helpers_after, 'forced_cleanup': 0, 'failures': failures,
    }
    write_new(output / 'result.json', result)
    print(json.dumps({'success': success, 'failures': failures,
                      'evidence': str(output / 'result.json')}, ensure_ascii=False))
    return 0 if success else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=180)
    parser.add_argument('--serve', type=int)
    parser.add_argument('--nonce')
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('Windows only: owned PID/birth observations are required')
    if not 1 <= args.timeout <= 600:
        parser.error('--timeout must be 1..600 seconds')
    if args.serve is not None:
        if not args.nonce or not 1024 <= args.serve <= 65535 or args.serve == 8089:
            parser.error('fixture child requires its nonce and a dedicated loopback port')
        return serve(args.serve, args.output, args.nonce)
    if args.nonce:
        parser.error('--nonce is reserved for the owned fixture child')
    return run(args.output, args.timeout)


if __name__ == '__main__':
    raise SystemExit(main())
