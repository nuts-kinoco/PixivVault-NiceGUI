from dataclasses import replace
from required_network_guard import no_network
from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile, ZIP_STORED
from uuid import uuid4
from types import SimpleNamespace
import asyncio
import pytest
from PIL import Image
from nicegui import Client, ui
from pixivvault_nicegui.models import Artwork
from pixivvault_nicegui.library_models import LibraryWork, LibrarySnapshot
from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads
from pixivvault_nicegui.duplicate_inspection import ManagedDuplicateInspection


def png():
    out=BytesIO();Image.new('RGB',(20,20),'blue').save(out,format='PNG');return out.getvalue()


def setup(tmp_path,*,readonly=False,extra_zip=False):
    from pixivvault_nicegui.legacy_quarantine import LegacyQuarantine
    original=tmp_path/'legacy';original.mkdir()
    folder=original/'Author(91)';folder.mkdir()
    (folder/'Folder(1).png').write_bytes(png())
    archive=original/'Other(92).zip'
    with ZipFile(archive,'w',ZIP_STORED) as z:
        z.writestr('Zip(2).png',png());z.writestr('Pages(3)_1.png',png())
        z.writestr('Pages(3)_2.png',png());z.writestr('metadata.json','{"kept":true}')
    # The folder also needs a same-author peer to exercise folder quarantine.
    (folder/'Peer(4).png').write_bytes(png())
    works=tuple(LibraryWork(Artwork(wid,title,'Generated',page_count=count),aid)
                for wid,title,count,aid in [('1','Folder',1,'91'),('4','Peer',1,'91'),
                                           ('2','Zip',1,'92'),('3','Pages',2,'92')])
    if extra_zip:
        with ZipFile(original/'Third(93).zip','w',ZIP_STORED) as z:
            z.writestr('Five(5).png',png());z.writestr('Six(6).png',png())
        works+=tuple(LibraryWork(Artwork(wid,title,'Generated'), '93')
                     for wid,title in [('5','Five'),('6','Six')])
    snapshot=LibrarySnapshot(works)
    owner=None if readonly else ManagedPixivDownloads(tmp_path/'managed',object(),resolve_record=lambda _:None)
    legacy=LegacyQuarantine(original,snapshot,owner=owner,source_guard=lambda:None)
    source=ManagedDuplicateInspection(owner,legacy=legacy)
    return source,legacy,owner,snapshot,original,archive


def select(source,work_ids):
    scan=source.scan()
    selections={g.group_id:{p.key for p in g.items if p.work_id in work_ids and p.page==1} for g in scan.groups}
    return source.plan(scan,selections)


def test_x01_legacy_only_folder_and_zip_are_scanned_without_managed_receipts(tmp_path):
    source,legacy,owner,*_=setup(tmp_path,readonly=True)
    try:
        scan=source.scan()
        assert scan.scanned_files==5 and len(scan.groups)==2
        assert {p.work_id for g in scan.groups for p in g.items}=={'1','2','3','4'}
        plan=select(source,{'2'})
        assert not plan.mutation_allowed
    finally:source.close()


@pytest.mark.parametrize('selected',[{'2'},{'1','2','3'}],ids=['X07-single','X08-batch'])
def test_zip_rebuilt_removed_pages_quarantined_and_reopen_mapping(tmp_path,selected):
    source,legacy,owner,snapshot,original,archive=setup(tmp_path)
    before={p.relative_to(original):sha256(p.read_bytes()).hexdigest() for p in original.rglob('*') if p.is_file()}
    try:
        plan=select(source,selected);op=uuid4().hex
        result=source.execute(plan,operation_id=op)
        assert len(result.committed)==len(selected) and not result.failed
        state=legacy.state()
        rebuilt=legacy.active_archive(archive)
        assert rebuilt!=archive
        with ZipFile(rebuilt) as z:
            assert 'Zip(2).png' not in z.namelist()
            assert ('Pages(3)_1.png' in z.namelist())==('3' not in selected)
            assert z.read('Pages(3)_2.png')==png()
            assert z.read('metadata.json')==b'{"kept":true}'
        assert before=={p.relative_to(original):sha256(p.read_bytes()).hexdigest() for p in original.rglob('*') if p.is_file()}
        view,catalog=legacy.project(snapshot)
        assert '2' not in {w.work_id for w in view.works}
        assert catalog.get('3',0,'original').data==png()
        if '3' in selected:assert catalog.source_pages['3']==(1,)
        catalog.close()
        assert legacy.snapshot().excluded_pages==len(selected)
        source.close();owner.close()
        from pixivvault_nicegui.legacy_quarantine import LegacyQuarantine
        owner=ManagedPixivDownloads(tmp_path/'managed',object(),resolve_record=lambda _:None)
        reopened=LegacyQuarantine(original,snapshot,owner=owner,source_guard=lambda:None)
        assert reopened.state()==state
        assert reopened.active_archive(archive)==rebuilt
        reopened.close()
    finally:source.close();owner.close()


