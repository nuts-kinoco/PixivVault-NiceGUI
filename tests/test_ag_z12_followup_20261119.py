"""Z12 acceptance: owned jobs and fake encoders only; no real network/codec."""
import socket
import subprocess
import sys
from types import SimpleNamespace

import pytest
from nicegui import ui

from pixivvault_nicegui.job_service import JobCancelled
from pixivvault_nicegui.managed_ugoira import ManagedUgoiraError, ManagedUgoiraJobs
from pixivvault_nicegui.ugoira_mp4 import UgoiraMp4Error, mp4_plan
from pixivvault_nicegui.ui.job_views import QueueView
from test_q01_render_permission import archive


@pytest.fixture(autouse=True)
def no_real_network_or_codec(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('external network or codec process is forbidden')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def terminal(owner):
    with owner.jobs._condition:
        assert owner.jobs._condition.wait_for(
            lambda: owner.jobs.snapshot() and owner.jobs.snapshot()[0].state in {'done', 'error', 'stopped'},
            timeout=15)
    return owner.jobs.snapshot()[0]


def queue_result(owner, job):
    with ui.column() as holder:
        view = QueueView(SimpleNamespace(jobs=owner.jobs, ugoira=owner, artworks=()))
    try:
        view.refresh()
        return view.rows[job.job_id]['result'].text
    finally:
        view.close()
        holder.delete()


def test_absent_codec_bypasses_mp4_reservation_before_gif(tmp_path, monkeypatch):
    owner = ManagedUgoiraJobs(tmp_path / 'owner')
    assert owner.codec_python is None
    budget = owner._budget
    mp4_reservations = []
    def bounded(files, amount):
        if files == 6:
            mp4_reservations.append((files, amount))
            raise ManagedUgoiraError()
        return budget(files, amount)
    monkeypatch.setattr(owner, '_budget', bounded)
    try:
        owner.enqueue('missing-codec', *archive(), output='mp4')
        job = terminal(owner)
        assert job.state == 'done' and job.result.startswith('gifs/')
        assert not mp4_reservations
        assert not tuple(owner.codec_incomplete.iterdir())
    finally:
        owner.close()


def test_fallback_done_shows_failure_reasons_in_queue(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise UgoiraMp4Error()
    monkeypatch.setattr('pixivvault_nicegui.ugoira_mp4.render_mp4', fail)
    owner = ManagedUgoiraJobs(tmp_path / 'owner', codec_python=sys.executable)
    try:
        owner.enqueue('notify-fallback', *archive(), output='mp4')
        job = terminal(owner)
        assert job.state == 'done' and job.result.startswith('gifs/')
        notice = queue_result(owner, job)
        assert 'MP4' in notice and 'GIF' in notice
        assert 'OpenCV' in notice and 'imageio' in notice
    finally:
        owner.close()


@pytest.mark.parametrize('outcome', ['cancel', 'gif_error'])
def test_no_successful_fallback_notice_for_stopped_or_failed_job(tmp_path, monkeypatch, outcome):
    def converter(*args, **kwargs):
        if outcome == 'cancel':
            raise JobCancelled()
        raise UgoiraMp4Error()
    monkeypatch.setattr('pixivvault_nicegui.ugoira_mp4.render_mp4', converter)
    if outcome == 'gif_error':
        def gif_failure(*args, **kwargs):
            raise ValueError('synthetic GIF failure')
        monkeypatch.setattr('pixivvault_nicegui.managed_ugoira.render_ugoira_gif', gif_failure)
    owner = ManagedUgoiraJobs(tmp_path / 'owner', codec_python=sys.executable)
    try:
        owner.enqueue('unsuccessful-fallback', *archive(), output='mp4')
        job = terminal(owner)
        assert job.state == ('stopped' if outcome == 'cancel' else 'error')
        assert job.result is None
        notice = queue_result(owner, job)
        assert not ('MP4' in notice and 'GIF' in notice)
    finally:
        owner.close()


def fake_mp4(raw, inspection, backend):
    plan = mp4_plan(inspection)
    report = dict(codec='libx264' if backend == 'imageio' else 'mp4v',
                  width=plan['width'], height=plan['height'], fps=plan['fps'],
                  frame_count=len(inspection.frames), source_duration_ms=inspection.duration_ms,
                  timing='legacy-average-delay-cfr-clamped-1-60')
    return b'synthetic-mp4-output', report, raw


@pytest.mark.parametrize('imageio_succeeds', [True, False])
def test_opencv_failure_tries_imageio_before_gif(tmp_path, monkeypatch, imageio_succeeds):
    calls, stages = [], []
    # Proposed boundary: each backend is attempted in a distinct owned stage.
    def converter(raw, inspection, stage, codec_python, checkpoint, *, backend='opencv'):
        calls.append(backend)
        stages.append(stage)
        if backend == 'opencv' or not imageio_succeeds:
            raise UgoiraMp4Error()
        return fake_mp4(raw, inspection, backend)
    monkeypatch.setattr('pixivvault_nicegui.ugoira_mp4.render_mp4', converter)
    owner = ManagedUgoiraJobs(tmp_path / 'owner', codec_python=sys.executable)
    try:
        owner.enqueue('backend-order', *archive(), output='mp4')
        job = terminal(owner)
        assert job.state == 'done'
        assert calls == ['opencv', 'imageio']
        assert len(stages) == 2 and stages[0] != stages[1]
        assert job.result.startswith('mp4s/' if imageio_succeeds else 'gifs/')
    finally:
        owner.close()
    reopened = ManagedUgoiraJobs(tmp_path / 'owner', codec_python=sys.executable)
    try:
        assert reopened.jobs.snapshot()[0].state == 'done'
        assert reopened.jobs.snapshot()[0].result == job.result
    finally:
        reopened.close()


def test_shell_warns_after_durable_fallback_once_and_not_after_restart(tmp_path, monkeypatch):
    from nicegui import Client
    from pixivvault_nicegui.services import ApplicationServices
    from pixivvault_nicegui.ui.shell import DemoShell
    calls = []
    monkeypatch.setattr(ui, 'notify', lambda message, **kw: calls.append((message, kw)))
    owner = ManagedUgoiraJobs(tmp_path / 'owner')
    services = ApplicationServices(2)
    services.ugoira = owner
    client = Client(ui.page('/_z12_shell_warning'))
    try:
        with client:
            shell = DemoShell(services)
            try:
                shell.show('download')
                owner.enqueue('warning', *archive(), output='mp4')
                job = terminal(owner)
                assert job.state == 'done'
                shell._notify_ugoira_fallback()
                shell._notify_ugoira_fallback()
                assert len(calls) == 1 and calls[0][1]['type'] == 'warning'
                assert all(word in calls[0][0] for word in ('MP4', 'GIF', 'OpenCV', 'imageio'))
            finally:
                shell.close()
            shell = DemoShell(services)
            try:
                shell._notify_ugoira_fallback()
                assert len(calls) == 1
            finally:
                shell.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.parametrize('outcome', ['cancel', 'gif_error'])
def test_shell_does_not_warn_on_incomplete_fallback(tmp_path, monkeypatch, outcome):
    from nicegui import Client
    from pixivvault_nicegui.services import ApplicationServices
    from pixivvault_nicegui.ui.shell import DemoShell
    calls = []
    monkeypatch.setattr(ui, 'notify', lambda *args, **kwargs: calls.append(args))
    def convert(*args, **kwargs):
        if outcome == 'cancel':
            raise JobCancelled()
        raise UgoiraMp4Error()
    monkeypatch.setattr('pixivvault_nicegui.ugoira_mp4.render_mp4', convert)
    if outcome == 'gif_error':
        def fail(*args, **kwargs): raise ValueError('synthetic failure')
        monkeypatch.setattr('pixivvault_nicegui.managed_ugoira.render_ugoira_gif', fail)
    owner = ManagedUgoiraJobs(tmp_path / 'owner', codec_python=sys.executable)
    services = ApplicationServices(2)
    services.ugoira = owner
    client = Client(ui.page('/_z12_shell_cancel_' + outcome))
    try:
        with client:
            shell = DemoShell(services)
            try:
                owner.enqueue('incomplete', *archive(), output='mp4')
                job = terminal(owner)
                assert job.state == ('stopped' if outcome == 'cancel' else 'error')
                shell._notify_ugoira_fallback()
                assert not calls
            finally:
                shell.close()
    finally:
        client.delete()
        services.close()


def test_fake_pyav_worker_encodes_and_decodes_without_subprocess_or_network(tmp_path, monkeypatch):
    from PIL import Image
    from pixivvault_nicegui.ugoira_mp4_worker import _imageio_encode_decode
    calls, frames = [], []
    class Array:
        shape = (2, 2, 3)
        def copy(self): return self
        def mean(self, axis): return (1, 2, 3)
    monkeypatch.setitem(sys.modules, 'numpy', SimpleNamespace(asarray=lambda image: Array()))
    monkeypatch.setattr(Image, 'fromarray', lambda array: Image.new('RGB', (2, 2), (1, 2, 3)))
    class Writer:
        def __enter__(self): return self
        def __exit__(self, *args): (tmp_path / 'animation.mp4').write_bytes(b'fake-mp4')
        def init_video_stream(self, codec, *, fps): calls.append((codec, fps))
        def write_frame(self, frame): frames.append(frame.copy())
    def imopen(path, mode, *, plugin):
        calls.append((mode, plugin))
        return Writer()
    class Frame:
        def to_ndarray(self, *, format):
            assert format == 'rgb24'
            return frames[0]
    stream = SimpleNamespace(codec_context=SimpleNamespace(name='h264'), average_rate=10)
    class Container:
        streams = SimpleNamespace(video=[stream])
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def decode(self, requested):
            assert requested is stream
            return iter([Frame()])
    monkeypatch.setitem(sys.modules, 'imageio', SimpleNamespace(v3=SimpleNamespace(imopen=imopen)))
    monkeypatch.setitem(sys.modules, 'imageio.v3', SimpleNamespace(imopen=imopen))
    monkeypatch.setitem(sys.modules, 'av', SimpleNamespace(open=lambda *a, **kw: Container()))
    raw, manifest = archive()
    plan = dict(width=2, height=2, fps=10, frames=[dict(file='000000.png', delay=100)],
                max_frame_bytes=8*1024*1024, max_archive_bytes=64*1024*1024)
    report = _imageio_encode_decode(tmp_path, plan, raw, tmp_path / 'animation.mp4')
    assert calls == [('w', 'pyav'), ('libx264', 10)]
    assert report['codec'] == 'libx264' and report['frame_count'] == 1
    assert (tmp_path / 'decoded_frames.zip').is_file()


@pytest.mark.parametrize('legacy_version', [1, 2])
def test_legacy_v1_fallback_receipt_survives_restart(tmp_path, monkeypatch, legacy_version):
    import pixivvault_nicegui.managed_ugoira as module
    original = module._gif_receipt
    def legacy(request, source, rendered, fallback=None):
        return original(request, source, rendered, 'failed' if legacy_version == 2 else None)
    owner = ManagedUgoiraJobs(tmp_path / 'owner')
    try:
        with monkeypatch.context() as isolated:
            isolated.setattr(module, '_gif_receipt', legacy)
            owner.enqueue('legacy-v1', *archive(), output='mp4')
            job = terminal(owner)
            assert job.state == 'done'
    finally:
        owner.close()
    reopened = ManagedUgoiraJobs(tmp_path / 'owner')
    try:
        recovered = reopened.jobs.snapshot()[0]
        assert recovered.state == 'done' and recovered.result == job.result
        assert 'GIF' in reopened.fallback_message(recovered)
    finally:
        reopened.close()


def test_existing_opencv_mp4_receipt_survives_restart(tmp_path, monkeypatch):
    calls = []
    def converter(raw, inspection, stage, codec_python, checkpoint, *, backend='opencv'):
        calls.append(backend)
        return fake_mp4(raw, inspection, backend)
    monkeypatch.setattr('pixivvault_nicegui.ugoira_mp4.render_mp4', converter)
    owner = ManagedUgoiraJobs(tmp_path / 'owner', codec_python=sys.executable)
    try:
        owner.enqueue('existing-mp4', *archive(), output='mp4')
        job = terminal(owner)
        assert job.state == 'done' and job.result.startswith('mp4s/')
        assert calls == ['opencv']
    finally:
        owner.close()
    reopened = ManagedUgoiraJobs(tmp_path / 'owner', codec_python=sys.executable)
    try:
        assert reopened.jobs.snapshot()[0].state == 'done'
        assert reopened.jobs.snapshot()[0].result == job.result
        assert calls == ['opencv']
    finally:
        reopened.close()


def test_frozen_entry_dispatches_codec_without_bootstrap(tmp_path, monkeypatch):
    from pixivvault_nicegui import crash_entry, ugoira_mp4_worker, bootstrap
    calls = []
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'argv', ['pv.exe', '--internal-ugoira-codec', str(tmp_path)])
    monkeypatch.setattr(ugoira_mp4_worker, 'main', lambda path: calls.append(path))
    monkeypatch.setattr(bootstrap, 'main', lambda: pytest.fail('bootstrap must not run'))
    assert crash_entry.main() == 0
    assert calls == [str(tmp_path)]


@pytest.mark.parametrize('args,frozen', [
    (['--internal-ugoira-codec'], True),
    (['--internal-ugoira-codec', 'one', 'extra'], True),
    (['--internal-ugoira-codec', 'one'], False),
])
def test_invalid_internal_codec_invocation_never_bootstraps(monkeypatch, args, frozen):
    from pixivvault_nicegui import crash_entry, bootstrap
    monkeypatch.setattr(sys, 'frozen', frozen, raising=False)
    monkeypatch.setattr(sys, 'argv', ['pv.exe', *args])
    monkeypatch.setattr(bootstrap, 'main', lambda: pytest.fail('bootstrap must not run'))
    assert crash_entry.main() != 0


def test_frozen_entry_keeps_worker_token_and_stage_validation(tmp_path, monkeypatch):
    from pixivvault_nicegui import crash_entry, bootstrap
    (tmp_path / 'source.zip').write_bytes(b'synthetic')
    (tmp_path / 'plan.json').write_bytes(b'{}')
    (tmp_path / 'codec.log').write_bytes(b'')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'argv', ['pv.exe', '--internal-ugoira-codec', str(tmp_path)])
    monkeypatch.delenv('PIXIVVAULT_UGOIRA_CODEC_TOKEN', raising=False)
    monkeypatch.setattr(bootstrap, 'main', lambda: pytest.fail('bootstrap must not run'))
    assert crash_entry.main() != 0


