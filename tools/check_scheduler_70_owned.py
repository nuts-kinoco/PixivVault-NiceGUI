"""T01 only: owned Edge lifecycle for 70 authors with one work each.

The existing opt-in synthetic fixture and inspect function provide the product
operations. This wrapper owns one browser, two fresh contexts, and two normal
server starts sharing one managed root and port. It never forces a process exit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ''):
    sys.path.insert(0, str(ROOT))

from tools.check_author_revisit_owned import (  # noqa: E402
    OwnedTree, deny_external_connections, helper_hashes as author_helper_hashes,
    owned_output, write_new, child_environment, free_port, port_released,
    source_hashes, wait_for_health,
)

HOST = '127.0.0.1'
SCOPE = 'T01: 70 authors, one work each; functional evidence only'
OWNER_FILE = 'scheduler-70-run-owner.json'
PHASES = ('initial', 'restart')
VIEWPORT = {'width': 1920, 'height': 1080}


def helper_hashes():
    result = author_helper_hashes()
    for name in (Path(__file__).name, 'check_author_download_browser.py',
                 'check_managed_downloads_browser.py', 'check_live_bookmark_browser.py'):
        result[f'tools/{name}'] = hashlib.sha256((ROOT / 'tools' / name).read_bytes()).hexdigest()
    return result


def owned_run(output, port, nonce):
    output = owned_output(output)
    owner = json.loads((output / OWNER_FILE).read_text(encoding='utf-8'))
    if (owner.get('scope') != SCOPE or owner.get('port') != port
            or owner.get('managed_relative') != 'managed'
            or not secrets.compare_digest(
                owner.get('nonce_sha256', ''), hashlib.sha256(nonce.encode()).hexdigest())):
        raise ValueError('scheduler fixture ownership mismatch')
    return output


def serve(port, output, nonce, phase):
    output = owned_run(output, port, nonce)
    if phase not in PHASES:
        raise ValueError('unknown owned phase')
    destination = output / phase
    temporary = (destination / 'tmp').resolve()
    if Path(tempfile.gettempdir()).resolve() != temporary or not temporary.is_dir():
        raise ValueError('synthetic Cookie temporary storage is not owned by this phase')
    network = deny_external_connections()
    from fastapi import Header, HTTPException
    from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient
    from tools import check_managed_downloads_browser as fixture

    state = {
        'pid': os.getpid(), 'scope': SCOPE, 'data': 'synthetic', 'phase': phase,
        'owned_temporary_directory': True, 'server_returned': False,
        'server_error': None, 'hook_installed': False,
        'services_close': {'calls': 0, 'completed': 0, 'errors': []},
        'reader_close': {'calls': 0, 'completed': 0, 'errors': []},
    }
    owners = {}

    def observe_close(owner, key):
        original = owner.close
        observation = state[key]

        def close():
            observation['calls'] += 1
            try:
                result = original()
            except BaseException as error:
                observation['errors'].append(type(error).__name__)
                raise
            observation['completed'] += 1
            return result

        owner.close = close

    def install(app, services, client, injected, shutdown_state):
        expected = dict(fixture.SCHEDULER_70_PAIRS)
        if (type(client) is not LivePixivReadClient
                or type(injected) is not fixture.ManagedTransport
                or injected.scheduler_70_authors is not True
                or injected.scheduler_author_works != expected
                or len(expected) != 70 or len(set(expected.values())) != 70
                or client._transport is not injected or client._owned_transport is not None
                or services.live_client is not client or services.managed_downloads is None
                or Path(services.managed_downloads.root).resolve() != (output / 'managed').resolve()
                or services.discovery is not None or services.author_media is not None
                or services.novel_jobs is not None or services.ugoira is not None
                or services.pixiv_actions is not None):
            raise RuntimeError('expected the static-only synthetic 70-author service configuration')
        owners.update(services=services, client=client, injected=injected,
                      shutdown_state=shutdown_state)
        # Observe the existing bootstrap shutdown callback and fixture finally.
        # These wrappers do not initiate, repeat, or replace resource cleanup.
        observe_close(services, 'services_close')
        observe_close(client, 'reader_close')
        state['hook_installed'] = True

        @app.get('/_scheduler_70_fixture/owner', include_in_schema=False)
        def identity(x_acceptance: str | None = Header(default=None)):
            if not secrets.compare_digest(x_acceptance or '', nonce):
                raise HTTPException(status_code=403)
            return {
                'pid': os.getpid(), 'scope': SCOPE, 'data': 'synthetic',
                'fixture_transport': True, 'authors': 70, 'works': 70,
                'discovery_enabled': False, 'combined_media_enabled': False,
            }

    try:
        fixture.serve(port, output / 'managed', nonce, scheduler_70_authors=True,
                      acceptance_hooks=install)
        state['server_returned'] = True
    except BaseException as error:
        state['server_error'] = type(error).__name__
        raise
    finally:
        services, client, injected = (owners.get(name) for name in ('services', 'client', 'injected'))
        state['normal_shutdown'] = dict(owners.get('shutdown_state', {}))
        state['services_closed'] = services is not None and services.closed is True
        state['reader_closed'] = client is not None and client._closed is True
        state['reader_shutdown_unverified'] = None if client is None else client.shutdown_unverified
        state['credentials_cleared'] = (client is not None and client._verified_identity is None
                                       and client._cookie_user_id is None and not client._cookies)
        if injected is not None:
            state['transport'] = {
                'request_count': injected.request_count,
                'original_gets': dict(injected.original_gets),
                'scheduler_profile_gets': dict(injected.scheduler_profile_gets),
                'scheduler_metadata_gets': dict(injected.scheduler_metadata_gets),
                'all_requests_get': injected.all_requests_get,
                'allowed_https_hosts_only': injected.allowed_https_hosts_only,
                'image_requests_cookie_free': injected.image_requests_cookie_free,
            }
        state['synthetic_cookie_directories_remaining'] = sum(
            path.name.startswith('pv-synthetic-cookie-') for path in temporary.iterdir())
        state['denied_external_connections'] = network['denied']
        write_new(destination / 'server-lifecycle.json', state)
    return 0


def lifecycle_valid(value, endpoint_owner, phase):
    if not (isinstance(value, dict) and endpoint_owner is not None
            and value.get('pid') == endpoint_owner['pid'] and value.get('scope') == SCOPE
            and value.get('phase') == phase and value.get('data') == 'synthetic'
            and value.get('hook_installed') is True and value.get('server_returned') is True
            and value.get('server_error') is None and value.get('owned_temporary_directory') is True
            and value.get('normal_shutdown') == {'requests': 1, 'accepted': True}
            and value.get('services_closed') is True and value.get('reader_closed') is True
            and value.get('reader_shutdown_unverified') is False
            and value.get('credentials_cleared') is True
            and value.get('synthetic_cookie_directories_remaining') == 0
            and value.get('denied_external_connections') == 0):
        return False
    for key in ('services_close', 'reader_close'):
        observation = value.get(key, {})
        if (type(observation.get('calls')) is not int or observation['calls'] < 1
                or observation.get('completed') != observation['calls']
                or observation.get('errors') != []):
            return False
    transport = value.get('transport', {})
    if not all(transport.get(key) is True for key in (
            'all_requests_get', 'allowed_https_hosts_only', 'image_requests_cookie_free')):
        return False
    if phase == 'restart':
        return (transport.get('request_count') == 0 and transport.get('original_gets') == {}
                and transport.get('scheduler_profile_gets') == {}
                and transport.get('scheduler_metadata_gets') == {})
    from tools.check_managed_downloads_browser import SCHEDULER_70_PAIRS
    return transport.get('original_gets') == {wid: 2 for _, wid in SCHEDULER_70_PAIRS}


def run_phase(browser, output, port, nonce, phase, previous, native):
    from tools.check_author_download_browser import inspect_scheduler_70_authors

    destination = output / phase
    destination.mkdir()
    temporary, evidence = destination / 'tmp', destination / 'ui'
    temporary.mkdir()
    evidence.mkdir()
    env = child_environment()
    env.update(PYTHONDONTWRITEBYTECODE='1', TEMP=str(temporary), TMP=str(temporary))
    url = f'http://{HOST}:{port}'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    failures, console_errors, page_errors, external_requests = [], [], [], []
    context = process = tree = page = None
    inspection = lifecycle = process_report = endpoint_owner = None
    closed_context = False
    shutdown = {'attempted': False, 'acknowledged': False}
    record = {'success': False, 'scope': SCOPE, 'phase': phase, 'viewport': VIEWPORT,
              'port': port, 'inspection_file': f'{phase}/ui/scheduler-70-inspection.json'}

    def fail(label):
        if label not in failures:
            failures.append(label)

    def request(path, *, post=False):
        req = urllib.request.Request(url + path, method='POST' if post else 'GET',
                                     headers={'X-Acceptance': nonce}, data=b'' if post else None)
        with opener.open(req, timeout=5) as response:
            if response.status != 200:
                raise RuntimeError('fixture endpoint returned a non-200 status')
            return json.loads(response.read(65536))

    def guard(route):
        parsed = urlsplit(route.request.url)
        if (parsed.scheme in ('http', 'https') and parsed.scheme == 'http'
                and parsed.hostname == HOST and parsed.port == port
                and parsed.username is None and parsed.password is None):
            route.continue_()
        else:
            # Record no URL or request body, even though the fixture is synthetic.
            external_requests.append({'scheme': parsed.scheme, 'resource_type': route.request.resource_type})
            route.abort()

    if not port_released(port):
        raise RuntimeError('owned port is occupied before the next server start')
    with (destination / 'server.log').open('xb') as log:
        try:
            process = subprocess.Popen(
                [sys.executable, '-B', str(Path(__file__).resolve()),
                 '--serve', str(port), '--phase', phase, '--nonce', nonce,
                 '--output', str(output)],
                cwd=str(output), env=env, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT)
            tree = OwnedTree(process, native)
            wait_for_health(url, process, timeout=35, expected_data='pixiv-readonly')
            identity = request('/_scheduler_70_fixture/owner')
            expected = {
                'scope': SCOPE, 'data': 'synthetic', 'fixture_transport': True,
                'authors': 70, 'works': 70, 'discovery_enabled': False,
                'combined_media_enabled': False,
            }
            if (type(identity) is not dict or set(identity) != set(expected) | {'pid'}
                    or any(identity[key] != value for key, value in expected.items())):
                raise RuntimeError('unexpected scheduler fixture identity')
            endpoint_owner = tree.observed_endpoint(identity['pid'])
            if endpoint_owner is None:
                raise RuntimeError('endpoint PID/birth is not inside the owned server tree')
            context = browser.new_context(viewport=VIEWPORT, service_workers='block')
            context.route('**/*', guard)
            page = context.new_page()
            page.on('console', lambda message: console_errors.append(message.text)
                    if message.type == 'error' else None)
            page.on('pageerror', lambda error: page_errors.append(str(error)))
            inspection = inspect_scheduler_70_authors(
                page, url, {'X-Acceptance': nonce}, output / 'managed',
                restart=phase == 'restart', evidence_dir=evidence, previous=previous,
                result_record=record)
            if not (inspection.get('success') is True and inspection.get('scope') == SCOPE
                    and inspection.get('restart') is (phase == 'restart')):
                fail('70_author_product_operations_not_accepted')
        except Exception as error:
            record['failure_location'] = [
                {'file': Path(frame.filename).name, 'line': frame.lineno, 'function': frame.name}
                for frame in traceback.extract_tb(error.__traceback__)[-8:]]
            if page is not None:
                try:
                    page.screenshot(path=str(evidence / 'failure.png'))
                except Exception as capture_error:
                    record['failure_capture_error'] = type(capture_error).__name__
            fail(type(error).__name__ + ': phase_failed')
        finally:
            if context is not None:
                try:
                    context.close()
                    closed_context = True
                except Exception as error:
                    fail(type(error).__name__ + ': browser_context_close_unconfirmed')
            if tree is not None:
                try:
                    if (endpoint_owner is not None
                            and tree.observed_endpoint(endpoint_owner['pid']) == endpoint_owner):
                        shutdown['attempted'] = True
                        shutdown['endpoint_owner_verified'] = True
                        shutdown['acknowledged'] = request('/_managed_test/shutdown', post=True) == {'stopping': True}
                    else:
                        fail('owned_endpoint_missing_before_normal_shutdown')
                except Exception as error:
                    fail(type(error).__name__ + ': normal_shutdown_unconfirmed')
                # Only observe normal draining. No terminate, kill, retry, or
                # Popen context-manager cleanup is allowed on a failed run.
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    tree.sample()
                    if process.poll() is not None and tree.last_living == []:
                        break
                    time.sleep(0.1)
                process_report = tree.finish()
                if (process_report['exit_code'] != 0 or process_report['living'] != []
                        or process_report['errors'] or not process_report['observed']):
                    fail('server_owned_exit_not_proven')
            else:
                fail('server_owned_tree_not_created')
    if not closed_context or len(browser.contexts) != 0:
        fail('fresh_browser_context_close_not_proven')
    if not shutdown['attempted'] or not shutdown['acknowledged']:
        fail('normal_shutdown_not_acknowledged')
    try:
        released = port_released(port)
    except Exception as error:
        released = False
        fail(type(error).__name__ + ': port_observation_failed')
    if not released:
        fail('app_port_not_released')
    try:
        lifecycle = json.loads((destination / 'server-lifecycle.json').read_text(encoding='utf-8'))
    except Exception as error:
        fail(type(error).__name__ + ': lifecycle_evidence_invalid')
    if not lifecycle_valid(lifecycle, endpoint_owner, phase):
        fail('service_reader_close_or_fixture_gate_failed')
    if console_errors or page_errors or external_requests:
        fail('browser_error_or_external_request')
    record.update(
        success=not failures, normal_shutdown=shutdown, endpoint_owner=endpoint_owner,
        server_process=process_report, server_lifecycle=lifecycle, context_closed=closed_context,
        remaining_browser_contexts=len(browser.contexts), port_released=released,
        console_errors=console_errors, page_errors=page_errors,
        external_requests=external_requests, forced_cleanup=0, failures=failures,
    )
    write_new(destination / 'owned-result.json', record)
    return record, inspection


def browser_child(port, output, nonce, *, headed=False):
    output = owned_run(output, port, nonce)
    network = deny_external_connections()
    from playwright.sync_api import sync_playwright
    from tools import check_native_window as native

    result = {
        'success': False, 'scope': SCOPE, 'data': 'synthetic', 'channel': 'msedge',
        'viewport': VIEWPORT, 'port': port, 'expected_phases': list(PHASES),
        'runs': [], 'browser_closed': False, 'playwright_closed': False,
        'forced_cleanup': 0, 'failures': [],
    }
    browser = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=not headed)
            try:
                previous = None
                for phase in PHASES:
                    current, inspection = run_phase(browser, output, port, nonce, phase, previous, native)
                    result['runs'].append(current)
                    if not current['success']:
                        result['failures'].append('phase_failed; remaining_operations_not_started')
                        break
                    previous = inspection
            finally:
                browser.close()
                result['browser_closed'] = not browser.is_connected()
        result['playwright_closed'] = True
    except Exception as error:
        result['failures'].append(type(error).__name__ + ': browser_child_failed')
    finally:
        result['denied_external_connections'] = network['denied']
        if network['denied']:
            result['failures'].append('external_connection_attempt')
        result['success'] = (
            not result['failures'] and len(result['runs']) == 2
            and all(run['success'] for run in result['runs'])
            and result['browser_closed'] and result['playwright_closed'])
        write_new(output / 'browser-result.json', result)
    return 0 if result['success'] else 1


def run(output, timeout, *, headed=False):
    from tools import check_native_window as native

    output = owned_output(output)
    base = (ROOT / 'artifacts' / 'p3-318-scheduler-70-authors').resolve()
    if output == base or not output.is_relative_to(base):
        raise ValueError('output must be a fresh run below artifacts/p3-318-scheduler-70-authors')
    output.mkdir(parents=True, exist_ok=False)
    temporary = output / 'tmp'
    temporary.mkdir()
    (output / 'managed').mkdir()
    before, helpers_before = source_hashes(), helper_hashes()
    nonce, port = secrets.token_urlsafe(32), free_port()
    write_new(output / OWNER_FILE, {
        'scope': SCOPE, 'runner_pid': os.getpid(), 'port': port,
        'nonce_sha256': hashlib.sha256(nonce.encode()).hexdigest(),
        'managed_relative': 'managed', 'viewport': VIEWPORT,
    })
    network = deny_external_connections()
    env = child_environment()
    env.update(PYTHONDONTWRITEBYTECODE='1', TEMP=str(temporary), TMP=str(temporary))
    process = tree = report = browser_result = None
    failures = []
    command = [sys.executable, '-B', str(Path(__file__).resolve()),
               '--browser', str(port), '--nonce', nonce, '--output', str(output)]
    if headed:
        command.append('--headed')
    with (output / 'checker.log').open('xb') as log:
        try:
            process = subprocess.Popen(
                command, cwd=str(output), env=env, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT)
            tree = OwnedTree(process, native)
            deadline = time.monotonic() + timeout
            while process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.1)
            if process.poll() is None:
                failures.append('browser_child_not_finished; no_operations_replayed')
            elif process.returncode != 0:
                failures.append('browser_child_failed')
        except Exception as error:
            failures.append(type(error).__name__ + ': runner_failed')
        finally:
            if tree is not None:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    tree.sample()
                    if process.poll() is not None and tree.last_living == []:
                        break
                    time.sleep(0.1)
                report = tree.finish()
                if (report['exit_code'] != 0 or report['living'] != []
                        or report['errors'] or not report['observed']):
                    failures.append('owned_browser_server_descendants_not_exited')
            else:
                failures.append('browser_child_owner_not_created')
    try:
        if process is None or process.poll() is None:
            raise RuntimeError('child result remains pending')
        browser_result = json.loads((output / 'browser-result.json').read_text(encoding='utf-8'))
    except Exception as error:
        failures.append(type(error).__name__ + ': completed_browser_evidence_unavailable')
    if not (isinstance(browser_result, dict) and browser_result.get('success') is True
            and browser_result.get('scope') == SCOPE
            and browser_result.get('expected_phases') == list(PHASES)
            and [entry.get('phase') for entry in browser_result.get('runs', [])] == list(PHASES)
            and browser_result.get('browser_closed') is True
            and browser_result.get('playwright_closed') is True
            and browser_result.get('denied_external_connections') == 0
            and not browser_result.get('failures')):
        failures.append('two_phase_browser_acceptance_not_proven')
    try:
        released = port_released(port)
    except Exception as error:
        released = False
        failures.append(type(error).__name__ + ': port_observation_failed')
    if not released:
        failures.append('owned_port_not_released')
    try:
        after, helpers_after = source_hashes(), helper_hashes()
    except Exception as error:
        after, helpers_after = {}, {}
        failures.append(type(error).__name__ + ': source_observation_failed')
    if before != after or helpers_before != helpers_after:
        failures.append('source_or_checker_changed')
    if network['denied']:
        failures.append('external_connection_attempt')
    result = {
        'success': not failures, 'scope': SCOPE, 'data': 'synthetic',
        'channel': 'msedge', 'viewport': VIEWPORT, 'port': port,
        'browser_result': 'browser-result.json', 'phases': list(PHASES),
        'processes': {'browser_and_server_tree': report}, 'port_released': released,
        'source_before': before, 'source_after': after, 'source_changed': before != after,
        'helpers_before': helpers_before, 'helpers_after': helpers_after,
        'helpers_changed': helpers_before != helpers_after,
        'denied_external_connections': network['denied'], 'forced_cleanup': 0,
        'failures': failures,
    }
    write_new(output / 'result.json', result)
    print(json.dumps({'success': result['success'], 'failures': failures,
                      'evidence': str(output / 'result.json')}, ensure_ascii=False))
    return 0 if result['success'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=600)
    parser.add_argument('--headed', action='store_true')
    children = parser.add_mutually_exclusive_group()
    children.add_argument('--serve', type=int)
    children.add_argument('--browser', type=int)
    parser.add_argument('--nonce')
    parser.add_argument('--phase', choices=PHASES)
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('Windows only: owned PID/birth observations are required')
    if not 1 <= args.timeout <= 1200:
        parser.error('--timeout must be 1..1200 seconds')
    port = args.serve if args.serve is not None else args.browser
    if port is not None:
        if not args.nonce or not 1024 <= port <= 65535 or port == 8089:
            parser.error('owned child requires a nonce and a dedicated loopback port')
        if args.serve is not None:
            if args.phase is None:
                parser.error('fixture child requires its initial/restart phase')
            return serve(port, args.output, args.nonce, args.phase)
        if args.phase is not None:
            parser.error('--phase is reserved for the fixture child')
        return browser_child(port, args.output, args.nonce, headed=args.headed)
    if args.nonce or args.phase is not None:
        parser.error('--nonce and --phase are reserved for owned children')
    return run(args.output, args.timeout, headed=args.headed)


if __name__ == '__main__':
    raise SystemExit(main())
