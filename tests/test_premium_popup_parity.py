"""Borrow ON popup parity with the original app's API-route dialog (gui.py 807-1055)."""
import asyncio
import pytest
from test_premium_popup_recovery import Harness
from test_premium_authorization import TEST_PASSWORD


def saved(h):
    return h.vault._snapshot()[1]


async def start(h, d):
    task = asyncio.create_task(d.request_enable())
    await asyncio.sleep(0)
    return task


# ---- item 3: signing key is saved without surrounding whitespace -------------

@pytest.mark.asyncio
async def test_signing_key_is_trimmed_before_saving(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    try:
        h.open_dialog(None)
        await h.submit(TEST_PASSWORD, '  first-key \t', b'12345')
        assert saved(h)['secret_key'] == 'first-key'
        h.gate.disable()
        await h.submit(TEST_PASSWORD, '\n second-key  ')
        assert saved(h)['secret_key'] == 'second-key'
    finally:
        h.close()


# ---- item 2: wrong password keeps the dialog and the other inputs ------------

@pytest.mark.asyncio
async def test_wrong_password_shows_message_under_field_and_keeps_inputs(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    try:
        d = h.open_dialog(None)
        task = await start(h, d)
        d.password.set_value('generated-wrong-password')
        d.secret.set_value('keep-key')
        assert d._load_uid_bytes(b'12345\n67890')
        d.password_dialog.submit(True)
        await h.wait_for(lambda: 'パスワードが違います' in d.dialog_error.text, task)
        assert not task.done(), 'dialog flow ended instead of staying open'
        assert d.password_dialog.value and 'パスワードが違います' in d.dialog_error.text
        assert d.notice.text == ''
        assert d.secret.value == 'keep-key' and d._uids == [12345, 67890]
        assert 'generated-wrong-password' not in d.dialog_error.text
        assert not h.gate.status()['enabled'] and h.gate._attempts()['failures'] == 1
        # the retry in the same dialog completes the ON with the kept inputs
        h.now[0] += 1000
        d.password.set_value(TEST_PASSWORD)
        d.password_dialog.submit(True)
        await asyncio.wait_for(task, 10)
        assert h.gate.status()['enabled'] and h.vault.status()['open']
        assert saved(h)['secret_key'] == 'keep-key' and saved(h)['shaft_uids'] == [12345, 67890]
        assert d.dialog_error.text == '' and not d.password_dialog.value
    finally:
        h.close()


@pytest.mark.asyncio
async def test_retry_during_backoff_waits_inside_the_dialog_without_new_failure(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    try:
        d = h.open_dialog(None)
        task = await start(h, d)
        d.password.set_value('generated-wrong-password')
        d.secret.set_value('keep-key')
        assert d._load_uid_bytes(b'12345')
        d.password_dialog.submit(True)
        await h.wait_for(lambda: 'パスワードが違います' in d.dialog_error.text, task)
        failures = h.gate._attempts()['failures']
        d.password.set_value(TEST_PASSWORD)
        d.password_dialog.submit(True)
        await h.wait_for(lambda: '待って' in d.dialog_error.text, task)
        assert not task.done() and '待って' in d.dialog_error.text
        assert h.gate._attempts()['failures'] == failures and not h.gate.status()['enabled']
        assert d.password.value == TEST_PASSWORD and d.secret.value == 'keep-key'
        d.password_dialog.submit(False)
        await asyncio.wait_for(task, 10)
        assert not d.password_dialog.value and d.secret.value == '' and d._uids == []
    finally:
        h.close()


@pytest.mark.asyncio
async def test_cancel_after_wrong_password_discards_everything(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    try:
        d = h.open_dialog(None)
        task = await start(h, d)
        d.password.set_value('generated-wrong-password')
        d.secret.set_value('keep-key')
        d.password_dialog.submit(True)
        await h.wait_for(lambda: 'パスワードが違います' in d.dialog_error.text, task)
        d.password_dialog.submit(False)
        await asyncio.wait_for(task, 10)
        assert not h.gate.status()['enabled'] and not h.vault.status()['open']
        assert d.secret.value == '' and d.password.value == '' and d.dialog_error.text == ''
    finally:
        h.close()


# ---- items 4/5/6: the UID txt is the authority, as in the original -----------

import copy
from pixivvault_nicegui.premium_uid_file import apply_uid_file


async def seed(h, uids=b'12345\n67890', secret='first-key'):
    """Initialise the vault (popup path), then switch the permission OFF again."""
    h.open_dialog(None)
    await h.submit(TEST_PASSWORD, secret, uids)
    h.gate.disable()
    h.keeper.clear()
    return h.dialog


def set_default(h, text):
    if isinstance(text, str):
        text = text.encode()
    h.default.write_bytes(text)
    h.dialog.default_uid_path = h.default


@pytest.mark.asyncio
async def test_default_file_is_shown_when_popup_opens_and_saved_on_confirm(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, default_text=b'11111\n22222\n')
    try:
        d = await seed(h)
        set_default(h, '11111\n22222\n33333\n')
        task = await start(h, d)
        assert d.uid_notice.text == 'UID一覧: 3件を自動読み込みしました（default.txt）'
        d.password.set_value(TEST_PASSWORD)  # only the password is typed
        d.password_dialog.submit(True)
        await asyncio.wait_for(task, 10)
        assert saved(h)['shaft_uids'] == [11111, 22222, 33333]
        assert saved(h)['secret_key'] == 'first-key'
        assert h.gate.status()['enabled'] and h.vault.status()['open']
    finally:
        h.close()


@pytest.mark.asyncio
async def test_file_equal_to_saved_uids_writes_nothing(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    try:
        d = await seed(h)
        set_default(h, '12345\n67890\n')
        calls = []
        original = h.vault.update_config
        monkeypatch.setattr(h.vault, 'update_config', lambda **kw: calls.append(kw) or original(**kw))
        revision = h.vault._revision
        await h.submit(TEST_PASSWORD)
        assert h.gate.status()['enabled'] and calls == [] and h.vault._revision == revision
    finally:
        h.close()


@pytest.mark.asyncio
async def test_missing_file_keeps_saved_uids_and_says_nothing_is_changed(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    try:
        d = await seed(h)
        d.default_uid_path = tmp_path / 'absent.txt'
        task = await start(h, d)
        assert d.uid_notice.text == 'UID一覧: 未読み込み（変更しません）'
        d.password.set_value(TEST_PASSWORD)
        d.password_dialog.submit(True)
        await asyncio.wait_for(task, 10)
        assert saved(h)['shaft_uids'] == [12345, 67890] and h.gate.status()['enabled']
    finally:
        h.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', [b'12345\nabc\n0\n', b'\xff\xfe\x00bad', b'', b'1\n2\n3\n4\n5\n6\n7\n8\n9\n10\n11\n'])
async def test_unusable_file_never_overwrites_saved_uids_and_lists_every_problem(tmp_path, monkeypatch, payload):
    h = Harness(tmp_path, monkeypatch)
    try:
        d = await seed(h)
        set_default(h, payload)
        task = await start(h, d)
        text = d.uid_notice.text
        if payload.startswith(b'12345'):
            assert '2行目' in text and '3行目' in text  # every bad line, not only the first
        assert text != 'UID一覧: 未読み込み（変更しません）' and text
        assert not task.done() and d.password_dialog.value  # shown inside the open dialog
        d.password.set_value(TEST_PASSWORD)
        d.password_dialog.submit(True)
        await asyncio.wait_for(task, 10)
        assert h.gate.status()['enabled'] and saved(h)['shaft_uids'] == [12345, 67890]
    finally:
        h.close()


@pytest.mark.asyncio
async def test_explicit_pick_overrides_default_file_and_bad_pick_keeps_previous_list(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    try:
        d = await seed(h)
        set_default(h, '11111\n')
        task = await start(h, d)
        assert d._load_uid_bytes(b'44444\n55555\n')
        assert d.uid_notice.text == 'UID一覧: 2件を読み込みました（有効にすると保存されます）'
        assert not d._load_uid_bytes(b'66666\nxx\nyy\n')
        assert '2行目' in d.uid_notice.text and '3行目' in d.uid_notice.text and '66666' not in d.uid_notice.text
        assert not task.done() and d._uids == [44444, 55555]
        d.password.set_value(TEST_PASSWORD)
        d.password_dialog.submit(True)
        await asyncio.wait_for(task, 10)
        assert saved(h)['shaft_uids'] == [44444, 55555]
    finally:
        h.close()


@pytest.mark.asyncio
async def test_first_setup_without_usable_uids_stays_in_dialog_and_spends_no_attempt(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    try:
        d = h.open_dialog(tmp_path / 'absent.txt')
        task = await start(h, d)
        d.password.set_value(TEST_PASSWORD)
        d.secret.set_value('only-a-key')
        d.password_dialog.submit(True)
        await h.wait_for(lambda: '両方が必要' in d.dialog_error.text, task)
        assert not task.done() and '両方が必要' in d.dialog_error.text
        assert h.gate._attempts()['failures'] == 0 and not h.gate.status()['enabled']
        assert d.secret.value == 'only-a-key'
        assert d._load_uid_bytes(b'12345')
        d.password_dialog.submit(True)
        await asyncio.wait_for(task, 10)
        assert saved(h)['shaft_uids'] == [12345] and saved(h)['secret_key'] == 'only-a-key'
    finally:
        h.close()


def make_pending(h):
    with h.vault._mutex:
        data = copy.deepcopy(h.vault._data)
        data['slots']['12345']['oauth_send_pending'] = True
        h.vault._commit(data, h.vault._key, h.vault._meta, h.vault._revision)
        h.vault._data = data


@pytest.mark.asyncio
async def test_pending_send_plus_different_file_does_not_block_on_or_change_uids(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    try:
        d = await seed(h)
        h.gate.disable()
        h.vault.unlock(TEST_PASSWORD)
        make_pending(h)
        h.gate.disable()
        set_default(h, '99999\n')
        await h.submit(TEST_PASSWORD)  # plain ON: the file is not applied, ON is not blocked
        assert h.gate.status()['enabled'] and h.keeper == []
        assert saved(h)['shaft_uids'] == [12345, 67890]
        assert saved(h)['slots']['12345']['oauth_send_pending'] is True
    finally:
        h.close()


@pytest.mark.asyncio
async def test_startup_reflects_file_into_opened_vault_only_when_usable(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    try:
        d = await seed(h)
        await h.submit(TEST_PASSWORD)  # ON again (not remembered), vault open with [12345, 67890]
        assert h.gate.status()['enabled']
        calls = []
        original = h.vault.update_config
        monkeypatch.setattr(h.vault, 'update_config', lambda **kw: calls.append(kw) or original(**kw))
        set_default(h, '12345\n67890\n')
        assert apply_uid_file(h.vault, h.default) == 'same' and calls == []
        for payload in (b'12345\nabc\n', b'\xff\xfe\x00'):
            set_default(h, payload)
            assert apply_uid_file(h.vault, h.default) == 'error' and calls == []
        assert apply_uid_file(h.vault, tmp_path / 'absent.txt') == 'absent'
        assert apply_uid_file(h.vault, tmp_path) in ('error', 'absent', 'failed')  # a directory never raises
        set_default(h, '13579\n24680\n')
        assert d.reflect_uid_file_at_startup() == 'applied'
        assert saved(h)['shaft_uids'] == [13579, 24680] and saved(h)['secret_key'] == 'first-key'
        assert len(calls) == 1
        assert d.reflect_uid_file_at_startup() == 'same' and len(calls) == 1
    finally:
        h.close()


@pytest.mark.asyncio
async def test_startup_reflection_skips_off_pending_and_unset_path(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    try:
        d = await seed(h)
        set_default(h, '13579\n')
        assert d.reflect_uid_file_at_startup() == 'skipped'  # permission OFF: nothing is touched
        assert h.vault.registered_uids() is None
        d.default_uid_path = None
        await h.submit(TEST_PASSWORD)
        make_pending(h)
        set_default(h, '13579\n')
        assert d.reflect_uid_file_at_startup() == 'pending'
        assert saved(h)['shaft_uids'] == [12345, 67890]
        d.default_uid_path = None
        assert d.reflect_uid_file_at_startup() == 'skipped'
    finally:
        h.close()


# ---- no "save the enabled state?" question: ON always stays ON next launch ----

@pytest.mark.asyncio
async def test_on_is_always_kept_for_next_launch_without_a_save_question(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    try:
        d = h.open_dialog(None)
        assert not hasattr(d, 'save_dialog')
        await h.submit(TEST_PASSWORD, 'keep-key', b'12345')
        assert h.gate.status()['enabled'] and h.gate.status()['remembered']
        assert 'このセッション限り' not in d.notice.text and '次回起動後も維持' in d.notice.text
        # a fresh start of the same vault restores ON by itself
        from test_premium_authorization import create
        h.gate.close(); h.vault.close()
        h.vault, h.gate, _ = create(tmp_path/'vault', h.protection, h.now)
        assert h.gate.status()['enabled'] and h.vault.status()['open']
    finally:
        h.close()
