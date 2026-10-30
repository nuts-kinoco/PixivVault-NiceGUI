"""D15: review display follows verified saved metadata, never queue admission."""
import asyncio
from types import SimpleNamespace

import pytest
from nicegui import Client, background_tasks, core, ui
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.learning_view import LearningView


def row(wid, saved=False):
    return {'work_id':wid, 'verdict':'like', 'title':wid, 'tags':'[]',
            'judged_at':'2026-10-26T12:00:00', 'revision':1,
            'work_meta':{'verified_managed_receipt':True} if saved else None,
            'is_deleted':False}


class ReviewStorage:
    def __init__(self):
        self.rows = [row('1'), row('2')]
        self.entered = None
        self.release = None
        self.fail = False
        self.calls = 0
    async def review_snapshot(self, session):
        self.calls += 1
        result = {'rows': [dict(r) for r in self.rows]}
        if self.entered is not None:
            self.entered.set()
            await self.release.wait()
        if self.fail:
            raise OSError('synthetic receipt read failure')
        return result
    def release_session(self, session):
        pass


class Jobs:
    def __init__(self): self.items = []
    def snapshot(self): return tuple(self.items)


def buttons(view):
    return [(b.text, b.enabled) for b in view.review_content.descendants()
            if isinstance(b, ui.button) and b.props.get('data-testid') == 'discovery-review-save']


@pytest.mark.asyncio
async def test_review_waits_for_verified_receipt_and_updates_filter(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services = ApplicationServices(count=2, learning_sandbox=tmp_path/'learning')
    client = Client(ui.page('/_d15_saved_review'))
    before = set(background_tasks.running_tasks)
    with client, ui.card(): view = LearningView(services)
    owned = set(background_tasks.running_tasks) - before
    restore = [task for task in owned if task.get_name() == 'learning restore']
    old_storage, old_discovery, old_jobs = view.storage, view.discovery, services.static_jobs
    storage, jobs = ReviewStorage(), Jobs()
    try:
        assert len(restore) == 1
        await restore[0]
        view.storage = storage; view.discovery = object(); services.static_jobs = jobs
        view.active = True; view._review_open = True; view._review_session_id = 'review'
        view._review_rows = tuple(storage.rows)
        view._review_watched_jobs['j1'] = '1'
        with client:
            view.review_unsaved.value = False
            view._render_review()
            assert buttons(view) == [('作品を保存', True), ('作品を保存', True)]
            jobs.items = [SimpleNamespace(job_id='j1', state='running', revision=1)]
            await view._poll_review_jobs()
            assert storage.calls == 0 and buttons(view)[0][0] == '作品を保存'
            jobs.items = [SimpleNamespace(job_id='j1', state='error', revision=2)]
            await view._poll_review_jobs()
            assert buttons(view)[0] == ('作品を保存', True)
            assert '失敗' in view.review_status.text
            jobs.items = [SimpleNamespace(job_id='j1', state='done', revision=3)]
            # A done state without verified metadata must never become saved.
            await view._poll_review_jobs()
            assert buttons(view)[0] == ('作品を保存', True)
            storage.rows = [row('1', True), row('2')]
            jobs.items = [SimpleNamespace(job_id='j1', state='done', revision=4)]
            await view._poll_review_jobs()
            assert buttons(view) == [('保存済み', False), ('作品を保存', True)]
            view.review_unsaved.value = True
            view._render_review()
            assert buttons(view) == [('作品を保存', True)]
            # Revisit consumes the same authoritative saved metadata.
            view._review_rows = tuple((await storage.review_snapshot('fresh'))['rows'])
            view.review_unsaved.value = False
            view._render_review()
            assert buttons(view)[0] == ('保存済み', False)
    finally:
        view._review_open = False; view._review_session_id = None
        view.storage, view.discovery, services.static_jobs = old_storage, old_discovery, old_jobs
        view.close(); client.delete()
        for task in owned:
            if not task.done(): task.cancel()
        if owned: await asyncio.gather(*owned, return_exceptions=True)
        await asyncio.to_thread(services.close)


@pytest.mark.asyncio
async def test_review_discards_late_snapshot_and_reports_read_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services = ApplicationServices(count=2, learning_sandbox=tmp_path/'learning')
    client = Client(ui.page('/_d15_review_stale'))
    before = set(background_tasks.running_tasks)
    with client, ui.card(): view = LearningView(services)
    owned = set(background_tasks.running_tasks) - before
    restore = [task for task in owned if task.get_name() == 'learning restore']
    old_storage, old_discovery, old_jobs = view.storage, view.discovery, services.static_jobs
    storage, jobs = ReviewStorage(), Jobs()
    try:
        assert len(restore) == 1
        await restore[0]
        view.storage = storage; view.discovery = object(); services.static_jobs = jobs
        view.active = True; view._review_open = True; view._review_session_id = 'old'
        view._review_rows = tuple(storage.rows); view._review_watched_jobs['j1'] = '1'
        jobs.items = [SimpleNamespace(job_id='j1', state='done', revision=1)]
        storage.rows = [row('1', True)]; storage.entered = asyncio.Event(); storage.release = asyncio.Event()
        with client:
            task = asyncio.create_task(view._poll_review_jobs())
            await storage.entered.wait()
            view._review_epoch += 1; view._review_session_id = 'new'
            storage.release.set(); await task
            assert view._review_rows[0]['work_meta'] is None
            storage.entered = None; storage.fail = True
            await view._poll_review_jobs()
            assert '確認できません' in view.review_status.text
            assert view._review_rows[0]['work_meta'] is None
    finally:
        view._review_open = False; view._review_session_id = None
        view.storage, view.discovery, services.static_jobs = old_storage, old_discovery, old_jobs
        view.close(); client.delete()
        for task in owned:
            if not task.done(): task.cancel()
        if owned: await asyncio.gather(*owned, return_exceptions=True)
        await asyncio.to_thread(services.close)
