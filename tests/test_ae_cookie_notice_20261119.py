from types import SimpleNamespace

import pytest


def test_cookie_notice_only_after_saved_changes_with_original_throttle(monkeypatch):
    from pixivvault_nicegui.queue_log import QueueLog
    from pixivvault_nicegui.services import ApplicationServices
    import pixivvault_nicegui.queue_log as queue_log
    now = [1000.0]
    monkeypatch.setattr(queue_log.time, 'monotonic', lambda: now[0])
    log = QueueLog()
    candidate = SimpleNamespace(changed=True)
    store = SimpleNamespace(prepare=lambda *a, **k: candidate, commit=lambda _: None, discard=lambda _: None)
    profile = SimpleNamespace(_open_cookie_store=lambda: store,
                              store=SimpleNamespace(snapshot=lambda: SimpleNamespace(revision=1)))
    owner = SimpleNamespace(native_profile=profile, closing=False, queue_log=log)
    rows = [{'name': 'PHPSESSID', 'value': 'synthetic-one', 'domain': '.pixiv.net', 'path': '/'}]
    sync = lambda: ApplicationServices._extension_cookie_sync(owner, rows)
    assert sync().state == 'pending'
    assert [(e.message, e.level) for e in log.snapshot()] == [
        ('[Cookie Auto-Sync] 拡張機能から最新のCookieを自動同期しました', 'success')]
    now[0] += 10
    assert sync().state == 'pending'
    assert len(log.snapshot()) == 1
    candidate.changed = False
    rows[0]['value'] = 'synthetic-two'
    assert sync().state == 'unchanged'
    assert len(log.snapshot()) == 1
    candidate.changed = True
    store.commit = lambda _: (_ for _ in ()).throw(RuntimeError('failure'))
    with pytest.raises(RuntimeError): sync()
    assert len(log.snapshot()) == 1
    store.commit = lambda _: None
    sync()
    assert len(log.snapshot()) == 2
    now[0] += 300
    sync()
    assert len(log.snapshot()) == 3
    assert all('synthetic' not in e.message for e in log.snapshot())


def test_cookie_notice_is_connected_to_isolated_profile_store(tmp_path, monkeypatch):
    from pixivvault_nicegui.queue_log import QueueLog
    from pixivvault_nicegui.services import ApplicationServices
    import test_h01_extension_default_20261117 as fixtures
    original_rows = fixtures._cookie_rows
    generated = [0]
    def changing_clock_rows():
        rows = original_rows()
        generated[0] += 1
        rows[0]['expirationDate'] += generated[0]
        return rows
    monkeypatch.setattr(fixtures, '_cookie_rows', changing_clock_rows)
    from test_h01_extension_default_20261117 import _binding, _cookie_rows
    binding, _ = _binding(tmp_path)
    log = QueueLog()
    owner = SimpleNamespace(native_profile=binding, closing=False, queue_log=log)
    try:
        rows = _cookie_rows()
        result = ApplicationServices._extension_cookie_sync(owner, rows)
        assert result.state == 'pending'
        assert log.snapshot()[-1].message == '[Cookie Auto-Sync] 拡張機能から最新のCookieを自動同期しました'
        assert ApplicationServices._extension_cookie_sync(owner, rows).state == 'unchanged'
        # A separately generated expiry is a real change, even when the
        # session value is the same; notification throttling still applies.
        assert ApplicationServices._extension_cookie_sync(owner, _cookie_rows()).state == 'pending'
        assert len(log.snapshot()) == 1
    finally:
        binding.close()
