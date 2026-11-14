"""Deterministic real-thread keeper admission races; generated wire only."""
import threading
import pytest
from test_oauth_send_boundary import make_vault
from pixivvault_nicegui.external_vault_transport import VaultResponse


class PausedKeeperAdmission:
    def __init__(self, real):
        self.real = real
        self.entered = [threading.Event(), threading.Event()]
        self.release_admission = [threading.Event(), threading.Event()]
        self.threads = []

    def acquire(self, blocking=True):
        current = threading.current_thread()
        if current.name == 'pixivvault-external-token-keeper' and current not in self.threads:
            index = len(self.threads)
            self.threads.append(current)
            self.entered[index].set()
            assert self.release_admission[index].wait(5), 'generated barrier timeout'
        return self.real.acquire(blocking)

    def release(self):
        self.real.release()

    def locked(self):
        return self.real.locked()


@pytest.mark.parametrize('replace_event', (False, True))
def test_old_keeper_cannot_enter_reopened_session_even_after_event_replacement(tmp_path, replace_event):
    v, wire, _ = make_vault(tmp_path, [
        VaultResponse(200, {'account': {'access_token': 'generated-unwanted'}, 'expiresAt': 9000000}),
    ])
    admission = PausedKeeperAdmission(v._busy)
    try:
        epoch, data = v._snapshot()
        # Slot 1 is fresh; the escaped old worker would fetch slot 2.
        data['slots']['1'].update(access_token='generated-fresh', token_expires_at_ms=9000000, last_fetch_at=1000)
        v._publish(epoch, data)
        v._busy = admission
        v.start_keeper()
        old_thread, old_stop = v._keeper, v._keeper_stop
        assert admission.entered[0].wait(3)
        # Old loop has passed while-not-stop, but has not acquired busy.
        v.reopen_saved_state('generated-password')
        revision = v._revision
        assert old_stop.is_set() and wire.calls == []
        if replace_event:
            v.start_keeper()
            assert admission.entered[1].wait(3)
            assert v._keeper_stop is not old_stop
            assert not v._keeper_stop.is_set()
        admission.release_admission[0].set()
        old_thread.join(3)
        assert not old_thread.is_alive()
        assert wire.calls == [], 'stopped old keeper dispatched ACCOUNT for slot 2'
        assert v._revision == revision
        assert 'access_token' not in v._snapshot()[1]['slots']['2']
    finally:
        # Stop and join only threads owned by this synthetic test.
        v.lock()
        for release in admission.release_admission:
            release.set()
        for thread in admission.threads:
            thread.join(3)
            assert not thread.is_alive()
        v.close()
