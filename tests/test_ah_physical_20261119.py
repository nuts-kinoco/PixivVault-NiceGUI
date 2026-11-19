from uuid import uuid4
from zipfile import ZipFile
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