def test_normal_entry_still_uses_bootstrap(monkeypatch):
    from pixivvault_nicegui import crash_entry, bootstrap, crash_evidence
    calls = []
    monkeypatch.setattr(sys, 'argv', ['pv.exe'])
    monkeypatch.setattr(crash_evidence, 'install', lambda: calls.append('diagnostics'))
    monkeypatch.setattr(bootstrap, 'main', lambda: calls.append('bootstrap') or 0)
    assert crash_entry.main() == 0
    assert calls == ['diagnostics', 'bootstrap']


def test_backend_reasons_survive_receipt_restart_and_user_notifications(tmp_path, monkeypatch):
    from pixivvault_nicegui.ui.shell import DemoShell
    import json
    calls = []
    def convert(*args, backend, **kwargs):
        raise UgoiraMp4Error(failure='unavailable' if backend == 'opencv' else 'failed')
    monkeypatch.setattr('pixivvault_nicegui.ugoira_mp4.render_mp4', convert)
    monkeypatch.setattr(ui, 'notify', lambda message, **kw: calls.append(message))
    owner = ManagedUgoiraJobs(tmp_path / 'owner', codec_python=sys.executable)
    try:
        owner.enqueue('distinct-errors', *archive(), output='mp4')
        job = terminal(owner)
        assert job.state == 'done'
        packet = json.loads((owner.root / job.result).read_text(encoding='utf-8'))
        assert packet['mp4_fallback'] == {'opencv': 'unavailable', 'imageio': 'failed'}
        message = queue_result(owner, job)
        assert 'OpenCV: 利用できません' in message and 'imageio: 変換に失敗' in message
        shell = DemoShell.__new__(DemoShell)
        shell.closed = False
        shell.services = SimpleNamespace(ugoira=owner)
        shell.client = ui.context.client
        shell._ugoira_notified = set()
        shell._notify_ugoira_fallback()
        assert calls == [message]
    finally:
        owner.close()
    owner = ManagedUgoiraJobs(tmp_path / 'owner', codec_python=sys.executable)
    try:
        assert owner.fallback_message(owner.jobs.snapshot()[0]) == message
    finally:
        owner.close()


