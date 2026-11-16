"""Original X07/X08 acceptance, generated files only; no external transport."""
from required_network_guard import no_network
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile
import pytest
from test_required_legacy_duplicates import setup, select, png


@pytest.mark.parametrize('selected', [{'2'}, {'1','2','3'}], ids=['X07','X08'])
def test_original_sources_are_relocated_with_preserved_before_generation(tmp_path, selected):
    source, legacy, owner, snapshot, original, archive = setup(tmp_path)
    before = archive.read_bytes()
    try:
        plan = select(source, selected)
        result = source.execute(plan, operation_id=uuid4().hex, mode='move', confirmed_move=True)
        assert {p.work_id for p in result.committed} == selected
        assert not result.failed and not result.unprocessed
        with ZipFile(archive) as z:
            assert 'Zip(2).png' not in z.namelist()
            assert ('Pages(3)_1.png' in z.namelist()) == ('3' not in selected)
            assert z.read('Pages(3)_2.png') == png()
            assert z.read('metadata.json') == b'{"kept":true}'
        if '1' in selected:
            assert not (original/'Author(91)'/'Folder(1).png').exists()
        trash = original.parent/'quarantine'
        assert any(p.read_bytes() == before for p in trash.rglob('before') if p.is_file())
        assert any(p.read_bytes() == png() for p in trash.rglob('Folder(1).png')) == ('1' in selected)
        assert all(r.status == 'success' for r in result.item_results)
        assert {w.work_id for w in legacy.project(snapshot)[0].works} == ({'4','3'} if '1' in selected else {'1','4','3'})
    finally:
        source.close(); owner.close()


def test_failed_item_is_rolled_back_while_independent_item_completes(tmp_path, monkeypatch):
    source, legacy, owner, _, original, archive = setup(tmp_path)
    before = archive.read_bytes()
    try:
        plan = select(source, {'1','2'})
        def fail(stage):
            if stage == 'physical-installed:2:1':
                raise PermissionError('generated permission failure')
        monkeypatch.setattr(legacy, '_checkpoint', fail)
        result = source.execute(plan, operation_id=uuid4().hex, mode='move', confirmed_move=True)
        assert {p.work_id for p in result.committed} == {'1'}
        assert {p.work_id for p in result.failed} == {'2'}
        assert not result.unprocessed and archive.read_bytes() == before
        assert '2:1' not in legacy.state()['exclusions']
        assert '2:1' in {p.key for g in source.latest_scan.groups for p in g.items}
        assert [(r.key,r.status) for r in result.item_results] == [('1:1','success'),('2:1','failed')]
    finally:
        source.close(); owner.close()


class SimulatedPowerLoss(BaseException):
    pass


@pytest.mark.parametrize('stage,committed', [('physical-intent:2:1',False),('physical-source-retained:2:1',False),('physical-installed:2:1',False),('physical-published:2:1',True)])
def test_reopen_recovers_exact_before_or_committed_result(tmp_path, monkeypatch, stage, committed):
    from pixivvault_nicegui.legacy_quarantine import LegacyQuarantine
    source, legacy, owner, snapshot, original, archive = setup(tmp_path)
    before = archive.read_bytes()
    try:
        plan = select(source, {'2'})
        def crash(where):
            if where == stage: raise SimulatedPowerLoss()
        monkeypatch.setattr(legacy, '_checkpoint', crash)
        with pytest.raises(SimulatedPowerLoss):
            source.execute(plan,operation_id=uuid4().hex,mode='move',confirmed_move=True)
        source.close()
        reopened = LegacyQuarantine(original,snapshot,owner=owner)
        state = reopened.state()
        assert ('2:1' in state['exclusions']) is committed
        with ZipFile(archive) as z:
            assert ('Zip(2).png' not in z.namelist()) is committed
            assert z.read('Pages(3)_2.png') == png()
        if not committed: assert archive.read_bytes() == before
        reopened.close()
    finally:
        source.close(); owner.close()


def test_partial_settlement_marker_does_not_lose_committed_result(tmp_path,monkeypatch):
    from pixivvault_nicegui import legacy_physical_quarantine as module
    source,legacy,owner,*_=setup(tmp_path)
    write=module._write
    def failed(path,data):
        if path.name.startswith('settled-'):
            write(path,b'{');raise OSError('generated marker interruption')
        return write(path,data)
    try:
        plan=select(source,{'2'})
        with monkeypatch.context() as patch:
            patch.setattr(module,'_write',failed)
            result=source.execute(plan,operation_id=uuid4().hex,mode='move',confirmed_move=True)
        assert [p.key for p in result.committed]==['2:1']
        assert '2:1' in legacy.state()['exclusions']
    finally:source.close();owner.close()


