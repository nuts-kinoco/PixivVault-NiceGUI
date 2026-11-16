"""R5 interruption/configuration recovery on generated real files."""
import hashlib
import pytest
from test_oauth_send_boundary import make_vault, payout, reopened
from test_oauth_history_capacity_r5 import json_size
from pixivvault_nicegui.external_vault import LIMIT, LegacyVaultCrypto, VaultError
from pixivvault_nicegui.external_vault_transport import VaultResponse, ACCOUNT, OAUTH
from pixivvault_nicegui.external_vault_rejections import ARCHIVE_FIELD


def legacy_seed(v, monkeypatch, *, count=129, large=False):
    epoch, data = v._snapshot()
    hashes = [hashlib.sha256(('generated-removed-'+str(i)).encode()).hexdigest() for i in range(count)]
    data['slots']['1']['rejected_refresh_hashes'] = hashes
    data['slots']['2'].update(access_token='generated-retained', token_expires_at_ms=6000000)
    if large:
        data['secret_key'] = ''
        data['secret_key'] = 'g'*(LIMIT-16-json_size(data))
        assert json_size(data) == LIMIT-16
    with monkeypatch.context() as seed:
        seed.setattr(v, '_compact_rejections', lambda *a: None)
        v._publish(epoch, data)
    return hashes


@pytest.mark.parametrize('partial_marker', (False, True))
def test_initial_marker_failure_can_retry_after_close_and_reopen(tmp_path, monkeypatch, partial_marker):
    pytest.importorskip('cryptography')
    import pixivvault_nicegui.external_vault_rejections as archive_module
    v, _, _ = make_vault(tmp_path, [], crypto=LegacyVaultCrypto())
    original_write = archive_module._write
    def interrupted(path, payload):
        if path.name == 'HISTORY_V1':
            if partial_marker:
                original_write(path, b'generated-incomplete-marker')
            raise OSError('generated marker write interrupted')
        return original_write(path, payload)
    try:
        hashes = legacy_seed(v, monkeypatch)
        before = v._snapshot()[1]
        revision = v._revision
        with monkeypatch.context() as failure:
            failure.setattr(archive_module, '_write', interrupted)
            with pytest.raises(VaultError, match='rejected-history-unavailable'):
                v.update_config(secret_key='generated-new-secret')
        assert v._revision == revision and v._snapshot()[1] == before
        v = reopened(v)
        v.update_config(secret_key='generated-new-secret')
        state = v._snapshot()[1]
        assert v._rejections.read(state, v._key, v.crypto, LIMIT) == frozenset(hashes)
        assert not v.status()['oauth_review_required']
    finally:
        v.close()


@pytest.mark.parametrize('foreign_marker', (False, True))
def test_unrecognized_canonical_archive_directory_is_not_adopted(tmp_path, monkeypatch, foreign_marker):
    v, _, _ = make_vault(tmp_path, [])
    try:
        legacy_seed(v, monkeypatch)
        directory = v.root/'rejected-refresh-history-v1'
        directory.mkdir()
        if foreign_marker:
            (directory/'HISTORY_V1').write_bytes(b'generated-foreign-marker')
        before_files = {p.name: p.read_bytes() for p in directory.iterdir()}
        revision = v._revision
        with pytest.raises(VaultError, match='rejected-history-unavailable'):
            v.update_config(secret_key='generated-new-secret')
        assert v._revision == revision
        assert {p.name: p.read_bytes() for p in directory.iterdir()} == before_files
    finally:
        v.close()


def test_secret_shrink_and_uid_removal_use_final_capacity_and_keep_proofs(tmp_path, monkeypatch):
    pytest.importorskip('cryptography')
    old = payout()
    old.body['account']['refresh_token'] = 'generated-removed-0'
    v, wire, _ = make_vault(tmp_path, [
        old, VaultResponse(200, {'account': {'access_token': 'generated-recovered'}, 'expiresAt': 10000000}),
    ], crypto=LegacyVaultCrypto())
    try:
        hashes = legacy_seed(v, monkeypatch, count=3, large=True)
        retained = v._snapshot()[1]['slots']['2']
        v.update_config(secret_key='generated-small', uid_text='2')
        state = v._snapshot()[1]
        assert state['secret_key'] == 'generated-small' and state['shaft_uids'] == [2]
        assert state['slots']['2'] == retained
        assert v._rejections.read(state, v._key, v.crypto, LIMIT) == frozenset(hashes)
        v = reopened(v)
        v.refresh(2)
        assert wire.calls == [ACCOUNT, ACCOUNT] and OAUTH not in wire.calls
        assert not v.status()['oauth_review_required']
    finally:
        v.close()