def test_changed_source_or_forged_plan_never_changes_original_or_state(tmp_path):
    source,legacy,owner,_,_,archive=setup(tmp_path)
    try:
        plan=select(source,{'2'})
        with pytest.raises(ValueError):source.execute(replace(plan),operation_id=uuid4().hex)
        with ZipFile(archive,'a') as z:z.writestr('external.txt','changed')
        before=archive.read_bytes()
        with pytest.raises(ValueError):source.execute(plan,operation_id=uuid4().hex)
        assert archive.read_bytes()==before and not legacy.state()['exclusions']
    finally:source.close();owner.close()


@pytest.mark.parametrize('point',['quarantine','archive','state','before_current','after_current'])
def test_interrupted_batch_is_old_or_complete_and_original_is_preserved(tmp_path,monkeypatch,point):
    source,legacy,owner,snapshot,original,archive=setup(tmp_path)
    before=archive.read_bytes()
    try:
        plan=select(source,{'1','2','3'})
        def interrupt(stage):
            if stage==point:raise OSError('generated interruption')
        monkeypatch.setattr(legacy,'_checkpoint',interrupt)
        with pytest.raises((OSError,ValueError)):source.execute(plan,operation_id=uuid4().hex)
        assert archive.read_bytes()==before
        from pixivvault_nicegui.legacy_quarantine import LegacyQuarantine
        reopened=LegacyQuarantine(original,snapshot,owner=owner,source_guard=lambda:None)
        count=len(reopened.state()['exclusions'])
        assert count==(3 if point=='after_current' else 0)
        if count:
            assert reopened.snapshot().copy_count==3
            with ZipFile(reopened.active_archive(archive)) as z:
                assert z.namelist()==['Pages(3)_2.png','metadata.json']
        reopened.close()
    finally:source.close();owner.close()


@pytest.mark.asyncio
async def test_real_ui_requires_confirmation_for_legacy_single_and_batch(tmp_path):
    from pixivvault_nicegui.ui.duplicate_view import DuplicateView
    source,legacy,owner,*_=setup(tmp_path)
    client=Client(ui.page('/_required_legacy_duplicate'))
    try:
        with client:
            view=DuplicateView(source);view.set_active(True)
            await view.scan()
            view.set_mode('near')
            group=next(g for g in view.scan_result.groups if g.author_id=='92')
            view.toggle(group.group_id,'2:1',True,view.selection_revision)
            await view.confirm(group.group_id,view.selection_revision)
            assert not legacy.state()['exclusions']
            assert view._confirmation and not view.relocation_choice.enabled
            assert 'ZIP' in view.legacy_notice.text
            await view.execute_confirmation()
            assert '2:1' in legacy.state()['exclusions']
            view.close()
    finally:client.delete();source.close();owner.close()


def test_pinned_old_reader_rejects_quarantined_page_and_service_publishes_visible_mapping(tmp_path):
    from pixivvault_nicegui.services import ApplicationServices
    from pixivvault_nicegui.managed_download_catalog import build_managed_library
    source,legacy,owner,snapshot,original,archive=setup(tmp_path)
    service=ApplicationServices(1)
    old_view,old_reader=legacy.project(snapshot)
    try:
        plan=select(source,{'2','3'});source.execute(plan,operation_id=uuid4().hex)
        with pytest.raises(ValueError):old_reader.get('2',0,'original')
        with pytest.raises(ValueError):old_reader.get('3',0,'original')
        assert old_reader.get('3',1,'original').data==png()
        service.library_image_root=original;service.legacy_quarantine=legacy
        current,catalog,local=build_managed_library(owner)
        service._publish_managed_library(current,catalog,local,legacy_snapshot=snapshot,
            legacy_local=tuple(w.artwork for w in snapshot.works),records=())
        assert {w.work_id:w.page_count for w in service.library.works}=={'1':1,'4':1,'3':1}
        assert {w.work_id for w in service._cached_local_bookmarks}=={'1','4','3'}
        assert service._legacy_library_snapshot is snapshot
    finally:old_reader.close();service.close();source.close();owner.close()


@pytest.mark.parametrize('entry',['../outside.png','same.png'])
def test_unsafe_or_duplicate_zip_members_do_not_publish(tmp_path,entry):
    source,legacy,owner,_,_,archive=setup(tmp_path)
    try:
        with ZipFile(archive,'a') as z:
            z.writestr(entry,b'one')
            if entry=='same.png':z.writestr(entry,b'two')
        # The catalog itself can reject the whole archive before planning.
        try:plan=select(source,{'2'})
        except ValueError:plan=None
        before=archive.read_bytes()
        if plan is not None:
            with pytest.raises(ValueError):source.execute(plan,operation_id=uuid4().hex)
        assert archive.read_bytes()==before and not legacy.state()['exclusions']
    finally:source.close();owner.close()


