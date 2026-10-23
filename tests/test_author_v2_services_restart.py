"""Accepted v2 author updates survive JobService SQLite and process restart."""
import asyncio
from io import BytesIO
import json
import sqlite3
import time

from PIL import Image

from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.managed_download_requests import ManagedAuthorIntent
from pixivvault_nicegui.services import ApplicationServices


def record():
    return RemoteBookmark('123', 'illustration', 'show', 'Synthetic', '24',
                          'Synthetic Author', (), '', '2026', 1, 0, 0)


class Client:
    def __init__(self, date):
        self.date = date
        self.originals = 0
        self.fail_download = False

    def verify_identity(self):
        return '999'

    def get_artwork_update_revision(self, work_id, author_id):
        assert author_id == '24'
        return {'work_id': work_id, 'author_id': '24', 'illustration_type': 0,
                'page_count': 1, 'update_date': self.date}

    def get_artwork_original_pages(self, work_id):
        return ('https://i.pximg.net/synthetic/123_p0.png',)

    def get_image_bytes(self, url):
        self.originals += 1
        if self.fail_download:
            raise OSError('synthetic failure before active pointer')
        out = BytesIO()
        Image.new('RGB', (20, 12), (23, 24, 25) if self.date == '2026-10-23'
                  else (71, 72, 73)).save(out, 'PNG')
        return out.getvalue()

    def close(self):
        pass


def done(services):
    end = time.monotonic() + 10
    while time.monotonic() < end:
        result = services.jobs.snapshot()
        if result and result[0].state in ('done', 'error', 'stopped'):
            assert result[0].state == 'done', result[0].error_code
            return result[0]
        time.sleep(.03)
    raise AssertionError('managed author job did not finish')


def accept(services, intent, op):
    owner = 'a' * 32
    selection = services.remember_managed_author_batch(owner, (record(),))
    assert selection == (record(),)
    return services.enqueue_managed_download(intent, op, owner=owner)


def simulate_terminal_sqlite_not_committed(root, job_id, *, state='running',
                                          error_code=''):
    """Test-owned DB only: imitate a process crash before terminal commit."""
    with sqlite3.connect(root / 'jobs.sqlite3') as conn:
        row = conn.execute('SELECT payload FROM jobs WHERE job_id=?', (job_id,)).fetchone()
        assert row is not None
        data = json.loads(row[0])
        data.update(state=state, done=0, total=0, result=None, error_code=error_code,
                    paused_from=None)
        conn.execute('UPDATE jobs SET payload=? WHERE job_id=?',
                     (json.dumps(data), job_id))


def test_new_then_updated_job_durable_and_old_generation_preserved(tmp_path):
    root = tmp_path / 'managed'
    one = Client('2026-10-23')
    service = ApplicationServices(live_client=one, pixiv_download_root=root)
    try:
        accept(service, ManagedAuthorIntent(record(), 'id', one.date, None, 'new'), 'first')
        done(service)
        old_rel, _, old_sha = service.managed_downloads.saved_receipts()[0]
        assert one.originals == 1
        assert service.managed_downloads.saved_update_date('123') == one.date
    finally:
        service.close()

    two = Client('2026-10-24')
    restored = ApplicationServices(live_client=two, pixiv_download_root=root)
    try:
        assert restored.jobs.snapshot()[0].state == 'done'
        assert restored.managed_downloads.saved_receipts()[0][0] == old_rel
        assert len(restored.library.works) == 1
        accept(restored, ManagedAuthorIntent(record(), 'follow', two.date, old_sha,
                                             'updated', old_rel), 'second')
        done(restored)
        new_rel, _, _ = restored.managed_downloads.saved_receipts()[0]
        assert new_rel != old_rel
        assert two.originals == 1
        assert (root / old_rel).exists()
        assert restored.managed_downloads.saved_update_date('123') == two.date
        assert len(restored.managed_downloads.saved_receipts()) == 1
        asyncio.run(restored.refresh_library_snapshot())
        assert len(restored.library.works) == 1
    finally:
        restored.close()

    third = Client('2026-10-24')
    again = ApplicationServices(live_client=third, pixiv_download_root=root)
    try:
        assert again.jobs.snapshot()[0].state == 'done'
        assert len(again.managed_downloads.saved_receipts()) == 1
        assert again.managed_downloads.saved_update_date('123') == '2026-10-24'
        assert third.originals == 0
    finally:
        again.close()


