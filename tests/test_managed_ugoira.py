"""Synthetic persisted JobService/ugoira lifecycle and publication boundaries."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from pathlib import Path
from threading import Event
import zipfile

from PIL import Image
import pytest

from pixivvault_nicegui import managed_ugoira as subject
from pixivvault_nicegui import ugoira_storage as storage
from pixivvault_nicegui.job_service import JobCancelled, JobContext, JobService, JobStorageError
from pixivvault_nicegui.media_parity_policy import parse_ugoira_manifest


def _fixture():
    output = BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for index, color in enumerate(('red', 'blue')):
            payload = BytesIO()
            with Image.new('RGB', (4, 3), color) as image:
                image.save(payload, format='PNG')
            archive.writestr(f'{index:06}.png', payload.getvalue())
    manifest = parse_ugoira_manifest({'originalSrc': 'https://i.pximg.net/img-zip-ugoira/offline/123_ugoira.zip',
                                      'frames': [{'file': '000000.png', 'delay': 50},
                                                 {'file': '000001.png', 'delay': 180}]}, work_id='123')
    return output.getvalue(), manifest


def _wait(owner, job_id, state):
    with owner.jobs._condition:
        assert owner.jobs._condition.wait_for(lambda: any(
            row.job_id == job_id and row.state == state for row in owner.jobs.snapshot()), timeout=12)
    return next(row for row in owner.jobs.snapshot() if row.job_id == job_id)


def _tree(folder):
    return {path.relative_to(folder).as_posix(): path.read_bytes()
            for path in folder.rglob('*') if path.is_file() and not path.name.endswith('.lock')}


@pytest.mark.parametrize('output', ['frames', 'gif'])
def test_managed_done_requires_typed_complete_receipt_and_persists_on_restart(tmp_path, output):
    raw, manifest = _fixture()
    root = tmp_path / 'managed'
    owner = subject.ManagedUgoiraJobs(root)
    try:
        accepted = owner.enqueue('operation-1', raw, manifest, output=output)
        done = _wait(owner, accepted.job_id, 'done')
        assert done.done == done.total == 1
        assert (root / done.result).is_file()
        assert len(list(owner.requests.iterdir())) == 1
        assert manifest.archive_url.encode() not in (next(owner.requests.iterdir()) / 'intent.json').read_bytes()
        candidate = subject._candidate(done)
        assert owner.frames.reconcile(f'frames/{candidate}/receipt.json').state == 'complete'
        if output == 'gif':
            receipt = json.loads((root / done.result).read_bytes())
            assert receipt['encoded_delays_ms'] == [50, 180]
            assert receipt['timing'] == 'gif-centiseconds-floor'
            assert (root / done.result).parent.joinpath('completion.json').is_file()
        before = _tree(root / 'media')
    finally:
        owner.close()
    reopened = subject.ManagedUgoiraJobs(root)
    try:
        restored, = reopened.jobs.snapshot()
        assert restored.state == 'done' and restored.result == done.result
        again = reopened.enqueue('operation-1', raw, manifest, output=output)
        assert again.job_id == accepted.job_id and again.state == 'done'
        assert _tree(root / 'media') == before
        assert reopened.jobs._thread is None
    finally:
        reopened.close()


def test_damaged_done_receipt_is_demoted_durably_and_retry_uses_a_new_attempt(tmp_path):
    raw, manifest = _fixture()
    root = tmp_path / 'managed'
    owner = subject.ManagedUgoiraJobs(root)
    accepted = owner.enqueue('operation-1', raw, manifest)
    done = _wait(owner, accepted.job_id, 'done')
    old_folder = (root / done.result).parent
    owner.close()
    (old_folder / '000000.png').write_bytes(b'synthetic corruption')
    before = _tree(old_folder)
    reopened = subject.ManagedUgoiraJobs(root)
    try:
        invalid, = reopened.jobs.snapshot()
        assert invalid.state == 'error' and invalid.error_code == 'receipt_invalid' and invalid.result is None
        assert reopened.jobs.control(invalid.job_id, 'retry')
        retry = _wait(reopened, invalid.job_id, 'done')
        assert retry.attempt == 2 and retry.result != done.result
        assert _tree(old_folder) == before
    finally:
        reopened.close()


def test_transient_recovery_io_does_not_persist_receipt_invalid_or_expose_done_result(tmp_path, monkeypatch):
    raw, manifest = _fixture()
    root = tmp_path / 'managed'
    owner = subject.ManagedUgoiraJobs(root)
    accepted = owner.enqueue('operation-io', raw, manifest)
    done = _wait(owner, accepted.job_id, 'done')
    owner.close()

    def temporary_io(self, *args, **kwargs):
        raise storage.UgoiraStorageIOError()

    monkeypatch.setattr(storage.UgoiraFrameStore, 'reconcile', temporary_io)
    with pytest.raises(subject.ManagedUgoiraError):
        subject.ManagedUgoiraJobs(root)

    # Recovery failed closed: the public managed owner was not returned, and
    # the durable JobService row remains done rather than being rewritten error.
    persisted = JobService(lambda job, context: None, root / 'jobs.sqlite3')
    try:
        row, = persisted.snapshot()
        assert row.job_id == done.job_id
        assert row.state == 'done' and row.result == done.result
        assert row.error_code == ''
    finally:
        persisted.close()

@pytest.mark.parametrize('output', ['frames', 'gif'])
def test_interrupted_verified_publication_reconciles_without_duplicate_outputs(tmp_path, output):
    raw, manifest = _fixture()
    root = tmp_path / 'managed'
    owner = subject.ManagedUgoiraJobs(root)
    accepted = owner.enqueue('operation-1', raw, manifest, output=output)
    done = _wait(owner, accepted.job_id, 'done')
    before_media, before_gifs = _tree(root / 'media'), _tree(root / 'gifs')
    with owner.jobs._condition:
        owner.jobs._replace(owner.jobs._jobs[done.job_id], state='running', result=None, done=0, total=0)
    owner.close()
    reopened = subject.ManagedUgoiraJobs(root)
    try:
        restored, = reopened.jobs.snapshot()
        assert restored.state == 'done' and restored.result == done.result
        assert _tree(root / 'media') == before_media
        assert _tree(root / 'gifs') == before_gifs
        assert reopened.jobs._thread is None
    finally:
        reopened.close()


@pytest.mark.parametrize('boundary', ['before-publish', 'after-proof'])
def test_explicit_stop_never_revives_from_existing_output_on_restart(tmp_path, monkeypatch, boundary):
    raw, manifest = _fixture()
    root = tmp_path / 'managed'
    owner = subject.ManagedUgoiraJobs(root)
    reached, release = Event(), Event()
    if boundary == 'before-publish':
        publish = storage._publish_directory
        def paused(stage, target):
            if stage.parent == owner.frames.incomplete:
                reached.set()
                assert release.wait(12)
            publish(stage, target)
        monkeypatch.setattr(storage, '_publish_directory', paused)
    else:
        save = owner.frames.save
        def paused(*args, **kwargs):
            result = save(*args, **kwargs)
            reached.set()
            assert release.wait(12)
            return result
        monkeypatch.setattr(owner.frames, 'save', paused)
    try:
        accepted = owner.enqueue('operation-1', raw, manifest)
        assert reached.wait(12)
        assert owner.jobs.control(accepted.job_id, 'stop')
        release.set()
        stopped = _wait(owner, accepted.job_id, 'stopped')
        assert stopped.result is None
    finally:
        release.set()
        owner.close()
    reopened = subject.ManagedUgoiraJobs(root)
    try:
        restored, = reopened.jobs.snapshot()
        assert restored.state == 'stopped' and restored.result is None
        if boundary == 'after-proof':
            assert reopened.frames.reconcile(f'frames/{subject._candidate(restored)}/receipt.json').state == 'complete'
    finally:
        reopened.close()


def test_converter_failure_is_error_not_done_and_retry_can_complete(tmp_path, monkeypatch):
    raw, manifest = _fixture()
    owner = subject.ManagedUgoiraJobs(tmp_path / 'managed')
    render = subject.render_ugoira_gif
    def fail(*args, **kwargs):
        raise OSError('synthetic-private-codec-secret')
    monkeypatch.setattr(subject, 'render_ugoira_gif', fail)
    try:
        accepted = owner.enqueue('operation-1', raw, manifest, output='gif')
        failed = _wait(owner, accepted.job_id, 'error')
        assert failed.result is None and failed.error_code == 'runner_failed'
        assert list(owner.gifs.iterdir()) == []
        monkeypatch.setattr(subject, 'render_ugoira_gif', render)
        assert owner.jobs.control(failed.job_id, 'retry')
        assert _wait(owner, failed.job_id, 'done').attempt == 2
    finally:
        owner.close()


def test_sqlite_terminal_commit_failure_never_publishes_done_and_restart_reconciles(tmp_path, monkeypatch):
    raw, manifest = _fixture()
    root = tmp_path / 'managed'
    owner = subject.ManagedUgoiraJobs(root)
    write = owner.jobs._store.write
    def fail_done(jobs, operations):
        if any(row.state == 'done' for row in jobs.values()):
            raise JobStorageError('synthetic terminal commit failure')
        write(jobs, operations)
    monkeypatch.setattr(owner.jobs._store, 'write', fail_done)
    try:
        owner.enqueue('operation-1', raw, manifest)
        with owner.jobs._condition:
            assert owner.jobs._condition.wait_for(lambda: owner.jobs.faulted, timeout=12)
        assert all(row.state != 'done' for row in owner.jobs.snapshot())
    finally:
        monkeypatch.setattr(owner.jobs._store, 'write', write)
        owner.close()
    reopened = subject.ManagedUgoiraJobs(root)
    try:
        row, = reopened.jobs.snapshot()
        assert row.state == 'done' and (root / row.result).is_file()
    finally:
        reopened.close()


def test_unsealed_publication_never_reconciles_as_done(tmp_path, monkeypatch):
    raw, manifest = _fixture()
    root = tmp_path / 'managed'
    owner = subject.ManagedUgoiraJobs(root)
    fsync = storage._fsync_dir
    def fail(folder):
        if folder == owner.frames.frames:
            raise OSError('synthetic pre-proof fault')
        fsync(folder)
    monkeypatch.setattr(storage, '_fsync_dir', fail)
    try:
        accepted = owner.enqueue('operation-1', raw, manifest)
        failed = _wait(owner, accepted.job_id, 'error')
        with owner.jobs._condition:
            owner.jobs._replace(owner.jobs._jobs[failed.job_id], state='running', result=None)
    finally:
        monkeypatch.setattr(storage, '_fsync_dir', fsync)
        owner.close()
    reopened = subject.ManagedUgoiraJobs(root)
    try:
        restored, = reopened.jobs.snapshot()
        assert restored.state == 'error' and restored.error_code == 'interrupted' and restored.result is None
        assert len(list(reopened.frames.frames.iterdir())) == 1
    finally:
        reopened.close()


def test_mp4_is_explicitly_pending_and_never_silently_falls_back(tmp_path):
    raw, manifest = _fixture()
    owner = subject.ManagedUgoiraJobs(tmp_path / 'managed')
    try:
        with pytest.raises(subject.ManagedUgoiraError) as caught:
            owner.enqueue('operation-1', raw, manifest, output='mp4')
        assert caught.value.code == 'mp4_environment_unverified'
        assert owner.jobs.snapshot() == () and list(owner.requests.iterdir()) == []
    finally:
        owner.close()


def test_invalid_zip_never_reaches_durable_input_or_managed_completion(tmp_path):
    _, manifest = _fixture()
    owner = subject.ManagedUgoiraJobs(tmp_path / 'managed')
    try:
        with pytest.raises(subject.ManagedUgoiraError):
            owner.enqueue('operation-1', b'not a zip', manifest)
        assert owner.jobs.snapshot() == () and list(owner.requests.iterdir()) == []
        assert not owner.frames.root.exists()
    finally:
        owner.close()


def test_control_storage_budget_refuses_before_creating_input_stage(tmp_path, monkeypatch):
    raw, manifest = _fixture()
    owner = subject.ManagedUgoiraJobs(tmp_path / 'managed')
    monkeypatch.setattr(subject, 'MAX_CONTROL_BYTES', 1)
    try:
        with pytest.raises(subject.ManagedUgoiraError):
            owner.enqueue('operation-1', raw, manifest)
        assert owner.jobs.snapshot() == ()
        assert list(owner.requests.iterdir()) == []
        assert list(owner.request_incomplete.iterdir()) == []
    finally:
        owner.close()


def test_foreign_root_is_not_adopted(tmp_path):
    root = tmp_path / 'foreign'
    root.mkdir()
    sentinel = root / 'synthetic-old.db'
    sentinel.write_bytes(b'synthetic retained database')
    with pytest.raises(subject.ManagedUgoiraError):
        subject.ManagedUgoiraJobs(root)
    assert sentinel.read_bytes() == b'synthetic retained database'
    assert set(path.name for path in root.iterdir()) == {'synthetic-old.db'}


def test_pause_resume_uses_existing_job_context_before_gif_work(tmp_path, monkeypatch):
    raw, manifest = _fixture()
    owner = subject.ManagedUgoiraJobs(tmp_path / 'managed')
    reached, release, checked = Event(), Event(), Event()
    render = subject.render_ugoira_gif
    def paused(raw, manifest, *, checkpoint):
        reached.set()
        assert release.wait(12)
        checked.set()
        checkpoint()
        return render(raw, manifest, checkpoint=checkpoint)
    monkeypatch.setattr(subject, 'render_ugoira_gif', paused)
    try:
        accepted = owner.enqueue('operation-1', raw, manifest, output='gif')
        assert reached.wait(12)
        assert owner.jobs.control(accepted.job_id, 'pause')
        release.set()
        assert checked.wait(12)
        paused_row = next(row for row in owner.jobs.snapshot() if row.job_id == accepted.job_id)
        assert paused_row.state == 'paused' and paused_row.result is None
        assert owner.jobs.control(accepted.job_id, 'resume')
        assert _wait(owner, accepted.job_id, 'done').result.startswith('gifs/')
    finally:
        release.set()
        owner.close()


def test_completed_integrity_gate_is_compare_and_set_and_never_revives_stopped(tmp_path):
    raw, manifest = _fixture()
    owner = subject.ManagedUgoiraJobs(tmp_path / 'managed')
    try:
        accepted = owner.enqueue('operation-1', raw, manifest)
        done = _wait(owner, accepted.job_id, 'done')
        assert not owner.jobs.reconcile_completed(done.job_id, expected_result='stale', valid=False)
        assert next(row for row in owner.jobs.snapshot() if row.job_id == done.job_id).state == 'done'
        assert owner.jobs.reconcile_completed(done.job_id, expected_result=done.result, valid=False)
        failed = next(row for row in owner.jobs.snapshot() if row.job_id == done.job_id)
        assert failed.state == 'error' and failed.result is None
        assert not owner.jobs.reconcile_completed(done.job_id, expected_result=done.result, valid=True)
    finally:
        owner.close()


def test_second_controller_is_refused_without_releasing_first_owner(tmp_path):
    raw, manifest = _fixture()
    root = tmp_path / 'managed'
    owner = subject.ManagedUgoiraJobs(root)
    try:
        with pytest.raises(subject.ManagedUgoiraError):
            subject.ManagedUgoiraJobs(root)
        accepted = owner.enqueue('operation-1', raw, manifest)
        assert _wait(owner, accepted.job_id, 'done').result.startswith('media/frames/')
    finally:
        owner.close()


def test_corrupt_accepted_input_never_reuses_a_previously_done_output(tmp_path):
    raw, manifest = _fixture()
    root = tmp_path / 'managed'
    owner = subject.ManagedUgoiraJobs(root)
    accepted = owner.enqueue('operation-1', raw, manifest)
    _wait(owner, accepted.job_id, 'done')
    request, = owner.requests.iterdir()
    owner.close()
    (request / 'source.zip').write_bytes(b'synthetic corrupt accepted input')
    reopened = subject.ManagedUgoiraJobs(root)
    try:
        row, = reopened.jobs.snapshot()
        assert row.state == 'error' and row.error_code == 'receipt_invalid' and row.result is None
    finally:
        reopened.close()


def test_storage_checkpoint_cancel_preserves_stage_and_can_retry_stable_candidate(tmp_path):
    raw, manifest = _fixture()
    store = storage.UgoiraFrameStore(tmp_path / 'frames-store')
    calls = []
    def stop_before_first_frame():
        calls.append(1)
        if len(calls) == 4:
            raise JobCancelled('synthetic cooperative cancel')
    token = 'c' * 32
    with pytest.raises(JobCancelled):
        store.save(raw, manifest, generation_token=token, checkpoint=stop_before_first_frame)
    before = {path.name for path in store.incomplete.iterdir()}
    assert len(before) == 1 and list(store.frames.iterdir()) == []
    saved = store.save(raw, manifest, generation_token=token)
    assert saved.relative_receipt == f'frames/{token}/receipt.json'
    assert store.reconcile(saved.relative_receipt).state == 'complete'
    assert {path.name for path in store.incomplete.iterdir()} == before


def test_close_wakes_paused_gif_publication_before_waiting_for_acceptance(tmp_path, monkeypatch):
    raw, manifest = _fixture()
    owner = subject.ManagedUgoiraJobs(tmp_path / 'managed')
    reached, release = Event(), Event()
    budget = owner._budget
    def paused_publication(incoming_files, incoming_bytes):
        if incoming_files == 3:
            reached.set()
            assert release.wait(12)
        return budget(incoming_files, incoming_bytes)
    monkeypatch.setattr(owner, '_budget', paused_publication)
    accepted = None
    try:
        accepted = owner.enqueue('operation-1', raw, manifest, output='gif')
        assert reached.wait(12)
        assert owner.jobs.control(accepted.job_id, 'pause')
        release.set()
        with ThreadPoolExecutor(max_workers=1) as pool:
            closing = pool.submit(owner.close)
            try:
                closing.result(timeout=12)
            finally:
                # Release a regressed implementation instead of leaving an
                # unbounded test thread waiting on a paused JobContext.
                owner.jobs.control(accepted.job_id, 'resume')
        assert owner.jobs.closed
        assert all(row.state != 'done' for row in owner.jobs.snapshot())
    finally:
        release.set()
        if accepted is not None:
            owner.jobs.control(accepted.job_id, 'resume')
        owner.close()


@pytest.mark.parametrize('action', ['resume', 'stop'])
def test_paused_gif_publication_does_not_block_other_enqueue_or_control(tmp_path, monkeypatch, action):
    raw, manifest = _fixture()
    owner = subject.ManagedUgoiraJobs(tmp_path / 'managed')
    reached, release, waiting = Event(), Event(), Event()
    original_budget = owner._budget
    original_checkpoint = JobContext.checkpoint
    pool = ThreadPoolExecutor(max_workers=1)
    accepted = None

    def gate(incoming_files, incoming_bytes):
        if incoming_files == 3:
            reached.set()
            assert release.wait(12)
        return original_budget(incoming_files, incoming_bytes)

    def observe(context):
        if context._service is owner.jobs:
            with owner.jobs._condition:
                row = owner.jobs._jobs.get(context._job_id)
                if row is not None and row.state == 'paused':
                    waiting.set()
        return original_checkpoint(context)

    monkeypatch.setattr(owner, '_budget', gate)
    monkeypatch.setattr(JobContext, 'checkpoint', observe)
    try:
        accepted = owner.enqueue('gif-operation', raw, manifest, output='gif')
        assert reached.wait(12)
        assert owner.jobs.control(accepted.job_id, 'pause')
        release.set()
        assert waiting.wait(12)
        # On v1 this waits for _accept held by the paused worker. The
        # cleanup resumes it even if the assertion times out.
        incoming = pool.submit(owner.enqueue, 'frames-operation', raw, manifest)
        other = incoming.result(timeout=6)
        assert other.job_id != accepted.job_id and other.state == 'queued'
        assert owner.jobs.control(accepted.job_id, action)
        _wait(owner, accepted.job_id, 'done' if action == 'resume' else 'stopped')
        assert _wait(owner, other.job_id, 'done').result.startswith('media/frames/')
    finally:
        release.set()
        if accepted is not None:
            owner.jobs.control(accepted.job_id, 'resume')
        pool.shutdown(wait=True)
        owner.close()


def test_enqueue_failure_retains_unaccepted_bounded_cache_and_retry_reuses_it(tmp_path, monkeypatch):
    raw, manifest = _fixture()
    root = tmp_path / 'managed'
    owner = subject.ManagedUgoiraJobs(root)
    enqueue = owner.jobs.enqueue

    def fail(*args, **kwargs):
        raise JobStorageError('synthetic-private-ledger-fault')

    try:
        monkeypatch.setattr(owner.jobs, 'enqueue', fail)
        with pytest.raises(subject.ManagedUgoiraError) as caught:
            owner.enqueue('same-operation', raw, manifest)
        assert str(caught.value) == 'managed_ugoira_failed'
        assert owner.jobs.snapshot() == ()
        retained = _tree(owner.requests)
        assert len(list(owner.requests.iterdir())) == 1
        assert not owner.frames.root.exists()
    finally:
        owner.close()
    # Reopen never promotes a cached request without a durable job record.
    reopened = subject.ManagedUgoiraJobs(root)
    try:
        assert reopened.jobs.snapshot() == () and reopened.jobs._thread is None
        accepted = reopened.enqueue('same-operation', raw, manifest)
        assert _wait(reopened, accepted.job_id, 'done').result.startswith('media/')
        assert _tree(reopened.requests) == retained
        assert list(reopened.request_incomplete.iterdir()) == []
    finally:
        reopened.close()


def test_failed_gif_retry_reuses_exact_completed_frames_without_saving_again(tmp_path, monkeypatch):
    raw, manifest = _fixture()
    root = tmp_path / 'managed'
    owner = subject.ManagedUgoiraJobs(root)
    render = subject.render_ugoira_gif

    def fail(*args, **kwargs):
        raise ValueError('synthetic conversion failure')

    monkeypatch.setattr(subject, 'render_ugoira_gif', fail)
    try:
        accepted = owner.enqueue('gif-operation', raw, manifest, output='gif')
        failed = _wait(owner, accepted.job_id, 'error')
        source_token = subject._candidate(failed)
        before = _tree(owner.frames.root)
    finally:
        owner.close()
    monkeypatch.setattr(subject, 'render_ugoira_gif', render)
    reopened = subject.ManagedUgoiraJobs(root)

    def forbidden(*args, **kwargs):
        pytest.fail('retry regenerated already verified frames')

    monkeypatch.setattr(reopened.frames, 'save', forbidden)
    try:
        assert reopened.jobs.control(failed.job_id, 'retry')
        done = _wait(reopened, failed.job_id, 'done')
        assert done.attempt == 2
        receipt = json.loads((root / done.result).read_bytes())
        assert receipt['source_receipt'] == f'frames/{source_token}/receipt.json'
        assert _tree(reopened.frames.root) == before
        assert len(list(reopened.frames.frames.iterdir())) == 1
    finally:
        reopened.close()


def test_paused_empty_stage_reservation_prevents_concurrent_quota_overspend(tmp_path, monkeypatch):
    raw, manifest = _fixture()
    owner = subject.ManagedUgoiraJobs(tmp_path / 'managed')
    reached, release = Event(), Event()
    write = subject._write

    def gate(path, data):
        if path.name == 'animation.gif':
            reached.set()
            assert release.wait(12)
        return write(path, data)

    monkeypatch.setattr(subject, '_write', gate)
    try:
        accepted = owner.enqueue('gif-operation', raw, manifest, output='gif')
        assert reached.wait(12)
        with owner._artifact:
            stage, = owner._reservations
            files, planned_bytes = owner._reservations[stage]
            assert files == 3 and list(stage.iterdir()) == []
            actual = sum(path.stat().st_size for path in owner.requests.rglob('*') if path.is_file())
            monkeypatch.setattr(subject, 'MAX_CONTROL_BYTES', actual + planned_bytes)
        with pytest.raises(subject.ManagedUgoiraError):
            owner.enqueue('other-operation', raw, manifest, output='frames')
        assert len(owner.jobs.snapshot()) == 1
        assert list(owner.request_incomplete.iterdir()) == []
        release.set()
        assert _wait(owner, accepted.job_id, 'done').result.startswith('gifs/')
        assert owner._reservations == {}
    finally:
        release.set()
        owner.close()


def test_reconcile_cancels_between_frame_hashes_without_mutating_or_leaking_lock(tmp_path, monkeypatch):
    raw, manifest = _fixture()
    store = storage.UgoiraFrameStore(tmp_path / 'media')
    saved = store.save(raw, manifest)
    before = _tree(store.root)
    read = storage._read_bounded
    frame_read = Event()
    reads = []

    def observed(path, limit):
        result = read(path, limit)
        if path.suffix == '.png':
            reads.append(path.name)
            frame_read.set()
        return result

    def stop():
        if frame_read.is_set():
            raise JobCancelled('synthetic stop during scan')

    monkeypatch.setattr(storage, '_read_bounded', observed)
    with pytest.raises(JobCancelled):
        store.reconcile(saved.relative_receipt, checkpoint=stop)
    assert reads == ['000000.png']
    assert _tree(store.root) == before
    assert storage.UgoiraFrameStore(store.root).reconcile(saved.relative_receipt).state == 'complete'


def test_reconcile_cancels_while_waiting_for_same_instance_lock(tmp_path):
    raw, manifest = _fixture()
    store = storage.UgoiraFrameStore(tmp_path / 'media')
    saved = store.save(raw, manifest)
    calls = []

    def stop():
        calls.append(1)
        if len(calls) == 2:
            raise JobCancelled('synthetic cancel while lock busy')

    with store._lock:
        with pytest.raises(JobCancelled):
            store.reconcile(saved.relative_receipt, checkpoint=stop)
    assert len(calls) == 2
    assert store.reconcile(saved.relative_receipt).state == 'complete'


def test_managed_inventory_scan_propagates_cancel_before_next_generation(tmp_path):
    raw, manifest = _fixture()
    owner = subject.ManagedUgoiraJobs(tmp_path / 'managed')
    try:
        row = owner.enqueue('frames-operation', raw, manifest)
        done = _wait(owner, row.job_id, 'done')
        inspection = subject.inspect_ugoira_archive(raw, manifest)
        calls = []

        def stop():
            calls.append(1)
            # First check is before inventory, second is per entry.
            if len(calls) == 2:
                raise JobCancelled('synthetic scan cancel')

        with pytest.raises(JobCancelled):
            owner._find_source(done, inspection, stop)
        assert len(calls) == 2
    finally:
        owner.close()


def test_source_reuse_does_not_turn_busy_reconcile_into_a_missing_result(tmp_path, monkeypatch):
    raw, manifest = _fixture()
    owner = subject.ManagedUgoiraJobs(tmp_path / 'managed')
    try:
        row = owner.enqueue('frames-operation', raw, manifest)
        done = _wait(owner, row.job_id, 'done')
        inspection = subject.inspect_ugoira_archive(raw, manifest)

        def busy(*args, **kwargs):
            raise storage.UgoiraStorageError()

        monkeypatch.setattr(owner.frames, 'reconcile', busy)
        with pytest.raises(storage.UgoiraStorageError):
            owner._find_source(done, inspection, lambda: None)
    finally:
        owner.close()

def test_post_proof_frame_failure_retry_reuses_completed_source_and_restores_exact_reference(tmp_path, monkeypatch):
    raw, manifest = _fixture()
    root = tmp_path / 'managed'
    owner = subject.ManagedUgoiraJobs(root)
    save = owner.frames.save

    def fail_after_proof(*args, **kwargs):
        save(*args, **kwargs)
        raise OSError('synthetic lost acknowledgement')

    monkeypatch.setattr(owner.frames, 'save', fail_after_proof)
    try:
        accepted = owner.enqueue('operation-1', raw, manifest)
        failed = _wait(owner, accepted.job_id, 'error')
        old_token = subject._candidate(failed)
        before = _tree(owner.frames.root)

        def forbidden(*args, **kwargs):
            pytest.fail('completed frames regenerated after lost acknowledgement')

        monkeypatch.setattr(owner.frames, 'save', forbidden)
        assert owner.jobs.control(failed.job_id, 'retry')
        done = _wait(owner, failed.job_id, 'done')
        assert done.attempt == 2 and subject._candidate(done) != old_token
        assert done.result == f'media/frames/{old_token}/receipt.json'
        assert _tree(owner.frames.root) == before
    finally:
        owner.close()
    reopened = subject.ManagedUgoiraJobs(root)
    try:
        restored, = reopened.jobs.snapshot()
        assert restored.state == 'done' and restored.result == done.result
        assert _tree(reopened.frames.root) == before
    finally:
        reopened.close()


def test_failed_acceptance_caches_are_bounded_and_existing_cache_can_be_retried_at_capacity(tmp_path, monkeypatch):
    raw, manifest = _fixture()
    owner = subject.ManagedUgoiraJobs(tmp_path / 'managed')
    enqueue = owner.jobs.enqueue

    def fail(*args, **kwargs):
        raise JobStorageError('synthetic acceptance failure')

    monkeypatch.setattr(owner.jobs, 'enqueue', fail)
    monkeypatch.setattr(subject, 'MAX_CONTROL_GENERATIONS', 1)
    try:
        with pytest.raises(subject.ManagedUgoiraError):
            owner.enqueue('operation-1', raw, manifest)
        before = _tree(owner.requests)
        # A distinct intent cannot reserve another generation.
        with pytest.raises(subject.ManagedUgoiraError):
            owner.enqueue('operation-2', raw, manifest, output='gif')
        assert _tree(owner.requests) == before
        assert list(owner.request_incomplete.iterdir()) == []
        assert owner.jobs.snapshot() == ()
        monkeypatch.setattr(owner.jobs, 'enqueue', enqueue)
        accepted = owner.enqueue('operation-1', raw, manifest)
        assert _wait(owner, accepted.job_id, 'done').result.startswith('media/')
        assert _tree(owner.requests) == before
    finally:
        owner.close()


def test_cancelled_gif_stage_releases_only_reservation_and_retains_actual_bytes(tmp_path, monkeypatch):
    raw, manifest = _fixture()
    owner = subject.ManagedUgoiraJobs(tmp_path / 'managed')
    reached, release = Event(), Event()
    write = subject._write

    def gate(path, data):
        result = write(path, data)
        if path.name == 'receipt.json' and path.parent.parent == owner.gif_incomplete:
            reached.set()
            assert release.wait(12)
        return result

    monkeypatch.setattr(subject, '_write', gate)
    try:
        accepted = owner.enqueue('gif-operation', raw, manifest, output='gif')
        assert reached.wait(12)
        assert owner.jobs.control(accepted.job_id, 'stop')
        release.set()
        _wait(owner, accepted.job_id, 'stopped')
        assert owner._reservations == {}
        stage, = owner.gif_incomplete.iterdir()
        assert set(path.name for path in stage.iterdir()) == {'animation.gif', 'receipt.json'}
        before = _tree(stage)
        assert list(owner.gifs.iterdir()) == []
        actual = sum(path.stat().st_size for parent in (
            owner.requests, owner.request_incomplete, owner.gifs, owner.gif_incomplete)
            for path in parent.rglob('*') if path.is_file())
        monkeypatch.setattr(subject, 'MAX_CONTROL_BYTES', actual)
        with pytest.raises(subject.ManagedUgoiraError):
            owner.enqueue('frames-operation', raw, manifest)
        assert _tree(stage) == before
    finally:
        release.set()
        owner.close()

def test_recovery_coordinator_cancel_does_not_demote_verified_done_job(tmp_path):
    raw, manifest = _fixture()
    owner = subject.ManagedUgoiraJobs(tmp_path / 'managed')
    try:
        accepted = owner.enqueue('frames-operation', raw, manifest)
        done = _wait(owner, accepted.job_id, 'done')
        before = _tree(owner.frames.root)
        calls = []

        def stop():
            calls.append(1)
            if len(calls) == 2:
                raise JobCancelled('synthetic recovery coordinator cancel')

        with pytest.raises(JobCancelled):
            owner._recover(checkpoint=stop)
        restored, = owner.jobs.snapshot()
        assert restored.state == 'done' and restored.result == done.result
        assert _tree(owner.frames.root) == before
    finally:
        owner.close()