@pytest.mark.parametrize('fail_reservation', [1, 2])
def test_codec_capacity_exhausted_can_still_save_gif(tmp_path, monkeypatch, fail_reservation):
    import pixivvault_nicegui.managed_ugoira as module
    def convert(*args, **kwargs): raise UgoiraMp4Error()
    monkeypatch.setattr('pixivvault_nicegui.ugoira_mp4.render_mp4', convert)
    owner = ManagedUgoiraJobs(tmp_path / 'owner', codec_python=sys.executable)
    original, seen = owner._budget, []
    def budget(files, amount):
        if files == 6:
            seen.append(True)
            if len(seen) == fail_reservation:
                with monkeypatch.context() as local:
                    local.setattr(module, 'MAX_CONTROL_BYTES', amount - 1)
                    return original(files, amount)
        return original(files, amount)
    monkeypatch.setattr(owner, '_budget', budget)
    try:
        owner.enqueue('capacity-fallback', *archive(), output='mp4')
        job = terminal(owner)
        assert job.state == 'done' and job.result.startswith('gifs/')
        assert len(seen) == fail_reservation
    finally:
        owner.close()


def test_capacity_fallback_still_rejects_unsafe_retained_entry(tmp_path, monkeypatch):
    import pixivvault_nicegui.managed_ugoira as module
    owner = ManagedUgoiraJobs(tmp_path / 'owner', codec_python=sys.executable)
    original = owner._budget
    def budget(files, amount):
        if files == 6:
            (owner.codec_incomplete / 'unrecognized-entry').mkdir()
            with monkeypatch.context() as local:
                local.setattr(module, 'MAX_CONTROL_BYTES', amount - 1)
                return original(files, amount)
        return original(files, amount)
    monkeypatch.setattr(owner, '_budget', budget)
    try:
        owner.enqueue('unsafe-capacity', *archive(), output='mp4')
        job = terminal(owner)
        assert job.state == 'error' and job.result is None
    finally:
        owner.close()


