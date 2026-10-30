"""P2 generated archives only; no real Cookie, API, or GPU."""
from pathlib import Path
import os
import sys
from threading import Event
import pytest
from pixivvault_nicegui.ugoira_fixtures import FixtureUgoiraReader
from pixivvault_nicegui.ugoira_practical import PinnedUgoiraSource, PracticalUgoiraJobs
from pixivvault_nicegui.managed_ugoira import ManagedUgoiraError
CODEC=Path(os.environ.get('PV_TEST_CODEC_PYTHON',sys.executable))

def wait(owner,jid,state):
    with owner.jobs._condition:
        assert owner.jobs._condition.wait_for(lambda: any(j.job_id==jid and j.state==state for j in owner.jobs.snapshot()),20),owner.jobs.snapshot()
    return next(j for j in owner.jobs.snapshot() if j.job_id==jid)

@pytest.mark.parametrize('fmt',['frames','gif','mp4'])
def test_selection_acquisition_receipt_playback_restart(tmp_path,fmt):
    if fmt=='mp4' and 'PV_TEST_CODEC_PYTHON' not in os.environ:
        pytest.skip('explicit existing CPU OpenCV runtime not configured')
    reader=FixtureUgoiraReader()
    root=tmp_path/'jobs'
    owner=PracticalUgoiraJobs(root,PinnedUgoiraSource(reader),codec_python=CODEC)
    try:
        selection=owner.preview('123','91',fmt)
        assert not any(c[0]=='archive' for c in reader.calls)
        job=owner.enqueue_selection(selection,'one')
        wait(owner,job.job_id,'done')
        assert owner.enqueue_selection(selection,'one').job_id==job.job_id
        entry=owner.library()[0]
        playback=owner.playback(entry)
        assert [f['delay'] for f in playback['frames']]==([100,100,100] if fmt=='mp4' else [40,90,170])
        assert playback['duration_ms']==300
        from PIL import Image
        from io import BytesIO
        import base64
        pixel=Image.open(BytesIO(base64.b64decode(playback['frames'][0]['src'].split(',')[1]))).convert('RGB').getpixel((0,0))
        assert pixel[0]>240 and pixel[1:] == (0,0)
        if fmt=='mp4': assert pixel[0]<255  # decoded mp4v, not source PNG
        with pytest.raises(ManagedUgoiraError): owner.enqueue_selection(selection,'stale')
        if fmt=='mp4':
            import json
            packet=json.loads((root/entry['proof']['receipt']).read_bytes())
            report=packet['report']
            assert report['codec']=='mp4v' and report['frame_count']==3
            assert report['encoded_duration_ms']==pytest.approx(300,abs=1)
            assert report['fps']==pytest.approx(10)
            assert report['mean_rgb'][0][0]>240 and report['mean_rgb'][2][2]>240
    finally: owner.close()
    fresh=FixtureUgoiraReader()
    owner=PracticalUgoiraJobs(root,PinnedUgoiraSource(fresh),codec_python=CODEC)
    try:
        assert owner.library()==(entry,)
        assert owner.playback(entry)==playback
        assert fresh.calls==[]
    finally: owner.close()


def test_acquisition_failure_retry_retains_operation(tmp_path):
    class Reader(FixtureUgoiraReader):
        fail=True
        def get_ugoira_archive(self,manifest):
            if self.fail:
                self.fail=False
                raise OSError('synthetic once')
            return super().get_ugoira_archive(manifest)
    owner=PracticalUgoiraJobs(tmp_path/'jobs',PinnedUgoiraSource(Reader()))
    try:
        job=owner.enqueue_selection(owner.preview('123','91','gif'),'retry')
        failed=wait(owner,job.job_id,'error')
        assert not owner.library()
        assert owner.jobs.control(job.job_id,'retry',expected_control_revision=failed.control_revision)
        assert wait(owner,job.job_id,'done').attempt==2
        assert owner.jobs.operation_ids(job.job_id)==('retry',)
    finally: owner.close()


def test_stop_during_archive_never_becomes_saved(tmp_path):
    entered,release=Event(),Event()
    class Reader(FixtureUgoiraReader):
        def get_ugoira_archive(self,manifest):
            entered.set()
            assert release.wait(10)
            return super().get_ugoira_archive(manifest)
    owner=PracticalUgoiraJobs(tmp_path/'jobs',PinnedUgoiraSource(Reader()))
    try:
        job=owner.enqueue_selection(owner.preview('123','91','gif'),'stop')
        assert entered.wait(10)
        assert owner.jobs.control(job.job_id,'stop')
        release.set()
        wait(owner,job.job_id,'stopped')
        assert owner.library()==()
    finally:
        release.set(); owner.close()
def test_practical_update_cas_and_frame_corruption_fail_closed(tmp_path):
    owner=PracticalUgoiraJobs(tmp_path/'jobs',PinnedUgoiraSource(FixtureUgoiraReader()))
    try:
        original=owner.preview('123','91','frames')
        wait(owner,owner.enqueue_selection(original,'first').job_id,'done')
        old=owner.library()[0]
        replacement=owner.preview('123','91','frames')
        wait(owner,owner.enqueue_selection(replacement,'second').job_id,'done')
        new=owner.library()[0]
        assert new['proof']['job_id']!=old['proof']['job_id']
        with pytest.raises(ManagedUgoiraError):owner.enqueue_selection(replacement,'stale-update')
        with pytest.raises(ManagedUgoiraError):owner.playback(old)
        folder=(owner.root/new['proof']['receipt']).parent
        (folder/'000000.png').write_bytes(b'corrupt')
        with pytest.raises(Exception):owner.playback(new)
    finally:owner.close()


