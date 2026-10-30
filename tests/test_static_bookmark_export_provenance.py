"""A managed request ledger is not evidence that an artwork is bookmarked."""
from hashlib import sha256
from pathlib import Path

from pixivvault_nicegui.managed_download_requests import ManagedDownloadRequests
from pixivvault_nicegui.save_exports import SaveExports
from test_save_exports import fixture, settled


def file_proofs(root):
    return {path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()
            for path in Path(root).rglob('*') if path.is_file()}


def install_real_request_store(exports, remote, managed):
    requests = ManagedDownloadRequests(managed.root)
    for work_id, record in remote.records.items():
        requests.store('manual-owned-' + work_id, record)
    exports.s._managed_requests = requests
    return requests


def test_manual_completed_requests_are_not_automatically_bookmark_exports(tmp_path):
    remote, managed, exports = fixture(tmp_path, auto='0')
    requests = install_real_request_store(exports, remote, managed)
    exports.s.bookmark_download_candidates = lambda: ()
    before_sources = file_proofs(managed.root)
    before_requests = file_proofs(requests.folder)
    calls = remote.remote_requests
    try:
        # These real generated receipts have completed jobs and durable v1
        # request records, but no bookmark source ever admitted their IDs.
        assert requests.folder.is_dir() and before_requests
        assert all(record.visibility in ('show', 'hide') for record in remote.records.values())
        exports.scan()
        assert not exports.jobs.snapshot()
        assert not tuple(exports.root.glob('*.export.json'))
        assert file_proofs(managed.root) == before_sources
        assert file_proofs(requests.folder) == before_requests
        assert remote.remote_requests == calls
    finally:
        exports.close()
        managed.close()


def test_already_accepted_bookmark_projection_survives_cache_change_and_restart(
        tmp_path, monkeypatch):
    remote, managed, exports = fixture(tmp_path, auto='0')
    requests = install_real_request_store(exports, remote, managed)
    chosen = next(iter(remote.records))
    original = exports._run_static_projection

    def fail_selected(identity, manifest, context):
        if identity['work_id'] == chosen:
            raise OSError('synthetic export failure before publication')
        return original(identity, manifest, context)

    monkeypatch.setattr(exports, '_run_static_projection', fail_selected)
    calls = remote.remote_requests
    try:
        exports.scan()
        initial = settled(exports)
        assert initial and all(job.kind == 'bookmark-projection' for job in initial)
        failed = next(job for job in initial if job.state == 'error')
        assert sum(job.state == 'error' for job in initial) == 1
        assert any(job.state == 'done' for job in initial)
        inputs = {path.name: path.read_bytes() for path in exports.root.glob('*.json')}
        projected = file_proofs(exports.projection.root)
        originals = file_proofs(managed.root)
        request_bytes = file_proofs(requests.folder)
        service, root = exports.s, exports.root
        service.bookmark_download_candidates = lambda: ()
        exports.close()
        exports = SaveExports(service, root, '12')
        assert [(job.job_id, job.state, job.target_id) for job in exports.jobs.snapshot()] == [
            (job.job_id, job.state, job.target_id) for job in initial]
        exports.scan()
        assert {job.job_id for job in exports.jobs.snapshot()} == {job.job_id for job in initial}
        # The previously accepted export keeps its exact durable source and
        # operation. Explicit Retry may finish it without a fresh bookmark GET.
        restored = next(job for job in exports.jobs.snapshot() if job.job_id == failed.job_id)
        assert exports.jobs.control(restored.job_id, 'retry',
                                    expected_control_revision=restored.control_revision)
        result = settled(exports)
        assert all(job.state == 'done' for job in result)
        retried = next(job for job in result if job.job_id == failed.job_id)
        assert retried.target_id == failed.target_id and retried.attempt == failed.attempt + 1
        assert all((exports.root / name).read_bytes() == data for name, data in inputs.items())
        after = file_proofs(exports.projection.root)
        assert all(after[path] == proof for path, proof in projected.items())
        assert file_proofs(managed.root) == originals
        assert file_proofs(requests.folder) == request_bytes
        assert remote.remote_requests == calls
    finally:
        exports.close()
        managed.close()

