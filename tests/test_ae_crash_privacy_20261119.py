import sys
import threading


def test_delegated_hooks_never_receive_exception_value_or_traceback(tmp_path, monkeypatch):
    from pixivvault_nicegui import crash_evidence
    seen = []
    monkeypatch.setattr(crash_evidence.faulthandler, 'enable', lambda **_: None)
    monkeypatch.setattr(crash_evidence.faulthandler, 'disable', lambda: None)
    monkeypatch.setattr(crash_evidence.faulthandler, 'cancel_dump_traceback_later', lambda: None)
    monkeypatch.setattr(sys, 'excepthook', lambda *args: seen.append(args))
    monkeypatch.setattr(threading, 'excepthook', lambda args: seen.append((args.exc_type, args.exc_value, args.exc_traceback)))
    guard = crash_evidence.install(tmp_path / 'crash_log.txt')
    try:
        try:
            raise ValueError('synthetic-sensitive-body')
        except ValueError as error:
            sys.excepthook(type(error), error, error.__traceback__)
            threading.excepthook(threading.ExceptHookArgs((type(error), error, error.__traceback__, None)))
        assert len(seen) == 2
        assert all('synthetic-sensitive-body' not in str(item[1]) and item[2] is None for item in seen)
    finally:
        guard.close()
