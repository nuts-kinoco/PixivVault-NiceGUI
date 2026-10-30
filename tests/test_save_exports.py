"""CoS synthetic post-save exports; no API, Cookie, GUI or original data."""
from types import SimpleNamespace
import json,os,time
import pytest
from test_legacy_zip_export import writer,Context
from pixivvault_nicegui.save_exports import SaveExports


def fixture(tmp_path, *, auto='1',mode='direct',override=None):
    remote,managed=writer(tmp_path)
    settings={'zip_all_after_download':auto,'bookmark_download_mode':mode}
    service=SimpleNamespace(managed_downloads=managed,
        preferences=SimpleNamespace(get_setting=lambda key,default=None:settings.get(key,default)),
        author_media=SimpleNamespace(author_zip_setting=lambda aid:override),
        static_jobs=SimpleNamespace(snapshot=lambda:tuple(SimpleNamespace(target_id=wid,state='done',job_id=wid) for wid in remote.records)),
        _managed_job_records=dict(remote.records),bookmark_download_candidates=lambda:tuple(remote.records.values()))
    return remote,managed,SaveExports(service,tmp_path/'exports','12')


def settled(exports):
    with exports.jobs._condition:
        assert exports.jobs._condition.wait_for(lambda:all(j.state in ('done','error','stopped') for j in exports.jobs.snapshot()),10)
    return exports.jobs.snapshot()


def test_completed_receipt_exports_deduplicate_and_restart_without_get(tmp_path):
    remote,managed,exports=fixture(tmp_path)
    before=managed.saved_receipts();calls=remote.remote_requests
    try:
        exports.scan();jobs=settled(exports)
        assert len(jobs)==5 and all(j.state=='done' for j in jobs)
        exports.scan();assert len(exports.jobs.snapshot())==5
        assert managed.saved_receipts()==before and remote.remote_requests==calls
        ids=[j.job_id for j in jobs];service=exports.s;root=exports.root
        exports.close();exports=SaveExports(service,root,'12')
        assert [j.job_id for j in exports.jobs.snapshot()]==ids
        exports.scan();assert len(exports.jobs.snapshot())==5
    finally:exports.close();managed.close()


@pytest.mark.parametrize('mode',['direct','link'])
def test_projection_links_only_owned_copy_and_mutation_cannot_reach_cas(tmp_path,mode):
    remote,managed,exports=fixture(tmp_path,auto='0',mode=mode)
    for record in remote.records.values():
        (exports.projection.root/(record.author_name+'('+record.author_id+')')).mkdir(exist_ok=True)
    before=managed.saved_receipts();originals=[managed.read_saved_page(rel,i,sha) for rel,row,sha in before for i in range(row['page_count'])]
    try:
        exports.scan();jobs=settled(exports);assert all(j.state=='done' for j in jobs)
        job=jobs[0];manifest=json.loads((exports.root/job.result).read_bytes())
        from pixivvault_nicegui.fs_paths import native_io_path
        target=native_io_path(exports.projection.root)/manifest['files'][0]['path']
        body=native_io_path(exports.projection.root)/'.bodies'/manifest['source']['sha256']/'0'
        assert os.path.samefile(body,target)
        if mode=='direct':assert target.parent.name=='☆ブックマーク(12)'
        else:assert len(manifest['files'])>=2
        target.write_bytes(b'edited projection')
        assert [managed.read_saved_page(rel,i,sha) for rel,row,sha in before for i in range(row['page_count'])]==originals
        with pytest.raises(ValueError,match='changed'):exports.run(job,Context())
    finally:exports.close();managed.close()


def test_existing_zip_remains_refused_and_explicit_retry_is_visible(tmp_path):
    remote,managed,exports=fixture(tmp_path,override=True)
    try:
        exports.zip.export_author('23')
        before={p.name:p.read_bytes() for p in exports.zip.root.glob('*.zip')}
        exports.scan();jobs=settled(exports)
        failure=next(j for j in jobs if j.kind=='auto-zip' and j.state=='error')
        assert exports.jobs.control(failure.job_id,'retry',expected_control_revision=failure.control_revision)
        settled(exports)
        current=next(j for j in exports.jobs.snapshot() if j.job_id==failure.job_id)
        assert current.state=='error' and current.attempt==2
        after={p.name:p.read_bytes() for p in exports.zip.root.glob('*.zip')}
        assert all(after[name]==data for name,data in before.items())
        assert set(after)-set(before)=={'作者Y(24).zip'}
        assert any(j.kind=='auto-zip' and j.state=='done' for j in exports.jobs.snapshot())
    finally:exports.close();managed.close()


