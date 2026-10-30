"""Generated Chrome/Netscape credentials only; no HTTP or browser state access."""
from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import sqlite3
import stat
from types import SimpleNamespace

import pytest

import pixivvault_nicegui.extension_cookie_store as sync
from pixivvault_nicegui.fs_paths import logical_path, native_io_path
from pixivvault_nicegui.native_profile import NativeProfileStore, STORAGE_FOLDER
from pixivvault_nicegui.pixiv_read_client import _read_cookies


NOW = 2_000_000_000
SESSION_EXPIRY = NOW + 7 * 86400
PERSISTENT_EXPIRY = NOW + 20 * 86400


def chrome(value='55_generated_A'):
    return [
        {'domain': '.pixiv.net', 'path': '/', 'name': 'PHPSESSID',
         'value': value, 'secure': True, 'session': True,
         'hostOnly': False, 'httpOnly': True, 'sameSite': 'no_restriction'},
        {'domain': 'www.pixiv.net', 'path': '/', 'name': 'generated_pref',
         'value': 'generated-only', 'secure': False,
         'expirationDate': PERSISTENT_EXPIRY, 'session': False},
    ]


def netscape(value='55_generated_A'):
    # Different comments/order deliberately remain valid, unchanged source bytes.
    return ('# Netscape HTTP Cookie File\n# generated original, preserve verbatim\n\n'
            f'www.pixiv.net\tFALSE\t/\tFALSE\t{PERSISTENT_EXPIRY}\tgenerated_pref\tgenerated-only\n'
            f'.pixiv.net\tTRUE\t/\tTRUE\t{SESSION_EXPIRY}\tPHPSESSID\t{value}\n').encode()


def proof(path):
    path = native_io_path(path)
    info = path.stat()
    return sha256(path.read_bytes()).hexdigest(), info.st_mtime_ns, info.st_nlink


def tree(root):
    io_root = native_io_path(root)
    def observed(path):
        if path.parent == io_root and path.name == sync.LOCK:
            # Windows correctly denies another handle reading the owned byte
            # lease. Its file identity and unchanged metadata remain observable.
            info = path.stat()
            return ('exclusive-lock', info.st_dev, info.st_ino, info.st_nlink,
                    info.st_size, info.st_mtime_ns,
                    getattr(info, 'st_birthtime_ns', info.st_ctime_ns))
        return proof(path)
    return {str(path.relative_to(io_root)): observed(path)
            for path in io_root.rglob('*') if path.is_file()}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(sync.time, 'time', lambda: NOW)
    original = tmp_path / 'readonly-cookie' / 'cookies.txt'
    original.parent.mkdir()
    original.write_bytes(netscape())
    parent = tmp_path / 'selected-save-parent'
    parent.mkdir()
    profile = NativeProfileStore(tmp_path / 'native-profile')
    selected = profile.update(cookie_file=original, save_parent=parent, expected_revision=0)
    root = tmp_path / 'owned-extension-credentials'
    yield SimpleNamespace(original=original, parent=parent, profile=profile,
                          selected=selected, root=root, tmp=tmp_path)
    profile.close()


