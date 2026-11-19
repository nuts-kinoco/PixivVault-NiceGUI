from hashlib import sha256
from zipfile import ZipFile
import pytest
from test_u11_zip_contract import generated_exports, settled


@pytest.mark.parametrize('operation', ['normalize', 'remove'])
def test_direct_mutation_keeps_auto_zip_receipt_current(tmp_path, operation):
    with generated_exports(tmp_path, True, None) as q:
        q.exports.scan(); assert all(j.state=='done' for j in settled(q.exports))
        path=q.exports.zip.root/'tester(456).zip'
        prior=sha256(path.read_bytes()).hexdigest()
        if operation=='normalize':
            q.exports.zip.normalize_archive(path,prior,zip64_threshold=1)
        else:
            with ZipFile(path) as z: name=z.namelist()[0]
            q.exports.zip.remove_entry(path,name,prior)
        assert q.exports._previous_auto_zip('456') == sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize('failure', ['before_archive_replace', 'after_archive_replace'])
def test_direct_mutation_recovery_keeps_receipt_consistent(tmp_path, monkeypatch, failure):
    import os
    from pixivvault_nicegui.save_exports import SaveExports
    with generated_exports(tmp_path, True, None) as q:
        q.exports.scan(); assert all(j.state=='done' for j in settled(q.exports))
        path=q.exports.zip.root/'tester(456).zip'
        prior=sha256(path.read_bytes()).hexdigest()
        original=os.replace
        def fail(src,dst):
            if (failure=='before_archive_replace' and str(dst).endswith('.zip')
                    or failure=='after_archive_replace' and str(dst).endswith('.export.json')):
                raise OSError('injected mutation failure')
            return original(src,dst)
        monkeypatch.setattr(os,'replace',fail)
        with pytest.raises((ValueError,OSError)):
            q.exports.zip.normalize_archive(path,prior,zip64_threshold=1)
        monkeypatch.setattr(os,'replace',original)
        q.exports.close()
        reopened=SaveExports(q.exports.s,q.exports.root)
        try:
            assert reopened._previous_auto_zip('456') == sha256(path.read_bytes()).hexdigest()
            assert (sha256(path.read_bytes()).hexdigest()==prior) == (failure=='before_archive_replace')
            assert not list(reopened.root.glob('zip-mutation-*.json'))
        finally: reopened.close()


def test_completed_auto_zip_can_be_read_after_direct_mutation(tmp_path):
    from test_managed_downloads import Context
    with generated_exports(tmp_path, True, None) as q:
        q.exports.scan(); job=settled(q.exports)[0]; assert job.state=='done'
        path=q.exports.zip.root/'tester(456).zip'
        q.exports.zip.normalize_archive(path,sha256(path.read_bytes()).hexdigest(),zip64_threshold=1)
        result=q.exports.run(job,Context())
        assert result==job.result


def test_direct_mutation_refuses_unfinished_auto_zip_stage(tmp_path):
    import json
    with generated_exports(tmp_path, True, None) as q:
        q.exports.scan(); job=settled(q.exports)[0]; assert job.state=='done'
        archive=q.exports.zip.root/'tester(456).zip'; before=archive.read_bytes()
        pending=q.exports.root/(job.target_id+'.zip-pending.json')
        stage=q.exports.zip.incomplete/json.loads(pending.read_bytes())['stage']
        stage.write_bytes(b'unfinished')
        with pytest.raises(ValueError):
            q.exports.zip.normalize_archive(archive,sha256(before).hexdigest(),zip64_threshold=1)
        assert archive.read_bytes()==before and stage.read_bytes()==b'unfinished'


def test_partial_journal_does_not_block_reopen(tmp_path):
    from pixivvault_nicegui.save_exports import SaveExports
    with generated_exports(tmp_path, True, None) as q:
        q.exports.scan(); assert all(j.state=='done' for j in settled(q.exports))
        archive=q.exports.zip.root/'tester(456).zip'; before=archive.read_bytes()
        (q.exports.root/('zip-mutation-'+'a'*32+'.json')).write_bytes(b'{"version":1,')
        q.exports.close()
        reopened=SaveExports(q.exports.s,q.exports.root)
        try:
            assert reopened._previous_auto_zip('456')==sha256(before).hexdigest()
            assert archive.read_bytes()==before
        finally: reopened.close()


