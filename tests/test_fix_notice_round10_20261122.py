"""Regression coverage for the Dots acquisition-stop notice findings."""
from contextlib import closing
from pathlib import Path
import sqlite3

import pytest

from pixivvault_nicegui import managed_network_stop_gate as gate
from pixivvault_nicegui.fs_paths import sqlite_readonly_uri
from pixivvault_nicegui.native_profile import NativeProfileStore, PROFILE_DB
from pixivvault_nicegui.request_notice_gates import check_request_notice_gates
from test_fix_notice_round6_20261122 import windows_io_error
from test_fix_notice_round7_20261122 import linked_owners
from test_fix_notice_round9_20261122 import guarded_reader
from test_fix_notice_sweep_20261122 import retry_policy
from test_stage5_r1_shared_stop_transport import IMAGE


@pytest.mark.parametrize('outcome', ('stopped', 'uncertain'))
def test_final_new_primary_survives_failed_fingerprint(tmp_path, monkeypatch, outcome):
    local, shared, credential = linked_owners(tmp_path)
    sent = []
    open_lock = gate._open_existing_stop_lock
    deny_local, deny_shared = True, False
    changed = False
    final_read = False

    def open_checked(path):
        if ((path == local.gate.lock_path and deny_local)
                or (path == shared.gate.lock_path and deny_shared)):
            raise windows_io_error(5)
        return open_lock(path)

    def interrupt(phase, authority, boundary):
        nonlocal deny_shared, changed, final_read
        if (phase, authority, boundary) == ('last', 'shared', 'before') and not changed:
            changed = True
            if outcome == 'stopped':
                with closing(sqlite3.connect(shared.gate.path)) as writer:
                    writer.execute('UPDATE gate SET blocked=1,status=401,revision=1 WHERE id=1')
                    writer.commit()
            else:
                shared.gate.inflight_path.write_bytes(b'synthetic unresolved request')
        elif (phase, authority, boundary) == ('verify', 'shared', 'after') and changed:
            final_read = True
            deny_shared = True

    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local, _step=interrupt))
    with guarded_reader(local, shared, credential, lambda *_: sent.append(True)) as reader:
        reader.set_retry_policy(policy)
        monkeypatch.setattr(gate, '_open_existing_stop_lock', open_checked)
        with pytest.raises(gate.ManagedNetworkGateStorageError):
            reader.get_image_bytes(IMAGE)
        deny_local = False
        policy.recheck_gate_notices()
        records = policy.progress_snapshot()

    assert final_read and not sent
    assert any(row.get('gate_problem') == 'storage' and row['gate_owners'] == ('shared',)
               for row in records)
    if outcome == 'stopped':
        assert any(row.get('stop_cause') == 'durable_gate' and row['status'] == 401 for row in records)
    else:
        assert any(row.get('gate_problem') == 'uncertain' for row in records)


@pytest.mark.parametrize('authority', ('local', 'shared'))
@pytest.mark.parametrize('statement', ('PRAGMA synchronous=FULL', 'FROM sqlite_master', 'FROM gate LIMIT'))
def test_observe_persistent_rw_sql_denial_through_public_get(tmp_path, monkeypatch, authority, statement):
    local, shared, credential = linked_owners(tmp_path)
    owner = local if authority == 'local' else shared
    uris = {gate._gate_database_uri(owner.gate.path, immutable=value) for value in (False, True)}
    connect = sqlite3.connect
    sent = []

    class DenySql(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if statement in sql:
                raise sqlite3.OperationalError('synthetic R/W SQL failure')
            return super().execute(sql, *args, **kwargs)

    def connect_injected(path, *args, **kwargs):
        if path in uris:
            kwargs['factory'] = DenySql
        return connect(path, *args, **kwargs)

    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local))
    with guarded_reader(local, shared, credential, lambda *_: sent.append(True)) as reader:
        reader.set_retry_policy(policy)
        with monkeypatch.context() as fault:
            fault.setattr(sqlite3, 'connect', connect_injected)
            with pytest.raises(gate.ManagedNetworkGateStorageError):
                reader.get_image_bytes(IMAGE)
            for _ in range(3):
                policy.recheck_gate_notices()
            state, = (state for state in check_request_notice_gates(local).states
                      if state.authority == authority)
            assert state.state == 'unverified'
            assert any(row.get('gate_problem') == 'storage' and row['gate_owners'] == (authority,)
                       for row in policy.progress_snapshot())
        # A recovered immutable copy does not prove physical R/W SQL works.
        for _ in range(2):
            policy.recheck_gate_notices()
            assert policy.has_gate_notices()
        owner.gate.require_allowed()
        policy.recheck_gate_notices()
        assert policy.has_gate_notices()
        policy.recheck_gate_notices()
        assert not policy.has_gate_notices()
    assert not sent


