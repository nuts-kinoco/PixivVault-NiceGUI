"""CoS fixture regression for durable extension command acceptance and child CAS."""
import asyncio
from types import SimpleNamespace
from threading import Event
import time

from pixivvault_nicegui.extension_application import ExtensionApplication
from pixivvault_nicegui.extension_bridge import ExtensionDownload
from pixivvault_nicegui.job_service import JobService
from pixivvault_nicegui.media_jobs import MediaJobs


def eventually(predicate):
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        result=predicate()
        if result:return result
        time.sleep(.01)
    raise AssertionError('fixture job did not settle')


def fixture(tmp_path, *, fail=None, gate=None):
    def run(job, context):
        if gate is not None:
            while not gate.is_set():
                context.checkpoint();time.sleep(.01)
        if fail is not None and fail.is_set():raise ValueError('synthetic failure')
        context.checkpoint()
        return 'fixture verified receipt'
    child=JobService(run,tmp_path/'media.sqlite3')
    service=SimpleNamespace(closing=False,jobs=child,
        managed_downloads=SimpleNamespace(verified_saved_receipt=lambda wid:('receipt',{},'sha','date')))
    async def save(wid,operation):
        return child.enqueue('pixiv-illust',wid,operation_id=operation)
    service.save_explicit_work=save
    owner=ExtensionApplication(service,tmp_path/'commands')
    service.jobs=MediaJobs(child,owner)
    return service,child,owner


def snapshot(owner,jid):return next(j for j in owner.snapshot() if j.job_id==jid)


def test_acceptance_is_durable_and_not_early_save_success(tmp_path):
    gate=Event();service,child,owner=fixture(tmp_path,gate=gate)
    try:
        jid=owner.enqueue(ExtensionDownload('work','123'))
        eventually(lambda:child.snapshot())
        assert snapshot(owner,jid).state in ('queued','running')
        assert owner.enqueue(ExtensionDownload('work','123'))==jid
        gate.set()
        eventually(lambda:snapshot(owner,jid).state=='done')
        assert child.snapshot()[0].state=='done'
    finally:gate.set();owner.close();child.close()


def test_child_failure_then_explicit_parent_retry_preserves_job_identity(tmp_path):
    fail=Event();fail.set();service,child,owner=fixture(tmp_path,fail=fail)
    try:
        jid=owner.enqueue(ExtensionDownload('work','123'))
        eventually(lambda:snapshot(owner,jid).state=='error')
        original=child.snapshot()[0]
        fail.clear();parent=snapshot(owner,jid)
        assert owner.control(jid,'retry',expected_control_revision=parent.control_revision)
        eventually(lambda:snapshot(owner,jid).state=='done')
        assert child.snapshot()[0].job_id==original.job_id
        assert child.snapshot()[0].attempt==2
        assert snapshot(owner,jid).attempt==2
    finally:owner.close();child.close()


def test_parent_controls_child_with_revision_fences(tmp_path):
    gate=Event();service,child,owner=fixture(tmp_path,gate=gate)
    try:
        jid=owner.enqueue(ExtensionDownload('work','123'))
        eventually(lambda:child.snapshot() and child.snapshot()[0].state=='running')
        parent=snapshot(owner,jid)
        assert not owner.control(jid,'pause',expected_control_revision=parent.control_revision+10)
        assert owner.control(jid,'pause',expected_control_revision=parent.control_revision)
        eventually(lambda:child.snapshot()[0].state=='paused')
        assert owner.control(jid,'resume',expected_control_revision=snapshot(owner,jid).control_revision)
        eventually(lambda:child.snapshot()[0].state=='running')
        assert owner.control(jid,'stop',expected_control_revision=snapshot(owner,jid).control_revision)
        eventually(lambda:snapshot(owner,jid).state=='stopped')
        eventually(lambda:child.snapshot()[0].state=='stopped')
    finally:gate.set();owner.close();child.close()


def test_failed_command_reopens_without_fetch_and_can_retry(tmp_path):
    fail=Event();fail.set();service,child,owner=fixture(tmp_path,fail=fail)
    jid=owner.enqueue(ExtensionDownload('work','123'))
    eventually(lambda:snapshot(owner,jid).state=='error')
    owner.close();child.close()
    fail.clear();service,child,owner=fixture(tmp_path,fail=fail)
    try:
        assert snapshot(owner,jid).state=='error'
        assert child.snapshot()[0].attempt==1
        assert owner.control(jid,'retry',expected_control_revision=snapshot(owner,jid).control_revision)
        eventually(lambda:snapshot(owner,jid).state=='done')
    finally:owner.close();child.close()


def test_author_new_only_flag_and_all_children_are_awaited(tmp_path):
    service,child,owner=fixture(tmp_path)
    observed=[]
    from pixivvault_nicegui.author_download import Difference
    candidates=[SimpleNamespace(key=str(n),eligible=True,difference=Difference.NEW) for n in (1,2)]
    service.author_media_preview=lambda aid,**kw:observed.append((aid,kw)) or candidates
    service.enqueue_author_media=lambda c,operation,**kw:(child.enqueue('pixiv-illust',c.key,operation_id=operation),True,'proof')
    try:
        jid=owner.enqueue(ExtensionDownload('author','91',new_only=True))
        eventually(lambda:snapshot(owner,jid).state=='done')
        assert observed==[('91',{'new_only':True})]
        assert len(child.snapshot())==2
        assert all(j.state=='done' for j in child.snapshot())
    finally:owner.close();child.close()


def test_current_receipt_is_required_even_when_child_history_says_done(tmp_path):
    service,child,owner=fixture(tmp_path)
    service.managed_downloads.verified_saved_receipt=lambda wid:None
    try:
        jid=owner.enqueue(ExtensionDownload('work','123'))
        eventually(lambda:snapshot(owner,jid).state=='error')
        assert child.snapshot()[0].state=='done'
    finally:owner.close();child.close()


def test_explicit_parent_retry_restores_retired_failed_child_without_new_acceptance(tmp_path):
    fail=Event();fail.set();service,child,owner=fixture(tmp_path,fail=fail)
    try:
        jid=owner.enqueue(ExtensionDownload('work','123'))
        eventually(lambda:snapshot(owner,jid).state=='error')
        original=child.snapshot()[0];fail.clear()
        for number in range(50):
            child.enqueue('pixiv-illust',str(1000+number),operation_id='fixture-'+str(number))
        eventually(lambda:len(child.snapshot())==50 and all(j.state=='done' for j in child.snapshot()))
        assert all(j.job_id!=original.job_id for j in child.snapshot())
        assert child.retained_job(original.job_id).state=='error'
        operations=child.operation_count
        parent=snapshot(owner,jid)
        assert owner.control(jid,'retry',expected_control_revision=parent.control_revision)
        eventually(lambda:snapshot(owner,jid).state=='done')
        recovered=child.retained_job(original.job_id)
        assert recovered.state=='done' and recovered.attempt==2
        assert child.operation_count==operations
    finally:owner.close();child.close()
