from pathlib import Path
from uuid import uuid4
from collections import namedtuple
import pytest
from required_network_guard import no_network
from test_required_legacy_duplicates import setup,select
from pixivvault_nicegui import legacy_physical_quarantine as module
class PowerLoss(BaseException):pass

def test_normal_zip_rollback_never_copies_before(tmp_path,monkeypatch):
 source,legacy,owner,_,_,archive=setup(tmp_path);before=archive.read_bytes();inode=archive.stat().st_ino
 real=module._copy
 def guarded(src,dst,**kw):
  if dst.name.startswith('.pv-recover-'):raise OSError('generated no bytes available for rollback copy')
  return real(src,dst,**kw)
 def fail(where):
  if where=='physical-installed:2:1':raise OSError('generated forward failure')
 try:
  monkeypatch.setattr(module,'_copy',guarded);monkeypatch.setattr(legacy,'_checkpoint',fail)
  result=source.execute(select(source,{'2'}),operation_id=uuid4().hex,mode='move',confirmed_move=True)
  assert result.failed and not result.recovery_required
  assert archive.read_bytes()==before and archive.stat().st_ino==inode
  assert not legacy.state()['exclusions']
 finally:source.close();owner.close()

def interrupted(tmp_path,monkeypatch):
 source,legacy,owner,snapshot,original,archive=setup(tmp_path);before=archive.read_bytes()
 def fail(where):
  if where=='physical-installed:2:1':raise PowerLoss()
 with monkeypatch.context() as p:
  p.setattr(legacy,'_checkpoint',fail)
  with pytest.raises(PowerLoss):
   source.execute(select(source,{'2'}),operation_id=uuid4().hex,mode='move',confirmed_move=True)
 path=next(legacy._physical.journals.glob('*/intent.json'))
 intent,_=legacy._physical._read_intent(path)
 return source,legacy,owner,archive,before,intent

@pytest.mark.parametrize('legacy_missing',[False,True],ids=['cross-volume','legacy-no-retained'])
def test_copy_fallback_shortage_preserves_all_bytes_and_notice(tmp_path,monkeypatch,legacy_missing):
 source,legacy,owner,archive,before,intent=interrupted(tmp_path,monkeypatch)
 physical=legacy._physical;unit=physical._unit(intent['unit'])
 if legacy_missing:(unit/'retained').rename(tmp_path/'preserved-generated-original')
 changed=archive.read_bytes()
 usage=namedtuple('Usage','total used free')
 try:
  with monkeypatch.context() as p:
   if not legacy_missing:p.setattr(module,'_same_filesystem',lambda *args:False)
   p.setattr(module.shutil,'disk_usage',lambda _:usage(100,100,0))
   physical.recover()
   assert intent['source'] in physical.blocked_sources
   assert '空き容量' in legacy.recovery_notice()
   assert archive.read_bytes()==changed and (unit/'before').read_bytes()==before
   assert not list(archive.parent.glob('.pv-recover-*'))
  source.recover_legacy()
  assert archive.read_bytes()==before and not legacy.state()['exclusions']
 finally:source.close();owner.close()

@pytest.mark.parametrize('legacy_missing',[False,True],ids=['cross-volume','legacy-no-retained'])
def test_copy_fallback_success_keeps_before_and_reopen_integrity(tmp_path,monkeypatch,legacy_missing):
 from pixivvault_nicegui.legacy_quarantine import LegacyQuarantine
 source,legacy,owner,archive,before,intent=interrupted(tmp_path,monkeypatch)
 original=legacy.catalog.root;snapshot=legacy.original_snapshot;unit=legacy._physical._unit(intent['unit'])
 if legacy_missing:(unit/'retained').rename(tmp_path/'preserved-generated-original')
 try:
  with monkeypatch.context() as p:
   if not legacy_missing:p.setattr(module,'_same_filesystem',lambda *args:False)
   legacy._physical.recover(strict=True)
  assert archive.read_bytes()==before and (unit/'before').read_bytes()==before
  source.close()
  reopened=LegacyQuarantine(original,snapshot,owner=owner)
  try:assert not reopened.state()['exclusions'] and archive.read_bytes()==before
  finally:reopened.close()
 finally:source.close();owner.close()