def test_recovery_failure_reports_unprocessed_and_explicit_recovery_restores_retry(tmp_path,monkeypatch):
    source,legacy,owner,_,original,_=setup(tmp_path,extra_zip=True)
    try:
        plan=select(source,{'1','2','5'})
        def failed(stage):
            if stage=='physical-installed:1:1':raise OSError('generated failure')
        with monkeypatch.context() as patch:
            patch.setattr(legacy,'_checkpoint',failed)
            patch.setattr(legacy._physical,'_rollback',lambda _:(_ for _ in ()).throw(PermissionError('generated rollback denial')))
            result=source.execute(plan,operation_id=uuid4().hex,mode='move',confirmed_move=True)
        assert [p.key for p in result.failed]==['1:1']
        assert [p.key for p in result.unprocessed]==['2:1','5:1']
        assert {r.status for r in result.item_results}=={'failed','unprocessed'}
        source.recover_legacy()
        assert (original/'Author(91)'/'Folder(1).png').read_bytes()==png()
        assert not legacy.state()['exclusions']
        assert {'1:1','2:1','5:1'} <= {p.key for g in source.latest_scan.groups for p in g.items}
        group=next(g for g in source.latest_scan.groups if g.author_id=='91')
        assert source.plan(source.latest_scan,{group.group_id:{'1:1'}}).mutation_allowed
    finally:source.close();owner.close()


def test_different_managed_owners_cannot_remove_each_others_last_survivor(tmp_path):
    from pixivvault_nicegui.legacy_quarantine import LegacyQuarantine
    from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads
    from pixivvault_nicegui.duplicate_inspection import ManagedDuplicateInspection
    source,legacy,owner,snapshot,original,_=setup(tmp_path)
    other_owner=ManagedPixivDownloads(tmp_path/'second-managed',object(),resolve_record=lambda _:None)
    other=LegacyQuarantine(original,snapshot,owner=other_owner)
    second=ManagedDuplicateInspection(other_owner,legacy=other)
    try:
        first_plan=select(source,{'1'});second_plan=select(second,{'4'})
        legacy.execute_physical(first_plan,operation_id=uuid4().hex,confirmed=True)
        result=other.execute_physical(second_plan,operation_id=uuid4().hex,confirmed=True)
        assert not result.committed and [p.key for p in result.failed]==['4:1']
        assert not other.state()['exclusions']
        assert (original/'Author(91)'/'Peer(4).png').read_bytes()==png()
    finally:source.close();second.close();owner.close();other_owner.close()


def test_settled_history_read_does_not_rehash_old_backups(tmp_path,monkeypatch):
    from pixivvault_nicegui import legacy_physical_quarantine as module
    source,legacy,owner,*_=setup(tmp_path)
    try:
        source.execute(select(source,{'2'}),operation_id=uuid4().hex,mode='move',confirmed_move=True)
        real=module._digest
        def checked(path):
            assert path.name!='before','settled backup must not be reread by ordinary state access'
            return real(path)
        monkeypatch.setattr(module,'_digest',checked)
        for _ in range(3):assert '2:1' in legacy.state()['exclusions']
    finally:source.close();owner.close()


@pytest.mark.parametrize('where',['legacy','physical'])
def test_first_marker_write_failure_is_retryable_without_trusting_partial_directory(tmp_path,monkeypatch,where):
    from pixivvault_nicegui import legacy_physical_quarantine as physical_module
    from pixivvault_nicegui import legacy_quarantine as legacy_module
    source,legacy,owner,_,_,archive=setup(tmp_path)
    before=archive.read_bytes();module=legacy_module if where=='legacy' else physical_module
    write=module._write
    def failed(path,data):
        if path.name.startswith('legacy-identity-') or path.name=='IDENTITY.json':
            write(path,b'{');raise OSError('generated initial marker interruption')
        return write(path,data)
    try:
        plan=select(source,{'2'})
        with monkeypatch.context() as patch:
            patch.setattr(module,'_write',failed)
            result=source.execute(plan,operation_id=uuid4().hex,mode='move',confirmed_move=True)
        assert not result.committed and [p.key for p in result.unprocessed]==['2:1']
        assert archive.read_bytes()==before and not legacy.state()['exclusions']
        next_plan=source.plan(source.latest_scan,{source.latest_scan.groups[1].group_id:{'2:1'}})
        assert len(source.execute(next_plan,operation_id=uuid4().hex,mode='move',confirmed_move=True).committed)==1
    finally:source.close();owner.close()


