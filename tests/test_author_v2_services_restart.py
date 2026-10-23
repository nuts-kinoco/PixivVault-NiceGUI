"""Accepted v2 author updates survive JobService SQLite and process restart."""
import asyncio
from io import BytesIO
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