def test_mp4_publication_capacity_can_still_save_gif(tmp_path, monkeypatch):
    import pixivvault_nicegui.managed_ugoira as module
    def convert(raw, inspection, stage, codec_python, checkpoint, *, backend):
        return fake_mp4(raw, inspection, backend)
    monkeypatch.setattr('pixivvault_nicegui.ugoira_mp4.render_mp4', convert)
    owner = ManagedUgoiraJobs(tmp_path / 'owner', codec_python=sys.executable)
    original, attempts = owner._budget, []
    def budget(files, amount):
        if files == 4:
            attempts.append(True)
            with monkeypatch.context() as local:
                local.setattr(module, 'MAX_CONTROL_BYTES', amount - 1)
                return original(files, amount)
        return original(files, amount)
    monkeypatch.setattr(owner, '_budget', budget)
    try:
        owner.enqueue('mp4-publish-capacity', *archive(), output='mp4')
        job = terminal(owner)
        assert job.state == 'done' and job.result.startswith('gifs/')
        assert attempts == [True] and not tuple(owner.mp4s.iterdir())
    finally:
        owner.close()


@pytest.mark.parametrize('failure', ['permission', 'cancel'])
def test_mp4_publication_other_errors_do_not_fallback(tmp_path, monkeypatch, failure):
    def convert(raw, inspection, stage, codec_python, checkpoint, *, backend):
        return fake_mp4(raw, inspection, backend)
    monkeypatch.setattr('pixivvault_nicegui.ugoira_mp4.render_mp4', convert)
    owner = ManagedUgoiraJobs(tmp_path / 'owner', codec_python=sys.executable)
    original = owner._budget
    def budget(files, amount):
        if files == 4:
            if failure == 'permission': raise PermissionError('synthetic-secret')
            raise JobCancelled()
        return original(files, amount)
    monkeypatch.setattr(owner, '_budget', budget)
    try:
        owner.enqueue('mp4-publish-fault', *archive(), output='mp4')
        job = terminal(owner)
        assert job.state == ('error' if failure == 'permission' else 'stopped')
        assert job.result is None and not tuple(owner.gifs.iterdir())
        if failure == 'permission': assert job.error_code == 'permission_denied'
    finally:
        owner.close()


