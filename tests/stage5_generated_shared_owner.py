"""Test-only, explicit generated shared STOP owner, never a real migration.

This helper must not be imported by production code. Its history proof has no
authority outside the isolated tmp_path and deliberately records no secrets.
"""

import hashlib
import json

from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopGate, _file_identity
from pixivvault_nicegui.managed_network_stop_shared_scope import (
    ManagedUserNetworkStopScope, _OWNER_SCHEMA, _OWNER_FILE, _GATE_FILE,
    _file_identity_directory,
)


def generated_shared_scope(tmp_path):
    root = tmp_path / "generated-reviewed-user-stop"
    root.mkdir(exist_ok=True)
    gate = ManagedNetworkStopGate(root / _GATE_FILE)
    marker = root / _OWNER_FILE
    if not marker.exists():
        marker.write_text(json.dumps({
            "schema": _OWNER_SCHEMA,
            "state": "sealed",
            "root_identity": list(_file_identity_directory(root)),
            "gate_identity": list(_file_identity(gate.path)),
            "lock_identity": list(_file_identity(gate.lock_path)),
            "history_proof": hashlib.sha256(b"generated-only-no-real-historical-proof").hexdigest(),
        }), encoding="utf-8")
    return ManagedUserNetworkStopScope(root)