def test_prepare_is_offline_and_only_profile_cas_adopts_verified_generation(setup, monkeypatch):
    from pixivvault_nicegui import pixiv_read_client
    # A storage operation must never construct an account/network client.
    def no_client(*_args, **_kwargs):
        pytest.fail('credential storage attempted to construct a live reader')
    monkeypatch.setattr(pixiv_read_client.LivePixivReadClient, '__init__', no_client)
    before = proof(setup.original)
    owner = sync.ExtensionCookieStore(setup.root, profile=setup.profile)
    try:
        candidate = owner.prepare(chrome('55_generated_B'), expected_revision=setup.selected.revision)
        assert candidate.changed and candidate.profile_revision == setup.selected.revision
        assert candidate.cookie_count == 2 and 0 < candidate.byte_count <= 1024 * 1024
        assert logical_path(candidate.cookie_file).is_relative_to(logical_path(setup.root))
        assert proof(candidate.cookie_file)[0] == candidate.sha256
        jar, claimed = _read_cookies(native_io_path(candidate.cookie_file))
        assert claimed == '55' and {c.name for c in jar} == {'PHPSESSID', 'generated_pref'}
        assert setup.profile.snapshot() == setup.selected, 'prepare is not the adoption point'
        assert proof(setup.original) == before
        assert 'generated_B' not in repr(candidate) and str(setup.root) not in repr(candidate)
        saved = owner.commit(candidate)
        assert saved.changed and saved.profile_revision == setup.selected.revision + 1
        active = setup.profile.snapshot()
        assert active.cookie_file == candidate.cookie_file and active.revision == saved.profile_revision
        assert active.save_parent == setup.selected.save_parent
        assert NativeProfileStore.peek(setup.profile.root) == active
        assert proof(setup.original) == before
        assert list(setup.parent.iterdir()) == [], 'credential adoption must not provision managed storage'
        with pytest.raises(sync.CookieSyncConflict):
            owner.commit(candidate)
    finally:
        owner.close()


def test_semantically_same_external_cookie_keeps_original_bytes_reference_and_revision(setup):
    owner = sync.ExtensionCookieStore(setup.root, profile=setup.profile)
    try:
        before_source, before_root = proof(setup.original), tree(setup.root)
        before_profile = proof(setup.profile.path)
        candidate = owner.prepare(list(reversed(chrome())), expected_revision=setup.selected.revision)
        assert not candidate.changed and candidate.cookie_file == setup.original
        assert candidate.generation is None
        result = owner.commit(candidate)
        assert not result.changed and result.profile_revision == setup.selected.revision
        assert setup.profile.snapshot() == setup.selected
        assert proof(setup.original) == before_source and tree(setup.root) == before_root
        assert proof(setup.profile.path) == before_profile
    finally:
        owner.close()


