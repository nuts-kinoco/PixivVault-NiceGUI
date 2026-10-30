"""Committed quarantine must preserve the near-duplicate graph's components."""
from io import BytesIO
from types import SimpleNamespace

from PIL import Image
import pytest

from pixivvault_nicegui import duplicate_inspection as inspection
from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads
from test_managed_quarantine import Context, proof_bytes


def image_for_hash(value):
    # Exact 9 x 8 pixels: dhash has no resampling ambiguity. Each comparison
    # increases by 16 for a set bit and stays level for an unset bit.
    image = Image.new('RGB', (9, 8))
    for y in range(8):
        level = 0
        image.putpixel((0, y), (level,) * 3)
        for x in range(8):
            if value & (1 << (63 - (y * 8 + x))):
                level += 16
            image.putpixel((x + 1, y), (level,) * 3)
    assert inspection.dhash(image) == value
    stream = BytesIO()
    image.save(stream, 'PNG')
    return stream.getvalue()


class Client:
    def __init__(self, values):
        self.images = []
        self.payloads = {str(223 + index): image_for_hash(value)
                         for index, value in enumerate(values)}
    def verify_identity(self):
        return '99'
    def get_artwork_original_pages(self, wid):
        return (f'https://i.pximg.net/synthetic/{wid}/0.png',)
    def get_image_bytes(self, url):
        self.images.append(url)
        return self.payloads[url.rsplit('/', 2)[1]]


def record(wid):
    return RemoteBookmark(wid, 'illustration', 'show', 'Synthetic ' + wid,
                          '25', 'Synthetic author', (), '', '2026-10-29T00:00:00Z',
                          1, 0, 0)


def opened(tmp_path, values):
    client = Client(values)
    writer = ManagedPixivDownloads(tmp_path / 'managed', client, resolve_record=record)
    for wid in client.payloads:
        writer.run(SimpleNamespace(kind='pixiv-illust', target_id=wid), Context())
    return writer, inspection.ManagedDuplicateInspection(writer), client


def select(source, scan, key):
    group = next(group for group in scan.groups if any(item.key == key for item in group.items))
    return source.plan(scan, {group.group_id: {key}})


def test_removing_bridge_drops_disconnected_nonduplicate_survivors(tmp_path, monkeypatch):
    writer, source, client = opened(tmp_path, (0, 15, 255))
    other = inspection.ManagedDuplicateInspection(writer)
    try:
        before, gets = proof_bytes(writer), tuple(client.images)
        scan = source.scan()
        assert len(scan.groups) == 1
        assert scan.groups[0].edges == ((0, 1, 4), (1, 2, 4))
        assert inspection.hamming(0, 255) == 8 > inspection.THRESHOLD
        stale = select(other, other.scan(), '223:1')
        plan = select(source, scan, '224:1')
        with monkeypatch.context() as patch:
            patch.setattr(inspection, 'dhash',
                          lambda *_: pytest.fail('post-commit pruning must not rehash images'))
            result = source.execute(plan, operation_id='a' * 32)
        latest = source.latest_scan
        assert [item.key for item in result.committed] == ['224:1']
        assert result.failed == ()
        assert latest.scan_id != scan.scan_id
        assert latest.scanned_files == 2
        assert latest.groups == (), 'A/C have no near edge after bridge B is quarantined'
        with pytest.raises(inspection.DuplicateInspectionError):
            select(source, scan, '223:1')
        with pytest.raises(ValueError, match='changed|compare-and-swap|snapshot'):
            other.execute(stale, operation_id='b' * 32)
        assert source.scan().groups == ()
        assert writer.visible_source_pages('223') == (0,)
        assert writer.visible_source_pages('224') == ()
        assert writer.visible_source_pages('225') == (0,)
        assert proof_bytes(writer) == before and tuple(client.images) == gets
    finally:
        other.close()
        source.close()
        writer.close()


def test_removing_bridge_splits_retained_edges_into_fresh_valid_groups(tmp_path, monkeypatch):
    writer, source, client = opened(tmp_path, (0, 15, 255, 4095, 65535))
    try:
        before, gets = proof_bytes(writer), tuple(client.images)
        scan = source.scan()
        assert len(scan.groups) == 1 and len(scan.groups[0].items) == 5
        assert scan.groups[0].edges == ((0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 4, 4))
        plan = select(source, scan, '225:1')
        with monkeypatch.context() as patch:
            patch.setattr(inspection, 'dhash',
                          lambda *_: pytest.fail('post-commit pruning must not rehash images'))
            source.execute(plan, operation_id='c' * 32)
        latest = source.latest_scan
        assert {tuple(item.key for item in group.items) for group in latest.groups} == {
            ('223:1', '224:1'), ('226:1', '227:1')}
        assert all(group.edges == ((0, 1, 4),) for group in latest.groups)
        assert len({group.group_id for group in latest.groups}) == 2
        assert all(group.group_id != scan.groups[0].group_id for group in latest.groups)
        # Stable keys and mapped edges must match a new real scan, without
        # requiring that real scan to publish the immediate post-commit result.
        observed = source.scan()
        assert latest.groups == observed.groups
        next_plan = source.plan(observed, {
            group.group_id: {group.items[0].key} for group in observed.groups})
        assert {item.key for item in next_plan.items} == {'223:1', '226:1'}
        assert dict(next_plan.remaining) == {group.group_id: 1 for group in observed.groups}
        assert proof_bytes(writer) == before and tuple(client.images) == gets
    finally:
        source.close()
        writer.close()
