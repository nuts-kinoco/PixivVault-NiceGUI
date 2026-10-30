"""Bound v1 ancillary records and per-job diagnostic isolation; no deletion."""
import json,sqlite3
from types import SimpleNamespace
import pytest
from nicegui import Client,ui
from pixivvault_nicegui import managed_ugoira as subject
from pixivvault_nicegui.ui.job_views import QueueView
from pixivvault_nicegui.fs_paths import native_io_path
from test_managed_ugoira import _fixture,_wait,_tree


@pytest.mark.parametrize('attempt',[1,2])
def test_authentic_v1_retired_diagnostic_is_preserved_but_never_completion(tmp_path,monkeypatch,attempt):
    raw,manifest=_fixture();root=tmp_path/'owned';owner=subject.ManagedUgoiraJobs(root);render=subject.render_ugoira_gif
    def fail(*a,**kw):raise OSError('synthetic converter failure')
    monkeypatch.setattr(subject,'render_ugoira_gif',fail)
    try:
        first=owner.enqueue('first',raw,manifest,output='gif');_wait(owner,first.job_id,'error')
        if attempt==2:
            assert owner.jobs.control(first.job_id,'retry');assert _wait(owner,first.job_id,'error').attempt==2
        diagnostic=owner.diagnostics/first.job_id/(first.job_id+'-'+str(attempt)+'.io-error.json')
        legacy=owner.requests/first.target_id/diagnostic.name;legacy.write_bytes(diagnostic.read_bytes());before=_tree(legacy.parent)
        assert owner.jobs.control(first.job_id,'delete');owner.close()
        with sqlite3.connect(native_io_path(root/'jobs.sqlite3')) as db:
            db.execute('DROP TABLE job_history');db.execute('PRAGMA user_version=1')
        monkeypatch.setattr(subject,'render_ugoira_gif',render);owner=subject.ManagedUgoiraJobs(root)
        assert owner.jobs.retained_job(first.job_id) is None
        assert owner._load(first.target_id)[2]=='gif' and _tree(legacy.parent)==before
        assert owner.jobs.snapshot()==()  # diagnostic never manufactures done
        later=owner.enqueue('explicit-new-operation',raw,manifest,output='gif')
        assert _wait(owner,later.job_id,'done').attempt==1
        assert _tree(legacy.parent)==before
        forged=dict(json.loads(legacy.read_bytes()),job_id='f'*32)
        unknown=legacy.parent/('f'*32+'-'+str(attempt)+'.io-error.json');unknown.write_text(json.dumps(forged))
        with pytest.raises(ValueError):owner._load(first.target_id)
        assert unknown.is_file() and legacy.read_bytes()==diagnostic.read_bytes()
    finally:owner.close()


def test_one_bad_diagnostic_is_reported_after_restart_and_other_job_still_records(tmp_path,monkeypatch):
    raw,manifest=_fixture();root=tmp_path/'owned';owner=subject.ManagedUgoiraJobs(root);client=None
    def fail(*a,**kw):raise OSError('synthetic converter failure')
    monkeypatch.setattr(subject,'render_ugoira_gif',fail)
    try:
        first=owner.enqueue('first',raw,manifest,output='gif');_wait(owner,first.job_id,'error')
        bad=owner.diagnostics/first.job_id/(first.job_id+'-1.io-error.json');bad.write_bytes(b'{PRIVATE-SYNTHETIC-BROKEN')
        owner.close();owner=subject.ManagedUgoiraJobs(root)
        assert first.job_id in owner.diagnostic_errors
        assert owner.diagnostic_errors[first.job_id].startswith('invalid_record:')
        later=owner.enqueue('later',raw,manifest,output='gif');_wait(owner,later.job_id,'error')
        output=owner.diagnostics/later.job_id/(later.job_id+'-1.io-error.json')
        assert json.loads(output.read_bytes())['job_id']==later.job_id
        assert later.job_id not in owner.diagnostic_errors and bad.read_bytes()==b'{PRIVATE-SYNTHETIC-BROKEN'
        client=Client(ui.page('/_diagnostic_status_unit'))
        with client:
            view=QueueView(SimpleNamespace(artworks=(),jobs=owner.jobs,save_exports=None,ugoira=owner))
            assert first.job_id in view.export_notice.text and '形式または受付対応' in view.export_notice.text
            assert 'PRIVATE-SYNTHETIC' not in view.export_notice.text
        assert subject.MAX_CONTROL_GENERATIONS==256 and subject.MAX_CONTROL_FILES==1024 and subject.MAX_CONTROL_BYTES==256*1024*1024
    finally:
        if client is not None and client.id in Client.instances:client.delete()
        owner.close()
