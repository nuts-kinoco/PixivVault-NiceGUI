from types import SimpleNamespace
from threading import RLock
import json
import pytest

from pixivvault_nicegui.extension_application import ExtensionApplication
from pixivvault_nicegui.legacy_zip_export import LegacyZipExporter, LegacyZipExportError
from pixivvault_nicegui.notifications import NotificationDispatcher

class Notices:
    def __init__(self): self.events=[]
    def notify(self, title, body, **kwargs): self.events.append((title,body,kwargs))

def app():
    result=object.__new__(ExtensionApplication)
    result.services=SimpleNamespace(notifications=Notices(),queue_log=None,shared_log=None)
    result._lock=RLock()
    return result

def job(state='running'):
    return SimpleNamespace(job_id='j',attempt=1,target_id=json.dumps({'type':'work','work_id':'123'}),state=state,error_code='download_failed')

def test_extension_start_and_completion_toasts():
    a=app(); a._run_command=lambda j,c:'saved'
    assert a._run(job(), object())=='saved'
    a._terminal_log(job('done'))
    assert [(x[0],x[1]) for x in a.services.notifications.events]==[
        ('PixivVault ダウンロード開始','work (id=123) の保存を開始します。'),
        ('PixivVault ダウンロード完了','work (id=123) の保存が完了しました！')]

def test_extension_failure_toast_and_stopped_silent():
    a=app(); a._terminal_log(job('error')); a._terminal_log(job('stopped'))
    assert len(a.services.notifications.events)==1
    assert a.services.notifications.events[0][0]=='PixivVault エラー'

def test_toast_exception_does_not_change_job():
    a=app()
    a.services.notifications.notify=lambda *a,**k: (_ for _ in ()).throw(RuntimeError('notification unavailable'))
    a._run_command=lambda j,c:'saved'
    assert a._run(job(),object())=='saved'
    a._terminal_log(job('done'))

def test_corrupt_zip_reports_and_rejects(tmp_path):
    bad=tmp_path/'author.zip';bad.write_bytes(b'not a zip')
    notices=Notices()
    exporter=object.__new__(LegacyZipExporter)
    exporter.notify_corrupt=notices.notify
    with pytest.raises(LegacyZipExportError): exporter._verify_archive(bad, {'x'}, 'author')
    assert notices.events[0][0]=='PixivVault - ZIP破損検出'
    assert 'author' in notices.events[0][1]

def test_real_dispatcher_honours_notification_setting():
    class Preferences:
        value = '0'
        def get_setting(self, key, default): return self.value
    class Backend:
        def __init__(self): self.sent = []
        def send(self, title, body): self.sent.append((title, body))
    pref=Preferences(); backend=Backend(); dispatcher=NotificationDispatcher(pref, backend=backend)
    try:
        a=app(); a.services.notifications=dispatcher
        a._terminal_log(job('done'))
        assert backend.sent == []
        pref.value='1'
        a._terminal_log(job('error'))
        dispatcher._executor.shutdown(wait=True)
        assert len(backend.sent) == 1
        assert backend.sent[0][0] == 'PixivVault エラー'
    finally:
        dispatcher.close()

def test_deflate_corruption_reports_zip_error(tmp_path):
    from zipfile import ZipFile, ZIP_DEFLATED
    bad = tmp_path / 'corrupt.zip'
    with ZipFile(bad, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('x', b'example' * 100)
    data = bytearray(bad.read_bytes())
    name_len = int.from_bytes(data[26:28], 'little')
    extra_len = int.from_bytes(data[28:30], 'little')
    data[30 + name_len + extra_len] = 7
    bad.write_bytes(data)
    notices = Notices()
    exporter = object.__new__(LegacyZipExporter)
    exporter.notify_corrupt = notices.notify
    with pytest.raises(LegacyZipExportError):
        exporter._verify_archive(bad, {'x'}, 'author')
    assert len(notices.events) == 1
