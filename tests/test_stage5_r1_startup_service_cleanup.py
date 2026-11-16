"""Generated-only launch failure must close an already-open application owner.

Never starts a web server or reads Cookie, network, user files or legacy DB.
"""

from types import SimpleNamespace

import pytest


def test_media_route_registration_failure_releases_initialized_service(monkeypatch):
    from pixivvault_nicegui import bootstrap
    import pixivvault_nicegui.media_routes as media_routes
    import pixivvault_nicegui.services as service_module

    closed = []
    service = SimpleNamespace(
        managed_downloads=None,
        images=object(),
        close=lambda: closed.append("closed"),
        preferences=None,
    )

    monkeypatch.setattr(bootstrap, "_registered", False)
    monkeypatch.setattr(service_module, "get_services", lambda *_args, **_kwargs: service)
    def reject_registration(*_args, **_kwargs):
        raise RuntimeError("synthetic media route registration failed")
    monkeypatch.setattr(media_routes, "register_media_routes", reject_registration)

    with pytest.raises(RuntimeError, match="synthetic media route registration failed"):
        bootstrap.register_pages()
    assert closed == ["closed"], "startup must revoke the acquired service on registration failure"


def test_ui_startup_failure_closes_exactly_the_service_created_by_this_launch(monkeypatch):
    from nicegui import app, ui
    from pixivvault_nicegui import bootstrap
    from pixivvault_nicegui import services as services_module

    key = services_module._APP_KEY
    previous = getattr(app.state, key, None)
    closed = []
    owner = SimpleNamespace(
        closing=False,
        close=lambda: closed.append("closed"),
    )

    def register(*_args, **_kwargs):
        setattr(app.state, key, owner)

    def failed_run(**_kwargs):
        raise OSError("synthetic NiceGUI binding failed")

    monkeypatch.setattr(bootstrap, "register_pages", register)
    monkeypatch.setattr(ui, "run", failed_run)
    try:
        with pytest.raises(OSError, match="synthetic NiceGUI binding failed"):
            bootstrap.main([])
        assert closed == ["closed"], "owned application must not survive ui.run startup failure"
    finally:
        if previous is None:
            try:
                delattr(app.state, key)
            except AttributeError:
                pass
        else:
            setattr(app.state, key, previous)
