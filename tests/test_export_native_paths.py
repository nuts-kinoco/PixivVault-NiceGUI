"""CoS regressions at real deep paths, never by shortening the fixture root."""
from pathlib import Path
import os,json
from types import SimpleNamespace
from pixivvault_nicegui.fs_paths import native_io_path,logical_path,record_io_failure
from pixivvault_nicegui.ugoira_fixtures import FixtureUgoiraReader
from pixivvault_nicegui.ugoira_practical import PracticalUgoiraJobs,PinnedUgoiraSource


def test_native_spelling_round_trip_and_extended_prefix_are_idempotent(tmp_path):
    native=native_io_path(tmp_path)
    assert native_io_path(native)==native
    assert logical_path(native)==tmp_path.absolute()
    if os.name=='nt':assert str(native).startswith(chr(92)*2+'?'+chr(92))


def test_deep_pointer_is_budgeted_and_reopened_as_actual_file(tmp_path):
    root=tmp_path
    while len(str(root))<200:root=root/'owned-segment'
    # Keep the same physical deep location, use native spelling only for IO.
    native_io_path(root.parent).mkdir(parents=True,exist_ok=True)
    source=PinnedUgoiraSource(FixtureUgoiraReader())
    owner=PracticalUgoiraJobs(root,source)
    try:
        selection=owner.preview('123','91','frames')
        job=owner.enqueue_selection(selection,'native-path-fixture')
        with owner.jobs._condition:
            assert owner.jobs._condition.wait_for(lambda:any(j.job_id==job.job_id and j.state in ('done','error') for j in owner.jobs.snapshot()),15)
        saved=next(j for j in owner.jobs.snapshot() if j.job_id==job.job_id)
        assert saved.state=='done',saved
        pointer=owner.requests/job.target_id/'source-pointer.json'
        assert len(str(logical_path(pointer)))>=260
        assert pointer.is_file()
        owner._budget(1,1)  # Must stat all retained files, including the long pointer.
    finally:owner.close()
    owner=PracticalUgoiraJobs(root,PinnedUgoiraSource(FixtureUgoiraReader()))
    try:
        assert owner.jobs.snapshot()[0].state=='done'
        owner._budget(1,1)
    finally:owner.close()


def test_diagnostic_preserves_operation_type_winerror_without_path(tmp_path):
    error=OSError(2,'fixture message with private filename')
    error.winerror=3
    job=SimpleNamespace(job_id='a'*32,attempt=2)
    record_io_failure(tmp_path,job,error,'projection_body_mkdir')
    record=json.loads((tmp_path/(job.job_id+'-2.io-error.json')).read_bytes())
    assert record['operation']=='projection_body_mkdir'
    assert record['error_type']=='FileNotFoundError' and record['winerror']==3
    assert 'private filename' not in json.dumps(record)