def test_unsupported_image_never_reserves_codec_stage(tmp_path):
    from test_q01_ugoira_reasons import archive as odd_archive
    owner = ManagedUgoiraJobs(tmp_path / 'owner', codec_python=sys.executable)
    try:
        owner.enqueue('odd-dimensions', *odd_archive(), output='mp4')
        job = terminal(owner)
        assert job.state == 'done' and job.result.startswith('gifs/')
        assert not tuple(owner.codec_incomplete.iterdir())
    finally:
        owner.close()


def test_worker_permission_exit_is_restored_by_parent(tmp_path, monkeypatch):
    from pixivvault_nicegui import ugoira_mp4 as mp4
    from test_q01_render_permission import plan_input
    class Process:
        returncode = 77
        def poll(self): return self.returncode
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **kw: Process())
    with pytest.raises(UgoiraMp4Error) as failure:
        mp4.render_mp4(b'synthetic', plan_input(), tmp_path, sys.executable, lambda: None)
    assert failure.value.reason_code == 'permission_denied'


def test_frozen_and_standalone_worker_permission_exit(monkeypatch):
    from pixivvault_nicegui import crash_entry, ugoira_mp4_worker as worker
    def deny(*args): raise PermissionError('synthetic-secret')
    monkeypatch.setattr(worker, 'main', deny)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'argv', ['pv.exe', '--internal-ugoira-codec', 'stage'])
    assert worker.run('stage') == 77
    assert crash_entry.main() == 77


