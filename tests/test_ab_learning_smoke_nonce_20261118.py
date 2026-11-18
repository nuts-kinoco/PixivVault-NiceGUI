from types import SimpleNamespace
import pytest
from tools import ui_smoke_browser as smoke


def test_smoke_server_receives_dash_prefixed_nonce_as_value(tmp_path, monkeypatch):
    class Captured(Exception):
        pass
    arguments = []
    def launch(argv, **kwargs):
        arguments.extend(argv)
        raise Captured()
    monkeypatch.setattr(smoke.H, 'load_expectations', lambda _: {'screens': {'startup': {}}})
    monkeypatch.setattr(smoke.H, 'screen_order', lambda _: ['startup'])
    monkeypatch.setattr(smoke, 'port_free', lambda _: True)
    monkeypatch.setattr(smoke, 'seed_all', lambda _: None)
    monkeypatch.setattr(smoke.secrets, 'token_urlsafe', lambda _: '-synthetic-nonce')
    monkeypatch.setattr(smoke.subprocess, 'Popen', launch)
    args = SimpleNamespace(expectations=None, only=None, port=22499, out=tmp_path / 'out')
    with pytest.raises(Captured):
        smoke.run(args)
    assert '--nonce=-synthetic-nonce' in arguments