@pytest.mark.parametrize('late_old_request', (False, True))
@pytest.mark.parametrize('replace_gate', (False, True))
def test_reader_replacement_retires_old_selection_proof(tmp_path, monkeypatch, late_old_request, replace_gate):
    local, shared, credential = linked_owners(tmp_path)
    replacement = credential.with_name('synthetic-next-selection.txt')
    replacement.write_bytes(credential.read_bytes())
    observed_scope = local
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(observed_scope, shared))
    sent = []
    read_owner = Path.lstat

    def deny_current_reference(path):
        if path == replacement:
            raise windows_io_error(5)
        return read_owner(path)

    def save(path):
        store = NativeProfileStore(local.root)
        try:
            store.update(cookie_file=path, expected_revision=store.snapshot().revision)
        finally:
            store.close()

    def refuse(reader):
        with pytest.raises(gate.ManagedNetworkGateStorageError):
            reader.get_image_bytes(IMAGE)

    with guarded_reader(local, shared, credential, lambda *_: sent.append(True)) as old:
        old.set_retry_policy(policy)
        save(replacement)
        refuse(old)
        policy.recheck_gate_notices()
        policy.recheck_gate_notices()
        assert policy.has_gate_notices()

        if replace_gate:
            replacement_root = tmp_path / 'replacement-owner'
            replacement_root.mkdir()
            new_scope, _, next_credential = linked_owners(replacement_root)
            next_path = next_credential.with_name('synthetic-current-selection.txt')
            next_path.write_bytes(next_credential.read_bytes())
            current_store = NativeProfileStore(new_scope.root)
            try:
                current_store.update(cookie_file=next_path, expected_revision=current_store.snapshot().revision)
            finally:
                current_store.close()
            observed_scope, replacement = new_scope, next_path

        with guarded_reader(observed_scope, shared, replacement, lambda *_: sent.append(True)) as current:
            current.set_retry_policy(policy)
            with monkeypatch.context() as fault:
                fault.setattr(Path, 'lstat', deny_current_reference)
                refuse(current)
            if late_old_request:
                refuse(old)

            # A healthy selection for a different path cannot clear the current
            # reader's refusal even when the old reader has just published.
            if not replace_gate:
                save(credential)
                policy.recheck_gate_notices()
                policy.recheck_gate_notices()
                assert policy.has_gate_notices()
                save(replacement)
            for _ in range(2):
                policy.recheck_gate_notices()
                assert policy.has_gate_notices()
            assert policy.confirm_gate_notices()
            if replace_gate:
                # The current gate proves its own repair. The distinct old
                # gate still needs its genuine saved-read route to work.
                assert policy.has_gate_notices()
                save(credential)
                assert policy.confirm_gate_notices()
            assert not policy.has_gate_notices()
    assert not sent


def test_rw_sql_observation_does_not_hold_a_sqlite_write_lock_or_mutate_files(tmp_path, monkeypatch):
    local, shared, _ = linked_owners(tmp_path)
    connect = sqlite3.connect
    paths = (local.gate.path, shared.gate.path)
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
    sql_uris = {gate._gate_database_uri(path, immutable=True): path for path in paths}
    checked = set()

    class Probe(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            cursor = super().execute(sql, *args, **kwargs)
            if 'FROM sqlite_master' in sql and self.probed_path not in checked:
                with closing(connect(self.probed_path, timeout=0)) as other:
                    other.execute('BEGIN EXCLUSIVE')
                    other.rollback()
                checked.add(self.probed_path)
            return cursor

    def connect_guarded(path, *args, **kwargs):
        if path not in sql_uris:
            return connect(path, *args, **kwargs)
        kwargs['factory'] = Probe
        conn = connect(path, *args, **kwargs)
        conn.probed_path = sql_uris[path]

        def authorize(action, name, value, *_):
            if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ):
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_PRAGMA and (value is None or (name, value) == ('synchronous', 'FULL')):
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        conn.set_authorizer(authorize)
        return conn

    monkeypatch.setattr(sqlite3, 'connect', connect_guarded)
    assert [state.state for state in check_request_notice_gates(local).states] == ['open', 'open']
    assert checked == set(paths)
    assert all((path.read_bytes(), path.stat().st_mtime_ns) == before[path] for path in paths)
    assert all(not path.with_name(path.name + suffix).exists()
               for path in paths for suffix in ('-journal', '-shm', '-wal'))