def test_partial_temp_write_cannot_publish_journal(tmp_path, monkeypatch):
    from pixivvault_nicegui.save_exports import SaveExports
    import pixivvault_nicegui.save_exports as module
    with generated_exports(tmp_path, True, None) as q:
        q.exports.scan(); assert all(j.state=='done' for j in settled(q.exports))
        archive=q.exports.zip.root/'tester(456).zip'; before=archive.read_bytes()
        original=module._write
        def partial(path,data):
            if path.name.startswith('zip-mutation-'):
                path.write_bytes(data[:15]); raise OSError('injected')
            return original(path,data)
        monkeypatch.setattr(module,'_write',partial)
        with pytest.raises(ValueError): q.exports.zip.normalize_archive(archive,sha256(before).hexdigest(),zip64_threshold=1)
        monkeypatch.setattr(module,'_write',original)
        assert archive.read_bytes()==before and not list(q.exports.root.glob('zip-mutation-*.json'))
        q.exports.close(); reopened=SaveExports(q.exports.s,q.exports.root)
        try: assert reopened._previous_auto_zip('456')==sha256(before).hexdigest()
        finally: reopened.close()


def test_direct_normalize_waits_for_other_pending_auto_zip(tmp_path,monkeypatch):
    import os
    from test_fail7_batch1 import add_second
    from test_managed_downloads import Context
    with generated_exports(tmp_path,True,None) as q:
        q.exports.scan();assert all(j.state=='done' for j in settled(q.exports))
        path=q.exports.zip.root/'tester(456).zip';before=path.read_bytes()
        add_second(q);original=os.replace
        def fail(src,dst):
            if str(dst).endswith('.zip'):raise OSError('injected')
            return original(src,dst)
        monkeypatch.setattr(os,'replace',fail)
        q.exports.scan();failed=next(j for j in settled(q.exports) if j.state=='error')
        monkeypatch.setattr(os,'replace',original)
        with pytest.raises(ValueError):
            q.exports.zip.normalize_archive(path,sha256(before).hexdigest(),zip64_threshold=1)
        assert path.read_bytes()==before
        assert q.exports.run(failed,Context()).endswith('.export.json')
        with ZipFile(path) as z:assert len(z.namelist())==4


@pytest.mark.parametrize('publication_link', [False, True], ids=['stage', 'stage-and-link'])
def test_new_publication_retires_superseded_zip_stage(tmp_path, monkeypatch, publication_link):
    from dataclasses import replace
    import json
    import os
    from types import SimpleNamespace
    from test_fail7_batch1 import add_second
    from test_managed_downloads import Context, record

    with generated_exports(tmp_path, True, None) as q:
        q.exports.scan()
        assert settled(q.exports)[0].state == 'done'
        path = q.exports.zip.root / 'tester(456).zip'
        add_second(q)
        original = os.replace

        def fail(src, dst):
            if str(dst).endswith('.zip'):
                raise OSError('synthetic interruption')
            return original(src, dst)

        monkeypatch.setattr(os, 'replace', fail)
        q.exports.scan()
        failed = next(job for job in settled(q.exports) if job.state == 'error')
        pending = q.exports.root / (failed.target_id + '.zip-pending.json')
        stage = q.exports.zip.incomplete / json.loads(pending.read_bytes())['stage']
        assert stage.is_file()
        link = stage.with_name(stage.name + '.publish')
        if publication_link:
            os.link(stage, link)

        monkeypatch.setattr(os, 'replace', original)
        q.managed.resolve_record = lambda wid: replace(record(), remote_id=wid, title='third')
        q.managed.run(SimpleNamespace(kind='pixiv-illust', target_id='125'), Context())
        q.exports.scan()
        assert sum(job.state == 'done' for job in settled(q.exports)) == 2
        assert not stage.exists() and not link.exists() and not pending.exists()
        with pytest.raises(ValueError, match='saved generation changed'):
            q.exports.run(failed, Context())
        q.exports.zip.normalize_archive(path, sha256(path.read_bytes()).hexdigest(), zip64_threshold=1)
        with ZipFile(path) as z:
            assert len(z.namelist()) == 6 and z.testzip() is None
