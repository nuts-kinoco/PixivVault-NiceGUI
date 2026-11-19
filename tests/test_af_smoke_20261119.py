import json
from pathlib import Path

import pytest

from tools.ui_smoke_browser import FakeClient


def test_smoke_uses_actual_blocked_gate_before_fake_transport(tmp_path):
    from tools.ui_smoke_browser import make_stopped_reader
    from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopped
    from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient
    fake = FakeClient()
    reader, scope = make_stopped_reader(tmp_path, fake)
    try:
        assert type(reader) is LivePixivReadClient
        assert scope.gate.snapshot().blocked
        with pytest.raises(ManagedNetworkStopped):
            reader.require_identity()
        assert fake.calls == []
    finally:
        reader.close()


def test_scheduler_smoke_requires_real_star_description():
    data = json.loads((Path(__file__).resolve().parents[1] / 'tools' / 'ui_smoke_expectations.json').read_text(encoding='utf-8'))
    scheduler = data['screens']['scheduler']
    assert 'scheduler-authors' not in scheduler['testids']
    assert '☆お気に入りにした作者全員をチェックします。' in scheduler['texts']
    assert not data['global'].get('startup_allowed_calls')
