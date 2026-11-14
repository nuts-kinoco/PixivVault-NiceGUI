from pathlib import Path
from uuid import uuid4
import pytest
from test_required_legacy_duplicates import setup, select
from pixivvault_nicegui import legacy_physical_quarantine as module

class PowerLoss(BaseException): pass

def leftovers(original):
    return list(original.rglob('.pv-ready-*')) + list(original.rglob('.pv-recover-*'))

@pytest.mark.parametrize('stage', ['physical-source-retained:2:1','physical-installed:2:1'])
def test_p1_reopen_cleans_owned_ready_and_preserves_original(tmp_path, monkeypatch, stage):
    from pixivvault_nicegui.legacy_quarantine import LegacyQuarantine
    source, legacy, owner, snapshot, original, archive = setup(tmp_path)
    before=archive.read_bytes()
    try:
        plan=select(source,{'2'})
        def crash(where):
            if where==stage: raise PowerLoss()
        with monkeypatch.context() as patch:
            patch.setattr(legacy,'_checkpoint',crash)
            with pytest.raises(PowerLoss):
                source.execute(plan,operation_id=uuid4().hex,mode='move',confirmed_move=True)
        source.close()
        reopened=LegacyQuarantine(original,snapshot,owner=owner)
        try:
            assert not reopened.state()['exclusions']
            assert archive.read_bytes()==before
            assert not leftovers(original)
        finally: reopened.close()
    finally:source.close();owner.close()

@pytest.mark.parametrize('kind',['ready','recover'])
def test_p1_partial_owned_copy_is_cleaned_after_power_loss(tmp_path,monkeypatch,kind):
    from pixivvault_nicegui.legacy_quarantine import LegacyQuarantine
    source,legacy,owner,snapshot,original,archive=setup(tmp_path)
    before=archive.read_bytes()
    try:
        plan=select(source,{'2'})
        if kind=='recover':monkeypatch.setattr(module,'_same_filesystem',lambda *args:False)
        def crash(where):
            if kind=='recover' and where=='physical-installed:2:1':
                raise OSError('generated rollback')
            if where=='physical-scratch-owned:'+kind+':2:1':
                target=next(original.rglob('.pv-'+kind+'-*'))
                with target.open('ab') as f:f.write(b'generated partial scratch')
                raise PowerLoss()
        with monkeypatch.context() as patch:
            patch.setattr(legacy,'_checkpoint',crash)
            with pytest.raises(PowerLoss):
                source.execute(plan,operation_id=uuid4().hex,mode='move',confirmed_move=True)
        source.close()
        reopened=LegacyQuarantine(original,snapshot,owner=owner)
        try:
            assert not reopened.state()['exclusions']
            assert archive.read_bytes()==before and not leftovers(original)
        finally:reopened.close()
    finally:source.close();owner.close()

def test_p1_replaced_scratch_is_never_unlinked(tmp_path,monkeypatch):
    source,legacy,owner,_,original,archive=setup(tmp_path)
    before=archive.read_bytes()
    try:
        plan=select(source,{'2'})
        foreign=b'generated foreign data must be preserved'
        def fail(where):
            if where=='physical-source-retained:2:1':
                ready=next(original.rglob('.pv-ready-*'))
                ready.rename(tmp_path/'preserved-generated-scratch')
                ready.write_bytes(foreign)
                raise OSError('generated interruption')
        with monkeypatch.context() as patch:
            patch.setattr(legacy,'_checkpoint',fail)
            result=source.execute(plan,operation_id=uuid4().hex,mode='move',confirmed_move=True)
        assert result.failed and result.cleanup_warning and result.recovery_required
        assert archive.read_bytes()==before
        assert next(original.rglob('.pv-ready-*')).read_bytes()==foreign
        with pytest.raises(module.ScratchCleanupError):source.recover_legacy()
        assert next(original.rglob('.pv-ready-*')).read_bytes()==foreign
    finally:source.close();owner.close()

