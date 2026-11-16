"""Synthetic-only AES-GCM retention index; no transport or result mutation."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import socket

import pytest
from pixivvault_nicegui.external_vault import LegacyVaultCrypto
from pixivvault_nicegui.external_vault_result_retention import (
    KnownResultRetention, KnownResultRetentionError, MAX_NORMAL_RESULTS, INDEX_NAME,
)
import pixivvault_nicegui.external_vault_result_retention as retention_module


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError('network forbidden in retention helper tests')
    monkeypatch.setattr(socket.socket, 'connect', reject)
    monkeypatch.setattr(socket.socket, 'connect_ex', reject)
    monkeypatch.setattr(socket, 'create_connection', reject)
    monkeypatch.setattr(socket, 'getaddrinfo', reject)


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / 'synthetic-vault'
    root.mkdir()
    (root / 'EXTERNAL_VAULT_V1').write_bytes(b'PixivVault external vault sidecar v1\n')
    return root, KnownResultRetention(root), bytes(range(32)), LegacyVaultCrypto()


def pair(number):
    return f'{number:032x}', hashlib.sha256(f'synthetic-proof-{number}'.encode()).hexdigest()


def records(root, key, crypto):
    return crypto.decrypt((root / INDEX_NAME).read_bytes(), key)['records']


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def test_absent_index_does_not_enumerate_or_reclassify_existing_records(setup, monkeypatch):
    root, index, key, crypto = setup
    old = root / 'known-results' / ('f' * 32)
    old.mkdir(parents=True)
    (old / 'result.enc').write_bytes(b'synthetic-unclassified-result')
    (old / 'proof.enc').write_bytes(b'synthetic-unclassified-proof')
    before = hashes(old)
    def no_enumeration(*args, **kwargs):
        raise AssertionError('existing records must not be enumerated')
    with monkeypatch.context() as fence:
        fence.setattr(Path, 'iterdir', no_enumeration)
        fence.setattr(Path, 'rglob', no_enumeration)
        assert index.can_record(key, crypto)
        assert not (root / INDEX_NAME).exists()
        index.mark_success(*pair(1), key, crypto)
    assert hashes(old) == before
    assert records(root, key, crypto) == [{'base': pair(1)[0], 'proof_sha256': pair(1)[1], 'phase': 'normal'}]
    encrypted = (root / INDEX_NAME).read_bytes()
    assert pair(1)[0].encode() not in encrypted and pair(1)[1].encode() not in encrypted


def test_exact_sixteen_cap_reopen_and_idempotent_mark_at_capacity(setup):
    root, index, key, crypto = setup
    assert MAX_NORMAL_RESULTS == 16
    for number in range(16):
        assert index.can_record(key, crypto)
        index.mark_success(*pair(number), key, crypto)
    saved = (root / INDEX_NAME).read_bytes()
    again = KnownResultRetention(root)
    assert not again.can_record(key, crypto)
    again.mark_success(*pair(15), key, crypto)
    assert (root / INDEX_NAME).read_bytes() == saved
    with pytest.raises(KnownResultRetentionError, match='known-result-retention-full'):
        again.mark_success(*pair(16), key, crypto)
    assert (root / INDEX_NAME).read_bytes() == saved
    assert len(records(root, key, crypto)) == 16


@pytest.mark.parametrize(('base', 'proof'), [
    ('../outside', 'a' * 64), ('a' * 31, 'a' * 64), ('A' * 32, 'a' * 64),
    ('a' * 32, 'z' * 64), ('a' * 32, 'a' * 63), (None, 'a' * 64),
])
def test_bad_mark_input_never_creates_index(setup, base, proof):
    root, index, key, crypto = setup
    before = hashes(root)
    with pytest.raises(KnownResultRetentionError):
        index.mark_success(base, proof, key, crypto)
    assert hashes(root) == before and index.can_record(key, crypto)


def test_same_base_different_proof_and_reused_proof_are_rejected(setup):
    root, index, key, crypto = setup
    index.mark_success(*pair(1), key, crypto)
    before = (root / INDEX_NAME).read_bytes()
    with pytest.raises(KnownResultRetentionError):
        index.mark_success(pair(1)[0], pair(2)[1], key, crypto)
    with pytest.raises(KnownResultRetentionError):
        index.mark_success(pair(2)[0], pair(1)[1], key, crypto)
    assert (root / INDEX_NAME).read_bytes() == before


def test_wrong_key_tamper_and_foreign_root_are_not_empty_success(setup, tmp_path):
    root, index, key, crypto = setup
    index.mark_success(*pair(1), key, crypto)
    saved = (root / INDEX_NAME).read_bytes()
    with pytest.raises(KnownResultRetentionError):
        index.can_record(b'x' * 32, crypto)
    foreign = tmp_path / 'foreign-vault'
    foreign.mkdir()
    (foreign / 'EXTERNAL_VAULT_V1').write_bytes(b'PixivVault external vault sidecar v1\n')
    (foreign / INDEX_NAME).write_bytes(saved)
    with pytest.raises(KnownResultRetentionError):
        KnownResultRetention(foreign).can_record(key, crypto)
    (root / INDEX_NAME).write_bytes(saved[:-1] + bytes([saved[-1] ^ 1]))
    with pytest.raises(KnownResultRetentionError):
        index.can_record(key, crypto)
    with pytest.raises(KnownResultRetentionError):
        index.mark_success(*pair(2), key, crypto)


@pytest.mark.parametrize('malformation', [
    'purpose', 'version-bool', 'root', 'list', 'record-type', 'missing-field',
    'base', 'proof', 'duplicate-base', 'duplicate-proof', 'over-cap', 'extra-field',
    'missing-phase', 'invalid-phase', 'phase-type',
])
def test_authenticated_malformed_index_is_rejected(setup, malformation):
    root, index, key, crypto = setup
    index.mark_success(*pair(1), key, crypto)
    item = crypto.decrypt((root / INDEX_NAME).read_bytes(), key)
    if malformation == 'purpose':item['purpose'] = 'foreign-purpose'
    elif malformation == 'version-bool':item['version'] = True
    elif malformation == 'root':item['root_binding'] = '0' * 64
    elif malformation == 'list':item['records'] = {}
    elif malformation == 'record-type':item['records'] = ['not-a-record']
    elif malformation == 'missing-field':item['records'][0].pop('proof_sha256')
    elif malformation == 'base':item['records'][0]['base'] = 'x' * 32
    elif malformation == 'proof':item['records'][0]['proof_sha256'] = 'x' * 64
    elif malformation == 'duplicate-base':item['records'].append(dict(item['records'][0]))
    elif malformation == 'duplicate-proof':
        item['records'].append({'base': pair(2)[0], 'proof_sha256': pair(1)[1], 'phase': 'normal'})
    elif malformation == 'over-cap':
        item['records'] = [{'base': pair(i)[0], 'proof_sha256': pair(i)[1], 'phase': 'normal'} for i in range(17)]
    elif malformation == 'missing-phase':item['records'][0].pop('phase')
    elif malformation == 'invalid-phase':item['records'][0]['phase'] = 'failed'
    elif malformation == 'phase-type':item['records'][0]['phase'] = True
    else:item['unexpected'] = True
    (root / INDEX_NAME).write_bytes(crypto.encrypt(item, key))
    before = hashes(root)
    with pytest.raises(KnownResultRetentionError):index.can_record(key, crypto)
    with pytest.raises(KnownResultRetentionError):index.mark_success(*pair(3), key, crypto)
    assert hashes(root) == before


@pytest.mark.parametrize('partial', [False, True])
def test_first_write_interruption_leaves_retryable_absent_index(setup, monkeypatch, partial):
    root, index, key, crypto = setup
    def fail_write(path, encrypted):
        if partial:
            with path.open('xb') as stream:stream.write(encrypted[:7])
        raise OSError('synthetic first-write failure')
    with monkeypatch.context() as fault:
        fault.setattr(retention_module, '_write', fail_write)
        with pytest.raises(KnownResultRetentionError):index.mark_success(*pair(1), key, crypto)
    assert not (root / INDEX_NAME).exists()
    assert KnownResultRetention(root).can_record(key, crypto)
    index.mark_success(*pair(2), key, crypto)
    assert records(root, key, crypto) == [{'base': pair(2)[0], 'proof_sha256': pair(2)[1], 'phase': 'normal'}]


@pytest.mark.parametrize('stage', ['before-replace', 'after-replace', 'after-directory-sync'])
def test_atomic_failure_is_old_or_readback_confirmed_new(setup, monkeypatch, stage):
    root, index, key, crypto = setup
    index.mark_success(*pair(1), key, crypto)
    old = (root / INDEX_NAME).read_bytes()
    replace = retention_module.os.replace
    sync = retention_module._fsync_dir
    syncs = 0
    def interrupted_replace(source, target):
        if stage == 'before-replace':raise OSError('synthetic before-replace')
        replace(source, target)
        if stage == 'after-replace':raise OSError('synthetic after-replace')
    def interrupted_sync(path):
        nonlocal syncs
        syncs += 1
        sync(path)
        if stage == 'after-directory-sync' and syncs == 2:raise OSError('synthetic after-sync')
    with monkeypatch.context() as fault:
        fault.setattr(retention_module.os, 'replace', interrupted_replace)
        fault.setattr(retention_module, '_fsync_dir', interrupted_sync)
        if stage == 'before-replace':
            with pytest.raises(KnownResultRetentionError):index.mark_success(*pair(2), key, crypto)
        else:
            assert index.mark_success(*pair(2), key, crypto) is None
    if stage == 'before-replace':assert (root / INDEX_NAME).read_bytes() == old
    assert len(records(root, key, crypto)) == (1 if stage == 'before-replace' else 2)
    assert KnownResultRetention(root).can_record(key, crypto)


def test_parallel_instances_cannot_overfill_or_lose_recorded_slots(setup):
    root, index, key, crypto = setup
    def append(number):
        try:
            KnownResultRetention(root).mark_success(*pair(number), key, crypto)
            return True
        except KnownResultRetentionError as error:
            assert error.code == 'known-result-retention-full'
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(append, range(20)))
    assert sum(results) == 16
    assert len(records(root, key, crypto)) == 16
    assert not index.can_record(key, crypto)


@pytest.mark.parametrize('marker', [None, b'foreign marker'])
def test_absent_index_in_unowned_root_is_rejected_without_writes(tmp_path, marker):
    root = tmp_path / 'unowned'
    root.mkdir()
    if marker is not None:(root / 'EXTERNAL_VAULT_V1').write_bytes(marker)
    before = hashes(root)
    with pytest.raises(KnownResultRetentionError):
        KnownResultRetention(root).can_record(bytes(range(32)), LegacyVaultCrypto())
    assert hashes(root) == before


def test_reservation_precedes_journal_and_mark_changes_only_its_phase(setup):
    root, index, key, crypto = setup
    index.reserve_normal(*pair(1), key, crypto)
    assert records(root, key, crypto) == [
        {'base': pair(1)[0], 'proof_sha256': pair(1)[1], 'phase': 'reserved'}]
    assert not (root / 'known-results').exists()
    reserved = (root / INDEX_NAME).read_bytes()
    KnownResultRetention(root).reserve_normal(*pair(1), key, crypto)
    assert (root / INDEX_NAME).read_bytes() == reserved
    index.mark_success(*pair(1), key, crypto)
    assert records(root, key, crypto)[0]['phase'] == 'normal'
    normal = (root / INDEX_NAME).read_bytes()
    index.reserve_normal(*pair(1), key, crypto)
    index.mark_success(*pair(1), key, crypto)
    assert (root / INDEX_NAME).read_bytes() == normal


def test_continuous_mark_failure_keeps_sixteen_reservations_and_blocks_new_record(setup, monkeypatch):
    root, index, key, crypto = setup
    def fail_write(*args):
        raise OSError('synthetic persistent completion mark failure')
    for number in range(16):
        assert index.can_record(key, crypto)
        index.reserve_normal(*pair(number), key, crypto)
        with monkeypatch.context() as fault:
            fault.setattr(retention_module, '_write', fail_write)
            with pytest.raises(KnownResultRetentionError):
                index.mark_success(*pair(number), key, crypto)
    again = KnownResultRetention(root)
    assert not again.can_record(key, crypto)
    assert len(records(root, key, crypto)) == 16
    assert all(record['phase'] == 'reserved' for record in records(root, key, crypto))
    before = (root / INDEX_NAME).read_bytes()
    with pytest.raises(KnownResultRetentionError, match='known-result-retention-full'):
        again.reserve_normal(*pair(16), key, crypto)
    assert (root / INDEX_NAME).read_bytes() == before
    # Marking an existing reservation succeeds even when the combined cap is full.
    again.mark_success(*pair(0), key, crypto)
    assert not again.can_record(key, crypto)
    assert records(root, key, crypto)[0]['phase'] == 'normal'
    again.release_failed(*pair(1), key, crypto)
    assert again.can_record(key, crypto)
    again.reserve_normal(*pair(16), key, crypto)
    assert not again.can_record(key, crypto) and len(records(root, key, crypto)) == 16


def test_release_only_reserved_preserves_failed_record_files_and_normal_entries(setup):
    root, index, key, crypto = setup
    index.reserve_normal(*pair(1), key, crypto)
    index.mark_success(*pair(2), key, crypto)
    failed = root / 'known-results' / pair(1)[0]
    failed.mkdir(parents=True)
    (failed / 'result.enc').write_bytes(b'synthetic-preserved-failed-result')
    (failed / 'proof.enc').write_bytes(b'synthetic-preserved-failed-proof')
    before = hashes(failed)
    index.release_failed(*pair(1), key, crypto)
    assert hashes(failed) == before
    assert records(root, key, crypto) == [
        {'base': pair(2)[0], 'proof_sha256': pair(2)[1], 'phase': 'normal'}]
    saved = (root / INDEX_NAME).read_bytes()
    index.release_failed(*pair(1), key, crypto)
    with pytest.raises(KnownResultRetentionError):
        index.release_failed(*pair(2), key, crypto)
    assert (root / INDEX_NAME).read_bytes() == saved


@pytest.mark.parametrize('method', ['reserve_normal', 'mark_success', 'release_failed'])
def test_reserved_conflicting_proof_is_never_adopted(setup, method):
    root, index, key, crypto = setup
    index.reserve_normal(*pair(1), key, crypto)
    saved = (root / INDEX_NAME).read_bytes()
    with pytest.raises(KnownResultRetentionError):
        getattr(index, method)(pair(1)[0], pair(2)[1], key, crypto)
    assert (root / INDEX_NAME).read_bytes() == saved


@pytest.mark.parametrize('after_replace', [False, True])
def test_release_failure_preserves_reservation_or_confirms_atomic_release(setup, monkeypatch, after_replace):
    root, index, key, crypto = setup
    index.reserve_normal(*pair(1), key, crypto)
    saved = (root / INDEX_NAME).read_bytes()
    replace = retention_module.os.replace
    def fail(source, target):
        if after_replace:replace(source, target)
        raise OSError('synthetic release failure')
    with monkeypatch.context() as fault:
        fault.setattr(retention_module.os, 'replace', fail)
        if after_replace:
            assert index.release_failed(*pair(1), key, crypto) is None
        else:
            with pytest.raises(KnownResultRetentionError):
                index.release_failed(*pair(1), key, crypto)
    assert records(root, key, crypto) == ([] if after_replace else [
        {'base': pair(1)[0], 'proof_sha256': pair(1)[1], 'phase': 'reserved'}])
    if not after_replace:assert (root / INDEX_NAME).read_bytes() == saved


def test_hard_interruption_of_mark_retains_reservation_across_reopen(setup, monkeypatch):
    root, index, key, crypto = setup
    index.reserve_normal(*pair(1), key, crypto)
    def hard_stop(*args):
        raise KeyboardInterrupt('synthetic hard interruption')
    with monkeypatch.context() as fault:
        fault.setattr(retention_module, '_write', hard_stop)
        with pytest.raises(KeyboardInterrupt):
            index.mark_success(*pair(1), key, crypto)
    again = KnownResultRetention(root)
    assert again.can_record(key, crypto)
    assert records(root, key, crypto) == [
        {'base': pair(1)[0], 'proof_sha256': pair(1)[1], 'phase': 'reserved'}]