def test_restore_rename_failure_preserves_original_inode_and_explicit_recovery(tmp_path,monkeypatch):
 source,legacy,owner,archive,before,intent=interrupted(tmp_path,monkeypatch)
 unit=legacy._physical._unit(intent['unit']);inode=(unit/'retained').stat().st_ino
 rename=module.os.rename
 def deny(src,dst):
  if Path(src).name=='retained':raise PermissionError('generated sharing violation')
  return rename(src,dst)
 try:
  with monkeypatch.context() as p:
   p.setattr(module.os,'rename',deny)
   legacy._physical.recover()
   assert intent['source'] in legacy._physical.blocked_sources
   assert (unit/'retained').read_bytes()==before and (unit/'before').read_bytes()==before
  source.recover_legacy()
  assert archive.read_bytes()==before and archive.stat().st_ino==inode
 finally:source.close();owner.close()

def test_capacity_probe_failure_is_before_batch_mutation(tmp_path,monkeypatch):
 source,legacy,owner,_,_,archive=setup(tmp_path);before=archive.read_bytes()
 try:
  monkeypatch.setattr(module.shutil,'disk_usage',lambda _:(_ for _ in ()).throw(OSError('generated usage unavailable')))
  result=source.execute(select(source,{'2'}),operation_id=uuid4().hex,mode='move',confirmed_move=True)
  assert not result.committed and result.unprocessed
  assert '空き容量' in result.item_results[0].reason
  assert archive.read_bytes()==before and not legacy.state()['exclusions']
 finally:source.close();owner.close()

def test_zip_hardlink_restored_to_original_inode_without_copy(tmp_path,monkeypatch):
 import os
 source,legacy,owner,_,_,archive=setup(tmp_path);before=archive.read_bytes()
 alias=tmp_path/'generated-alias';os.link(archive,alias);inode=archive.stat().st_ino
 def fail(where):
  if where=='physical-installed:2:1':raise OSError('generated failure')
 try:
  monkeypatch.setattr(legacy,'_checkpoint',fail)
  result=source.execute(select(source,{'2'}),operation_id=uuid4().hex,mode='move',confirmed_move=True)
  assert result.failed and not result.recovery_required
  assert archive.read_bytes()==alias.read_bytes()==before
  assert archive.stat().st_ino==alias.stat().st_ino==inode
 finally:source.close();owner.close()

def test_insufficient_capacity_stops_whole_batch_before_source_changes(tmp_path,monkeypatch):
 source,legacy,owner,_,original,archive=setup(tmp_path)
 before={p.relative_to(original):p.read_bytes() for p in original.rglob('*') if p.is_file()}
 usage=namedtuple('Usage','total used free')
 try:
  monkeypatch.setattr(module.shutil,'disk_usage',lambda _:usage(100,100,0))
  result=source.execute(select(source,{'1','2'}),operation_id=uuid4().hex,mode='move',confirmed_move=True)
  assert not result.committed and len(result.unprocessed)==2
  assert all('空き容量' in item.reason for item in result.item_results)
  assert not legacy.state()['exclusions']
  assert before=={p.relative_to(original):p.read_bytes() for p in original.rglob('*') if p.is_file()}
  assert not list(legacy._physical.journals.glob('*/intent.json'))
 finally:source.close();owner.close()

@pytest.mark.parametrize('point',['physical-rollback-displaced:2:1','physical-rollback-restored:2:1'])
def test_reopen_around_rename_restores_exact_original(tmp_path,monkeypatch,point):
 from pixivvault_nicegui.legacy_quarantine import LegacyQuarantine
 source,legacy,owner,snapshot,original,archive=setup(tmp_path);before=archive.read_bytes();inode=archive.stat().st_ino
 def fail(where):
  if where=='physical-installed:2:1':raise OSError('generated rollback trigger')
  if where==point:raise PowerLoss()
 try:
  with monkeypatch.context() as p:
   p.setattr(legacy,'_checkpoint',fail)
   with pytest.raises(PowerLoss):source.execute(select(source,{'2'}),operation_id=uuid4().hex,mode='move',confirmed_move=True)
  source.close()
  reopened=LegacyQuarantine(original,snapshot,owner=owner)
  try:
   assert not reopened.state()['exclusions']
   assert archive.read_bytes()==before and archive.stat().st_ino==inode
  finally:reopened.close()
 finally:source.close();owner.close()
