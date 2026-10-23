"""Private native managed acceptance: entirely synthetic filesystem tests."""
import json
import hashlib
import io
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from tools.check_managed_download_native import IDENTITY, MARKER, preflight, verify_storage, cookie_unchanged
from tools.check_native_window import managed_capture_allowed


def test_new_root_guards(tmp_path):
    cookie = tmp_path / "secrets" / "cookie.txt"
    cookie.parent.mkdir()
    cookie.write_text("synthetic", encoding="utf-8")
    fresh = tmp_path / "dedicated"
    assert preflight(fresh, cookie, restart=False)["fresh"] is True
    with pytest.raises(ValueError):
        preflight(cookie.parent / "nested", cookie, restart=False)
    fresh.mkdir()
    with pytest.raises(ValueError):
        preflight(fresh, cookie, restart=False)


def test_restart_refuses_missing_or_malformed_receipt(tmp_path):
    root = tmp_path / "managed"
    root.mkdir()
    (root / MARKER).write_text(json.dumps(IDENTITY), encoding="utf-8")
    outputs = root / "outputs"
    outputs.mkdir()
    with pytest.raises(ValueError, match="database"):
        verify_storage(root)
    folder = outputs / ("work-123-" + "a" * 32)
    folder.mkdir()
    with pytest.raises((ValueError, FileNotFoundError)):
        verify_storage(root)


def test_reject_duplicate_json_marker_and_oversize(tmp_path):
    root = tmp_path / "managed"
    root.mkdir()
    (root / "outputs").mkdir()
    marker = root / MARKER
    marker.write_text('{"version":1,"version":1}', encoding="utf-8")
    with pytest.raises(ValueError):
        verify_storage(root)
    marker.write_bytes(b"x" * 4097)
    with pytest.raises(ValueError):
        verify_storage(root)


@pytest.mark.parametrize("inspection", [None, {}, {"passed": True},
    {"passed": False, "synthetic_return": True},
    {"passed": 1, "synthetic_return": True},
    {"passed": True, "synthetic_return": 1},
    {"passed": False, "synthetic_return": False, "error_class": "TimeoutError"}])
def test_private_capture_denied_without_exact_synthetic_gate(inspection):
    assert managed_capture_allowed(inspection) is False


def test_capture_allowed_only_after_exact_success():
    assert managed_capture_allowed({"passed": True, "synthetic_return": True}) is True


@pytest.mark.parametrize('inspection', ['exception', 'timeout', 'return_false', 'success'])
def test_managed_main_capture_cleanup_sanitization(monkeypatch, tmp_path, inspection):
    import tools.check_native_window as checker
    import tools.check_managed_download_native as managed

    private = 'SECRET-ARTWORK-998877-cookie-private'
    events = []
    exe = tmp_path / 'synthetic.exe'
    exe.write_bytes(b'mocked process only')
    cookie = tmp_path / 'cookie' / 'private.txt'
    cookie.parent.mkdir()
    cookie.write_text(private, encoding='utf-8')
    output = tmp_path / 'public.json'
    root_dir = tmp_path / 'managed'
    before = hashlib.sha256(cookie.read_bytes()).digest()
    current = [100.0]
    closed = [False]
    monkeypatch.setattr(checker, 'time', SimpleNamespace(
        monotonic=lambda: current[0],
        sleep=lambda seconds: current.__setitem__(0, current[0] + seconds)))
    class Process:
        pid = 10
        def poll(self):
            return 0 if closed[0] else None
    monkeypatch.setattr(checker, 'os', SimpleNamespace(name='nt', environ={}))
    monkeypatch.setattr(checker, 'subprocess', SimpleNamespace(
        Popen=lambda *a, **kw: events.append('spawn') or Process(), DEVNULL=-3, STDOUT=-2))
    ports = iter((32000, 32001))
    monkeypatch.setattr(checker, 'available_port', lambda: next(ports))
    monkeypatch.setattr(checker, 'process_identity', lambda pid: None if closed[0] else 1000)
    monkeypatch.setattr(checker, 'process_pairs', lambda: [(10, 1)])
    monkeypatch.setattr(checker, 'owned_windows', lambda pids: [100] if pids == {10} else [])
    monkeypatch.setattr(checker, 'health_ready', lambda *a: True)
    monkeypatch.setattr(checker, 'ui_ready', lambda *a: True)
    monkeypatch.setattr(checker, 'living_pids', lambda identities: set() if closed[0] else set(identities))
    monkeypatch.setattr(checker, 'port_released', lambda port: closed[0])
    monkeypatch.setattr(checker, 'owned_window_identity', lambda *a: (10, 1000))
    monkeypatch.setattr(managed, 'preflight', lambda *a, **kw: {
        'fresh': True, 'receipts': 0, 'pages': 0, 'jobs': 0,
        '_signature': (), '_cookie_signature': before})
    monkeypatch.setattr(managed, 'verify_storage', lambda root: {
        'receipts': 1, 'pages': 2, 'jobs': 1, '_signature': (b'original',)})
    monkeypatch.setattr(managed, 'cookie_unchanged', lambda path, state:
                        hashlib.sha256(path.read_bytes()).digest() == state['_cookie_signature'])

    def inspect(*args, **kwargs):
        events.append('inspect')
        kwargs['check_owner']()
        if inspection == 'exception':
            raise RuntimeError(private)
        if inspection == 'timeout':
            current[0] = 185
            raise TimeoutError(private)
        return {'passed': inspection == 'success', 'synthetic_return': inspection == 'success',
                'phase': 'done' if inspection == 'success' else private,
                'note': private}

    def restore(*args, **kwargs):
        events.append('restore')
        return {'passed': True, 'minimized': True}

    def close(*args):
        events.append('close')
        closed[0] = True
        return True

    monkeypatch.setattr(checker, 'inspect_ui', inspect)
    monkeypatch.setattr(checker, 'run_restore_gate', restore)
    monkeypatch.setattr(checker, 'close_owned_window', close)
    monkeypatch.setattr(checker, 'terminate_owned', lambda *_: pytest.fail('unexpected terminate'))
    result = checker.main(['--exe', str(exe), '--output', str(output), '--inspect-ui',
                           '--pixiv-cookie-file', str(cookie), '--pixiv-download-root', str(root_dir)])
    report_text = output.read_text('utf-8')
    report = json.loads(report_text)
    assert private not in report_text and str(cookie) not in report_text
    assert result == (0 if inspection == 'success' else 1)
    assert ('restore' in events) is (inspection == 'success')
    assert events[-1] == 'close' and report['normal_exit'] and report['port_released']
    assert report['cookie_unchanged'] is True
    assert report['capture_skipped'] is (inspection != 'success')
    assert report['restore_passed'] is (inspection == 'success')


