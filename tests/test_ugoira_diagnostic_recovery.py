"""Self diagnostics cannot poison accepted ZIP/intent or completed frames."""
import json
from pathlib import Path
import pytest
from pixivvault_nicegui import managed_ugoira as subject
from test_managed_ugoira import _fixture,_wait,_tree


@pytest.mark.parametrize('output,fault',[('gif','render'),('gif','post-proof'),('frames','post-proof')])
def test_failure_retry_reuses_frames_and_keeps_diagnostics_out_of_input(tmp_path,monkeypatch,output,fault):
    raw,manifest=_fixture();owner=subject.ManagedUgoiraJobs(tmp_path/'owned')
    render,save=subject.render_ugoira_gif,owner.frames.save;fired=[]
    def broken_render(*a,**kw):
        fired.append('render');raise OSError('synthetic private diagnostic detail')
    def broken_save(*a,**kw):
        result=save(*a,**kw);fired.append('post-proof');raise OSError('synthetic lost acknowledgement')
    monkeypatch.setattr(subject,'render_ugoira_gif',broken_render if fault=='render' else render)
    monkeypatch.setattr(owner.frames,'save',broken_save if fault=='post-proof' else save)
    try:
        accepted=owner.enqueue('first',raw,manifest,output=output);failed=_wait(owner,accepted.job_id,'error')
        assert fired==[fault]
        folder=owner.requests/failed.target_id
        assert {p.name for p in folder.iterdir()}=={'source.zip','intent.json'}
        before=_tree(folder);frames_before=_tree(owner.frames.root)
        diagnostic=owner.diagnostics/failed.job_id/(failed.job_id+'-1.io-error.json')
        packet=diagnostic.read_bytes();value=json.loads(packet)
        assert value['operation']==('render_ugoira_gif' if fault=='render' else 'save_frame_generation')
        assert value['attempt']==1 and b'private' not in packet
        def forbidden(*a,**kw):pytest.fail('verified frame generation was regenerated')
        monkeypatch.setattr(owner.frames,'save',forbidden)
        monkeypatch.setattr(subject,'render_ugoira_gif',render)
        assert owner.jobs.control(failed.job_id,'retry')
        done=_wait(owner,failed.job_id,'done')
        assert done.attempt==2 and _tree(folder)==before and _tree(owner.frames.root)==frames_before
        assert diagnostic.read_bytes()==packet
    finally:owner.close()


def test_old_input_diagnostic_is_strictly_verified_and_preserved_on_retry_restart(tmp_path,monkeypatch):
    raw,manifest=_fixture();root=tmp_path/'owned';owner=subject.ManagedUgoiraJobs(root)
    render=subject.render_ugoira_gif
    def fail(*a,**kw):raise OSError('synthetic')
    monkeypatch.setattr(subject,'render_ugoira_gif',fail)
    try:
        job=owner.enqueue('legacy',raw,manifest,output='gif');failed=_wait(owner,job.job_id,'error')
        folder=owner.requests/job.target_id
        packet=json.loads((owner.diagnostics/job.job_id/(job.job_id+'-1.io-error.json')).read_bytes())
        packet['operation']='save_frame_generation'  # original buggy writer's phase
        old=folder/(job.job_id+'-1.io-error.json');old.write_text(json.dumps(packet))
        before=_tree(folder)
        owner.close();monkeypatch.setattr(subject,'render_ugoira_gif',render)
        owner=subject.ManagedUgoiraJobs(root)
        assert owner.jobs.control(job.job_id,'retry')
        assert _wait(owner,job.job_id,'done').attempt==2
        assert _tree(owner.requests/job.target_id)==before
        owner.close();owner=subject.ManagedUgoiraJobs(root)
        assert owner.jobs.snapshot()[0].state=='done' and _tree(owner.requests/job.target_id)==before
    finally:owner.close()


@pytest.mark.parametrize('bad',['unknown-name','extra-schema','unaccepted-job','bool-attempt','unknown-operation'])
def test_unknown_or_unbound_diagnostic_is_not_an_input_exception(tmp_path,monkeypatch,bad):
    raw,manifest=_fixture();owner=subject.ManagedUgoiraJobs(tmp_path/'owned')
    def fail(*a,**kw):raise OSError('synthetic')
    monkeypatch.setattr(subject,'render_ugoira_gif',fail)
    try:
        job=owner.enqueue('strict',raw,manifest,output='gif');_wait(owner,job.job_id,'error')
        folder=owner.requests/job.target_id
        value=json.loads((owner.diagnostics/job.job_id/(job.job_id+'-1.io-error.json')).read_bytes())
        name=job.job_id+'-1.io-error.json'
        if bad=='unknown-name':name='unknown.json'
        elif bad=='extra-schema':value['source']='not accepted'
        elif bad=='unaccepted-job':value['job_id']='f'*32;name='f'*32+'-1.io-error.json'
        elif bad=='bool-attempt':value['attempt']=True
        else:value['operation']='unrecognized_operation'
        target=folder/name;target.write_text(json.dumps(value));before=_tree(folder)
        with pytest.raises((subject.ManagedUgoiraError,ValueError)):owner._load(job.target_id)
        assert _tree(folder)==before
    finally:owner.close()