def test_failed_final_commit_keeps_original_config_and_allows_restart_retry(tmp_path, monkeypatch):
    v, _, _ = make_vault(tmp_path, [])
    try:
        hashes = legacy_seed(v, monkeypatch, count=3)
        before = v._snapshot()[1]
        revision = v._revision
        with monkeypatch.context() as failure:
            def reject(*args):
                raise VaultError('generated-current-commit-refused')
            failure.setattr(v, '_commit', reject)
            with pytest.raises(VaultError, match='generated-current-commit-refused'):
                v.update_config(secret_key='generated-small', uid_text='2')
        assert v._revision == revision and v._snapshot()[1] == before
        v = reopened(v)
        v.update_config(secret_key='generated-small', uid_text='2')
        assert v._rejections.read(v._snapshot()[1], v._key, v.crypto, LIMIT) == frozenset(hashes)
    finally:
        v.close()


def test_unknown_pending_still_blocks_combined_config_update(tmp_path, monkeypatch):
    v, _, _ = make_vault(tmp_path, [])
    try:
        legacy_seed(v, monkeypatch, count=3)
        epoch, data = v._snapshot()
        data['slots']['1']['oauth_send_pending'] = True
        v._publish(epoch, data)
        before = v._snapshot()[1]
        revision = v._revision
        with pytest.raises(VaultError, match='pixiv-outcome-uncertain'):
            v.update_config(secret_key='generated-small', uid_text='2')
        assert v._revision == revision and v._snapshot()[1] == before
        assert not (v.root/'rejected-refresh-history-v1').exists()
    finally:
        v.close()


def test_archive_publication_race_does_not_replace_foreign_directory(tmp_path, monkeypatch):
    import pixivvault_nicegui.external_vault_rejections as archive_module
    v, _, _ = make_vault(tmp_path, [])
    publish = archive_module._publish_directory
    def raced(source, target):
        target.mkdir()
        (target/'generated-foreign').write_bytes(b'preserve')
        publish(source, target)
    try:
        legacy_seed(v, monkeypatch)
        before = v._snapshot()[1]
        revision = v._revision
        with monkeypatch.context() as failure:
            failure.setattr(archive_module, '_publish_directory', raced)
            with pytest.raises(VaultError, match='rejected-history-unavailable'):
                v.update_config(secret_key='generated-small')
        assert v._revision == revision and v._snapshot()[1] == before
        directory = v.root/'rejected-refresh-history-v1'
        assert {p.name: p.read_bytes() for p in directory.iterdir()} == {'generated-foreign': b'preserve'}
    finally:
        v.close()


@pytest.mark.parametrize('after_replace', (False, True))
def test_current_publication_interruption_reopens_observed_generation(tmp_path, monkeypatch, after_replace):
    pytest.importorskip('cryptography')
    import pixivvault_nicegui.external_vault as vault_module
    v, _, _ = make_vault(tmp_path, [], crypto=LegacyVaultCrypto())
    replace = vault_module.os.replace
    def interrupted(source, target):
        if target.name == 'CURRENT':
            if after_replace:
                replace(source, target)
            raise OSError('generated CURRENT publication interrupted')
        return replace(source, target)
    try:
        hashes = legacy_seed(v, monkeypatch, count=3)
        before = v._snapshot()[1]
        revision = v._revision
        with monkeypatch.context() as failure:
            failure.setattr(vault_module.os, 'replace', interrupted)
            with pytest.raises(OSError, match='generated CURRENT publication interrupted'):
                v.update_config(secret_key='generated-small', uid_text='2')
        observed = (v.root/'CURRENT').read_text(encoding='ascii')
        assert (observed != revision) is after_replace
        assert v._revision == revision and v._snapshot()[1] == before
        v = reopened(v)
        state = v._snapshot()[1]
        if after_replace:
            assert state['secret_key'] == 'generated-small' and state['shaft_uids'] == [2]
        else:
            assert state['secret_key'] == before['secret_key'] and state['shaft_uids'] == [1, 2]
            assert state['slots']['1']['rejected_refresh_hashes'] == hashes
            v.update_config(secret_key='generated-small', uid_text='2')
        assert v._rejections.read(v._snapshot()[1], v._key, v.crypto, LIMIT) == frozenset(hashes)
        assert not v.status()['oauth_review_required']
    finally:
        v.close()

