from nicegui import ui
from pixivvault_nicegui import bootstrap
from pixivvault_nicegui import managed_network_stop_shared_scope as owners


def test_borrow_only_startup_binds_existing_owner_without_cookie(tmp_path,monkeypatch):
    sentinel=object();calls=[];registered=[]
    monkeypatch.setattr(owners,'open_existing_windows_user_stop',lambda: calls.append(True) or sentinel)
    monkeypatch.setattr(bootstrap,'register_pages',lambda **kwargs: registered.append(kwargs))
    monkeypatch.setattr(ui,'run',lambda **kwargs: None)
    bootstrap.main(['--external-vault-root',str(tmp_path/'vault')])
    assert calls==[True]
    assert registered[0]['shared_network_stop_scope'] is sentinel