def test_same_owned_generation_survives_restart_and_session_expiry_does_not_drift(setup, monkeypatch):
    owner = sync.ExtensionCookieStore(setup.root, profile=setup.profile)
    candidate = owner.prepare(chrome('55_generated_B'), expected_revision=setup.selected.revision)
    first = owner.commit(candidate)
    owner.close()
    before = tree(setup.root)
    profile_before = setup.profile.snapshot()
    expected_expiry = (NOW // 43200) * 43200 + 86400 * 30
    jar, _ = _read_cookies(native_io_path(first.cookie_file))
    assert next(c for c in jar if c.name == 'PHPSESSID').expires == expected_expiry
    monkeypatch.setattr(sync.time, 'time', lambda: NOW + 3 * 86400)
    reopened = sync.ExtensionCookieStore(setup.root, profile=setup.profile)
    try:
        second = reopened.prepare(chrome('55_generated_B'), expected_revision=profile_before.revision)
        assert not second.changed and second.cookie_file == first.cookie_file
        assert second.generation == first.generation
        unchanged = reopened.commit(second)
        assert not unchanged.changed and setup.profile.snapshot() == profile_before
        assert tree(setup.root) == before
    finally:
        reopened.close()


@pytest.mark.parametrize('field,value', [
    ('domain', 'pixiv.net.attacker.invalid'), ('path', '/\nforged'),
    ('name', 'injected\tname'), ('value', 'secret\nforged'),
    ('secure', 'TRUE'), ('expirationDate', float('nan')),
])
def test_invalid_chrome_fields_fail_before_creating_a_generation(setup, field, value):
    owner = sync.ExtensionCookieStore(setup.root, profile=setup.profile)
    try:
        packet = chrome('55_generated_B')
        packet[0][field] = value
        before = tree(setup.root)
        with pytest.raises(sync.CookieSyncError) as caught:
            owner.prepare(packet, expected_revision=setup.selected.revision)
        assert tree(setup.root) == before and setup.profile.snapshot() == setup.selected
        assert 'forged' not in str(caught.value) and 'attacker' not in repr(caught.value)
    finally:
        owner.close()


@pytest.mark.parametrize('bad', ['empty', 'duplicate', 'encoded_limit'])
def test_ambiguous_or_unbounded_payload_is_not_saved(setup, bad):
    owner = sync.ExtensionCookieStore(setup.root, profile=setup.profile)
    try:
        packet = [] if bad == 'empty' else chrome('55_generated_B')
        if bad == 'duplicate':
            packet.append(dict(packet[0], value='55_generated_C'))
        elif bad == 'encoded_limit':
            # The generated Netscape body exceeds the existing reader's 1 MiB cap.
            packet = [dict(packet[0], name=f'generated_{n}', value='x' * 16384)
                      for n in range(65)]
        before = tree(setup.root)
        with pytest.raises(sync.CookieSyncError):
            owner.prepare(packet, expected_revision=setup.selected.revision)
        assert tree(setup.root) == before and setup.profile.snapshot() == setup.selected
    finally:
        owner.close()


def test_concurrent_profile_change_rejects_adoption_and_preserves_both_references(setup):
    owner = sync.ExtensionCookieStore(setup.root, profile=setup.profile)
    other = NativeProfileStore(setup.profile.root)
    try:
        candidate = owner.prepare(chrome('55_generated_B'), expected_revision=setup.selected.revision)
        replacement_parent = setup.tmp / 'later-user-choice'
        replacement_parent.mkdir()
        newer = other.update(save_parent=replacement_parent, expected_revision=setup.selected.revision)
        source = proof(setup.original)
        with pytest.raises(sync.CookieSyncConflict):
            owner.commit(candidate)
        assert setup.profile.snapshot() == newer and newer.cookie_file == setup.original
        assert native_io_path(candidate.cookie_file).is_file(), 'unadopted immutable generation is retained'
        assert proof(setup.original) == source
        with pytest.raises(sync.CookieSyncConflict):
            owner.commit(candidate)
    finally:
        owner.close()
        other.close()


def test_real_sqlite_commit_failure_never_activates_prepared_cookie(setup):
    owner = sync.ExtensionCookieStore(setup.root, profile=setup.profile)
    try:
        candidate = owner.prepare(chrome('55_generated_B'), expected_revision=setup.selected.revision)
        with sqlite3.connect(setup.profile.path) as connection:
            connection.execute("""CREATE TRIGGER reject_cookie_adoption
                BEFORE UPDATE ON native_profile_state
                BEGIN SELECT RAISE(ABORT, 'generated fault'); END""")
        with pytest.raises(sync.CookieSyncError):
            owner.commit(candidate)
        assert setup.profile.snapshot() == setup.selected
        assert NativeProfileStore.peek(setup.profile.root) == setup.selected
        assert setup.original.read_bytes() == netscape()
    finally:
        owner.close()


@pytest.mark.parametrize('failure', ['fsync', 'offline_parser'])
def test_preparation_failure_has_no_profile_commit_and_fixed_public_diagnostic(setup, monkeypatch, caplog, failure):
    owner = sync.ExtensionCookieStore(setup.root, profile=setup.profile)
    called = []
    original_update = setup.profile.update
    def observed_update(**kwargs):
        called.append(True)
        return original_update(**kwargs)
    monkeypatch.setattr(setup.profile, 'update', observed_update)
    secret = 'GENERATED_PRIVATE_VALUE_DO_NOT_LOG'
    def fail(*_args, **_kwargs):
        raise OSError(secret)
    try:
        with monkeypatch.context() as injected:
            if failure == 'fsync':
                injected.setattr(sync.os, 'fsync', fail)
            else:
                injected.setattr(sync, '_read_cookies', fail)
            with pytest.raises(sync.CookieSyncError) as caught:
                owner.prepare(chrome('55_generated_B'), expected_revision=setup.selected.revision)
        assert not called and setup.profile.snapshot() == setup.selected
        assert secret not in str(caught.value) and secret not in repr(caught.value)
        assert caught.value.__suppress_context__ is True
        assert secret not in caplog.text
        # A failed stage is not active and does not strand the in-process lease.
        fresh = owner.prepare(chrome('55_generated_C'), expected_revision=setup.selected.revision)
        assert owner.discard(fresh) is True
    finally:
        owner.close()


def test_one_owner_one_pending_and_close_never_adopts_or_closes_borrowed_profile(setup):
    owner = sync.ExtensionCookieStore(setup.root, profile=setup.profile)
    with pytest.raises(sync.CookieSyncBusy):
        sync.ExtensionCookieStore(setup.root, profile=setup.profile)
    pending = owner.prepare(chrome('55_generated_B'), expected_revision=setup.selected.revision)
    with pytest.raises(sync.CookieSyncBusy):
        owner.prepare(chrome('55_generated_C'), expected_revision=setup.selected.revision)
    owner.close()
    owner.close()
    with pytest.raises(sync.CookieSyncError):
        owner.commit(pending)
    assert setup.profile.snapshot() == setup.selected
    replacement = sync.ExtensionCookieStore(setup.root, profile=setup.profile)
    try:
        assert setup.profile.snapshot().cookie_file == setup.original
        assert native_io_path(pending.cookie_file).is_file()
    finally:
        replacement.close()


@pytest.mark.parametrize('boundary', ['profile', 'managed', 'old_cookie', 'explicit_source'])
def test_overlapping_credential_root_is_rejected_before_any_creation(setup, boundary):
    roots = {
        'profile': setup.profile.root / 'nested-credentials',
        'managed': setup.parent / STORAGE_FOLDER / 'nested-credentials',
        'old_cookie': setup.original.parent / 'nested-credentials',
        'explicit_source': setup.tmp / 'frozen-library' / 'nested-credentials',
    }
    protected = setup.tmp / 'frozen-library'
    protected.mkdir()
    root = roots[boundary]
    with pytest.raises(sync.CookieSyncError):
        sync.ExtensionCookieStore(root, profile=setup.profile, protected_roots=(protected,))
    assert not native_io_path(root).exists()
    assert setup.original.read_bytes() == netscape()


def test_unmarked_existing_root_is_not_adopted_even_when_empty(setup):
    setup.root.mkdir()
    with pytest.raises(sync.CookieSyncError):
        sync.ExtensionCookieStore(setup.root, profile=setup.profile)
    assert list(setup.root.iterdir()) == []


def test_ancestor_reparse_is_rejected_before_new_files(setup, monkeypatch):
    redirect = setup.tmp / 'generated-reparse-ancestor'
    redirect.mkdir()
    target = redirect / 'credentials'
    original = Path.lstat
    def redirected(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if logical_path(path) == logical_path(redirect):
            fields = {name: getattr(info, name) for name in
                      ('st_mode', 'st_nlink', 'st_size', 'st_dev', 'st_ino', 'st_mtime_ns', 'st_ctime_ns')}
            fields['st_file_attributes'] = getattr(info, 'st_file_attributes', 0) | 0x400
            return SimpleNamespace(**fields)
        return info
    monkeypatch.setattr(Path, 'lstat', redirected)
    with pytest.raises(sync.CookieSyncError):
        sync.ExtensionCookieStore(target, profile=setup.profile)
    assert not target.exists() and list(redirect.iterdir()) == []


@pytest.mark.parametrize('changed', ['original', 'candidate', 'hardlink'])
def test_prepared_generation_rechecks_source_bytes_and_single_link_before_commit(setup, changed):
    owner = sync.ExtensionCookieStore(setup.root, profile=setup.profile)
    try:
        candidate = owner.prepare(chrome('55_generated_B'), expected_revision=setup.selected.revision)
        if changed == 'original':
            setup.original.write_bytes(netscape('55_external_editor_C'))
        elif changed == 'candidate':
            native_io_path(candidate.cookie_file).write_bytes(netscape('55_untrusted_C'))
        else:
            alias = setup.tmp / 'generated-cookie-hardlink'
            os.link(native_io_path(candidate.cookie_file), native_io_path(alias))
            assert native_io_path(candidate.cookie_file).stat().st_nlink == 2
        before_source = proof(setup.original)
        with pytest.raises(sync.CookieSyncError):
            owner.commit(candidate)
        assert setup.profile.snapshot() == setup.selected
        assert proof(setup.original) == before_source
    finally:
        owner.close()


def test_missing_old_cookie_inside_requested_root_is_not_ownership(setup):
    location = setup.tmp / 'missing-former-cookie-source'
    location.mkdir()
    old = location / 'cookies.txt'
    old.write_bytes(netscape())
    prior = setup.profile.update(cookie_file=old, expected_revision=setup.selected.revision)
    old.unlink()
    location.rmdir()
    with pytest.raises(sync.CookieSyncError):
        sync.ExtensionCookieStore(location, profile=setup.profile)
    assert not location.exists()
    assert setup.profile.snapshot() == prior


def test_same_cookie_preserves_exact_native_reference_spelling(setup):
    prior = setup.profile.update(cookie_file=native_io_path(setup.original),
                                 expected_revision=setup.selected.revision)
    owner = sync.ExtensionCookieStore(setup.root, profile=setup.profile)
    try:
        before = tree(setup.root)
        candidate = owner.prepare(chrome(), expected_revision=prior.revision)
        assert not candidate.changed
        assert str(candidate.cookie_file) == str(prior.cookie_file)
        result = owner.commit(candidate)
        assert result.profile_revision == prior.revision
        assert setup.profile.snapshot() == prior and tree(setup.root) == before
    finally:
        owner.close()


def test_transient_admission_read_failure_consumes_candidate_without_adopting(setup, monkeypatch):
    owner = sync.ExtensionCookieStore(setup.root, profile=setup.profile)
    try:
        candidate = owner.prepare(chrome('55_generated_B'), expected_revision=setup.selected.revision)
        original_read = sync._read_stable
        def temporarily_unreadable(path, limit):
            if Path(path).name == sync.MARKER:
                raise OSError('generated sharing violation')
            return original_read(path, limit)
        with monkeypatch.context() as injected:
            injected.setattr(sync, '_read_stable', temporarily_unreadable)
            with pytest.raises(sync.CookieSyncError):
                owner.commit(candidate)
        assert setup.profile.snapshot() == setup.selected
        with pytest.raises(sync.CookieSyncConflict):
            owner.commit(candidate)
    finally:
        owner.close()


def test_unknown_post_commit_result_retains_adopted_generation_and_noop_can_reconcile(setup, monkeypatch):
    owner = sync.ExtensionCookieStore(setup.root, profile=setup.profile)
    try:
        candidate = owner.prepare(chrome('55_generated_B'), expected_revision=setup.selected.revision)
        update = setup.profile.update
        def committed_then_observation_failed(**kwargs):
            update(**kwargs)
            raise OSError('generated observer failure after durable commit')
        with monkeypatch.context() as injected:
            injected.setattr(setup.profile, 'update', committed_then_observation_failed)
            with pytest.raises(sync.CookieSyncCommitUnconfirmed) as caught:
                owner.commit(candidate)
        assert caught.value.code == 'cookie_sync_commit_unconfirmed'
        active = setup.profile.snapshot()
        assert active.cookie_file == candidate.cookie_file
        adopted = proof(candidate.cookie_file)
        with pytest.raises(sync.CookieSyncConflict):
            owner.commit(candidate)
        recovered = owner.prepare(chrome('55_generated_B'), expected_revision=active.revision)
        assert not recovered.changed and recovered.generation == candidate.generation
        result = owner.commit(recovered)
        assert result.profile_revision == active.revision
        assert setup.profile.snapshot() == active and proof(candidate.cookie_file) == adopted
        assert setup.original.read_bytes() == netscape()
    finally:
        owner.close()