def test_author_override_off_beats_global_and_nonbookmark_is_not_projected(tmp_path):
    remote,managed,exports=fixture(tmp_path,override=False)
    try:
        exports.s.bookmark_download_candidates=lambda:()
        exports.scan();assert not exports.jobs.snapshot()
    finally:exports.close();managed.close()


def test_author_zip_override_uses_existing_author_cas_without_erasing_target(tmp_path):
    from pixivvault_nicegui.author_media import AuthorMediaService
    service=SimpleNamespace(closing=False,managed_downloads=SimpleNamespace(root=tmp_path))
    owner=AuthorMediaService(service)
    try:
        owner.enable_zip_settings()
        assert owner.author_zip_setting('23') is None
        setting=owner.configure_author_target('23','illust',expected_revision=0)
        revision=owner.configure_author_zip('23',True,expected_revision=setting.revision)
        assert owner.author_target('23').target_type=='illust'
        assert owner.author_zip_setting('23') is True
        with pytest.raises(ValueError):owner.configure_author_zip('23',False,expected_revision=setting.revision)
        owner.configure_author_target('23','both',expected_revision=revision)
        assert owner.author_zip_setting('23') is True
    finally:owner.close()


def test_auto_zip_rejects_generation_change_between_admission_and_export_snapshot(tmp_path, monkeypatch):
    from pixivvault_nicegui.save_exports import packet,digest
    from pixivvault_nicegui.legacy_zip_export import LegacyZipExportError
    remote,managed,exports=fixture(tmp_path,auto='0')
    try:
        original=managed.saved_receipts
        rows=exports.author_snapshot('23')
        identity={'author_id':'23','proofs':[[row['work_id'],proof] for _,row,proof in rows]}
        key=digest(identity);(exports.root/(key+'.json')).write_bytes(packet(identity))
        calls=[]
        def changing(*, verify_pages=True):
            calls.append(1);all_rows=original(verify_pages=verify_pages)
            if len(calls)==1:return all_rows
            return [(rel,row,('f'*64 if row['author_id']=='23' else proof)) for rel,row,proof in all_rows]
        monkeypatch.setattr(managed,'saved_receipts',changing)
        job=SimpleNamespace(kind='auto-zip',target_id=key)
        with pytest.raises(LegacyZipExportError,match='changed before'):
            exports._run(job,Context())
        assert len(calls)==2 and not list(exports.zip.root.glob('*.zip'))
        assert not (exports.root/(key+'.export.json')).exists()
    finally:exports.close();managed.close()


def test_interrupted_projection_stage_never_creates_partial_final_and_retry_succeeds(tmp_path,monkeypatch):
    from pixivvault_nicegui import bookmark_projection
    from pixivvault_nicegui.save_exports import _publish_owned
    target=tmp_path/'owned'/'result';target.parent.mkdir()
    original=bookmark_projection._write;hits=[]
    def interrupted(stage,data):
        hits.append(stage);stage.write_bytes(data[:2]);raise OSError('synthetic interrupted write')
    monkeypatch.setattr(bookmark_projection,'_write',interrupted)
    with pytest.raises(OSError):_publish_owned(target,b'complete')
    assert len(hits)==1 and not target.exists()
    monkeypatch.setattr(bookmark_projection,'_write',original)
    _publish_owned(target,b'complete');assert target.read_bytes()==b'complete'
    target.write_bytes(b'user-edit')
    with pytest.raises(ValueError,match='changed'):_publish_owned(target,b'complete')
    assert target.read_bytes()==b'user-edit'


