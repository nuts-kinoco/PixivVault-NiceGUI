"""A final probe must retain a primary result validated earlier in its sweep."""
import os

import pytest

from pixivvault_nicegui import managed_network_stop_gate as gate
from pixivvault_nicegui.request_notice_gates import check_request_notice_gates
from test_fix_notice_round6_20261122 import windows_io_error
from test_fix_notice_round7_20261122 import linked_owners
from test_fix_notice_sweep_20261122 import reject, retry_policy


@pytest.mark.parametrize('outcome', ('stopped', 'uncertain'))
@pytest.mark.parametrize('fault', ('lock-close', 'database-close', 'byte-busy'))
def test_final_probe_cannot_erase_an_observed_primary(tmp_path, monkeypatch, outcome, fault):
    local, shared, _ = linked_owners(tmp_path)
    owner = 'local' if outcome == 'stopped' else 'shared'
    target = local.gate if outcome == 'stopped' else shared.gate
    if outcome == 'stopped':
        target.trip(401)
    else:
        target.inflight_path.write_bytes(b'synthetic unresolved GET')

    active = False

    def interrupt(phase, authority, when):
        nonlocal active
        if (phase, authority, when) == ('read', owner, 'after'):
            active = True

    original_read = gate._read_gate_probe_bytes
    opener = gate._open_existing_stop_lock

    class Stream:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def close(self):
            self.stream.close()
            raise windows_io_error(5)

    if fault == 'byte-busy':
        if os.name != 'nt':
            pytest.skip('Windows byte lock result')
        def probe(stream, size):
            if active and os.fstat(stream.fileno()).st_ino == target._lock_identity[1]:
                raise windows_io_error(33)
            return original_read(stream, size)
        monkeypatch.setattr(gate, '_read_gate_probe_bytes', probe)
    else:
        damaged = target.lock_path if fault == 'lock-close' else target.path
        def probe(path):
            stream = opener(path)
            return Stream(stream) if active and path == damaged else stream
        monkeypatch.setattr(gate, '_open_existing_stop_lock', probe)

    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local, _step=interrupt))
    reject(policy, gate.ManagedNetworkGateStorageError(authority=owner))
    policy.recheck_gate_notices()
    records = policy.progress_snapshot()
    assert active
    if outcome == 'stopped':
        assert any(row.get('stop_cause') == 'durable_gate' and row['status'] == 401 for row in records)
    else:
        assert any(row.get('gate_problem') == 'uncertain' for row in records)
    assert any(row.get('gate_problem') == 'storage' and row.get('gate_owners') == (owner,)
               for row in records)
