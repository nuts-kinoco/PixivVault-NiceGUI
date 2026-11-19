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
        assert renamed.exists()
        assert q.exports._previous_auto_zip('456') is not None
        monkeypatch.setattr(q.managed, 'read_saved_page', original)
        q.exports.run(failed, Context())
        assert renamed.exists()
        assert len(list(q.exports.zip.root.glob('*.zip'))) == 1
