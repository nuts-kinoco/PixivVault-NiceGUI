"""The legacy DB and ZIP status participate in real author service preview.

No original database, images, cookies or external Pixiv requests are used.
"""
import asyncio
from types import SimpleNamespace
from threading import Lock

import pytest

from pixivvault_nicegui.author_download import Difference
from pixivvault_nicegui.services import ApplicationServices


def old(wid, date='2026', *, deleted=False, present=True, failed=False):
    return SimpleNamespace(work_id=wid, author_id='91', update_date=date,
                           deleted=deleted, files_present=present, failed=failed)


class Bridge:
    def __init__(self, entries):
        self.entries = entries
        self.calls = []

    def observe_author(self, uid, works, *, scan_id=None):
        self.calls.append((uid, tuple(item['id'] for item in works), scan_id))
        return SimpleNamespace(saved=self.entries, remote_missing_pending=1,
                               remote_missing_confirmed=2, failed_history=3)


class Reader:
    def __init__(self, works):
        self.works = works
        self.calls = []

    def verify_identity(self):
        self.calls.append('identity')
        return '999'

    def get_author_artworks_complete(self, uid):
        self.calls.append(('complete', uid))
        return self.works

    def get_author_artworks(self, uid):
        raise AssertionError('a partial listing cannot authorize legacy deletions')


def app_with_bridge(works, old_works):
    service = object.__new__(ApplicationServices)
    service.closing = False
    service.library_repository = None
    service.managed_downloads = SimpleNamespace(saved_receipts_with_dates=lambda: ())
    service.live_client = Reader(works)
    service.legacy_author_bridge = Bridge(old_works)
    service._legacy_inventory_lock = Lock()
    return service


def work(wid, date='2026', kind=0):
    return {'id': wid, 'user_id': '91', 'title': 'Synthetic', 'type': kind,
            'page_count': 1, 'update_date': date}


def test_legacy_restored_missing_unknown_updated_and_failed_history_visible():
    service = app_with_bridge(
        (work('101'), work('102'), work('103'), work('104'), work('105'),
         work('106'), work('107', ''), work('108', kind=2)),
        {'101': old('101', deleted=True, failed=True),
         '102': old('102', present=False),
         '103': old('103', present=None),
         '104': old('104', date='2025'),
         '105': old('105'),
         '107': old('107')})
    preview = asyncio.run(service.lookup_managed_author('91', owner='a'*32))
    states = {item['id']: state for item, state in preview}
    assert states == {'101': Difference.RESTORED, '102': Difference.MISSING,
                      '103': Difference.UNKNOWN, '104': Difference.UPDATED,
                      '105': Difference.SKIPPED, '106': Difference.NEW,
                      '107': Difference.UNKNOWN}
    assert preview[0][0]['legacy_diff_source'] is True
    assert preview[0][0]['legacy_failed'] is True
    assert preview.remote_missing_pending == 1
    assert preview.remote_missing_confirmed == 2
    assert service.live_client.calls == ['identity', ('complete', '91')]
    assert service.legacy_author_bridge.calls == [
        ('91', tuple(str(i) for i in range(101, 109)), None)]


def test_managed_active_receipt_outranks_old_tombstone_and_partial_copy():
    service = app_with_bridge((work('101'),), {'101': old('101', deleted=True, present=False)})
    service.managed_downloads = SimpleNamespace(saved_receipts_with_dates=lambda: (
        ('generations/work-101-' + 'b'*32 + '/receipt.json', {'work_id': '101'},
         'a'*64, '2026'),))
    ((metadata, state),) = asyncio.run(service.lookup_managed_author('91', owner='a'*32))
    assert state is Difference.SKIPPED
    assert metadata['expected_receipt_sha256'] == 'a'*64
    assert metadata['legacy_diff_source'] is False


def test_explicit_confirm_rejects_bad_token_and_requires_complete_get():
    service = app_with_bridge((work('101'),), {'101': old('101')})
    with pytest.raises(ValueError, match='invalid author inventory operation'):
        asyncio.run(service.confirm_legacy_author_inventory('91', owner='a'*32,
                                                             scan_id='bad'))
    assert service.live_client.calls == []
    scan = asyncio.run(service.confirm_legacy_author_inventory('91', owner='a'*32,
                                                               scan_id='b'*32))
    assert scan.remote_missing_pending == 1
    assert service.legacy_author_bridge.calls == [('91', ('101',), 'b'*32)]
    assert service.live_client.calls == ['identity', ('complete', '91')]


def test_original_preview_works_without_bridge_and_uses_regular_reader():
    service = app_with_bridge((work('101'),), {})
    service.legacy_author_bridge = None
    service.live_client.get_author_artworks = lambda uid: (work('101'),)
    ((metadata, state),) = asyncio.run(service.lookup_managed_author('91', owner='a'*32))
    assert state is Difference.NEW
    assert metadata['legacy_diff_source'] is False


def test_empty_complete_remote_inventory_still_displays_old_absence_counts():
    service = app_with_bridge((), {'101': old('101')})
    preview = asyncio.run(service.lookup_managed_author('91', owner='a'*32))
    assert preview == ()
    assert preview.remote_missing_pending == 1
    assert preview.remote_missing_confirmed == 2


def test_masked_or_partial_legacy_inventory_falls_back_to_managed_only_unknown():
    from pixivvault_nicegui.pixiv_read_client import InvalidPayload
    service = app_with_bridge((work('101'), work('102')), {'101': old('101', deleted=True)})

    def incomplete(uid):
        raise InvalidPayload()

    service.live_client.get_author_artworks_complete = incomplete
    service.live_client.get_author_artworks = lambda uid: (work('101'), work('102'))
    preview = asyncio.run(service.lookup_managed_author('91', owner='a'*32))
    assert preview.inventory_complete is False
    assert {item['id']: state for item, state in preview} == {
        '101': Difference.UNKNOWN, '102': Difference.UNKNOWN}
    assert not service.legacy_author_bridge.calls
    assert preview.remote_missing_pending == preview.remote_missing_confirmed == 0


def test_partial_inventory_can_preview_verified_managed_update_without_legacy_deletion():
    from pixivvault_nicegui.pixiv_read_client import InvalidPayload
    service = app_with_bridge((work('101'),), {'101': old('101', deleted=True)})
    service.managed_downloads = SimpleNamespace(saved_receipts_with_dates=lambda: (
        ('generations/work-101-' + 'b'*32 + '/receipt.json', {'work_id': '101'},
         'a'*64, '2025'),))
    service.live_client.get_author_artworks_complete = lambda uid: (_ for _ in ()).throw(InvalidPayload())
    service.live_client.get_author_artworks = lambda uid: (work('101'),)
    ((metadata, state),) = asyncio.run(service.lookup_managed_author('91', owner='a'*32))
    assert state is Difference.UPDATED
    assert metadata['expected_receipt_sha256'] == 'a'*64
    assert metadata['legacy_diff_source'] is False
    assert service.legacy_author_bridge.calls == []
