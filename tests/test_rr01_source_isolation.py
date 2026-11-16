from pathlib import Path
from dataclasses import replace
from uuid import uuid4
from zipfile import ZipFile
import json
import pytest
from required_network_guard import no_network
from pixivvault_nicegui.local_media_adapter import MediaUnavailable
from test_required_legacy_duplicates import setup,select,png
from pixivvault_nicegui.models import Artwork
from pixivvault_nicegui.library_models import LibraryWork,LibrarySnapshot
from pixivvault_nicegui.legacy_quarantine import LegacyQuarantine
from pixivvault_nicegui.duplicate_inspection import ManagedDuplicateInspection
from pixivvault_nicegui.fs_paths import native_io_path
from pixivvault_nicegui.frozen_source_pin import _kernel

class PowerLoss(BaseException):pass

def test_unreadable_before_does_not_block_other_zip_of_same_author(tmp_path,monkeypatch):
    source,legacy,owner,snapshot,original,archive=setup(tmp_path)
    source.close()
    with ZipFile(original/'More(92).zip','w') as z:z.writestr('Five(5).png',png())
    snapshot=LibrarySnapshot((*snapshot.works,LibraryWork(Artwork('5','Five','Generated'), '92')))
    legacy=LegacyQuarantine(original,snapshot,owner=owner)
    source=ManagedDuplicateInspection(owner,legacy=legacy)
    catalog=None;handle=None
    create,close,invalid=_kernel()
    result={}
    try:
        plan=select(source,{'2'})
        def crash(stage):
            if stage=='physical-installed:2:1':raise PowerLoss()
        with monkeypatch.context() as patch:
            patch.setattr(legacy,'_checkpoint',crash)
            with pytest.raises(PowerLoss):source.execute(plan,operation_id=uuid4().hex,mode='move',confirmed_move=True)
        legacy.catalog._ensure_index()
        before=next(native_io_path(legacy._physical.root).glob('*/before'))
        before_data=before.read_bytes()
        handle=create(str(before),0x80000000,0,None,3,0x00200000,None)
        assert handle is not None and handle!=invalid
        state=legacy.state()
        assert legacy.recovery_status()['blocked_sources']==('Other(92).zip',)
        result['blocked']=list(legacy.recovery_status()['blocked_sources'])
        _,catalog=legacy.project(snapshot)
        try:
            result['unrelated_read_size']=len(catalog.get('5',0,'original').data)
        except Exception as error:
            result['error']=type(error).__name__+': '+str(error)
        result['source_module']=__import__('pixivvault_nicegui.legacy_quarantine',fromlist=['x']).__file__
        assert legacy.recovery_status()['blocked_sources']==('Other(92).zip',)
        assert 'Other(92).zip' in legacy.recovery_notice()
        with pytest.raises(MediaUnavailable):
            catalog.get('3',0,'original')

        assert result.get('unrelated_read_size')==len(png()), result
        close(handle);handle=None
        assert before.read_bytes()==before_data
    finally:
        if handle is not None and handle!=invalid:close(handle)
        if catalog:catalog.close()
        source.close();owner.close()


def test_other_zip_recovery_continues_with_locked_before(tmp_path,monkeypatch):
    from pixivvault_nicegui.local_media_adapter import MediaUnavailable
    source,legacy,owner,snapshot,original,archive=setup(tmp_path)
    source.close()
    more=original/'More(92).zip'
    with ZipFile(more,'w') as z:
        z.writestr('Five(5).png',png());z.writestr('Six(6).png',png())
    snapshot=LibrarySnapshot((*snapshot.works,
        LibraryWork(Artwork('5','Five','Generated'),'92'),
        LibraryWork(Artwork('6','Six','Generated'),'92')))
    legacy=LegacyQuarantine(original,snapshot,owner=owner)
    source=ManagedDuplicateInspection(owner,legacy=legacy)
    create,close,invalid=_kernel();handle=None
    def direct_interrupt(plan,wid):
        def crash(stage):
            if stage=='physical-installed:'+wid+':1':raise PowerLoss()
        with monkeypatch.context() as patch:
            patch.setattr(legacy,'_checkpoint',crash)
            with pytest.raises(PowerLoss):
                legacy.execute_physical(plan,operation_id=uuid4().hex,confirmed=True)
    try:
        # Two previously confirmed operations in distinct similarity groups.
        # Exercise recovery of existing intents, not a new full-library scan.
        from PIL import Image
        from io import BytesIO
        stream=BytesIO()
        image=Image.new('RGB',(20,20),'white')
        for x in range(20):
            for y in range(20):image.putpixel((x,y),(0,0,0) if x%2 else (255,255,255))
        image.save(stream,format='PNG')
        with ZipFile(more,'w') as z:
            z.writestr('Five(5).png',stream.getvalue());z.writestr('Six(6).png',stream.getvalue())
        plan2=select(source,{'2'});plan5=select(source,{'5'})
        more_data=more.read_bytes()
        direct_interrupt(plan5,'5')
        rollback=legacy._physical._rollback
        def defer_more(intent):
            if intent['source']=='More(92).zip':raise OSError('generated temporary recovery denial')
            return rollback(intent)
        with monkeypatch.context() as patch:
            patch.setattr(legacy._physical,'_rollback',defer_more)
            direct_interrupt(plan2,'2')
        journal=next(p for p in native_io_path(legacy._physical.journals).glob('*/intent.json')
            if json.loads(p.read_bytes())['key']=='2:1')
        before=legacy._physical._unit(journal.parent.name)/'before'
        before_data=before.read_bytes()
        handle=create(str(before),0x80000000,0,None,3,0x00200000,None)
        assert handle is not None and handle!=invalid
        assert more.read_bytes()!=more_data
        legacy.state()
        assert more.read_bytes()==more_data
        assert legacy.recovery_status()['blocked_sources']==('Other(92).zip',)
        assert 'Other(92).zip' in legacy.recovery_notice()
        with pytest.raises(MediaUnavailable):legacy.excluded_pages('3')
        close(handle);handle=None
        assert before.read_bytes()==before_data
    finally:
        if handle is not None and handle!=invalid:close(handle)
        source.close();owner.close()