def test_reader_and_physical_writer_use_consistent_lock_order(tmp_path,monkeypatch):
    from contextlib import contextmanager
    from threading import Event,Thread,current_thread
    from pixivvault_nicegui import legacy_quarantine as legacy_module
    from pixivvault_nicegui import legacy_physical_quarantine as physical_module
    from pixivvault_nicegui.managed_root_writer import writer_fence
    source,legacy,owner,*_=setup(tmp_path)
    ready,finished=Event(),Event();errors=[];gated=[]
    try:
        source.execute(select(source,{'2'}),operation_id=uuid4().hex,mode='move',confirmed_move=True)
        plan=select(source,{'1'})
        @contextmanager
        def fence(root):
            if current_thread().name=='generated-reader' and Path(root)==owner.root and not gated:
                gated.append(True);ready.set()
                if not finished.wait(3):raise AssertionError('writer blocked by reader local lock')
            with writer_fence(root):yield
        monkeypatch.setattr(legacy_module,'writer_fence',fence)
        monkeypatch.setattr(physical_module,'writer_fence',fence)
        def read():
            try:legacy.state()
            except BaseException as error:errors.append(error)
        def move():
            try:source.execute(plan,operation_id=uuid4().hex,mode='move',confirmed_move=True)
            except BaseException as error:errors.append(error)
            finally:finished.set()
        reader=Thread(target=read,name='generated-reader');reader.start()
        assert ready.wait(3)
        writer=Thread(target=move,name='generated-writer');writer.start()
        writer.join(5);reader.join(5)
        assert not writer.is_alive() and not reader.is_alive() and not errors
    finally:finished.set();source.close();owner.close()


def test_failed_item_stays_retryable_after_success_removes_cluster_bridge(tmp_path,monkeypatch):
    from pixivvault_nicegui import duplicate_inspection as module
    source,legacy,owner,_,_,archive=setup(tmp_path)
    try:
        # Actual scan groups: two folder peers, then ZIP A--B--C.
        # A--C distance is 8, so moving bridge B would otherwise drop failed A.
        values=iter((0,0,0,15,255))
        with monkeypatch.context() as patch:
            patch.setattr(module,'dhash',lambda _:next(values))
            plan=select(source,{'2','3'})
        def fail(stage):
            if stage=='physical-installed:2:1':raise PermissionError('generated A failure')
        with monkeypatch.context() as patch:
            patch.setattr(legacy,'_checkpoint',fail)
            result=source.execute(plan,operation_id=uuid4().hex,mode='move',confirmed_move=True)
        assert [p.key for p in result.failed]==['2:1']
        assert [p.key for p in result.committed]==['3:1']
        remaining=next(g for g in source.latest_scan.groups if g.author_id=='92')
        assert {p.key for p in remaining.items}=={'2:1','3:2'}
        retry=source.plan(source.latest_scan,{remaining.group_id:{'2:1'}})
        assert len(source.execute(retry,operation_id=uuid4().hex,mode='move',confirmed_move=True).committed)==1
        with ZipFile(archive) as z:
            assert 'Zip(2).png' not in z.namelist() and z.read('Pages(3)_2.png')==png()
    finally:source.close();owner.close()


def test_success_keeps_unprocessed_groups_without_rescan(tmp_path, monkeypatch):
    source, legacy, owner, *_ = setup(tmp_path, extra_zip=True)
    try:
        plan = select(source, {'2'})
        def forbidden(): raise AssertionError('full scan is forbidden after execution')
        monkeypatch.setattr(source,'scan',forbidden)
        source.execute(plan,operation_id=uuid4().hex,mode='move',confirmed_move=True)
        keys = {p.key for g in source.latest_scan.groups for p in g.items}
        assert {'1:1','4:1','3:1','3:2','5:1','6:1'} <= keys
        group = next(g for g in source.latest_scan.groups if g.author_id == '93')
        next_plan = source.plan(source.latest_scan,{group.group_id:{'5:1'}})
        assert next_plan.mutation_allowed
    finally:
        source.close(); owner.close()