@pytest.mark.parametrize('interruption', ['before-link','before-manifest','after-manifest'])
def test_zip_journal_recovers_owned_stage_across_restart_without_overwrite(tmp_path,monkeypatch,interruption):
    import pixivvault_nicegui.save_exports as module
    import pixivvault_nicegui.legacy_zip_export as zipmodule
    from pixivvault_nicegui.save_exports import packet,digest
    remote,managed,exports=fixture(tmp_path,auto='0')
    original_link=zipmodule.os.link;original_publish=module._publish_owned;injected=[]
    rows=exports.author_snapshot('23')
    identity={'author_id':'23','proofs':[[row['work_id'],proof] for _,row,proof in rows]}
    key=digest(identity);(exports.root/(key+'.json')).write_bytes(packet(identity))
    job=SimpleNamespace(kind='auto-zip',target_id=key)
    def link(source,destination):
        if interruption=='before-link' and str(destination).endswith('.zip'):
            injected.append('before-link');raise OSError('synthetic interrupted publication')
        return original_link(source,destination)
    def publish(path,data):
        if str(path).endswith('.export.json') and interruption=='before-manifest':
            injected.append('before-manifest');raise OSError('synthetic interrupted manifest')
        result=original_publish(path,data)
        if str(path).endswith('.export.json') and interruption=='after-manifest':
            injected.append('after-manifest');raise OSError('synthetic interrupted post-manifest')
        return result
    try:
        monkeypatch.setattr(zipmodule.os,'link',link);monkeypatch.setattr(module,'_publish_owned',publish)
        expected=zipmodule.LegacyZipExportError if interruption=='before-link' else OSError
        with pytest.raises(expected):exports._run(job,Context())
        assert injected==[interruption]
        journal=exports.root/(key+'.zip-pending.json');assert journal.exists()
        pending=json.loads(journal.read_bytes());stage=exports.zip.incomplete/pending['stage']
        archive=exports.zip.root/pending['archive'];assert stage.exists()
        assert archive.exists()==(interruption!='before-link')
        monkeypatch.setattr(zipmodule.os,'link',original_link);monkeypatch.setattr(module,'_publish_owned',original_publish)
        service,root=exports.s,exports.root;exports.close();exports=SaveExports(service,root,'12')
        assert stage.exists()
        result=exports._run(job,Context())
        assert result==key+'.export.json' and archive.exists() and not stage.exists()
        receipt=json.loads((exports.root/result).read_bytes())
        assert receipt['source']==identity and receipt['archive']==archive.name
        assert receipt['sha256']==__import__('hashlib').sha256(archive.read_bytes()).hexdigest()
    finally:exports.close();managed.close()


def test_zip_recovery_refuses_foreign_destination_and_preserves_it(tmp_path,monkeypatch):
    import pixivvault_nicegui.legacy_zip_export as zipmodule
    from pixivvault_nicegui.save_exports import packet,digest
    remote,managed,exports=fixture(tmp_path,auto='0');link=zipmodule.os.link
    rows=exports.author_snapshot('23');identity={'author_id':'23','proofs':[[row['work_id'],proof] for _,row,proof in rows]}
    key=digest(identity);(exports.root/(key+'.json')).write_bytes(packet(identity))
    job=SimpleNamespace(kind='auto-zip',target_id=key)
    def blocked(src,dst):
        if str(dst).endswith('.zip'):raise OSError('synthetic stop')
        return link(src,dst)
    try:
        monkeypatch.setattr(zipmodule.os,'link',blocked)
        with pytest.raises(zipmodule.LegacyZipExportError):exports._run(job,Context())
        monkeypatch.setattr(zipmodule.os,'link',link)
        note=json.loads((exports.root/(key+'.zip-pending.json')).read_bytes())
        foreign=exports.zip.root/note['archive'];foreign.write_bytes(b'foreign data')
        with pytest.raises(ValueError,match='not owned'):exports._run(job,Context())
        assert foreign.read_bytes()==b'foreign data' and not (exports.root/(key+'.export.json')).exists()
    finally:exports.close();managed.close()


def test_zip_recovery_rejects_changed_owned_stage_and_retains_bytes(tmp_path,monkeypatch):
    import pixivvault_nicegui.legacy_zip_export as zipmodule
    from pixivvault_nicegui.save_exports import packet,digest
    remote,managed,exports=fixture(tmp_path,auto='0');link=zipmodule.os.link
    rows=exports.author_snapshot('23');identity={'author_id':'23','proofs':[[row['work_id'],proof] for _,row,proof in rows]}
    key=digest(identity);(exports.root/(key+'.json')).write_bytes(packet(identity))
    job=SimpleNamespace(kind='auto-zip',target_id=key)
    def blocked(src,dst):
        if str(dst).endswith('.zip'):raise OSError('synthetic stop')
        return link(src,dst)
    try:
        monkeypatch.setattr(zipmodule.os,'link',blocked)
        with pytest.raises(zipmodule.LegacyZipExportError):exports._run(job,Context())
        monkeypatch.setattr(zipmodule.os,'link',link)
        note=json.loads((exports.root/(key+'.zip-pending.json')).read_bytes())
        stage=exports.zip.incomplete/note['stage'];stage.write_bytes(b'edited')
        with pytest.raises(ValueError,match='bytes changed'):exports._run(job,Context())
        assert stage.read_bytes()==b'edited'
        assert not (exports.zip.root/note['archive']).exists()
        assert not (exports.root/(key+'.export.json')).exists()
    finally:exports.close();managed.close()
