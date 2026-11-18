from types import SimpleNamespace

import pytest
from nicegui import ui


def test_queue_log_bounded_and_view_toggles_from_title_and_list():
    from pixivvault_nicegui.queue_log import QueueLog
    from pixivvault_nicegui.ui.job_views import QueueView
    log = QueueLog(limit=2)
    log.append('old')
    log.append('first', level='success')
    log.append('second', level='error')
    assert [item.message for item in log.snapshot()] == ['first', 'second']
    services = SimpleNamespace(jobs=SimpleNamespace(snapshot=lambda: (), faulted=False), artworks=(), queue_log=log)
    with ui.column():
        view = QueueView(services)
    try:
        assert view.log_heading.visible and view.log_container.visible
        assert [line.text for line in view.log_container.descendants() if getattr(line, 'text', None)] == [
            f'[{item.timestamp}] {item.message}' for item in log.snapshot()]
        assert any(listener.type == 'dblclick' for listener in view.list._event_listeners.values())
        assert any(listener.type == 'dblclick' for listener in view.queue_heading._event_listeners.values())
        assert any(listener.type == 'keydown' for listener in view.queue_heading._event_listeners.values())
        view.toggle_log()
        assert not view.log_heading.visible and not view.log_container.visible
        view.toggle_log()
        assert view.log_heading.visible and view.log_container.visible
        log.append('third')
        view.refresh()
        assert any('third' in getattr(line, 'text', '') for line in view.log_container.descendants())
    finally:
        view.close()


def test_extension_enqueue_logs_only_after_acceptance():
    from pixivvault_nicegui.extension_application import ExtensionApplication
    from pixivvault_nicegui.extension_bridge import ExtensionDownload
    from pixivvault_nicegui.queue_log import QueueLog
    from threading import RLock
    log = QueueLog()
    owner = ExtensionApplication.__new__(ExtensionApplication)
    owner.services = SimpleNamespace(closing=False, queue_log=log)
    owner._lock = RLock()
    owner.jobs = SimpleNamespace(enqueue=lambda *args: SimpleNamespace(job_id='job'))
    assert owner.enqueue(ExtensionDownload('work', '123')) == 'job'
    assert log.snapshot()[-1].message == '[拡張機能連携] DL命令を受信: work (id=123)'
    owner.jobs.enqueue = lambda *args: (_ for _ in ()).throw(RuntimeError())
    with pytest.raises(RuntimeError):
        owner.enqueue(ExtensionDownload('work', '123'))
    assert len(log.snapshot()) == 1


def test_extension_execution_reports_real_result_without_exception_details():
    from pixivvault_nicegui.extension_application import ExtensionApplication
    from pixivvault_nicegui.queue_log import QueueLog
    log = QueueLog()
    owner = ExtensionApplication.__new__(ExtensionApplication)
    owner.services = SimpleNamespace(queue_log=log)
    job = SimpleNamespace(target_id='{"type":"work","work_id":"123"}')
    owner._run_command = lambda *_: 'done'
    assert owner._run(job, object()) == 'done'
    assert log.snapshot()[-1].message == '[拡張機能連携] ダウンロード完了: work (id=123)'
    owner._run_command = lambda *_: (_ for _ in ()).throw(RuntimeError('private details'))
    with pytest.raises(RuntimeError):
        owner._run(job, object())
    assert log.snapshot()[-1].message == '[拡張機能連携] ダウンロード失敗: work (id=123)'
