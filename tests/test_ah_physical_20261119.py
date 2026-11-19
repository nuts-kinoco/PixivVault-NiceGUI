from uuid import uuid4
from zipfile import ZipFile, ZIP_FILECOUNT_LIMIT
import pytest
from test_original_physical_quarantine import setup, select, SimulatedPowerLoss


def test_zip_remains_at_original_path_before_replace(tmp_path,monkeypatch):
    source,legacy,owner,_,original,archive=setup(tmp_path)
    before=archive.read_bytes()
    try:
        def crash(stage):
            if stage=='physical-source-retained:2:1':raise SimulatedPowerLoss()
        monkeypatch.setattr(legacy,'_checkpoint',crash)
        with pytest.raises(SimulatedPowerLoss):
            source.execute(select(source,{'2'}),operation_id=uuid4().hex,mode='move',confirmed_move=True)
        assert archive.read_bytes()==before
    finally:source.close();owner.close()


def test_small_rewritten_zip_uses_zip32(tmp_path):
    source,legacy,owner,_,original,archive=setup(tmp_path)
    try:
        source.execute(select(source,{'2'}),operation_id=uuid4().hex,mode='move',confirmed_move=True)
        with ZipFile(archive) as z:
            assert all(info.extract_version < 45 for info in z.infolist())
            assert z.testzip() is None
    finally:source.close();owner.close()


def test_physical_zip_removal_preserves_archives_above_zip32_member_limit(tmp_path):
    source, _, owner, _, original, archive = setup(tmp_path)
    try:
        with ZipFile(archive, 'a', allowZip64=True) as z:
            for index in range(ZIP_FILECOUNT_LIMIT + 3 - len(z.infolist())):
                z.writestr(f'sidecar/{index:05d}.txt', b'')
        before = archive.read_bytes()
        result = source.execute(select(source, {'2'}), operation_id=uuid4().hex,
                                mode='move', confirmed_move=True)
        assert [page.key for page in result.committed] == ['2:1'] and not result.failed
        with ZipFile(archive) as z:
            assert len(z.infolist()) == ZIP_FILECOUNT_LIMIT + 2
            assert 'Zip(2).png' not in z.namelist()
            assert z.read('metadata.json') == b'{"kept":true}'
            assert z.testzip() is None
        assert any(path.read_bytes() == before for path in (original.parent/'quarantine').rglob('before'))
    finally:
        source.close()
        owner.close()
