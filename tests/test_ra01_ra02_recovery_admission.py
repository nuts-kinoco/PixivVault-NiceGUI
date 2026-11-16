from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from threading import Event,Thread,current_thread
import json
import pytest
from required_network_guard import no_network
from test_required_legacy_duplicates import setup,select
from pixivvault_nicegui import legacy_physical_quarantine as physical
from pixivvault_nicegui.local_media_adapter import MediaUnavailable
from pixivvault_nicegui.fs_paths import native_io_path

class PowerLoss(BaseException):pass
def record(name,value):
    pass  # Observations are asserted below; immutable candidate is never written.
def crash_installed(source,legacy,monkeypatch):
    plan=select(source,{'2'})
    def crash(where):
        if where=='physical-installed:2:1':raise PowerLoss()
    with monkeypatch.context() as patch:
        patch.setattr(legacy,'_checkpoint',crash)
        with pytest.raises(PowerLoss):
            source.execute(plan,operation_id=uuid4().hex,mode='move',confirmed_move=True)

def test_same_blocked_zip_download_admission(tmp_path,monkeypatch):
    source,legacy,owner,*_=setup(tmp_path)
    try:
        crash_installed(source,legacy,monkeypatch)
        with monkeypatch.context() as patch:
            patch.setattr(legacy._physical,'_rollback',lambda _:(_ for _ in ()).throw(OSError('generated full disk')))
            legacy.state()
            assert legacy.recovery_status()['blocked_sources']==('Other(92).zip',)
            result={'catalog_indexed':legacy.catalog._indexed,'blocked':list(legacy.recovery_status()['blocked_sources'])}
            for wid in ('2','3'):
                row={}
                for name,call in [('excluded_pages',lambda:legacy.excluded_pages(wid)),
                    ('download_state',lambda:legacy.download_state(SimpleNamespace(work_id=wid,author_id='92',page_count=2 if wid=='3' else 1)))]:
                    try:value=call();row[name]={'allowed':True,'value':repr(value)}
                    except MediaUnavailable:row[name]={'allowed':False}
                result[wid]=row
            record('same-source-admission',result)
            assert not result['3']['download_state']['allowed'], 'blocked ZIP sibling download must be refused'
    finally:source.close();owner.close()


def test_blocked_source_is_not_temporarily_reenabled_during_recovery(tmp_path,monkeypatch):
    source,legacy,owner,snapshot,*_=setup(tmp_path)
    reader_done=Event();recovery_inside=Event();snapshot_returned=Event()
    release_reader=Event();release_recovery=Event();threads=[];catalog=None
    result={}
    try:
        _,catalog=legacy.project(snapshot)
        crash_installed(source,legacy,monkeypatch)
        physical_store=legacy._physical
        def fail_rollback(_):
            if current_thread().name=='pv-review-recover':
                recovery_inside.set()
                if not release_recovery.wait(5):raise AssertionError('review recover gate timed out')
            raise OSError('generated full disk')
        monkeypatch.setattr(physical_store,'_rollback',fail_rollback)
        legacy.state();legacy.catalog._ensure_index()
        original_state=legacy.state
        def state(*args,**kwargs):
            value=original_state(*args,**kwargs)
            if current_thread().name=='pv-review-reader' and not physical_store.recovering:
                snapshot_returned.set()
                if not release_reader.wait(5):raise AssertionError('review reader gate timed out')
            return value
        monkeypatch.setattr(legacy,'state',state)
        def reader():
            try:result['read_bytes']=len(catalog.get('3',0,'original').data)
            except BaseException as error:result['reader_error']=type(error).__name__
            finally:reader_done.set()
        def recover():
            try:legacy.state()
            except BaseException as error:result['recover_error']=type(error).__name__
        t=Thread(target=reader,name='pv-review-reader');threads.append(t);t.start()
        assert snapshot_returned.wait(5)
        t=Thread(target=recover,name='pv-review-recover');threads.append(t);t.start()
        assert recovery_inside.wait(5)
        result['blocked_during_recovery']=sorted(physical_store.blocked_sources)
        release_reader.set();assert reader_done.wait(5)
        release_recovery.set()
        for t in threads:t.join(5);assert not t.is_alive()
        result['blocked_after_recovery']=sorted(physical_store.blocked_sources)
        record('recovery-admission-race',result)
        assert result.get('reader_error')=='MediaUnavailable', 'failed source must remain blocked during another recovery'
    finally:
        release_reader.set();release_recovery.set()
        for t in threads:t.join(5)
        if catalog:catalog.close()
        source.close();owner.close()

@pytest.mark.parametrize('reopen',[False,True])
def test_ra01_missing_zip_still_blocks_all_bound_works(tmp_path,monkeypatch,reopen):
    from test_required_legacy_duplicates import setup,select
    from pixivvault_nicegui.legacy_quarantine import LegacyQuarantine
    from pixivvault_nicegui.local_media_adapter import MediaUnavailable
    from types import SimpleNamespace
    source,legacy,owner,snapshot,original,archive=setup(tmp_path)
    reopened=None
    try:
        plan=select(source,{'2'})
        def crash(stage):
            if stage=='physical-source-retained:2:1':raise PowerLoss()
        with monkeypatch.context() as patch:
            patch.setattr(legacy,'_checkpoint',crash)
            with pytest.raises(PowerLoss):
                source.execute(plan,operation_id=uuid4().hex,mode='move',confirmed_move=True)
        assert not archive.exists()
        if reopen:
            source.close()
            reopened=LegacyQuarantine(original,snapshot,owner=owner)
            legacy=reopened
        with monkeypatch.context() as patch:
            patch.setattr(legacy._physical,'_rollback',lambda _:(_ for _ in ()).throw(OSError('generated full disk')))
            legacy.state()
            assert not legacy.catalog._indexed
            with pytest.raises(MediaUnavailable):
                legacy.download_state(SimpleNamespace(work_id='3',author_id='92',page_count=2))
            assert not legacy.excluded_pages('1')
    finally:
        if reopened:reopened.close()
        source.close();owner.close()
