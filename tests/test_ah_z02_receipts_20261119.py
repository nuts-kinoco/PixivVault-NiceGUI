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
