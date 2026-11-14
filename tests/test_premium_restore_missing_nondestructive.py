from types import SimpleNamespace
from pixivvault_nicegui.premium_gate import PremiumAuthorization

def test_missing_activation_does_not_remove_or_create(tmp_path,monkeypatch):
    root=tmp_path/'absent'
    calls=[]
    monkeypatch.setattr(PremiumAuthorization,'_remove_material',lambda self:calls.append('remove'))
    gate=PremiumAuthorization(SimpleNamespace(root=root),protect=lambda *a,**k:None)
    assert not root.exists() and calls==[] and not gate.status()['enabled']

def test_access_failure_does_not_remove(tmp_path,monkeypatch):
    root=tmp_path/'vault'; root.mkdir()
    calls=[]
    monkeypatch.setattr(PremiumAuthorization,'_remove_material',lambda self:calls.append('remove'))
    def denied(*a,**k): raise PermissionError()
    monkeypatch.setattr('pixivvault_nicegui.premium_gate._read_bounded',denied)
    gate=PremiumAuthorization(SimpleNamespace(root=root),protect=lambda *a,**k:None)
    assert calls==[] and gate._error=='premium-restore-unconfirmed'
