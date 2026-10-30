"""Synthetic process-tree ownership checks with no real process manipulation."""
from __future__ import annotations

from types import SimpleNamespace

from tools.owned_process_tree import OwnedProcessTree, SystemProcessBackend


class FakeBackend:
    def __init__(self, entries):
        self.entries = entries

    def snapshot(self, anchors=None):
        # Model /proc and Win32 snapshots, not a mocked success claim.
        return dict(self.entries)


def test_owner_tree_adopts_live_child_and_grandchild_with_birth_pins():
    fake = FakeBackend({100: (9, 5000, True)})
    tree = OwnedProcessTree(SimpleNamespace(pid=100), fake, interval=100)
    try:
        fake.entries = {100: (9, 5000, True), 101: (100, 5001, True),
                        102: (101, 5002, True), 999: (9, 5500, True)}
        tree.sample()
        assert tree.identities == {100: 5000, 101: 5001, 102: 5002}
        assert tree.observed_endpoint(102)
        assert not tree.observed_endpoint(999)
        fake.entries = {100: (9, 5000, False), 101: (100, 5001, False),
                        102: (101, 5002, False), 999: (9, 5500, True)}
        receipt = tree.finish()
        assert receipt == {"observed_count": 3, "remaining_count": 0, "errors": []}
    finally:
        tree.finish()


def test_owner_tree_rejects_pid_reuse_and_unrelated_spawning():
    fake = FakeBackend({100: (9, 5000, True)})
    tree = OwnedProcessTree(SimpleNamespace(pid=100), fake, interval=100)
    try:
        # Root PID reuse: a new process at the same PID cannot adopt a child.
        fake.entries = {100: (9, 5005, True), 103: (100, 5006, True)}
        tree.sample()
        assert 103 not in tree.identities
        assert not tree.observed_endpoint(100)
        assert not tree.observed_endpoint(103)
        result = tree.finish()
        assert result["remaining_count"] == 0
    finally:
        tree.finish()


def test_owner_tree_surviving_child_is_not_called_clean():
    fake = FakeBackend({100: (9, 5000, True)})
    tree = OwnedProcessTree(SimpleNamespace(pid=100), fake, interval=100)
    try:
        fake.entries = {100: (9, 5000, True), 101: (100, 5001, True)}
        tree.sample()
        fake.entries = {100: (9, 5000, False), 101: (100, 5001, True)}
        result = tree.finish()
        assert result["observed_count"] == 2
        assert result["remaining_count"] == 1
        assert not result["errors"]
    finally:
        tree.finish()


def test_owner_tree_unknown_root_fails_closed():
    fake = FakeBackend({})
    tree = OwnedProcessTree(SimpleNamespace(pid=100), fake, interval=100)
    try:
        result = tree.finish()
        assert "root_identity_unknown" in result["errors"]
        assert result["remaining_count"] is None
    finally:
        tree.finish()


def test_windows_candidate_scan_never_expands_reused_parent_pid():
    class FakeNative:
        def __init__(self):
            self.queried = []

        def process_pairs(self):
            return [(100, 9), (101, 100), (102, 101)]

        def process_identity(self, pid):
            self.queried.append(pid)
            return {100: 6000, 101: 6001, 102: 6002}[pid]

        def living_pids(self, identities):
            return set(identities)

    backend = object.__new__(SystemProcessBackend)
    backend.native = FakeNative()
    # Parent PID 100 was once ours at birth 5000, now belongs to someone else.
    assert backend.snapshot({100: 5000}) == {}
    assert backend.native.queried == [100]


def test_windows_scan_does_not_follow_older_intermediate_parent():
    class FakeNative:
        def process_pairs(self):
            return [(100, 9), (101, 100), (102, 101)]

        def process_identity(self, pid):
            return {100: 6000, 101: 5900, 102: 6100}[pid]

        def living_pids(self, identities):
            return set(identities)

    backend = object.__new__(SystemProcessBackend)
    backend.native = FakeNative()
    assert backend.snapshot({100: 6000}) == {100: (9, 6000, True)}