def test_restart_reconciles_committed_pointer_to_done_without_get(tmp_path):
    root = tmp_path / 'managed'
    original = Client('2026-10-23')
    first = ApplicationServices(live_client=original, pixiv_download_root=root)
    try:
        job = accept(first, ManagedAuthorIntent(record(), 'id', original.date,
                                               None, 'new'), 'first')
        assert done(first).state == 'done'
        expected = first.managed_downloads.saved_receipts()[0][0]
    finally:
        first.close()
    # After normal close, the active worker may be durably stopping, but the
    # application (not the user) requested it. Its `interrupted` marker must
    # survive the restart and allow the matching active pointer to win.
    simulate_terminal_sqlite_not_committed(root, job.job_id,
                                            state='stopping', error_code='interrupted')
    reopened_client = Client('2026-10-23')
    reopened = ApplicationServices(live_client=reopened_client, pixiv_download_root=root)
    try:
        recovered = next(item for item in reopened.jobs.snapshot() if item.job_id == job.job_id)
        assert recovered.state == 'done' and recovered.result == expected
        assert recovered.done == recovered.total == 1
        assert reopened_client.originals == 0
        assert reopened.managed_downloads.saved_receipts()[0][0] == expected
    finally:
        reopened.close()


def test_restart_before_pointer_makes_interrupted_v2_retryable(tmp_path):
    root = tmp_path / 'managed'
    before = Client('2026-10-23')
    service = ApplicationServices(live_client=before, pixiv_download_root=root)
    try:
        job = accept(service, ManagedAuthorIntent(record(), 'follow', before.date,
                                                 None, 'new'), 'first')
        assert done(service).state == 'done'
        _, _, original_sha = service.managed_downloads.saved_receipts()[0]
        original_path = service.managed_downloads.saved_receipts()[0][0]
    finally:
        service.close()
    failing = Client('2026-10-24')
    failing.fail_download = True
    second = ApplicationServices(live_client=failing, pixiv_download_root=root)
    try:
        job2 = accept(second, ManagedAuthorIntent(record(), 'follow', failing.date,
                                                  original_sha, 'updated', original_path), 'second')
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if any(j.job_id == job2.job_id and j.state == 'error'
                   for j in second.jobs.snapshot()):
                break
            time.sleep(.03)
        assert any(j.job_id == job2.job_id and j.state == 'error'
                   for j in second.jobs.snapshot())
    finally:
        second.close()
    simulate_terminal_sqlite_not_committed(root, job2.job_id)
    retry_client = Client('2026-10-24')
    third = ApplicationServices(live_client=retry_client, pixiv_download_root=root)
    try:
        recovered = next(j for j in third.jobs.snapshot() if j.job_id == job2.job_id)
        assert recovered.state == 'error' and recovered.error_code == 'interrupted'
        assert third.jobs.control(job2.job_id, 'retry')
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            result = next(j for j in third.jobs.snapshot() if j.job_id == job2.job_id)
            if result.state in ('done', 'error', 'stopped'):
                break
            time.sleep(.03)
        assert result.state == 'done'
        assert retry_client.originals == 1
        assert third.managed_downloads.saved_update_date('123') == '2026-10-24'
    finally:
        third.close()


def test_user_explicit_stop_survives_restart_without_retry_or_done(tmp_path):
    root = tmp_path / 'managed'
    initial_client = Client('2026-10-23')
    first = ApplicationServices(live_client=initial_client, pixiv_download_root=root)
    try:
        job = accept(first, ManagedAuthorIntent(record(), 'id', initial_client.date,
                                               None, 'new'), 'manual-stop')
        assert done(first).state == 'done'
    finally:
        first.close()
    # Emulate durable UI Stop after generation pointer publication but before
    # the JobService worker has turned stopping into stopped. This is a user
    # decision, never a crash interrupt which may be reconciled back to Done.
    simulate_terminal_sqlite_not_committed(root, job.job_id, state='stopping')
    restarted_client = Client('2026-10-23')
    restarted = ApplicationServices(live_client=restarted_client, pixiv_download_root=root)
    try:
        persisted = next(j for j in restarted.jobs.snapshot() if j.job_id == job.job_id)
        assert persisted.state == 'stopped' and persisted.error_code == ''
        assert not restarted.jobs.control(job.job_id, 'retry')
        assert restarted_client.originals == 0
        # User stop never deletes already committed receipt or old pictures.
        assert len(restarted.managed_downloads.saved_receipts()) == 1
    finally:
        restarted.close()
