import pixivvault_nicegui.local_tagger as tagger


def test_unc_rejected_before_stat(monkeypatch):
    monkeypatch.setattr(tagger, '_identity', lambda *a: (_ for _ in ()).throw(AssertionError('stat called')))
    status, _ = tagger.verify_model(r'\\server\share\model')
    assert status == '利用不可（ローカルのフォルダを指定）'


def test_remote_drive_rejected_before_stat(monkeypatch):
    monkeypatch.setattr(tagger, '_drive_type', lambda _: 4, raising=False)
    monkeypatch.setattr(tagger, '_identity', lambda *a: (_ for _ in ()).throw(AssertionError('stat called')))
    status, _ = tagger.verify_model(r'Z:\model')
    assert status == '利用不可（ローカルのフォルダを指定）'
