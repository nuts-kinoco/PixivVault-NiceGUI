"""N09/N10: real owned loopback HTTP + durable generated media job, no Pixiv.

These tests deliberately separate HTTP acknowledgement, verified media bytes,
status, retry and restart. They do not certify the packaged native UI.
"""
from __future__ import annotations

from hashlib import sha256
import http.client
import json
import os
from pathlib import Path
from threading import Event, Lock
import time
from types import SimpleNamespace

from pixivvault_nicegui.extension_application import ExtensionApplication
from pixivvault_nicegui.extension_bridge import ExtensionBridge
from pixivvault_nicegui.job_service import JobService
from pixivvault_nicegui.media_jobs import MediaJobs


ORIGIN = 'chrome-extension://' + 'a' * 32
PAYLOAD = {'type': 'work', 'work_id': '123', 'is_novel': False}
IMAGE = b'generated N09/N10 verified receipt only'


def wait(predicate, seconds=7):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        Event().wait(0.015)
    raise AssertionError('generated extension operation did not settle')


def request(port, method, route, payload=None, *, origin=ORIGIN):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
    headers = {'Content-Type': 'application/json'}
    if origin is not None:
        headers['Origin'] = origin
    content = json.dumps(payload, sort_keys=True).encode() if payload is not None else None
    try:
        connection.request(method, route, body=content, headers=headers)
        response = connection.getresponse()
        body = response.read()
        return response.status, json.loads(body), response.getheader('Access-Control-Allow-Origin')
    finally:
        connection.close()


class Fixture:
    def __init__(self, root: Path, gate: Event, *, counter):
        self.root, self.gate, self.counter = root, gate, counter
        self.closed = False
        self._lock = Lock()
        self.media = root / 'generated-saved-image.bin'

        def run(job, context):
            with self._lock:
                counter.append(job.target_id)
            while not gate.is_set():
                context.checkpoint()
                Event().wait(0.02)
            context.checkpoint()
            if not self.media.exists():
                with self.media.open('xb') as output:
                    output.write(IMAGE)
                    output.flush()
                    os.fsync(output.fileno())
            if self.media.read_bytes() != IMAGE:
                raise ValueError('generated saved bytes changed')
            return 'verified fixture receipt'

        self.child = JobService(run, root / 'media.sqlite3')
        managed = SimpleNamespace(
            verified_saved_receipt=lambda wid: self.receipt(wid),
            saved_receipts_with_dates=lambda: (
                (str(root / 'synthetic-receipt'), {'author_id': '91'}, sha256(IMAGE).hexdigest(), '2026-10-29')
            ,) if self.receipt('123') else (),
        )
        service = SimpleNamespace(closing=False, jobs=self.child,
                                  managed_downloads=managed, novel_jobs=None, ugoira=None)

        async def save(wid, operation):
            return self.child.enqueue('pixiv-illust', wid, operation_id=operation)

        service.save_explicit_work = save
        service.saved_discovery_ids = lambda: frozenset({'123'} if self.receipt('123') else ())
        self.owner = ExtensionApplication(service, root / 'commands')
        service.jobs = MediaJobs(self.child, self.owner)
        self.service = service
        self.bridge = ExtensionBridge(enqueue=self.owner.enqueue,
                                      work_status=self.owner.work_status,
                                      author_status=self.owner.author_status,
                                      port=0)
        self.port = self.bridge.start()

    def receipt(self, work_id):
        if work_id != '123':
            return None
        if not self.media.is_file() or sha256(self.media.read_bytes()).hexdigest() != sha256(IMAGE).hexdigest():
            return None
        entries = [row for row in self.child.snapshot()
                   if row.target_id == work_id and row.state == 'done']
        return ('synthetic-receipt', {'author_id': '91', 'work_id': '123'},
                sha256(IMAGE).hexdigest(), '2026-10-29') if len(entries) == 1 else None

    def close(self):
        if not self.closed:
            self.closed = True
            self.bridge.close()
            self.owner.close()
            self.child.close()


def test_http_acceptance_is_not_published_receipt_and_restart_does_not_replay(tmp_path):
    gate, calls = Event(), []
    product = Fixture(tmp_path, gate, counter=calls)
    try:
        port = product.port
        status, before, allowed = request(port, 'GET', '/api/work/123')
        assert status == 200 and before == {'downloaded': False} and allowed == ORIGIN
        assert request(port, 'GET', '/api/user/91/status')[1] == {'downloaded': 0, 'total': 0}
        status, accepted, allowed = request(port, 'POST', '/download', PAYLOAD)
        assert status == 200 and allowed == ORIGIN and accepted['status'] == 'ok'
        job_id = accepted['jobId']
        assert isinstance(job_id, str) and job_id
        wait(lambda: product.child.snapshot() and product.child.snapshot()[0].state == 'running')
        assert len(calls) == 1
        assert request(port, 'GET', '/api/work/123')[1] == {'downloaded': False}
        assert request(port, 'GET', '/api/user/91/status')[1] == {'downloaded': 0, 'total': 0}
        # Duplicate extension clicks may recover the same acceptance, but must
        # not create two source downloads for the identical target.
        repeated = request(port, 'POST', '/download', PAYLOAD)
        assert repeated[0] == 200 and repeated[1]['jobId'] == job_id
        assert len(product.owner.snapshot()) == 1 and len(product.child.snapshot()) == 1
        gate.set()
        wait(lambda: product.owner.snapshot()[0].state == 'done')
        assert product.child.snapshot()[0].state == 'done'
        assert product.receipt('123')[2] == sha256(IMAGE).hexdigest()
        assert product.media.read_bytes() == IMAGE
        assert request(port, 'GET', '/api/work/123')[1] == {'downloaded': True}
        assert request(port, 'GET', '/api/user/91/status')[1] == {'downloaded': 1, 'total': 1}
        assert calls == ['123']
    finally:
        gate.set()
        product.close()
    assert not product.bridge.running and product.bridge.bound_port is None

    restarted = Fixture(tmp_path, gate, counter=calls)
    try:
        assert len(restarted.owner.snapshot()) == 1
        assert restarted.owner.snapshot()[0].job_id == job_id
        assert restarted.owner.snapshot()[0].state == 'done'
        assert request(restarted.port, 'GET', '/api/work/123')[1] == {'downloaded': True}
        assert request(restarted.port, 'GET', '/api/user/91/status')[1] == {'downloaded': 1, 'total': 1}
        assert len(restarted.child.snapshot()) == 1 and calls == ['123']
        assert restarted.media.read_bytes() == IMAGE
    finally:
        restarted.close()


def test_foreign_origin_cannot_create_durable_command(tmp_path):
    gate, calls = Event(), []
    product = Fixture(tmp_path, gate, counter=calls)
    try:
        for origin in (None, 'https://attacker.invalid', 'null'):
            response = request(product.port, 'POST', '/download', PAYLOAD, origin=origin)
            assert response[0] == 403
        assert product.owner.snapshot() == ()
        assert product.child.snapshot() == ()
        assert calls == []
    finally:
        gate.set()
        product.close()
