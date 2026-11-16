from types import SimpleNamespace

from pixivvault_nicegui import notifications


def test_toast_dependency_must_import_with_its_runtime(monkeypatch):
    def missing_runtime(name):
        assert name == 'win11toast'
        raise ModuleNotFoundError('winrt')
    monkeypatch.setattr(notifications, 'import_module', missing_runtime)
    assert notifications.windows_notifications_available() is False
    monkeypatch.setattr(notifications, 'import_module', lambda name: SimpleNamespace(toast=lambda *a: None))
    assert notifications.windows_notifications_available() is True
    monkeypatch.setattr(notifications, 'import_module', lambda name: SimpleNamespace())
    assert notifications.windows_notifications_available() is False
