"""Generated managed originals only; scan and plan never mutate saved files."""
from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace
import hashlib
import sqlite3
import pytest
from PIL import Image
from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads
from pixivvault_nicegui.duplicate_inspection import (
    ManagedDuplicateInspection,DuplicateInspectionError,DuplicatePage,DuplicateGroup,DuplicateScan,
    dhash,hamming,cluster_edges,best_item_index,low_quality_keys,validate_selection,visible_groups,
    read_undecidable_ledger,visible_undecidable_groups,
)

class Context:
    def checkpoint(self):pass
    def progress(self,*_):pass

class Client:
    def __init__(self):self.calls=0
    def verify_identity(self):return '9'
    def get_artwork_original_pages(self,wid):return ('https://i.pximg.net/synthetic',)
    def get_image_bytes(self,url):
        self.calls+=1;out=BytesIO();Image.new('RGB',(16,12),'red').save(out,format='PNG');return out.getvalue()

def create(tmp_path):
    client=Client()
    def record(wid):return RemoteBookmark(wid,'illustration','show','fixture '+wid,'9' if wid!='3' else '10','fixture author',(),'','2026-10-26T00:00:00Z',1,0,0)
    owner=ManagedPixivDownloads(tmp_path/'managed',client,resolve_record=record)
    for wid in ('1','2','3'):owner.run(SimpleNamespace(kind='pixiv-illust',target_id=wid),Context())
    return owner,client

def hashes(root):return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}

def test_original_integer_dhash_and_chain_components():
    image=Image.new('L',(9,8));image.putdata([x*20 for y in range(8) for x in range(9)])
    assert dhash(image)==(1<<64)-1
    image.putdata([160-x*20 for y in range(8) for x in range(9)])
    assert dhash(image)==0 and hamming(0,15)==4
    assert cluster_edges(4,[(0,1,4),(1,2,4)])==[(0,1,2)]

def test_verified_scan_stays_within_author_and_plan_is_read_only(tmp_path):
    owner,client=create(tmp_path);source=ManagedDuplicateInspection(owner)
    before=hashes(owner.root);calls=client.calls
    try:
        scan=source.scan();assert scan.scanned_files==3 and len(scan.groups)==1
        group=scan.groups[0]
        assert group.author_id=='9' and {p.work_id for p in group.items}=={'1','2'}
        assert group.edges==((0,1,0),) and all(p.thumb_b64 for p in group.items)
        selected=low_quality_keys(group);assert len(selected)==1
        plan=source.plan(scan,{group.group_id:selected})
        assert len(plan.items)==1 and plan.remaining==((group.group_id,1),)
        assert plan.mutation_allowed and plan.blocked_reason == ''
        assert plan.state_revision == owner._generations.state_snapshot()['revision']
        assert hashes(owner.root)==before and client.calls==calls
        with pytest.raises(DuplicateInspectionError):source.plan(scan,{group.group_id:{p.key for p in group.items}})
    finally:source.close();owner.close()

def page(wid,*,width=20,size=100):
    return DuplicatePage(str(wid),1,'9','author','outputs/proof/receipt.json','a'*64,'b'*64,width,10,size,None)

def group(items,gid='g'):
    return DuplicateGroup(gid,'9','author',tuple(items),((0,1,0),))

def scan_of(groups):return DuplicateScan('scan','a'*64,tuple(groups),sum(len(g.items) for g in groups),(),())

def test_quality_resolution_then_bytes_first_tie_and_visible_only():
    items=[page(i,width=20,size=100+i) for i in range(6)]
    items[0]=page(0,width=21,size=1)
    assert best_item_index(items)==0
    items[5]=page(5,width=22,size=1)
    g=group(items);assert best_item_index(items)==5
    assert low_quality_keys(g)==frozenset(f'{i}:1' for i in range(5))
    scan=scan_of([g])
    selected,remaining=validate_selection(scan,{'g':low_quality_keys(g)})
    assert len(selected)==5 and remaining==(('g',1),)
    with pytest.raises(DuplicateInspectionError):validate_selection(scan,{'g':{'5:1'}})
    assert best_item_index([page('1'),page('2')])==0

def test_filtered_or_nonvisible_groups_cannot_enter_plan():
    groups=[group([page('1'),page('2')],str(i)) for i in range(51)]
    scan=scan_of(groups)
    assert len(visible_groups(scan))==50
    assert not visible_groups(scan,'missing')
    with pytest.raises(DuplicateInspectionError):validate_selection(scan,{'50':{'1:1'}})
    with pytest.raises(DuplicateInspectionError):validate_selection(scan,{'0':{'1:1'}},'missing')
    with pytest.raises(DuplicateInspectionError):validate_selection(scan,{'0':{'unknown:1'}})

