from types import SimpleNamespace
from threading import RLock

from nicegui import Client, ui


def test_shared_log_hidden_original_tabs_and_keyboard_toggle():
    from pixivvault_nicegui.queue_log import QueueLog
    from pixivvault_nicegui.ui.shared_log_view import SharedLogView, LOG_HIDDEN_TABS
    assert LOG_HIDDEN_TABS == {'download','queue','failed','history','learning','ranking','personal','library','bookmarks','recommend','dedup'}
    log=QueueLog()
    for n in range(1002): log.append(str(n),level='success')
    client=Client(ui.page('/_ae_shared_log_isolated'))
    with client: view=SharedLogView(log)
    try:
        for tab in LOG_HIDDEN_TABS:
            view.show_tab(tab); assert not view.root.visible
        for tab in ('scheduler','metadata'):
            view.show_tab(tab); assert view.root.visible and view.panel.visible
        view.refresh()
        labels=[e.text for e in view.panel.descendants() if getattr(e,'text',None)]
        assert len(labels)==1000 and labels[0].endswith(' 2') and labels[-1].endswith(' 1001')
        assert labels[0].startswith('[') and '] ' in labels[0]
        view.toggle()
        assert not view.panel.visible and view.button.text == 'ログを表示'
        view.toggle()
        assert view.panel.visible and view.button.text == 'ログを折りたたむ'
    finally:
        view.close()
        client.delete()


def test_only_confirmed_extension_terminal_enters_both_logs_once():
    from pixivvault_nicegui.extension_application import ExtensionApplication
    from pixivvault_nicegui.queue_log import QueueLog
    owner=ExtensionApplication.__new__(ExtensionApplication);owner._lock=RLock()
    queue,shared=QueueLog(),QueueLog()
    owner.services=SimpleNamespace(queue_log=queue,shared_log=shared)
    job=SimpleNamespace(state='done',target_id='{"type":"work","work_id":"123"}')
    owner._terminal_log(job)
    assert len(shared.snapshot())==len(queue.snapshot())==1
    assert 'ダウンロードが完了しました' in shared.snapshot()[0].message
    assert shared.snapshot()[0].level=='success'
    queue.cookie_sync_notice([{'name':'PHPSESSID','value':'synthetic'}])
    assert len(shared.snapshot())==1


def test_real_shell_navigation_drives_common_log_visibility():
    from nicegui import Client
    from pixivvault_nicegui.services import ApplicationServices
    from pixivvault_nicegui.ui.shell import DemoShell
    services=ApplicationServices(2)
    client=Client(ui.page('/_ae_shared_log'))
    try:
        with client:
            shell=DemoShell(services)
            try:
                assert shell.automation.log is None
                shell.show('scheduler')
                log = shell.automation.log
                assert shell.active_tab == 'download' and log.root.visible
                log.toggle()
                assert not log.panel.visible
                shell.automation.dialog.close()
                shell.automation.hide()
                assert not log.root.visible
                shell.show('scheduler')
                assert shell.automation.log is log
                assert log.root.visible and not log.panel.visible
            finally: shell.close()
    finally:
        client.delete();services.close()


def test_scheduler_logs_only_accepted_dispatch_without_extra_network(monkeypatch, tmp_path):
    from threading import Event, Lock, RLock
    from pixivvault_nicegui import managed_scheduler
    from pixivvault_nicegui.queue_log import QueueLog
    from pixivvault_nicegui.services import ApplicationServices
    seen=[]
    class Ledger:
        def __init__(self,root):pass
        def favourites(self):return ('1',)
        def _sync_favourites(self, authors):pass
        def close(self):pass
    class Runner:
        def __init__(self,ledger,dispatch,*,reconcile):self.dispatch=dispatch
        def start(self):pass
    monkeypatch.setattr(managed_scheduler,'ManagedScheduler',Ledger)
    monkeypatch.setattr(managed_scheduler,'ManagedScheduleRunner',Runner)
    monkeypatch.setattr(managed_scheduler,'submit_author_batch',lambda *a,**k:seen.append('dispatch'))
    services=SimpleNamespace(_close_lock=RLock(),_managed_scheduler_lock=Lock(),
        _scheduler_dispatch_lock=Lock(),closing=False,managed_downloads=SimpleNamespace(root=tmp_path),
        live_client=object(),managed_scheduler=None,managed_schedule_runner=None,shared_log=QueueLog(),
        reconcile_managed_scheduler=lambda:None,scheduler_favourite_authors=lambda:('1',))
    ApplicationServices.enable_managed_scheduler(services)
    cancel=Event();services.managed_schedule_runner.dispatch('slot',cancel)
    assert seen == ['dispatch'] and len(services.shared_log.snapshot())==2
    assert '保存完了は各ジョブで確認' in services.shared_log.snapshot()[1].message
    cancel.set();services.managed_schedule_runner.dispatch('slot',cancel)
    assert len(services.shared_log.snapshot())==2


def test_real_committed_extension_job_emits_common_log_once(tmp_path):
    import json,time
    from pixivvault_nicegui.extension_application import ExtensionApplication
    from pixivvault_nicegui.job_service import JobService
    from pixivvault_nicegui.queue_log import QueueLog
    queue,shared=QueueLog(),QueueLog()
    owner=ExtensionApplication.__new__(ExtensionApplication);owner._lock=RLock()
    owner.services=SimpleNamespace(queue_log=queue,shared_log=shared)
    owner._run_command=lambda *_:'receipt'
    owner.jobs=JobService(owner._run,tmp_path/'jobs.sqlite',terminal_callback=owner._terminal_log)
    try:
        owner.jobs.enqueue('extension-command',json.dumps({'type':'work','work_id':'123'}),'work')
        deadline=time.monotonic()+5
        while not shared.snapshot() and time.monotonic()<deadline:time.sleep(.01)
        assert owner.jobs.snapshot()[0].state=='done'
        assert len(shared.snapshot())==len(queue.snapshot())==1
        assert 'ダウンロードが完了しました' in shared.snapshot()[0].message
    finally:owner.jobs.close()
