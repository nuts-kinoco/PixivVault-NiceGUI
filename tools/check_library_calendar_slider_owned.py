"""Owned, synthetic Windows runner for the existing L04/L07 Edge checker.

The UI checker runs exactly once and owns a fresh browser. A failed close is
recorded without terminating any process. This tool never attaches an existing
browser, opens an original database, reads a Cookie, or registers an OS hook.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ''):
    sys.path.insert(0, str(ROOT))

from tools.run_browser_checks import (  # noqa: E402
    child_environment, free_port, port_released, source_hashes, wait_for_health,
)

HOST = '127.0.0.1'
SCOPE = 'L04/L07 calendar-slider'
OWNER_FILE = 'calendar-run-owner.json'


def owned_output(value: Path) -> Path:
    path = value.resolve()
    artifacts = (ROOT / 'artifacts').resolve()
    if path == artifacts or not path.is_relative_to(artifacts):
        raise ValueError('a fresh, dedicated repository artifacts directory is required')
    return path


def write_new(path: Path, value) -> None:
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def helper_hashes() -> dict[str, str]:
    names = (
        Path(__file__).name, 'check_library_browser.py', 'run_browser_checks.py',
        'check_native_window.py', 'native_restore_gate.py', 'native_ui_guard.py',
        'native_failed_csv.py',
    )
    return {f'tools/{name}': hashlib.sha256((ROOT / 'tools' / name).read_bytes()).hexdigest()
            for name in names}


def deny_external_connections() -> dict[str, int]:
    """Process-local audit denial; no destination or request payload is logged."""
    counts = {'denied': 0}

    def local(host) -> bool:
        if host is None:
            return True  # getaddrinfo's passive bind query
        if isinstance(host, bytes):
            try:
                host = host.decode('ascii')
            except UnicodeDecodeError:
                return False
        if str(host).lower() == 'localhost':
            return True
        try:
            return ipaddress.ip_address(str(host)).is_loopback
        except ValueError:
            return False

    def guard(event, args):
        rejected = False
        if event in ('socket.connect', 'socket.sendto'):
            sock, address = args
            if sock.family in (socket.AF_INET, socket.AF_INET6):
                rejected = not (isinstance(address, tuple) and local(address[0]))
        elif event in ('socket.getaddrinfo', 'socket.gethostbyname', 'socket.gethostbyaddr'):
            rejected = not local(args[0])
        if rejected:
            counts['denied'] += 1
            raise PermissionError('calendar fixture allows loopback connections only')

    sys.addaudithook(guard)
    return counts


def serve(port: int, output: Path, nonce: str) -> int:
    output = owned_output(output)
    owner = json.loads((output / OWNER_FILE).read_text(encoding='utf-8'))
    if not secrets.compare_digest(owner['nonce_sha256'], hashlib.sha256(nonce.encode()).hexdigest()):
        raise ValueError('fixture ownership token mismatch')
    network = deny_external_connections()

    from fastapi import Header, HTTPException
    from nicegui import app, ui
    from pixivvault_nicegui.services import get_services
    from pixivvault_nicegui.media_routes import register_media_routes
    from pixivvault_nicegui.ui.shell import build_demo_page

    # No profile, explicit source, persistence root, or network reader is supplied.
    services = get_services(app)
    if (services.data_mode != 'synthetic' or services.live_client is not None
            or services.managed_downloads is not None):
        raise RuntimeError('default synthetic service contract changed')
    register_media_routes(app, services.images)
    ui.page('/')(build_demo_page)
    stopping = False

    def auth(value):
        if not secrets.compare_digest(value or '', nonce):
            raise HTTPException(status_code=403)

    @app.get('/healthz')
    def health():
        return {'status': 'ok', 'mode': 'demo', 'data': 'synthetic'}

    @app.get('/_calendar_fixture/owner', include_in_schema=False)
    def identity(x_acceptance: str | None = Header(default=None)):
        auth(x_acceptance)
        return {'pid': os.getpid(), 'data': 'synthetic',
                'network_reader': False, 'managed_downloads': False}

    @app.post('/_calendar_fixture/shutdown', include_in_schema=False)
    async def shutdown(x_acceptance: str | None = Header(default=None)):
        nonlocal stopping
        auth(x_acceptance)
        if stopping:
            raise HTTPException(status_code=409)
        stopping = True
        app.shutdown()
        return {'stopping': True}

    async def close_services():
        close_error = None
        try:
            await asyncio.to_thread(services.close)
        except BaseException as exc:
            close_error = type(exc).__name__
            raise
        finally:
            write_new(output / 'server-lifecycle.json', {
                'pid': os.getpid(), 'data': 'synthetic',
                'shutdown_requested': stopping,
                'services_closed': services.closed,
                'close_error': close_error,
                'denied_external_connections': network['denied'],
            })

    app.on_shutdown(close_services)
    ui.run(host=HOST, port=port, show=False, reload=False,
           binding_refresh_interval=None, timeout_graceful_shutdown=5)
    return 0


class OwnedTree:
    """Observe only children of a pinned PID/birth; never terminate them."""
    def __init__(self, process: subprocess.Popen, native):
        self.process = process
        self.native = native
        self.identities = {}
        self.errors = []
        self.last_living = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        try:
            birth = native.process_identity(process.pid)
            if birth is None:
                raise RuntimeError('root exited before its birth could be recorded')
            self.identities[process.pid] = birth
        except Exception as exc:
            self.errors.append(type(exc).__name__ + ': root_identity_unknown')
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def sample(self):
        with self._lock:
            try:
                pairs = self.native.process_pairs()
                # PID sorting is not generation sorting. Reobserve this one
                # snapshot until all still-owned parent links have been adopted.
                # A pinned, still-live child remains an ownership anchor after
                # the original root exits. Never seed from an unverified or
                # reused PID, but do not lose its late-spawned grandchildren.
                previous = -1
                while previous != len(self.identities):
                    previous = len(self.identities)
                    anchors = self.native.living_pids(self.identities)
                    for parent in sorted(anchors):
                        self.native.adopt_descendants(
                            parent, self.identities, pairs, True)
                self.last_living = sorted(self.native.living_pids(self.identities))
            except Exception as exc:
                label = type(exc).__name__ + ': process_observation_failed'
                if label not in self.errors:
                    self.errors.append(label)
                self.last_living = None

    def _watch(self):
        while not self._stop.is_set():
            self.sample()
            self._stop.wait(0.1)

    def root_is_owned(self) -> bool:
        with self._lock:
            birth = self.identities.get(self.process.pid)
        return (birth is not None and self.process.poll() is None
                and self.native.process_identity(self.process.pid) == birth)

    def observed_endpoint(self, pid) -> dict | None:
        """Admit a nonce-authenticated server only inside this live owned tree.

        A Windows venv redirector can be the Popen root while its child owns
        ASGI. Never substitute a process-name lookup or accept an arbitrary PID.
        """
        if type(pid) is not int or pid <= 0 or not self.root_is_owned():
            return None
        self.sample()
        with self._lock:
            birth = self.identities.get(pid)
            observed_alive = self.last_living is not None and pid in self.last_living
            trusted = not self.errors
        if (birth is None or not observed_alive or not trusted
                or self.native.process_identity(pid) != birth or not self.root_is_owned()):
            return None
        return {'pid': pid, 'birth': birth}

    def finish(self) -> dict:
        self._stop.set()
        self._thread.join(timeout=2)
        if self._thread.is_alive():
            # Do not claim an empty tree if an ownership observation is stuck.
            return {'pid': self.process.pid, 'exit_code': self.process.poll(),
                    'observed': {}, 'living': None, 'errors': ['observer_not_finished']}
        self.sample()
        with self._lock:
            return {'pid': self.process.pid, 'exit_code': self.process.poll(),
                    'observed': {str(pid): birth for pid, birth in self.identities.items()},
                    'living': self.last_living, 'errors': list(self.errors)}


def run(output: Path, channel: str, timeout: float, headed: bool) -> int:
    from tools import check_native_window as native

    output = owned_output(output)
    output.mkdir(parents=True, exist_ok=False)
    temporary = output / 'tmp'
    temporary.mkdir()
    nonce = secrets.token_urlsafe(32)
    write_new(output / OWNER_FILE, {
        'runner_pid': os.getpid(),
        'nonce_sha256': hashlib.sha256(nonce.encode()).hexdigest(),
        'scope': SCOPE,
    })
    before = source_hashes()
    helpers_before = helper_hashes()
    env = child_environment()
    env.update(PYTHONDONTWRITEBYTECODE='1', TEMP=str(temporary), TMP=str(temporary))
    port = free_port()
    url = f'http://{HOST}:{port}'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    server = checker = None
    server_tree = checker_tree = None
    failures = []
    browser_result = lifecycle = None
    endpoint_owner = None
    shutdown = {'attempted': False, 'acknowledged': False}
    process_reports = {}

    def fail(label):
        if label not in failures:
            failures.append(label)

    def request(path: str, *, post=False):
        req = urllib.request.Request(
            url + path, method='POST' if post else 'GET',
            headers={'X-Acceptance': nonce}, data=b'' if post else None)
        with opener.open(req, timeout=5) as response:
            if response.status != 200:
                raise RuntimeError('fixture endpoint returned a non-200 status')
            return json.loads(response.read(65536))

    with (output / 'server.log').open('xb') as server_log, (output / 'checker.log').open('xb') as checker_log:
        try:
            server = subprocess.Popen(
                [sys.executable, '-B', str(Path(__file__).resolve()),
                 '--serve', str(port), '--nonce', nonce, '--output', str(output)],
                cwd=str(output), env=env, stdin=subprocess.DEVNULL,
                stdout=server_log, stderr=subprocess.STDOUT,
            )
            server_tree = OwnedTree(server, native)
            wait_for_health(url, server, timeout=35, expected_data='synthetic')
            identity = request('/_calendar_fixture/owner')
            if (type(identity) is not dict
                    or set(identity) != {'pid', 'data', 'network_reader', 'managed_downloads'}
                    or identity['data'] != 'synthetic'
                    or identity['network_reader'] is not False
                    or identity['managed_downloads'] is not False):
                raise RuntimeError('synthetic endpoint ownership was not proven')
            endpoint_owner = server_tree.observed_endpoint(identity['pid'])
            if endpoint_owner is None:
                raise RuntimeError('synthetic endpoint PID/birth is not inside the owned tree')
            command = [sys.executable, '-B', str(ROOT / 'tools' / 'check_library_browser.py'),
                       '--calendar-slider-only', '--url', url, '--channel', channel,
                       '--output', str(output / 'ui')]
            if headed:
                command.append('--headed')
            checker = subprocess.Popen(
                command, cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
                stdout=checker_log, stderr=subprocess.STDOUT,
            )
            checker_tree = OwnedTree(checker, native)
            deadline = time.monotonic() + timeout
            while checker.poll() is None and time.monotonic() < deadline:
                if server.poll() is not None:
                    fail('server_exited_during_browser_operations')
                    break
                time.sleep(0.1)
            if checker.poll() is None:
                fail('browser_checker_not_finished')
            elif checker.returncode != 0:
                fail('browser_checker_failed')
            result_path = output / 'ui' / 'result.json'
            if result_path.is_file() and checker.poll() is not None:
                browser_result = json.loads(result_path.read_text(encoding='utf-8'))
            if not (isinstance(browser_result, dict)
                    and browser_result.get('success') is True
                    and browser_result.get('scope') == SCOPE
                    and set(browser_result.get('viewports', {})) == {'1280x720', '1920x1080'}
                    and browser_result.get('two_clients') == {
                        'calendar_pending_independent': True,
                        'calendar_selection_independent': True,
                        'slider_and_clear_independent': True}
                    and not browser_result.get('console_errors')
                    and not browser_result.get('page_errors')
                    and not browser_result.get('blocked_foreign_requests')):
                fail('calendar_slider_operations_not_accepted')
        except Exception as exc:
            fail(type(exc).__name__ + ': runner_failed')
        finally:
            # One nonce-authenticated normal shutdown attempt only. An unknown
            # result is not replayed, and no terminate/kill fallback is used.
            if server_tree is not None:
                try:
                    if (endpoint_owner is not None
                            and server_tree.observed_endpoint(endpoint_owner['pid']) == endpoint_owner):
                        shutdown['attempted'] = True
                        shutdown['endpoint_owner_verified'] = True
                        shutdown['acknowledged'] = request(
                            '/_calendar_fixture/shutdown', post=True) == {'stopping': True}
                    else:
                        fail('server_endpoint_owner_missing_before_normal_shutdown')
                except Exception as exc:
                    fail(type(exc).__name__ + ': normal_shutdown_unconfirmed')
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                processes = [p for p in (server, checker) if p is not None]
                if all(p.poll() is not None for p in processes):
                    # The sampling threads retain previously observed children
                    # after root exit; give owned descendants a normal drain.
                    trees = [t for t in (server_tree, checker_tree) if t is not None]
                    for tree in trees:
                        tree.sample()
                    if all(t.last_living == [] for t in trees):
                        break
                time.sleep(0.1)
            for name, tree in (('server', server_tree), ('checker', checker_tree)):
                if tree is not None:
                    process_reports[name] = tree.finish()
            if set(process_reports) != {'server', 'checker'}:
                fail('both_owned_roots_not_observed')
            for name, report in process_reports.items():
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
    lifecycle_path = output / 'server-lifecycle.json'
    try:
        if lifecycle_path.is_file():
            lifecycle = json.loads(lifecycle_path.read_text(encoding='utf-8'))
    except Exception as exc:
        fail(type(exc).__name__ + ': service_close_evidence_invalid')
    if not (isinstance(lifecycle, dict) and endpoint_owner is not None
            and lifecycle.get('pid') == endpoint_owner['pid'] and lifecycle.get('data') == 'synthetic'
            and lifecycle.get('shutdown_requested') is True
            and lifecycle.get('services_closed') is True
            and lifecycle.get('close_error') is None
            and lifecycle.get('denied_external_connections') == 0):
        fail('synthetic_service_close_or_network_gate_failed')
    try:
        after, helpers_after = source_hashes(), helper_hashes()
    except Exception as exc:
        after, helpers_after = {}, {}
        fail(type(exc).__name__ + ': final_source_observation_failed')
    if before != after or helpers_before != helpers_after:
        fail('source_or_checker_changed')
    result = {
        'success': not failures, 'scope': SCOPE, 'channel': channel,
        'data': 'synthetic', 'ui_checker_invocations': 1 if checker is not None else 0,
        'browser_result': 'ui/result.json', 'port': port,
        'normal_shutdown': shutdown, 'processes': process_reports, 'endpoint_owner': endpoint_owner,
        'port_released': released, 'forced_cleanup': 0,
        'server_lifecycle': lifecycle, 'source_before': before, 'source_after': after,
        'source_changed': before != after, 'helpers_before': helpers_before,
        'helpers_after': helpers_after, 'helpers_changed': helpers_before != helpers_after,
        'failures': failures,
    }
    write_new(output / 'result.json', result)
    print(json.dumps({'success': result['success'], 'failures': failures,
                      'evidence': str(output / 'result.json')}, ensure_ascii=False))
    return 0 if result['success'] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--channel', choices=('chromium', 'msedge'), default='msedge')
    parser.add_argument('--timeout', type=float, default=240)
    parser.add_argument('--headed', action='store_true')
    parser.add_argument('--serve', type=int)
    parser.add_argument('--nonce')
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('Windows only: PID/birth and owned descendant observations are required')
    if not 1 <= args.timeout <= 600:
        parser.error('--timeout must be 1..600 seconds')
    if args.serve is not None:
        if not args.nonce or not 1024 <= args.serve <= 65535:
            parser.error('fixture server requires its runner token and loopback port')
        return serve(args.serve, args.output, args.nonce)
    if args.nonce:
        parser.error('--nonce is reserved for the owned fixture child')
    return run(args.output, args.channel, args.timeout, args.headed)


if __name__ == '__main__':
    raise SystemExit(main())
