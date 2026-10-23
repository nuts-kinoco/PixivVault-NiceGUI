"""Verified author generations preserve old images under updates and crashes."""

from io import BytesIO

from PIL import Image
import pytest

from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.managed_download_requests import ManagedAuthorIntent
from pixivvault_nicegui.managed_downloads import ManagedDownloadError, ManagedPixivDownloads


class Job:
    kind = 'pixiv-illust'
    target_id = '123'
    job_id = 'a' * 32


class Context:
    def checkpoint(self):
        pass

    def progress(self, *_):
        pass


class Client:
    update_date = '2026-10-23'
    author_id = '24'
    count = 0
    mutate_during_get = False

    def verify_identity(self):
        return '999'

    def get_artwork_update_revision(self, work_id, author_id):
        assert author_id == '24'
        return {'work_id': work_id, 'author_id': self.author_id,
                'illustration_type': 0, 'page_count': 1,
                'update_date': self.update_date}

    def get_artwork_original_pages(self, work_id):
        return ('https://i.pximg.net/example/123_p0.png',)

    def get_image_bytes(self, url):
        self.count += 1
        if self.mutate_during_get:
            self.update_date = '2026-10-25'
        image = Image.new('RGB', (32, 20),
                          (11, 20, 30) if self.update_date == '2026-10-23' else (91, 72, 53))
        out = BytesIO()
        image.save(out, 'PNG')
        return out.getvalue()


def record():
    return RemoteBookmark('123', 'illustration', 'show', 'Synthetic', '24',
                          'Author', ('synthetic',), '', '2026-10-01', 1, 0, 0)


def make(tmp_path):
    client = Client()
    writer = ManagedPixivDownloads(tmp_path / 'managed', client, resolve_record=lambda _: record())
    new = ManagedAuthorIntent(record(), 'id', '2026-10-23', None, 'new')
    return client, writer, new


def test_v2_new_update_and_legacy_snapshot_are_non_destructive(tmp_path):
    client, writer, new = make(tmp_path)
    initial_path = writer.run(Job(), Context(), intent=new)
    old_path, old_receipt, old_sha = writer.saved_receipts()[0]
    assert initial_path == old_path and writer.saved_update_date('123') == '2026-10-23'
    old_page = writer.read_saved_page(old_path, 0, old_sha)
    assert client.count == 1
    client.update_date = '2026-10-24'
    updated = ManagedAuthorIntent(record(), 'follow', client.update_date,
                                  old_sha, 'updated', old_path)
    newer_path = writer.run(Job(), Context(), intent=updated)
    assert newer_path != old_path and writer.saved_update_date('123') == '2026-10-24'
    assert len(writer.saved_receipts()) == 1
    assert writer.read_saved_page(old_path, 0, old_sha) == old_page
    assert writer.read_saved_page(newer_path, 0, writer.saved_receipts()[0][2]) != old_page
    assert client.count == 2
    reopened = ManagedPixivDownloads(writer.root, client, resolve_record=lambda _: record())
    assert reopened.saved_receipts()[0][0] == newer_path
    assert reopened.saved_update_date('123') == '2026-10-24'


def test_stale_update_cas_preserves_active_and_makes_no_get(tmp_path):
    client, writer, new = make(tmp_path)
    writer.run(Job(), Context(), intent=new)
    old_rel, _, old_sha = writer.saved_receipts()[0]
    client.update_date = '2026-10-24'
    current = ManagedAuthorIntent(record(), 'id', client.update_date, old_sha, 'updated', old_rel)
    writer.run(Job(), Context(), intent=current)
    prev = client.count
    client.update_date = '2026-10-25'
    stale = ManagedAuthorIntent(record(), 'id', client.update_date, old_sha, 'updated', old_rel)
    with pytest.raises(ManagedDownloadError, match='generation changed'):
        writer.run(Job(), Context(), intent=stale)
    assert client.count == prev and writer.saved_update_date('123') == '2026-10-24'


def test_metadata_changed_before_save_blocks_get(tmp_path):
    client, writer, new = make(tmp_path)
    client.author_id = '25'
    with pytest.raises(ManagedDownloadError, match='metadata changed'):
        writer.run(Job(), Context(), intent=new)
    assert client.count == 0 and not writer.saved_receipts()


