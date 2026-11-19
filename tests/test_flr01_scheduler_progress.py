"""FLR-01: progress during a later author's inventory; generated data, no HTTP."""
import asyncio
from contextlib import nullcontext
from threading import Event
from types import SimpleNamespace
from datetime import datetime, timezone
import pytest
from nicegui import Client,ui
from pixivvault_nicegui.managed_scheduler import ManagedScheduler,submit_author_batch
from pixivvault_nicegui.author_media import AuthorMediaCandidate
from pixivvault_nicegui.author_download import Difference
from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.ui.scheduler_view import SchedulerView

@pytest.mark.asyncio
@pytest.mark.parametrize('route',('legacy','media'))
@pytest.mark.parametrize('ending',('complete','failure','cancel'))
async def test_accepted_count_visible_before_next_author_finishes(tmp_path,route,ending):
    ledger=ManagedScheduler(tmp_path)
    ledger.configure_all(author_ids=('11','12'),interval_hours=24,startup_check=False,
        expected_revision=ledger.snapshot().revision,now=datetime.now(timezone.utc))
    entered,release,cancel=Event(),Event(),Event()
    queue=[]
    record=RemoteBookmark('123','illustration','show','Generated','11','Generated author',
        (), '', '',1,0,0,page_count_known=True)
    def inventory(aid):
        if aid=='11':
            if route=='media':
                return (AuthorMediaCandidate('illustration','123','11','generated',1,'Generated',Difference.NEW),)
            return ({'id':'123','user_id':'11','type':0,'update_date':'generated','page_count':1},)
        entered.set()
        if not release.wait(10):raise RuntimeError('fixture timed out')
        if ending=='failure':raise ValueError('generated read failure')
        return ()
    def enqueue(*args,**kwargs):
        job=SimpleNamespace(job_id='b'*32,kind='pixiv-illust',state='queued',target_id='123')
        queue.append(job)
        return (job,True,'a'*64) if route=='media' else (job,True)
    services=SimpleNamespace(closing=False,author_media=object() if route=='media' else None,
        jobs=SimpleNamespace(snapshot=lambda:tuple(queue)),
        managed_downloads=SimpleNamespace(saved_receipts_with_dates=lambda:(),
            verified_saved_receipt=lambda *a,**k:None,work_lock=lambda *a:nullcontext()),
        live_client=SimpleNamespace(_verified_identity='42',verify_identity=lambda:'42',get_author_artworks_complete=inventory,
            get_artwork_download_record=lambda wid:record),
        author_media_preview=inventory,enqueue_author_media=enqueue,enqueue_managed_download=enqueue,
        remember_managed_author_batch=lambda owner,records:records,
        forget_accepted_managed_author_batch=lambda *a:None,release_managed_owner=lambda *a:None,
        managed_scheduler=ledger,reconcile_managed_scheduler=lambda:ledger.reconcile_jobs(queue,{}))
    client=Client(ui.page('/generated-flr01-'+route+'-'+ending))
    task=view=None
    try:
        with client:view=SchedulerView(services)
        task=asyncio.create_task(asyncio.to_thread(submit_author_batch,services,('11','12'),
            cancellation=cancel,ledger=ledger,slot='generated-progress'))
        assert await asyncio.to_thread(entered.wait,5)
        await view.refresh()
        assert len(queue)==1
        assert ledger.history()[0]['accepted']==1
        assert '受付 1件' in view.history_label.text
        if ending=='cancel':cancel.set()
        release.set()
        if ending=='failure':
            with pytest.raises(ValueError,match='generated read failure'):await task
        else:assert (await task)['accepted']==1
        await view.refresh()
        assert ledger.history()[0]['accepted']==1
        assert '受付 1件' in view.history_label.text
        assert len(queue)==1
    finally:
        release.set()
        if task is not None and not task.done():
            try:await asyncio.wait_for(task,12)
            except ValueError:pass
        if view is not None:view.close()
        client.delete()
        ledger.close()