def test_worker_missing_dependency_exit_is_restored_by_parent(tmp_path, monkeypatch):
    from pixivvault_nicegui import ugoira_mp4 as mp4
    from test_q01_render_permission import plan_input
    class Process:
        returncode = 78
        def poll(self): return self.returncode
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **kw: Process())
    with pytest.raises(UgoiraMp4Error) as failure:
        mp4.render_mp4(b'synthetic', plan_input(), tmp_path, sys.executable, lambda: None)
    assert failure.value.failure == 'unavailable'


def test_frozen_and_standalone_worker_missing_dependency_exit(monkeypatch):
    from pixivvault_nicegui import crash_entry, ugoira_mp4_worker as worker
    def unavailable(*args): raise ImportError('synthetic-secret')
    monkeypatch.setattr(worker, 'main', unavailable)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'argv', ['pv.exe', '--internal-ugoira-codec', 'stage'])
    assert worker.run('stage') == 78
    assert crash_entry.main() == 78


def test_nan_fps_is_rejected_by_receipt_verification(tmp_path, monkeypatch):
    def convert(raw, inspection, stage, codec_python, checkpoint, *, backend):
        data, report, decoded = fake_mp4(raw, inspection, backend)
        report['fps'] = float('nan')
        return data, report, decoded
    monkeypatch.setattr('pixivvault_nicegui.ugoira_mp4.render_mp4', convert)
    owner = ManagedUgoiraJobs(tmp_path / 'owner', codec_python=sys.executable)
    try:
        owner.enqueue('nan-fps', *archive(), output='mp4')
        job = terminal(owner)
        assert job.state == 'error'
    finally:
        owner.close()