def test_crash_after_pointer_commit_retries_without_second_download(tmp_path, monkeypatch):
    client, writer, new = make(tmp_path)
    original = writer._generations.publish

    def committed_then_lost(*args):
        original(*args)
        raise OSError('simulated loss after active pointer commit')

    with monkeypatch.context() as patcher:
        patcher.setattr(writer._generations, 'publish', committed_then_lost)
        with pytest.raises(ManagedDownloadError):
            writer.run(Job(), Context(), intent=new)
    assert client.count == 1
    active = writer.saved_receipts()[0]
    assert active[0].startswith('generations/')
    assert writer.run(Job(), Context(), intent=new) == active[0]
    assert client.count == 1


def test_undated_author_new_preserves_provenance_but_is_unknown_later(tmp_path):
    client, writer, _ = make(tmp_path)
    client.update_date = ''
    intent = ManagedAuthorIntent(record(), 'follow', '', None, 'new')
    relative = writer.run(Job(), Context(), intent=intent)
    assert relative.startswith('generations/')
    assert writer.saved_update_date('123') is None
    assert writer.run(Job(), Context(), intent=intent) == relative
    assert client.count == 1


def test_other_job_with_same_remote_date_cannot_reuse_predecessor_cas(tmp_path):
    client, writer, new = make(tmp_path)
    writer.run(Job(), Context(), intent=new)
    predecessor, _, before_sha = writer.saved_receipts()[0]
    client.update_date = '2026-10-24'
    updated = ManagedAuthorIntent(record(), 'follow', client.update_date,
                                  before_sha, 'updated', predecessor)
    writer.run(Job(), Context(), intent=updated)
    old_get_count = client.count
    other_job = type('OtherJob', (), {'kind': 'pixiv-illust',
                    'target_id': '123', 'job_id': 'b' * 32})()
    with pytest.raises(ManagedDownloadError, match='generation changed'):
        writer.run(other_job, Context(), intent=updated)
    assert client.count == old_get_count
    assert writer.saved_update_date('123') == '2026-10-24'


def test_remote_revision_changed_during_pages_never_publishes_stale_date(tmp_path):
    client, writer, new = make(tmp_path)
    first = writer.run(Job(), Context(), intent=new)
    first_digest = writer.saved_receipts()[0][2]
    first_bytes = writer.read_saved_page(first, 0, first_digest)
    client.update_date = '2026-10-24'
    client.mutate_during_get = True
    old_sealed = tuple(writer.generation_outputs.iterdir())
    intent = ManagedAuthorIntent(record(), 'id', '2026-10-24',
                                 first_digest, 'updated', first)
    with pytest.raises(ManagedDownloadError, match='metadata changed during download'):
        writer.run(Job(), Context(), intent=intent)
    assert writer.saved_update_date('123') == '2026-10-23'
    assert writer.saved_receipts()[0][0] == first
    assert writer.read_saved_page(first, 0, first_digest) == first_bytes
    assert tuple(writer.generation_outputs.iterdir()) == old_sealed, (
        'normal metadata drift must not seal full-size orphan generations')
    client.mutate_during_get = False
    client.update_date = '2026-10-24'
    updated = writer.run(Job(), Context(), intent=intent)
    assert updated != first and writer.saved_update_date('123') == '2026-10-24'


def test_undated_new_intent_cannot_reuse_preexisting_legacy_v1_receipt(tmp_path):
    client, writer, _ = make(tmp_path)
    legacy = writer.run(Job(), Context())
    before = writer.saved_receipts()
    assert before[0][0] == legacy and legacy.startswith('outputs/')
    assert writer.saved_update_date('123') is None
    client.update_date = ''
    # Regression: the empty v1 date must not be mistaken for an idempotent
    # replay of a completed v2 author NEW job, with a false terminal success.
    with pytest.raises(ManagedDownloadError, match='generation changed'):
        writer.run(Job(), Context(), intent=ManagedAuthorIntent(
            record(), 'id', '', None, 'new'))
    assert writer.saved_receipts() == before
    assert client.count == 1