@pytest.mark.parametrize('read_failure', (False, True))
def test_profile_disposal_failure_survives_translation_through_public_get(tmp_path, monkeypatch, read_failure):
    local, shared, credential = linked_owners(tmp_path)
    uri = sqlite_readonly_uri(local.root / PROFILE_DB)
    connect = sqlite3.connect
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local))
    sent = []

    class FailedProfileRead(sqlite3.Connection):
        def execute(self, *args, **kwargs):
            if read_failure:
                raise sqlite3.OperationalError('synthetic profile read failure')
            return super().execute(*args, **kwargs)

        def close(self):
            super().close()
            raise windows_io_error(5)

    def connect_injected(path, *args, **kwargs):
        if path == uri:
            kwargs['factory'] = FailedProfileRead
        return connect(path, *args, **kwargs)

    with guarded_reader(local, shared, credential, lambda *_: sent.append(True)) as reader:
        reader.set_retry_policy(policy)
        monkeypatch.setattr(sqlite3, 'connect', connect_injected)
        with pytest.raises(gate.ManagedNetworkGateStorageError, match='configured owned profile unavailable') as error:
            reader.get_image_bytes(IMAGE)
        assert not sent
        failure, = getattr(error.value, 'gate_cleanup_errors', ())
        assert failure.authority == 'local'
        assert str(failure) == 'native profile database close failed'
        assert any(row.get('gate_owners') == ('local',) and row.get('gate_problem') == 'storage'
                   for row in policy.progress_snapshot())


@pytest.mark.parametrize('outcome', ('stopped', 'uncertain'))
def test_gate_sql_diagnosis_cannot_erase_already_observed_primary(tmp_path, monkeypatch, outcome):
    local, shared, credential = linked_owners(tmp_path)
    sent = []
    deny = True
    open_lock = gate._open_existing_stop_lock

    def open_checked(path):
        if path == shared.gate.lock_path and deny:
            raise windows_io_error(5)
        return open_lock(path)

    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local))
    with guarded_reader(local, shared, credential, lambda *_: sent.append(True)) as reader:
        reader.set_retry_policy(policy)
        monkeypatch.setattr(gate, '_open_existing_stop_lock', open_checked)
        with pytest.raises(gate.ManagedNetworkGateStorageError):
            reader.get_image_bytes(IMAGE)
        deny = False
        if outcome == 'stopped':
            with closing(sqlite3.connect(shared.gate.path)) as writer:
                writer.execute('UPDATE gate SET blocked=1,status=401,revision=1 WHERE id=1')
                writer.commit()
        else:
            shared.gate.inflight_path.write_bytes(b'synthetic unresolved request')

        connect = sqlite3.connect
        rw_immutable = gate._gate_database_uri(shared.gate.path, immutable=True)
        ro_immutable = shared.gate.path.as_uri() + '?mode=ro&immutable=1'

        def connect_injected(path, *args, **kwargs):
            if path == ro_immutable or (outcome == 'uncertain' and path == rw_immutable):
                raise sqlite3.OperationalError('synthetic gate diagnosis failure')
            return connect(path, *args, **kwargs)

        monkeypatch.setattr(sqlite3, 'connect', connect_injected)
        policy.recheck_gate_notices()

    assert not sent
    if outcome == 'stopped':
        assert any(row.get('stop_cause') == 'durable_gate' and row['status'] == 401
                   for row in policy.progress_snapshot())
    else:
        assert any(row.get('gate_owners') == ('shared',) and row.get('gate_problem') == 'uncertain'
                   for row in policy.progress_snapshot())


def test_clear_streak_does_not_cross_a_replaced_gate(tmp_path, monkeypatch):
    local, shared, credential = linked_owners(tmp_path)
    next_root = tmp_path / 'next-owner'
    next_root.mkdir()
    next_local, next_shared, next_credential = linked_owners(next_root)
    observed_scope = local
    sent = []
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(observed_scope))
    open_lock = gate._open_existing_stop_lock

    def deny_local(path):
        if path == local.gate.lock_path:
            raise windows_io_error(5)
        return open_lock(path)

    with guarded_reader(local, shared, credential, lambda *_: sent.append(True)) as old:
        old.set_retry_policy(policy)
        with monkeypatch.context() as fault:
            fault.setattr(gate, '_open_existing_stop_lock', deny_local)
            with pytest.raises(gate.ManagedNetworkGateStorageError):
                old.get_image_bytes(IMAGE)
        policy.recheck_gate_notices()
        assert policy.has_gate_notices()

        with guarded_reader(next_local, next_shared, next_credential, lambda *_: sent.append(True)) as current:
            current.set_retry_policy(policy)
            observed_scope = next_local
            for _ in range(2):
                policy.recheck_gate_notices()
                assert policy.has_gate_notices()
            assert policy.confirm_gate_notices()
            assert not policy.has_gate_notices()
    assert not sent
