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
        await h.wait_for(lambda: d.save_dialog.value, task)
        d.save_dialog.submit(False)
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