def test_p1_old_complete_ready_copy_has_intent_digest_proof(tmp_path,monkeypatch):
    from pixivvault_nicegui.legacy_quarantine import LegacyQuarantine
    source,legacy,owner,snapshot,original,archive=setup(tmp_path)
    before=archive.read_bytes()
    try:
        def crash(where):
            if where=='physical-source-retained:2:1':raise PowerLoss()
        with monkeypatch.context() as patch:
            patch.setattr(legacy,'_checkpoint',crash)
            with pytest.raises(PowerLoss):
                source.execute(select(source,{'2'}),operation_id=uuid4().hex,mode='move',confirmed_move=True)
        receipt=next(legacy._physical.journals.glob('*/ready-owner.json'))
        receipt.rename(tmp_path/'preserved-generated-owner.json')
        source.close()
        reopened=LegacyQuarantine(original,snapshot,owner=owner)
        try:
            assert not reopened.state()['exclusions']
            assert archive.read_bytes()==before and not leftovers(original)
        finally:reopened.close()
    finally:source.close();owner.close()


def test_p1_rollback_copy_failure_is_reported_and_retry_recovers(tmp_path,monkeypatch):
    source,legacy,owner,_,original,archive=setup(tmp_path)
    before=archive.read_bytes()
    try:
        plan=select(source,{'2'})
        monkeypatch.setattr(module,'_same_filesystem',lambda *args:False)
        copy=module._copy
        def broken(src,dst,**kwargs):
            if dst.name.startswith('.pv-recover-'):
                copy(src,dst,**kwargs)
                raise PermissionError('generated recovery copy failure')
            return copy(src,dst,**kwargs)
        def fail(where):
            if where=='physical-installed:2:1':raise OSError('generated interruption')
        with monkeypatch.context() as patch:
            patch.setattr(module,'_copy',broken)
            patch.setattr(legacy,'_checkpoint',fail)
            result=source.execute(plan,operation_id=uuid4().hex,mode='move',confirmed_move=True)
            assert result.recovery_required and result.failed
        source.recover_legacy()
        assert archive.read_bytes()==before
        assert not leftovers(original)
        group=next(g for g in source.latest_scan.groups if any(p.key=='2:1' for p in g.items))
        retry=source.plan(source.latest_scan,{group.group_id:{'2:1'}})
        result=source.execute(retry,operation_id=uuid4().hex,mode='move',confirmed_move=True)
        assert [p.key for p in result.committed]==['2:1']
        assert not result.recovery_required and not result.cleanup_warning
    finally:source.close();owner.close()

def test_p1_cleanup_denial_visible_and_other_data_preserved(tmp_path,monkeypatch):
    source,legacy,owner,_,original,archive=setup(tmp_path)
    before=archive.read_bytes()
    try:
        plan=select(source,{'2','3'})
        def deny(path):
            if path.name.startswith('.pv-ready-'):raise PermissionError('generated unlink denial')
        def fail(where):
            if where=='physical-source-retained:2:1':raise OSError('generated interruption')
        with monkeypatch.context() as patch:
            patch.setattr(legacy._physical,'_before_scratch_delete',deny)
            patch.setattr(legacy,'_checkpoint',fail)
            result=source.execute(plan,operation_id=uuid4().hex,mode='move',confirmed_move=True)
            assert result.cleanup_warning and result.recovery_required
            assert [p.key for p in result.failed]==['2:1']
            assert [p.key for p in result.unprocessed]==['3:1']
            assert any('一時' in r.reason for r in result.item_results)
        source.recover_legacy()
        assert archive.read_bytes()==before and not leftovers(original)
    finally:source.close();owner.close()

def test_p1_unattributed_old_recovery_file_never_deleted_or_bypassed(tmp_path):
    source,legacy,owner,_,original,archive=setup(tmp_path)
    before=archive.read_bytes()
    unknown=original/('.pv-recover-'+'a'*32)
    unknown.write_bytes(b'generated unowned contents')
    try:
        result=source.execute(select(source,{'2'}),operation_id=uuid4().hex,mode='move',confirmed_move=True)
        assert not result.committed and result.failed
        assert unknown.read_bytes()==b'generated unowned contents'
        assert archive.read_bytes()==before and not legacy.state()['exclusions']
    finally:source.close();owner.close()

