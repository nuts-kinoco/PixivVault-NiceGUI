import json
import os
from pathlib import Path
from uuid import uuid4
import pytest
from test_required_legacy_duplicates import setup, select, png

class PowerLoss(BaseException): pass

def pending_ready(tmp_path,monkeypatch):
    source,legacy,owner,snapshot,original,archive=setup(tmp_path)
    def crash(where):
        if where=='physical-source-retained:2:1':raise PowerLoss()
    with monkeypatch.context() as patch:
        patch.setattr(legacy,'_checkpoint',crash)
        with pytest.raises(PowerLoss):
            source.execute(select(source,{'2'}),operation_id=uuid4().hex,mode='move',confirmed_move=True)
    path=next(legacy._physical.journals.glob('*/intent.json'))
    return source,legacy,owner,json.loads(path.read_bytes()),original

def test_scratch_after_validation_path_swap_never_deletes_foreign_file(tmp_path,monkeypatch):
    source,legacy,owner,intent,original=pending_ready(tmp_path,monkeypatch)
    foreign=tmp_path/'generated-foreign'
    foreign.write_bytes(b'foreign contents')
    ready=legacy._physical._scratch(intent,'ready')
    unlink=Path.unlink
    def raced(path,*args,**kwargs):
        if path==ready:
            path.rename(tmp_path/'preserved-generated-ready')
            foreign.rename(path)
        return unlink(path,*args,**kwargs)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(Path,'unlink',raced)
            legacy._physical._cleanup_one(intent,'ready')
        # The old path-based deletion loses both paths to these foreign bytes.
        assert (foreign.exists() and foreign.read_bytes()==b'foreign contents') or (
            ready.exists() and ready.read_bytes()==b'foreign contents')
    finally:source.close();owner.close()

def test_hardlink_move_interruption_reopens_to_same_inode(tmp_path,monkeypatch):
    from pixivvault_nicegui.legacy_quarantine import LegacyQuarantine
    source,legacy,owner,snapshot,original,_=setup(tmp_path)
    image=original/'Author(91)'/'Folder(1).png'
    alias=tmp_path/'generated-bookmark-alias.png'
    os.link(image,alias)
    ino=image.stat().st_ino
    try:
        def crash(where):
            if where=='physical-installed:1:1':raise PowerLoss()
        with monkeypatch.context() as patch:
            patch.setattr(legacy,'_checkpoint',crash)
            with pytest.raises(PowerLoss):
                source.execute(select(source,{'1'}),operation_id=uuid4().hex,mode='move',confirmed_move=True)
        source.close()
        reopened=LegacyQuarantine(original,snapshot,owner=owner)
        try:
            assert not reopened.state()['exclusions']
            assert image.read_bytes()==alias.read_bytes()==png()
            assert image.stat().st_ino==alias.stat().st_ino==ino
            assert image.stat().st_nlink==2
        finally:reopened.close()
    finally:source.close();owner.close()


def test_windows_scratch_handle_blocks_swap_and_write_after_validation(tmp_path,monkeypatch):
    assert os.name=='nt', 'Windows kernel acceptance requires Windows'
    source,legacy,owner,intent,original=pending_ready(tmp_path,monkeypatch)
    entered=[]
    ready=legacy._physical._scratch(intent,'ready')
    def attack(path):
        entered.append(True)
        with pytest.raises(OSError):path.rename(tmp_path/'generated-swapped-ready')
        with pytest.raises(OSError):
            with path.open('wb') as stream:stream.write(b'foreign data')
    try:
        monkeypatch.setattr(legacy._physical,'_before_scratch_delete',attack,raising=False)
        legacy._physical._cleanup_one(intent,'ready')
        assert entered==[True]
        assert not ready.exists()
        source.recover_legacy()
    finally:source.close();owner.close()

@pytest.mark.parametrize('failure',[False,True],ids=['success','rollback'])
def test_legacy_hardlink_move_retains_other_alias_and_failure_safety(tmp_path,monkeypatch,failure):
    source,legacy,owner,_,original,_=setup(tmp_path)
    image=original/'Author(91)'/'Folder(1).png'
    alias=tmp_path/'generated-bookmark-alias.png'
    os.link(image,alias)
    ino=alias.stat().st_ino
    try:
        plan=select(source,{'1'})
        assert image.stat().st_nlink==2
        def fail(where):
            if where=='physical-installed:1:1':raise PermissionError('generated failure')
        with monkeypatch.context() as patch:
            if failure:patch.setattr(legacy,'_checkpoint',fail)
            result=source.execute(plan,operation_id=uuid4().hex,mode='move',confirmed_move=True)
        assert alias.read_bytes()==png() and alias.stat().st_ino==ino
        assert (original/'Author(91)'/'Peer(4).png').read_bytes()==png()
        if failure:
            assert not result.committed and [p.key for p in result.failed]==['1:1']
            assert image.read_bytes()==png() and image.stat().st_ino==ino
            assert not legacy.state()['exclusions']
            assert '1:1' in {p.key for g in source.latest_scan.groups for p in g.items}
            source.recover_legacy()
            group=next(g for g in source.latest_scan.groups if any(p.key=='1:1' for p in g.items))
            retry=source.plan(source.latest_scan,{group.group_id:{'1:1'}})
            assert len(source.execute(retry,operation_id=uuid4().hex,mode='move',confirmed_move=True).committed)==1
        else:
            assert [p.key for p in result.committed]==['1:1'] and not result.failed
        assert not image.exists()
        moved=next(legacy._physical.root.glob('*/Folder(1).png'))
        assert moved.stat().st_ino==ino and moved.read_bytes()==alias.read_bytes()
        from pixivvault_nicegui.managed_downloads import _safe_path
        with pytest.raises(ValueError):_safe_path(alias,file=True)
    finally:source.close();owner.close()