def test_stale_scan_or_changed_generation_rejected(tmp_path):
    owner,_=create(tmp_path);source=ManagedDuplicateInspection(owner)
    try:
        old=source.scan();current=source.scan();g=current.groups[0]
        with pytest.raises(DuplicateInspectionError):source.plan(old,{g.group_id:low_quality_keys(g)})
        owner.run(SimpleNamespace(kind='pixiv-illust',target_id='4'),Context())
        with pytest.raises(DuplicateInspectionError):source.plan(current,{g.group_id:low_quality_keys(g)})
    finally:source.close();owner.close()

def test_corrupt_saved_page_does_not_become_empty_success(tmp_path):
    owner,_=create(tmp_path);source=ManagedDuplicateInspection(owner)
    try:
        relative,receipt,digest=owner.saved_receipts()[0]
        (owner.root/relative).parent.joinpath(receipt['files'][0]['name']).write_bytes(b'corrupt fixture')
        with pytest.raises(ValueError):source.scan()
    finally:source.close();owner.close()

def test_unreadable_page_is_reported_without_fake_candidate(tmp_path,monkeypatch):
    owner,_=create(tmp_path);source=ManagedDuplicateInspection(owner)
    original=owner.read_saved_page
    def read(relative,page,digest):
        if 'work-2-' in relative:raise OSError('transient fixture read')
        return original(relative,page,digest)
    monkeypatch.setattr(owner,'read_saved_page',read)
    try:
        scan=source.scan();assert scan.scanned_files==2 and scan.unreadable==('2:1',) and not scan.groups
    finally:source.close();owner.close()

def test_original_author_limit_prevents_pairwise_work(tmp_path,monkeypatch):
    owner,_=create(tmp_path);source=ManagedDuplicateInspection(owner)
    row=owner.saved_receipts()[0]
    monkeypatch.setattr(owner,'saved_receipts',lambda:tuple(row for _ in range(2001)))
    monkeypatch.setattr(owner,'read_saved_page',lambda *_:pytest.fail('over-limit author must not decode'))
    try:
        scan=source.scan();assert scan.skipped_authors==(('9',2001),) and scan.scanned_files==0 and not scan.groups
    finally:source.close();owner.close()

def test_closed_or_foreign_owner_refused(tmp_path):
    with pytest.raises(DuplicateInspectionError):ManagedDuplicateInspection(object())
    owner,_=create(tmp_path);source=ManagedDuplicateInspection(owner);source.close()
    try:
        with pytest.raises(DuplicateInspectionError):source.scan()
    finally:owner.close()