def test_diagnostic_quota_failure_does_not_replace_original_error_or_poison_retry(tmp_path,monkeypatch):
    raw,manifest=_fixture();owner=subject.ManagedUgoiraJobs(tmp_path/'owned');save=owner.frames.save;fired=[]
    def lost(*a,**kw):
        save(*a,**kw);fired.append(True);raise OSError('synthetic lost acknowledgement')
    monkeypatch.setattr(subject,'MAX_CONTROL_FILES',2)
    monkeypatch.setattr(owner.frames,'save',lost)
    try:
        job=owner.enqueue('quota',raw,manifest);failed=_wait(owner,job.job_id,'error')
        assert fired==[True] and failed.error_code=='runner_failed'
        assert list(owner.diagnostics.iterdir())==[]
        assert {p.name for p in (owner.requests/job.target_id).iterdir()}=={'source.zip','intent.json'}
        def forbidden(*a,**kw):pytest.fail('sealed frames must be reused')
        monkeypatch.setattr(owner.frames,'save',forbidden)
        assert owner.jobs.control(job.job_id,'retry')
        assert _wait(owner,job.job_id,'done').attempt==2
    finally:owner.close()


def test_partial_diagnostic_write_is_preserved_as_stage_and_does_not_poison_retry(tmp_path,monkeypatch):
    raw,manifest=_fixture();root=tmp_path/'owned';owner=subject.ManagedUgoiraJobs(root)
    render,write=subject.render_ugoira_gif,subject._write;fired=[]
    def fail_render(*a,**kw):raise OSError('synthetic converter error')
    def partial(path,data):
        if path.parent.parent==owner.diagnostics and path.name.startswith('stage-'):
            fired.append(path);write(path,data[:5]);raise OSError('synthetic diagnostic interruption')
        return write(path,data)
    monkeypatch.setattr(subject,'render_ugoira_gif',fail_render)
    monkeypatch.setattr(subject,'_write',partial)
    try:
        job=owner.enqueue('partial-diagnostic',raw,manifest,output='gif');failed=_wait(owner,job.job_id,'error')
        assert len(fired)==1 and fired[0].read_bytes() and len(fired[0].read_bytes())==5
        before=_tree(owner.requests/job.target_id);stage=fired[0];retained=stage.read_bytes()
        monkeypatch.setattr(subject,'render_ugoira_gif',render);monkeypatch.setattr(subject,'_write',write)
        assert owner.jobs.control(job.job_id,'retry')
        assert _wait(owner,job.job_id,'done').attempt==2
        assert stage.read_bytes()==retained and _tree(owner.requests/job.target_id)==before
        owner.close();owner=subject.ManagedUgoiraJobs(root)
        assert owner.jobs.snapshot()[0].state=='done' and stage.read_bytes()==retained
    finally:owner.close()


def test_legacy_diagnostic_hardlink_is_not_adopted(tmp_path,monkeypatch):
    import os
    raw,manifest=_fixture();owner=subject.ManagedUgoiraJobs(tmp_path/'owned')
    def fail(*a,**kw):raise OSError('synthetic')
    monkeypatch.setattr(subject,'render_ugoira_gif',fail)
    try:
        job=owner.enqueue('hardlink',raw,manifest,output='gif');_wait(owner,job.job_id,'error')
        diagnostic=owner.diagnostics/job.job_id/(job.job_id+'-1.io-error.json')
        target=owner.requests/job.target_id/diagnostic.name
        os.link(diagnostic,target)
        before=_tree(owner.requests/job.target_id)
        with pytest.raises(ValueError):owner._load(job.target_id)
        assert _tree(owner.requests/job.target_id)==before
    finally:owner.close()


def test_unknown_json_in_diagnostic_namespace_is_preserved_but_rejected_for_new_writes(tmp_path,monkeypatch):
    raw,manifest=_fixture();owner=subject.ManagedUgoiraJobs(tmp_path/'owned')
    def fail(*a,**kw):raise OSError('synthetic')
    monkeypatch.setattr(subject,'render_ugoira_gif',fail)
    try:
        job=owner.enqueue('unknown-aux',raw,manifest,output='gif');_wait(owner,job.job_id,'error')
        unknown=owner.diagnostics/job.job_id/'unknown.json';unknown.write_bytes(b'preserve')
        # Auxiliary corruption is refused by its writer, not by media accounting.
        owner._budget(0,0)
        with pytest.raises(subject.ManagedUgoiraError):owner._diagnostics_writable()
        assert unknown.read_bytes()==b'preserve'
        # It is not an input: loading canonical accepted ZIP/intent is separate.
        assert owner._load(job.target_id)[2]=='gif'
    finally:owner.close()