def test_mp4_unavailable_never_silently_becomes_gif(tmp_path):
    owner=PracticalUgoiraJobs(tmp_path/'jobs',PinnedUgoiraSource(FixtureUgoiraReader()))
    try:
        with pytest.raises(ManagedUgoiraError,match='mp4_environment_unverified'):owner.preview('123','91','mp4')
        assert owner.jobs.snapshot()==() and owner.library()==()
    finally:owner.close()


def test_changed_manifest_between_preview_and_execution_is_error(tmp_path):
    class Reader(FixtureUgoiraReader):
        changed=False
        def get_artwork_metadata_raw(self,wid,aid):
            body=super().get_artwork_metadata_raw(wid,aid)
            if self.changed:body['title']='changed'
            return body
    reader=Reader()
    owner=PracticalUgoiraJobs(tmp_path/'jobs',PinnedUgoiraSource(reader))
    try:
        selection=owner.preview('123','91','gif')
        reader.changed=True
        wait(owner,owner.enqueue_selection(selection,'changed').job_id,'error')
        assert not any(c[0]=='archive' for c in reader.calls)
        assert owner.library()==()
    finally:owner.close()


def test_bootstrap_ugoira_modes_are_explicit_and_isolated(tmp_path):
    from pixivvault_nicegui.bootstrap import parse_args
    config=parse_args(['--ugoira-fixture-root',str(tmp_path/'data')])
    assert config.ugoira_fixture_root==tmp_path/'data'
    for args in ([ '--ugoira-root',str(tmp_path/'data') ],
                 [ '--ugoira-fixture-root',str(tmp_path/'data'),'--pixiv-cookie-file',str(tmp_path/'fake') ],
                 [ '--ugoira-fixture-root',str(tmp_path/'data'),'--download-sandbox',str(tmp_path/'other') ]):
        with pytest.raises(SystemExit):parse_args(args)
    assert not (tmp_path/'data').exists()

@pytest.mark.parametrize('fmt,delays,expected',[
    ('frames',[7,19,31],[7,19,31]),
    ('gif',[7,19,31],[0,10,30]),
    ('mp4',[7,19,31],[19,19,19]),
    ('mp4',[1,2,3],[1000/60]*3),
    ('mp4',[1000,2000,3000],[1000]*3),
])
def test_saved_playback_uses_encoded_timeline(tmp_path,fmt,delays,expected):
    if fmt=='mp4' and 'PV_TEST_CODEC_PYTHON' not in os.environ:
        pytest.skip('explicit existing CPU OpenCV runtime not configured')
    from pixivvault_nicegui.media_parity_policy import parse_ugoira_manifest
    class Reader(FixtureUgoiraReader):
        def get_ugoira_manifest(self,wid):
            return parse_ugoira_manifest(dict(originalSrc=f'https://i.pximg.net/img-zip-ugoira/offline/{wid}_ugoira.zip',
                frames=[dict(file=f'{i:06d}.png',delay=d) for i,d in enumerate(delays)]),work_id=wid)
    owner=PracticalUgoiraJobs(tmp_path/'jobs',PinnedUgoiraSource(Reader()),codec_python=CODEC)
    try:
        wait(owner,owner.enqueue_selection(owner.preview('123','91',fmt),'timing').job_id,'done')
        entry=owner.library()[0]
        playback=owner.playback(entry)
        assert [f['delay'] for f in playback['frames']]==pytest.approx(expected,rel=.001)
        assert playback['source_duration_ms']==sum(delays)
        assert playback['duration_ms']==pytest.approx(sum(expected),rel=.001)
        if fmt=='mp4':
            folder=(owner.root/entry['proof']['receipt']).parent
            (folder/'decoded_frames.zip').write_bytes(b'corrupt')
            with pytest.raises(Exception): owner.playback(entry)
    finally: owner.close()


@pytest.mark.parametrize('delete_history', [False, True])
def test_saved_ugoira_survives_retirement_deletion_and_restart(tmp_path,delete_history):
    root=tmp_path/'jobs';reader=FixtureUgoiraReader()
    owner=PracticalUgoiraJobs(root,PinnedUgoiraSource(reader),codec_python=CODEC)
    try:
        job=owner.enqueue_selection(owner.preview('123','91','frames'),'saved-operation')
        completed=wait(owner,job.job_id,'done');entry=owner.library()[0]
        if delete_history:
            assert owner.jobs.control(job.job_id,'delete',expected_control_revision=completed.control_revision)
        else:
            for index in range(50):
                filler=owner.jobs.enqueue('ugoira-gif',job.target_id,operation_id='fixture-'+str(index))
                wait(owner,filler.job_id,'error')
        assert all(j.job_id!=job.job_id for j in owner.jobs.snapshot())
        assert owner.library()==(entry,) and owner.current('123','frames')==entry
        before=owner.playback(entry)
    finally:owner.close()
    reader=FixtureUgoiraReader();owner=PracticalUgoiraJobs(root,PinnedUgoiraSource(reader),codec_python=CODEC)
    try:
        assert owner.library()==(entry,) and owner.playback(entry)==before
        assert reader.calls==[]
    finally:owner.close()