def legacy_ledger_fixture(tmp_path):
    """Only legacy file_ledger evidence can create an undecidable group."""
    path=tmp_path/'source-legacy.sqlite'
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE works(work_id TEXT PRIMARY KEY, title TEXT, user_id TEXT);
            CREATE TABLE following_users(user_id TEXT PRIMARY KEY, name TEXT);
            CREATE TABLE file_ledger(work_id TEXT, page INTEGER, status TEXT,
                store_path TEXT, entry_name TEXT, reason TEXT,
                ambiguous_group TEXT, resolved_by TEXT);
            INSERT INTO following_users VALUES('9','Legacy Author');
            INSERT INTO works VALUES ('101','Ambiguous','9'),('102','Ambiguous','9'),
                ('201','Resolved','9'),('202','Resolved','9'),
                ('301','Unrelated','10');
            INSERT INTO file_ledger VALUES
                ('101',1,'unsure','folder','image-1.jpg','duplicate title','a',NULL),
                ('101',2,'unsure','folder','image-2.jpg','duplicate title','a',NULL),
                ('102',1,'unsure','folder','image-1.jpg','duplicate title','a',NULL),
                ('102',2,'unsure','folder','image-2.jpg','duplicate title','a',NULL),
                ('201',1,'present','folder','resolved.jpg','duplicate title','b','manual'),
                ('202',1,'unsure','folder','resolved.jpg','duplicate title','b',NULL),
                ('301',1,'present','folder','safe.jpg','',NULL,NULL),
                ('999',1,'unsure','folder','orphan.jpg','duplicate title','c',NULL);
        """)
    return path


def test_legacy_undecidable_groups_use_original_status_manual_exclusion_and_page_rows(tmp_path):
    path=legacy_ledger_fixture(tmp_path)
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    groups=read_undecidable_ledger(path)
    assert len(groups)==1
    assert groups[0]['ambiguous_group']=='a'
    assert [candidate['work_id'] for candidate in groups[0]['candidates']]==['101','102']
    assert groups[0]['candidates'][0]['pages']==[
        {'page':1,'store_path':'folder','entry_name':'image-1.jpg'},
        {'page':2,'store_path':'folder','entry_name':'image-2.jpg'},
    ]
    assert len(visible_undecidable_groups(groups,'legacy'))==1
    assert len(visible_undecidable_groups(groups,'102'))==1
    assert visible_undecidable_groups(groups,'no match')==()
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    assert list(tmp_path.iterdir())==[path]
    assert read_undecidable_ledger(path)==groups


def test_undecidable_ledger_frozen_wal_header_uses_immutable_connection(tmp_path,monkeypatch):
    from pixivvault_nicegui.duplicate_inspection import read_undecidable_ledger
    path=legacy_ledger_fixture(tmp_path)
    with sqlite3.connect(path) as writer:
        assert writer.execute('PRAGMA journal_mode=WAL').fetchone()[0]=='wal'
    original_connect=sqlite3.connect
    uris=[]
    before=hashlib.sha256(path.read_bytes()).hexdigest()

    def intercept(database,*args,**kwargs):
        if isinstance(database,str) and database.startswith('file:') and 'source-legacy' in database:
            uris.append(database)
        return original_connect(database,*args,**kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3,'connect',intercept)
        assert len(read_undecidable_ledger(path))==1
    assert uris and all('immutable=1' in uri for uri in uris)
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    assert list(tmp_path.iterdir())==[path]


def test_undecidable_ledger_rejects_active_wal_without_touching_it(tmp_path):
    from pixivvault_nicegui.duplicate_inspection import read_undecidable_ledger
    path=legacy_ledger_fixture(tmp_path)
    writer=sqlite3.connect(path)
    try:
        assert writer.execute('PRAGMA journal_mode=WAL').fetchone()[0]=='wal'
        writer.execute("INSERT INTO works VALUES('999','new','9')")
        writer.commit()
        assert (tmp_path/(path.name+'-wal')).exists()
        originals={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()}
        with pytest.raises(DuplicateInspectionError):
            read_undecidable_ledger(path)
        assert originals=={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in tmp_path.iterdir()}
    finally:writer.close()


def test_undecidable_reader_does_not_invent_groups_from_managed_receipts(tmp_path):
    owner,_=create(tmp_path);source=ManagedDuplicateInspection(owner)
    try:
        assert not source.undecidable_available
        assert source.undecidable_groups() is None  # Unavailable, not zero.
        missing=tmp_path/'missing.sqlite'
        with pytest.raises(DuplicateInspectionError):
            read_undecidable_ledger(missing)
        assert not missing.exists()
        legacy=legacy_ledger_fixture(tmp_path)
        sourced=ManagedDuplicateInspection(owner,undecidable_db=legacy)
        try:
            assert sourced.undecidable_available
            assert len(sourced.undecidable_groups())==1
            assert sourced.scan().groups  # Near dHash still comes from receipt source.
        finally:sourced.close()
    finally:source.close();owner.close()


def test_undecidable_empty_is_distinct_from_missing_or_unusable_original_ledger(tmp_path):
    path=legacy_ledger_fixture(tmp_path)
    # Adjust only this synthetic fixture to a fully resolved original ledger.
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE file_ledger SET status='present' WHERE ambiguous_group='a'")
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    assert read_undecidable_ledger(path)==()
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    broken=tmp_path/'unrelated.sqlite'
    sqlite3.connect(broken).close()
    with pytest.raises(DuplicateInspectionError):
        read_undecidable_ledger(broken)


def test_original_undecidable_display_cap_does_not_change_underlying_group_count():
    rows=tuple({'candidates':({'work_id':str(i),'author_name':'title artist'},),
                'ambiguous_group':str(i)} for i in range(61))
    assert len(visible_undecidable_groups(rows))==50
    assert len(visible_undecidable_groups(rows,limit=None))==61
    assert len(visible_undecidable_groups(rows,'title'))==50
    assert len(visible_undecidable_groups(rows,'60'))==1
    assert len(rows)==61

@pytest.mark.asyncio
async def test_review_ui_keeps_one_and_never_executes_move(tmp_path):
    from nicegui import Client as GuiClient,ui
    from pixivvault_nicegui.ui.duplicate_view import DuplicateView
    owner,client=create(tmp_path);source=ManagedDuplicateInspection(owner);before=hashes(owner.root)
    gui=GuiClient(ui.page('/_duplicate_fixture'))
    try:
        with gui:
            view=DuplicateView(source);view.set_active(True);await view.scan()
            assert '1組' in view.notice.text
            view.select_low();assert '1枚' in view.summary.text
            await view.plan();assert '移動していません' in view.summary.text
            group=view.scan_result.groups[0]
            for item in group.items:view.toggle(group.group_id,item.key,True,view.selection_revision)
            assert sum(len(v) for v in view.selections.values())==1
            assert '1枚以上' in view.notice.text
            view.filter('missing');assert not any(view.selections.values())
        assert hashes(owner.root)==before
    finally:gui.delete();source.close();owner.close()