def test_existing_nan_fps_receipt_is_rejected_on_restart(tmp_path, monkeypatch):
    import json
    from pixivvault_nicegui.managed_ugoira import _json, _mp4_completion
    from pixivvault_nicegui.ugoira_storage import COMPLETION
    def convert(raw, inspection, stage, codec_python, checkpoint, *, backend):
        return fake_mp4(raw, inspection, backend)
    monkeypatch.setattr('pixivvault_nicegui.ugoira_mp4.render_mp4', convert)
    root = tmp_path / 'owner'
    owner = ManagedUgoiraJobs(root, codec_python=sys.executable)
    try:
        owner.enqueue('existing-nan', *archive(), output='mp4')
        job = terminal(owner)
        assert job.state == 'done'
    finally:
        owner.close()
    receipt_path = root / job.result
    packet = json.loads(receipt_path.read_text(encoding='utf-8'))
    packet['report']['fps'] = float('nan')
    receipt = _json(packet)
    receipt_path.write_bytes(receipt)
    (receipt_path.parent / COMPLETION).write_bytes(_mp4_completion(receipt_path.parent.name, receipt))
    reopened = ManagedUgoiraJobs(root, codec_python=sys.executable)
    try:
        assert reopened.jobs.snapshot()[0].state != 'done'
    finally:
        reopened.close()


def test_opencv_worker_rejects_nan_fps(tmp_path, monkeypatch):
    from pixivvault_nicegui import ugoira_mp4_worker as worker
    from PIL import Image
    raw, _ = archive()
    plan = dict(width=2, height=2, fps=10, frames=[dict(file='000000.png', delay=100)],
                max_frame_bytes=8*1024*1024, max_archive_bytes=64*1024*1024)
    monkeypatch.setattr(worker, '_request', lambda folder: (tmp_path, plan, raw))
    class Frame:
        shape = (2, 2, 3)
        def mean(self, axis): return (1, 2, 3)
    class Writer:
        def isOpened(self): return True
        def write(self, frame): pass
        def release(self): pass
    class Capture:
        def __init__(self): self.first = True
        def isOpened(self): return True
        def get(self, prop): return float('nan')
        def read(self):
            if self.first:
                self.first = False
                return True, Frame()
            return False, None
        def release(self): pass
    cv2 = SimpleNamespace(setNumThreads=lambda n: None, ocl=SimpleNamespace(setUseOpenCL=lambda v: None),
        VideoWriter=lambda *a: Writer(), VideoWriter_fourcc=lambda *a: 1,
        VideoCapture=lambda *a: Capture(), CAP_PROP_FPS=1, COLOR_RGBA2BGR=1, COLOR_BGR2RGB=2,
        cvtColor=lambda frame, conversion: frame)
    monkeypatch.setitem(sys.modules, 'cv2', cv2)
    monkeypatch.setitem(sys.modules, 'numpy', SimpleNamespace(array=lambda image: Frame()))
    monkeypatch.setattr(Image, 'fromarray', lambda frame: Image.new('RGB', (2, 2), (1, 2, 3)))
    with pytest.raises(ValueError): worker.main(str(tmp_path))