def test_quarantined_legacy_pages_never_download_again_and_surviving_pages_stay_available(tmp_path):
    from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
    from pixivvault_nicegui.managed_download_catalog import build_managed_library
    source,legacy,owner,*_=setup(tmp_path)
    calls=[]
    class Reader:
        def verify_identity(self):calls.append('verify');return '7'
        def get_artwork_original_pages(self,wid):
            return ('https://i.pximg.net/p0',) if wid=='2' else ('https://i.pximg.net/p0','https://i.pximg.net/p1')
        def get_image_bytes(self,url):calls.append(url);return png()
    class Context:
        def checkpoint(self):pass
        def progress(self,*a):pass
    owner.client=Reader()
    owner.resolve_record=lambda wid:RemoteBookmark(wid,'illustration','show','Generated','92','Generated',(),
        'https://i.pximg.net/t','2026-11-01T00:00:00Z',1 if wid=='2' else 2,0,0)
    try:
        source.execute(select(source,{'2','3'}),operation_id=uuid4().hex)
        with pytest.raises(ValueError):owner.run(SimpleNamespace(kind='pixiv-illust',target_id='2'),Context())
        assert not calls
        owner.run(SimpleNamespace(kind='pixiv-illust',target_id='3'),Context())
        assert calls==['verify','https://i.pximg.net/p1']
        assert owner.visible_source_pages('3')==(1,)
        snapshot,catalog,_=build_managed_library(owner)
        assert catalog.source_pages['3']==(1,) and snapshot.works[0].page_count==1
        assert catalog.get('3',0,'original').data==png()
        catalog.close()
    finally:source.close();owner.close()


def test_two_plans_cannot_jointly_quarantine_every_survivor(tmp_path):
    from pixivvault_nicegui.legacy_quarantine import LegacyQuarantine
    source,legacy,owner,snapshot,original,_=setup(tmp_path)
    other=LegacyQuarantine(original,snapshot,owner=owner,source_guard=lambda:None)
    second=ManagedDuplicateInspection(owner,legacy=other)
    try:
        first_plan=select(source,{'2'})
        scan=second.scan();group=next(g for g in scan.groups if g.author_id=='92')
        second_plan=second.plan(scan,{group.group_id:{'3:1','3:2'}})
        legacy.execute(first_plan,operation_id=uuid4().hex)
        # Model the competing commit just after the inspection's precheck.
        with pytest.raises(ValueError):other.execute(second_plan,operation_id=uuid4().hex)
        assert set(legacy.state()['exclusions'])=={'2:1'}
    finally:second.close();source.close();owner.close()


def test_competing_commit_during_plan_does_not_rebase_an_old_selection(tmp_path,monkeypatch):
    from pixivvault_nicegui.legacy_quarantine import LegacyQuarantine
    source,legacy,owner,snapshot,original,_=setup(tmp_path)
    other=LegacyQuarantine(original,snapshot,owner=owner,source_guard=lambda:None)
    second=ManagedDuplicateInspection(owner,legacy=other)
    try:
        scan=source.scan();group=next(g for g in scan.groups if g.author_id=='92')
        competing=select(second,{'2'})
        original_catalog=source._catalog
        def raced():
            result=original_catalog()
            other.execute(competing,operation_id=uuid4().hex)
            return result
        monkeypatch.setattr(source,'_catalog',raced)
        with pytest.raises(ValueError):source.plan(scan,{group.group_id:{'3:1','3:2'}})
        assert set(legacy.state()['exclusions'])=={'2:1'}
    finally:second.close();source.close();owner.close()


@pytest.mark.parametrize('interrupt',[False,True])
def test_two_zip_batch_publication_and_second_archive_interruption(tmp_path,monkeypatch,interrupt):
    import pixivvault_nicegui.legacy_quarantine as module
    source,legacy,owner,_,original,archive=setup(tmp_path,extra_zip=True)
    third=original/'Third(93).zip'
    before=(archive.read_bytes(),third.read_bytes())
    try:
        plan=select(source,{'2','5'})
        original_zip=module.ZipFile
        def opened(path,*args,**kwargs):
            mode=args[0] if args else kwargs.get('mode','r')
            if interrupt and mode=='r' and str(getattr(path,'name',path)).endswith('Third(93).zip'):
                raise OSError('generated second archive interruption')
            return original_zip(path,*args,**kwargs)
        monkeypatch.setattr(module,'ZipFile',opened)
        if interrupt:
            with pytest.raises(OSError):source.execute(plan,operation_id=uuid4().hex)
            assert not legacy.state()['exclusions']
            assert legacy.active_archive(archive)==archive
            assert legacy.active_archive(third)==third
        else:
            assert len(source.execute(plan,operation_id=uuid4().hex).committed)==2
            with ZipFile(legacy.active_archive(archive)) as z:assert 'Zip(2).png' not in z.namelist()
            with ZipFile(legacy.active_archive(third)) as z:assert z.namelist()==['Six(6).png']
        assert before==(archive.read_bytes(),third.read_bytes())
    finally:source.close();owner.close()
