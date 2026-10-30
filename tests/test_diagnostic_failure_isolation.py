"""Ancillary corruption is rejected for diagnostics, not adopted as media input."""
import json,os
from pathlib import Path
from types import SimpleNamespace
import pytest
from pixivvault_nicegui import managed_ugoira as subject
from test_managed_ugoira import _fixture,_wait,_tree


def failed_owner(tmp_path,monkeypatch):
    raw,manifest=_fixture();owner=subject.ManagedUgoiraJobs(tmp_path/'owned');render=subject.render_ugoira_gif
    def fail(*a,**kw):raise OSError('synthetic converter error')
    monkeypatch.setattr(subject,'render_ugoira_gif',fail)
    first=owner.enqueue('first',raw,manifest,output='gif');_wait(owner,first.job_id,'error')
    monkeypatch.setattr(subject,'render_ugoira_gif',render)
    diagnostic=owner.diagnostics/first.job_id/(first.job_id+'-1.io-error.json')
    return owner,first,diagnostic,raw,manifest


@pytest.mark.parametrize('bad',['malformed','unbound','hardlink','symlink','reparse','folder-job-mismatch'])
def test_auxiliary_corruption_does_not_block_unrelated_gif_or_frames(tmp_path,monkeypatch,bad):
    owner,first,diagnostic,raw,manifest=failed_owner(tmp_path,monkeypatch);hits=[]
    try:
        if bad=='malformed':diagnostic.write_bytes(b'{broken')
        elif bad=='unbound':
            folder=owner.diagnostics/('f'*32);folder.mkdir()
            value=json.loads(diagnostic.read_bytes());value['job_id']='f'*32
            (folder/('f'*32+'-1.io-error.json')).write_text(json.dumps(value))
        elif bad in ('hardlink','symlink'):
            alias=diagnostic.parent/(first.job_id+'-2.io-error.json')
            if bad=='hardlink':os.link(diagnostic,alias)
            else:
                try:alias.symlink_to(diagnostic)
                except OSError:pytest.skip('existing Windows symlink privilege unavailable')
        elif bad=='folder-job-mismatch':
            second=owner.enqueue('second-before-corruption',raw,manifest,output='gif');_wait(owner,second.job_id,'done')
            value=json.loads(diagnostic.read_bytes());value['job_id']=second.job_id
            (diagnostic.parent/(second.job_id+'-1.io-error.json')).write_text(json.dumps(value))
        else:
            original=Path.lstat
            class Changed:
                def __init__(self,value):self.value=value
                def __getattr__(self,key):return 0x400 if key=='st_file_attributes' else getattr(self.value,key)
            def reparse(path,*args,**kwargs):
                value=original(path,*args,**kwargs)
                if path==diagnostic:hits.append(path);return Changed(value)
                return value
            monkeypatch.setattr(Path,'lstat',reparse)
        before=_tree(owner.diagnostics)
        with pytest.raises(ValueError):owner._diagnostics_writable()
        if bad=='reparse':assert hits
        for output in ('frames','gif'):
            job=owner.enqueue('unrelated-'+output,raw,manifest,output=output)
            assert _wait(owner,job.job_id,'done').state=='done'
        assert _tree(owner.diagnostics)==before
        assert {p.name for p in (owner.requests/first.target_id).iterdir()}=={'source.zip','intent.json'}
    finally:owner.close()


@pytest.mark.parametrize('bad',['duplicate-key','oversize','attempt-mismatch','attempt-excess','errno-text','errno-bool','winerror-text','winerror-bool','job-name'])
def test_strict_legacy_diagnostic_boundaries_remain_rejected(tmp_path,monkeypatch,bad):
    owner,first,diagnostic,_,_=failed_owner(tmp_path,monkeypatch)
    try:
        value=json.loads(diagnostic.read_bytes());name=diagnostic.name
        if bad=='attempt-mismatch':value['attempt']=2
        elif bad=='attempt-excess':value['attempt']=2;name=first.job_id+'-2.io-error.json'
        elif bad=='errno-text':value['errno']='2'
        elif bad=='errno-bool':value['errno']=True
        elif bad=='winerror-text':value['winerror']='3'
        elif bad=='winerror-bool':value['winerror']=False
        elif bad=='job-name':value['job_id']='f'*32
        raw=json.dumps(value).encode()
        if bad=='duplicate-key':raw=b'{"attempt":1,'+raw[1:]
        if bad=='oversize':raw+=b' '*(4097-len(raw))
        path=owner.requests/first.target_id/name;path.write_bytes(raw);before=_tree(path.parent)
        with pytest.raises(ValueError):owner._load(first.target_id)
        assert _tree(path.parent)==before
    finally:owner.close()


def test_retry_diagnostics_remain_bounded_at_the_unchanged_combined_quota(tmp_path,monkeypatch):
    owner,first,diagnostic,_,_=failed_owner(tmp_path,monkeypatch)
    # This reproduces the still-open capacity allocation decision, rather than
    # quietly increasing limits or deleting retained diagnostics to pass it.
    try:
        current=sum(1 for parent in (owner.requests,owner.diagnostics) for p in parent.rglob('*') if p.is_file())
        monkeypatch.setattr(subject,'MAX_CONTROL_FILES',current)
        before=_tree(owner.diagnostics)
        with pytest.raises(subject.ManagedUgoiraError):owner._budget(1,0)
        for attempt in range(2,4):
            def fail(*a,**kw):raise OSError('synthetic recurring conversion error')
            monkeypatch.setattr(subject,'render_ugoira_gif',fail)
            assert owner.jobs.control(first.job_id,'retry')
            row=_wait(owner,first.job_id,'error');assert row.attempt==attempt
        assert _tree(owner.diagnostics)==before and diagnostic.is_file()
    finally:owner.close()


def test_retired_diagnostic_cannot_claim_a_known_later_attempt(tmp_path,monkeypatch):
    owner,first,diagnostic,_,_=failed_owner(tmp_path,monkeypatch)
    try:
        assert owner.jobs.control(first.job_id,'delete')
        assert owner.jobs.retained_job(first.job_id).attempt==1
        value=json.loads(diagnostic.read_bytes());value['attempt']=2
        name=first.job_id+'-2.io-error.json'
        target=owner.requests/first.target_id/name;target.write_text(json.dumps(value))
        before=_tree(target.parent)
        with pytest.raises(ValueError):owner._load(first.target_id)
        assert _tree(target.parent)==before
    finally:owner.close()
