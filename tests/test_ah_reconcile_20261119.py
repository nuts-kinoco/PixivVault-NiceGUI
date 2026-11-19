from dataclasses import replace
from types import SimpleNamespace
from zipfile import ZipFile
from test_u11_zip_contract import generated_exports, settled
from test_managed_downloads import Context, record


def test_auto_zip_reconciles_author_rename(tmp_path):
    with generated_exports(tmp_path, True, None) as q:
        q.exports.scan()
        assert all(j.state == 'done' for j in settled(q.exports))
        old = q.exports.zip.root / 'tester(456).zip'
        q.managed.resolve_record = lambda wid: replace(record(), remote_id=wid, title='second', author_name='new')
        q.managed.run(SimpleNamespace(kind='pixiv-illust', target_id='124'), Context())
        q.exports.scan()
        assert all(j.state == 'done' for j in settled(q.exports))
        new = q.exports.zip.root / 'new(tester)(456).zip'
        assert not old.exists() and new.exists()
        with ZipFile(new) as archive:
            assert len(archive.namelist()) == 4
            assert archive.testzip() is None


def test_renamed_archive_keeps_receipt_after_interrupted_export(tmp_path, monkeypatch):
    with generated_exports(tmp_path, True, None) as q:
        q.exports.scan()
        assert all(j.state == 'done' for j in settled(q.exports))
        q.managed.resolve_record = lambda wid: replace(record(), remote_id=wid, title='second', author_name='new')
        q.managed.run(SimpleNamespace(kind='pixiv-illust', target_id='124'), Context())
        original = q.managed.read_saved_page
        def fail(*args):
            raise ValueError('injected')
        monkeypatch.setattr(q.managed, 'read_saved_page', fail)
        q.exports.scan()
        failed = next(j for j in settled(q.exports) if j.state == 'error')
        renamed = q.exports.zip.root / 'new(tester)(456).zip'
        old = q.exports.zip.root / 'tester(456).zip'
        assert old.exists() and not renamed.exists()
        assert q.exports._previous_auto_zip('456') is not None
        monkeypatch.setattr(q.managed, 'read_saved_page', original)
        q.exports.run(failed, Context())
        assert renamed.exists()
        assert len(list(q.exports.zip.root.glob('*.zip'))) == 1


def test_recovery_does_not_publish_when_previous_archive_is_missing(tmp_path, monkeypatch):
    import os
    import pytest
    with generated_exports(tmp_path, True, None) as q:
        q.exports.scan()
        assert all(j.state == 'done' for j in settled(q.exports))
        q.managed.resolve_record = lambda wid: replace(record(), remote_id=wid, title='second', author_name='new')
        q.managed.run(SimpleNamespace(kind='pixiv-illust', target_id='124'), Context())
        original_replace = os.replace
        def fail(src, dst):
            if str(dst).endswith('.zip'): raise OSError('injected')
            return original_replace(src, dst)
        monkeypatch.setattr(os, 'replace', fail)
        q.exports.scan()
        failed = next(j for j in settled(q.exports) if j.state == 'error')
        renamed = q.exports.zip.root / 'new(tester)(456).zip'
        original = q.exports.zip.root / 'tester(456).zip'
        held = q.exports.zip.root / 'temporarily-held.bin'
        if original.exists(): original.rename(held)
        else: renamed.rename(held)
        monkeypatch.setattr(os, 'replace', original_replace)
        with pytest.raises(ValueError): q.exports.run(failed, Context())
        assert held.exists() and not original.exists() and not renamed.exists()


def test_author_rename_recovers_after_receipt_write_failure(tmp_path,monkeypatch):
    import os
    from pixivvault_nicegui.save_exports import SaveExports
    with generated_exports(tmp_path,True,None) as q:
        q.exports.scan();assert all(j.state=='done' for j in settled(q.exports))
        q.managed.resolve_record=lambda wid:replace(record(),remote_id=wid,title='second',author_name='new')
        q.managed.run(SimpleNamespace(kind='pixiv-illust',target_id='124'),Context())
        original=os.replace
        def fail(src,dst):
            if str(dst).endswith('.export.json'):raise OSError('injected')
            return original(src,dst)
        monkeypatch.setattr(os,'replace',fail)
        q.exports.scan();failed=next(j for j in settled(q.exports) if j.state=='error')
        monkeypatch.setattr(os,'replace',original)
        q.exports.close(); reopened=SaveExports(q.exports.s,q.exports.root)
        try:
            assert (reopened.zip.root/'tester(456).zip').exists()
            reopened.run(failed,Context())
            target=reopened.zip.root/'new(tester)(456).zip'
            assert target.exists() and not (reopened.zip.root/'tester(456).zip').exists()
            assert reopened._previous_auto_zip('456') is not None
            assert not list(reopened.root.glob('zip-mutation-*.json'))
        finally:reopened.close()


def test_failed_rename_receipt_update_restores_old_path(tmp_path,monkeypatch):
    import os
    with generated_exports(tmp_path,True,None) as q:
        q.exports.scan();assert all(j.state=='done' for j in settled(q.exports))
        q.managed.resolve_record=lambda wid:replace(record(),remote_id=wid,title='second',author_name='new')
        q.managed.run(SimpleNamespace(kind='pixiv-illust',target_id='124'),Context())
        original=os.replace
        def fail(src,dst):
            if str(dst).endswith('.export.json'):raise OSError('injected')
            return original(src,dst)
        monkeypatch.setattr(os,'replace',fail)
        q.exports.scan();assert any(j.state=='error' for j in settled(q.exports))
        old=q.exports.zip.root/'tester(456).zip';new=q.exports.zip.root/'new(tester)(456).zip'
        assert old.exists() and not new.exists()
