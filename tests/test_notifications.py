from threading import Event
from types import SimpleNamespace
from pixivvault_nicegui.notifications import NotificationDispatcher


def test_notifications_require_enabled_setting_and_injected_backend():
    settings={'enable_notifications':'0'};calls=[]
    preferences=SimpleNamespace(get_setting=lambda key,default:settings[key])
    dispatcher=NotificationDispatcher(preferences,backend=SimpleNamespace(send=lambda *args:calls.append(args)))
    try:
        assert dispatcher.notify('title','body',event_id='job1:done') is None
        assert calls==[]
        settings['enable_notifications']='1'
        result=dispatcher.notify('title','body',event_id='job1:done').result(3)
        assert result.status=='sent' and calls==[('title','body')]
        assert dispatcher.notify('title','body',event_id='job1:done') is None
    finally:dispatcher.close()
    assert dispatcher.notify('title','body') is None


def test_native_dependency_absence_is_not_success_or_subprocess_fallback():
    preferences=SimpleNamespace(get_setting=lambda *_:'1')
    dispatcher=NotificationDispatcher(preferences)
    try:
        assert not dispatcher.available
        assert dispatcher.notify('title','body') is None
    finally:dispatcher.close()


def test_backend_failure_reported_without_automatic_retry():
    calls=[]
    def fail(*args):calls.append(args);raise RuntimeError('fixture error')
    dispatcher=NotificationDispatcher(SimpleNamespace(get_setting=lambda *_:'1'),backend=SimpleNamespace(send=fail))
    try:
        assert dispatcher.notify('title','body',event_id='1').result(3).status=='failed'
        assert dispatcher.notify('title','body',event_id='1') is None
        assert len(calls)==1
    finally:dispatcher.close()


def test_disabling_notifications_suppresses_queued_work():
    entered=Event();release=Event();settings={'enable_notifications':'1'};calls=[]
    def send(title,body):calls.append(title);entered.set();assert release.wait(3)
    dispatcher=NotificationDispatcher(SimpleNamespace(get_setting=lambda key,default:settings[key]),backend=SimpleNamespace(send=send))
    try:
        first=dispatcher.notify('first','body');assert entered.wait(3)
        second=dispatcher.notify('second','body')
        settings['enable_notifications']='0';release.set()
        assert first.result(3).status=='sent'
        assert second.result(3).status=='suppressed'
        assert calls==['first']
    finally:release.set();dispatcher.close()
