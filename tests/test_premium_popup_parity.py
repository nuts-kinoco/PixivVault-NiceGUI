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
