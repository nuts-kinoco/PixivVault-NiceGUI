from uuid import uuid4
import json
import pytest
from required_network_guard import no_network
from test_required_legacy_duplicates import setup,select,png
from pixivvault_nicegui import legacy_physical_quarantine as physical
from pixivvault_nicegui.local_media_adapter import MediaUnavailable

class PowerLoss(BaseException):pass

def interrupt(source,legacy,monkeypatch,stage):
    plan=select(source,{'2'})
    def crash(where):
        if where==stage:raise PowerLoss()
    with monkeypatch.context() as patch:
        patch.setattr(legacy,'_checkpoint',crash)
        with pytest.raises(PowerLoss):
            source.execute(plan,operation_id=uuid4().hex,mode='move',confirmed_move=True)

def test_receiptless_empty_scratch_is_preserved_and_reads_continue(tmp_path,monkeypatch):
    source,legacy,owner,snapshot,original,archive=setup(tmp_path)
    before=archive.read_bytes();write=physical._write
    def crash(path,data):
        if path.name.startswith('ready-owner-'):raise PowerLoss()
        return write(path,data)
    try:
        plan=select(source,{'2'})
        with monkeypatch.context() as patch:
            patch.setattr(physical,'_write',crash)
            with pytest.raises(PowerLoss):source.execute(plan,operation_id=uuid4().hex,mode='move',confirmed_move=True)
        assert next(original.glob('.pv-ready-*')).stat().st_size==0
        for _ in range(3):assert not legacy.state()['exclusions']
        assert not legacy.excluded_pages('1')
        visible,catalog=legacy.project(snapshot)
        try:assert catalog.get('1',0,'original').data==png()
        finally:catalog.close()
        assert archive.read_bytes()==before
        assert not list(original.glob('.pv-ready-*'))
        assert any(p.stat().st_size==0 for p in legacy._physical.root.glob('*/unreceipted-*'))
    finally:source.close();owner.close()

def test_unsettled_rollback_failure_blocks_only_affected_zip(tmp_path,monkeypatch):
    source,legacy,owner,snapshot,original,archive=setup(tmp_path)
    try:
        visible,pinned=legacy.project(snapshot)
        interrupt(source,legacy,monkeypatch,'physical-installed:2:1')
        with monkeypatch.context() as patch:
            patch.setattr(legacy._physical,'_rollback',lambda _:(_ for _ in ()).throw(OSError('generated full disk')))
            for _ in range(3):
                assert not legacy.state()['exclusions']
                assert not legacy.excluded_pages('1')
            visible,catalog=legacy.project(snapshot)
            try:
                assert catalog.get('1',0,'original').data==png()
                with pytest.raises(MediaUnavailable):catalog.get('3',0,'original')
                with pytest.raises(MediaUnavailable):pinned.get('2',0,'original')
            finally:catalog.close();pinned.close()
            status=legacy.recovery_status()
            assert status['blocked_sources']==('Other(92).zip',)
            assert 'Other(92).zip' in legacy.recovery_notice()
            with pytest.raises(MediaUnavailable):legacy.excluded_pages('2')
        assert not legacy.state()['exclusions']
        assert not legacy.recovery_status()['blocked_sources']
    finally:source.close();owner.close()

def test_known_committed_cleanup_failure_is_warning_only(tmp_path,monkeypatch):
    source,legacy,owner,snapshot,original,archive=setup(tmp_path)
    try:
        interrupt(source,legacy,monkeypatch,'physical-published:2:1')
        with monkeypatch.context() as patch:
            patch.setattr(legacy._physical,'_cleanup_scratch',lambda _:(_ for _ in ()).throw(physical.ScratchCleanupError('generated sharing violation')))
            for _ in range(3):
                assert '2:1' in legacy.state()['exclusions']
                visible,catalog=legacy.project(snapshot)
                try:assert catalog.get('3',0,'original').data==png()
                finally:catalog.close()
            status=legacy.recovery_status()
            assert not status['blocked_sources'] and status['warnings']
            assert 'Other(92).zip' in legacy.recovery_notice()
    finally:source.close();owner.close()

def test_reservation_is_durable_before_exclusive_creation(tmp_path,monkeypatch):
    source,legacy,owner,_,original,archive=setup(tmp_path)
    before=archive.read_bytes()
    try:
        interrupt(source,legacy,monkeypatch,'physical-scratch-reserved:ready:2:1')
        reservation=next(legacy._physical.journals.glob('*/ready-reservation.json'))
        assert json.loads(reservation.read_bytes())['name'].startswith('.pv-ready-')
        assert not list(original.glob('.pv-ready-*'))
        assert not legacy.state()['exclusions'] and archive.read_bytes()==before
    finally:source.close();owner.close()


def test_unreadable_affected_source_does_not_poison_unrelated_reads(tmp_path,monkeypatch):
    source,legacy,owner,snapshot,original,archive=setup(tmp_path)
    check=physical._legacy_input
    try:
        interrupt(source,legacy,monkeypatch,'physical-installed:2:1')
        def denied(path,**kwargs):
            if path.name==archive.name:raise PermissionError('generated sharing violation')
            return check(path,**kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(physical,'_legacy_input',denied)
            assert not legacy.state()['exclusions']
            assert not legacy.excluded_pages('1')
            assert legacy.recovery_status()['blocked_sources']==('Other(92).zip',)
    finally:source.close();owner.close()

def test_reservation_mismatch_preserves_scratch_and_scopes_block(tmp_path,monkeypatch):
    source,legacy,owner,_,original,archive=setup(tmp_path)
    write=physical._write
    def crash(path,data):
        if path.name.startswith('ready-owner-'):raise PowerLoss()
        return write(path,data)
    try:
        plan=select(source,{'2'})
        with monkeypatch.context() as patch:
            patch.setattr(physical,'_write',crash)
            with pytest.raises(PowerLoss):
                source.execute(plan,operation_id=uuid4().hex,mode='move',confirmed_move=True)
        reservation=next(legacy._physical.journals.glob('*/ready-reservation.json'))
        old=reservation.read_bytes()
        reservation.rename(tmp_path/'preserved-reservation')
        changed=json.loads(old);changed['unit']='f'*32
        reservation.write_text(json.dumps(changed))
        scratch=next(original.glob('.pv-ready-*'))
        for _ in range(3):assert not legacy.state()['exclusions']
        assert scratch.exists() and scratch.stat().st_size==0
        assert not legacy.excluded_pages('1')
        assert legacy.recovery_status()['blocked_sources']==('Other(92).zip',)
    finally:source.close();owner.close()


def test_preupgrade_receiptless_scratch_is_preserved_without_deletion(tmp_path,monkeypatch):
    source,legacy,owner,_,original,archive=setup(tmp_path)
    write=physical._write
    def crash(path,data):
        if path.name.startswith('ready-owner-'):raise PowerLoss()
        return write(path,data)
    try:
        plan=select(source,{'2'})
        with monkeypatch.context() as patch:
            patch.setattr(physical,'_write',crash)
            with pytest.raises(PowerLoss):
                source.execute(plan,operation_id=uuid4().hex,mode='move',confirmed_move=True)
        next(legacy._physical.journals.glob('*/ready-reservation.json')).rename(tmp_path/'preserved-preupgrade-reservation')
        before=archive.read_bytes()
        assert not legacy.state()['exclusions']
        assert not list(original.glob('.pv-ready-*'))
        assert any(p.stat().st_size==0 for p in legacy._physical.root.glob('*/unreceipted-*'))
        assert archive.read_bytes()==before
        assert legacy.recovery_status()['warnings']
    finally:source.close();owner.close()