def test_actual_synthetic_writer_jobservice_verifier_detects_mismatch(tmp_path):
    from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
    from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads
    from pixivvault_nicegui.managed_download_requests import ManagedDownloadRequests
    from pixivvault_nicegui.job_service import JobService
    import sqlite3

    root = tmp_path / 'managed'
    cookie = tmp_path / 'secrets' / 'cookie.txt'
    cookie.parent.mkdir()
    cookie.write_bytes(b'unchanged-synthetic-cookie')
    cookie_before = cookie.read_bytes()
    payload = io.BytesIO()
    Image.new('RGB', (8, 8)).save(payload, 'PNG')
    png = payload.getvalue()
    bookmark = RemoteBookmark('123', 'illustration', 'show', 'fake', '456', 'fake author',
                              ('fake tag',), '', '2026-10-23', 2, 0, 0)
    class Client:
        def verify_identity(self):
            return '456'
        def get_artwork_original_pages(self, wid):
            return ('https://i.pximg.net/one', 'https://i.pximg.net/two')
        def get_image_bytes(self, url):
            return png
    writer = ManagedPixivDownloads(root, Client(), resolve_record=lambda wid: bookmark)
    ManagedDownloadRequests(root).store('synthetic-operation', bookmark)
    jobs = JobService(writer.run, storage=writer.storage_path)
    before = {'_cookie_signature': hashlib.sha256(cookie.read_bytes()).digest()}
    job = jobs.enqueue('pixiv-illust', '123', 'fake', operation_id='synthetic-operation')
    until = time.monotonic() + 5
    while time.monotonic() < until and jobs.snapshot()[0].state not in ('done', 'error'):
        time.sleep(.01)
    assert jobs.snapshot()[0].state == 'done'
    jobs.close()
    writer.close()
    expected = verify_storage(root)
    assert expected['receipts'] == 1 and expected['pages'] == 2 and expected['jobs'] == 1
    assert cookie_unchanged(cookie, before) and cookie.read_bytes() == cookie_before
    db = root / 'jobs.sqlite3'
    with sqlite3.connect(db) as connection:
        original = connection.execute('SELECT payload FROM jobs WHERE job_id=?', (job.job_id,)).fetchone()[0]
        for updates in ({'kind': 'demo'}, {'state': 'done', 'total': 1, 'done': 1},
                        {'result': 'outputs/nonexistent/receipt.json'}):
            bad = {**json.loads(original), **updates}
            connection.execute('UPDATE jobs SET payload=? WHERE job_id=?', (json.dumps(bad), job.job_id))
            connection.commit()
            with pytest.raises(ValueError):
                verify_storage(root)
        connection.execute('UPDATE jobs SET payload=? WHERE job_id=?', (original, job.job_id))
        connection.commit()
    saved = next((root / 'outputs').iterdir()) / 'receipt.json'
    saved.write_bytes(saved.read_bytes().replace(b'"pixiv-original"', b'"thumbnail"'))
    with pytest.raises(ValueError):
        verify_storage(root)
    assert cookie.read_bytes() == cookie_before
