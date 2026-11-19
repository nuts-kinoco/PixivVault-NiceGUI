"""F: 「ごみ箱を空にする」(原本 gui.py:5595, 5640-5669) の復元。

隔離コピーだけを消し、除外記録・世代インデックス・保存済みページ検証の整合性を保つ。
すべて pytest の tmp_path 内の生成データのみを使う。
"""
from hashlib import sha256
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.ui.duplicate_view import DuplicateView
from pixivvault_nicegui.duplicate_inspection import DuplicateInspectionError, ManagedDuplicateInspection
from pixivvault_nicegui.managed_downloads import ManagedDownloadError, ManagedPixivDownloads
from test_managed_quarantine import Context, owned, plan_for, record


class PowerLoss(BaseException):
    pass


def _ops(writer):
    return writer.root / 'quarantine' / 'operations'


def _copy(writer, operation_id, wid='123', page=2):
    return _ops(writer) / operation_id / f'{wid}-p{page}.png'


def _source(writer, item):
    return writer.root / item.relative_receipt.rsplit('/', 1)[0] / f'page-{item.page}.png'


def _strict(writer):
    """Every strict index / receipt verification path must still pass."""
    state = writer._generations.state_snapshot()
    assert len(writer.saved_receipts(verify_pages=True)) == 4
    return state


def _reopen(writer, remote):
    return ManagedPixivDownloads(writer.root, remote, resolve_record=record)


def test_copy_mode_empty_keeps_exclusions_receipts_and_refuses_restore(owned):
    writer, source, remote = owned
    result = source.execute(plan_for(source, '123:2'), operation_id='a' * 32)
    op = result.operation_id
    before = writer._generations.state_snapshot()
    snap = writer.quarantine.snapshot()
    assert (snap.purgeable_count, snap.copy_count) == (1, 1) and snap.purgeable_bytes > 0
    done = writer.quarantine.purge(expected_count=1, confirmed=True)
    assert (done.removed, done.failed) == (1, 0)
    assert not _copy(writer, op).exists()
    assert (_ops(writer) / op / 'manifest.json').is_file()
    after = _strict(writer)
    assert after['operations'][op]['state'] == 'purged'
    assert after['exclusions'] == before['exclusions']  # (c)
    assert writer.visible_source_pages('123') == (0, 2)
    snap = writer.quarantine.snapshot()
    assert (snap.purgeable_count, snap.copy_count, snap.missing_count, snap.corrupt_count) == (0, 0, 0, 0)
    assert snap.excluded_pages == 1
    with pytest.raises(ManagedDownloadError):  # (b)
        writer.quarantine.restore(op, expected_revision=after['revision'])
    assert writer._generations.state_snapshot()['exclusions'] == before['exclusions']
    restarted = _reopen(writer, remote)  # (d)
    try:
        assert restarted._generations.state_snapshot()['exclusions'] == before['exclusions']
        assert len(restarted.saved_receipts(verify_pages=True)) == 4
        assert restarted.visible_source_pages('123') == (0, 2)
        assert restarted.quarantine.snapshot().purgeable_count == 0
    finally:
        restarted.close()
    assert '123:2' not in {p.key for g in source.scan().groups for p in g.items}


def test_move_mode_empty_destroys_only_copy_and_stays_consistent(owned):
    writer, source, remote = owned
    plan = plan_for(source, '123:2')
    item = plan.items[0]
    result = source.execute(plan, operation_id='b' * 32, mode='move', confirmed_move=True)
    op = result.operation_id
    assert not _source(writer, item).exists() and _copy(writer, op).exists()
    before = writer._generations.state_snapshot()
    assert writer.quarantine.purge(expected_count=1, confirmed=True).removed == 1
    assert not _copy(writer, op).exists() and not _source(writer, item).exists()
    after = _strict(writer)
    assert after['operations'][op]['state'] == 'purged'
    assert after['exclusions'] == before['exclusions']
    assert writer.visible_source_pages('123') == (0, 2)
    with pytest.raises(ManagedDownloadError):
        writer.quarantine.restore(op, expected_revision=after['revision'])
    assert writer.quarantine.snapshot().missing_count == 0
    assert source.recoverable_moves() == ()
    with pytest.raises(ManagedDownloadError):
        writer.read_saved_page(item.relative_receipt, 1, item.receipt_sha256)
    restarted = _reopen(writer, remote)
    try:
        assert len(restarted.saved_receipts(verify_pages=True)) == 4
        assert restarted._generations.state_snapshot()['exclusions'] == before['exclusions']
    finally:
        restarted.close()


def test_restored_move_copies_are_trash_too_without_state_change(owned):
    writer, source, _ = owned
    plan = plan_for(source, '123:2')
    item = plan.items[0]
    op = source.execute(plan, operation_id='c' * 32, mode='move', confirmed_move=True).operation_id
    writer.quarantine.restore(op, expected_revision=writer._generations.state_snapshot()['revision'])
    assert _source(writer, item).exists()
    assert writer.quarantine.snapshot().purgeable_count == 1
    assert writer.quarantine.purge(expected_count=1, confirmed=True).removed == 1
    assert not _copy(writer, op).exists() and _source(writer, item).exists()
    state = _strict(writer)
    assert state['operations'][op]['state'] == 'restored'
    assert writer.visible_source_pages('123') == (0, 1, 2)
    snap = writer.quarantine.snapshot()
    assert (snap.purgeable_count, snap.missing_count, snap.copy_count) == (0, 0, 0)


def test_in_progress_and_missing_manifest_operations_are_never_touched(owned, monkeypatch):
    writer, source, _ = owned
    plan = plan_for(source, '123:2', '223:1')
    original = writer.quarantine._relocate_verified_page

    def fail_second(item, operation_id):
        if item.work_id == '223':
            raise OSError('synthetic relocation failure')
        return original(item, operation_id)

    monkeypatch.setattr(writer.quarantine, '_relocate_verified_page', fail_second)
    with pytest.raises((ManagedDownloadError, OSError)):
        source.execute(plan, operation_id='d' * 32, mode='move', confirmed_move=True)
    monkeypatch.undo()
    state = writer._generations.state_snapshot()
    assert state['operations']['d' * 32]['state'] == 'relocating'
    files = sorted(p.name for p in (_ops(writer) / ('d' * 32)).iterdir())
    snap = writer.quarantine.snapshot()
    assert snap.purgeable_count == 0
    for count in (0, 1, 2):
        with pytest.raises((ManagedDownloadError, DuplicateInspectionError, ValueError)):
            writer.quarantine.purge(expected_count=count, confirmed=True)
    assert sorted(p.name for p in (_ops(writer) / ('d' * 32)).iterdir()) == files
    assert writer._generations.state_snapshot()['revision'] == state['revision']


def test_missing_manifest_operation_makes_empty_refuse_everything(owned):
    writer, source, _ = owned
    first = source.execute(plan_for(source, '123:2'), operation_id='e' * 32).operation_id
    second = source.execute(plan_for(source, '223:1'), operation_id='f' * 32).operation_id
    (_ops(writer) / second / 'manifest.json').unlink()
    snap = writer.quarantine.snapshot()
    assert snap.unconfirmed_count == 1 and snap.purgeable_count == 1
    with pytest.raises(ManagedDownloadError):
        writer.quarantine.purge(expected_count=1, confirmed=True)
    assert _copy(writer, first).exists() and _copy(writer, second, '223', 1).exists()
    assert (writer._generations.state_snapshot(tolerate_missing=True)['operations'][first]['state']
            == 'committed')


@pytest.mark.parametrize('confirmed,count', [(False, 1), (True, 0), (True, 2), (True, -1), (1, 1), (True, '1')])
def test_no_confirmation_or_wrong_count_removes_nothing(owned, confirmed, count):
    writer, source, _ = owned
    op = source.execute(plan_for(source, '123:2'), operation_id='1' * 32).operation_id
    revision = writer._generations.state_snapshot()['revision']
    with pytest.raises((ManagedDownloadError, DuplicateInspectionError, ValueError)):
        writer.quarantine.purge(expected_count=count, confirmed=confirmed)
    with pytest.raises((ManagedDownloadError, DuplicateInspectionError, ValueError)):
        source.empty_trash(expected_count=count, confirmed=confirmed)
    assert _copy(writer, op).exists()
    assert writer._generations.state_snapshot()['revision'] == revision
    assert writer.quarantine.snapshot().purgeable_count == 1


@pytest.mark.parametrize('mode', ['copy', 'move'])
def test_crash_after_commit_before_delete_resumes_and_validates_meanwhile(owned, monkeypatch, mode):
    writer, source, remote = owned
    plan = plan_for(source, '123:2')
    op = source.execute(plan, operation_id='2' * 32, mode=mode, confirmed_move=mode == 'move').operation_id
    before = writer._generations.state_snapshot()
    monkeypatch.setattr(writer.quarantine, '_delete_copy', lambda *a, **k: (_ for _ in ()).throw(PowerLoss()))
    with pytest.raises(PowerLoss):
        writer.quarantine.purge(expected_count=1, confirmed=True)
    monkeypatch.undo()
    assert _copy(writer, op).exists()  # committed intent, bytes not yet deleted
    restarted = _reopen(writer, remote)
    try:
        state = _strict(restarted)
        assert state['operations'][op]['state'] == 'purged'
        assert state['exclusions'] == before['exclusions']
        assert restarted.quarantine.snapshot().purgeable_count == 0  # not counted again
        assert _copy(writer, op).exists()
        reopened_source = ManagedDuplicateInspection(restarted)
        try:
            reopened_source.quarantine_snapshot()  # resumes the pending deletion
        finally:
            reopened_source.close()
        assert not _copy(writer, op).exists()
        _strict(restarted)
        assert restarted._generations.state_snapshot()['exclusions'] == before['exclusions']
    finally:
        restarted.close()


def test_delete_failure_is_reported_and_retried_on_next_call(owned, monkeypatch):
    writer, source, _ = owned
    plans = plan_for(source, '123:2', '223:1')
    op = source.execute(plans, operation_id='3' * 32).operation_id
    real = writer.quarantine._delete_copy
    calls = []

    def flaky(path):
        calls.append(path.name)
        if path.name.startswith('223'):
            raise OSError('synthetic delete refusal')
        return real(path)

    monkeypatch.setattr(writer.quarantine, '_delete_copy', flaky)
    done = writer.quarantine.purge(expected_count=2, confirmed=True)
    assert (done.removed, done.failed) == (1, 1)
    monkeypatch.undo()
    assert not _copy(writer, op).exists() and _copy(writer, op, '223', 1).exists()
    assert writer.quarantine.snapshot().purgeable_count == 0
    writer.quarantine.finish_purges()
    assert not _copy(writer, op, '223', 1).exists()
    _strict(writer)


def test_inspection_empty_trash_resets_scan_and_reports_counts(owned):
    writer, source, _ = owned
    source.execute(plan_for(source, '123:2'), operation_id='4' * 32)
    snap = source.quarantine_snapshot()
    assert snap.purgeable_count == 1
    done = source.empty_trash(expected_count=1, confirmed=True)
    assert (done.removed, done.failed) == (1, 0)
    assert source.latest_scan is None
    assert source.quarantine_snapshot().purgeable_count == 0
    _strict(writer)


def test_managed_proof_of_a_purged_page_fails_closed_without_network(owned):
    """A new generation needing a purged page's bytes must stop, never fetch or crash."""
    writer, source, remote = owned
    plan = plan_for(source, '123:2')
    item = plan.items[0]
    source.execute(plan, operation_id='5' * 32, mode='move', confirmed_move=True)
    writer.quarantine.purge(expected_count=1, confirmed=True)
    calls = tuple(remote.images)
    with pytest.raises(ManagedDownloadError, match='purged'):
        writer._read_saved_page_proof(item.relative_receipt, 1, item.receipt_sha256)
    assert tuple(remote.images) == calls


# ---- legacy side -------------------------------------------------------------

def _legacy(tmp_path, selected, mode):
    from test_required_legacy_duplicates import select, setup
    source, legacy, owner, snapshot, original, archive = setup(tmp_path)
    op = uuid4().hex
    kwargs = dict(mode='move', confirmed_move=True) if mode == 'move' else {}
    result = source.execute(select(source, selected), operation_id=op, **kwargs)
    assert len(result.committed) == len(selected) and not result.failed
    return source, legacy, owner, snapshot, original, archive


def _copies(legacy):
    state = legacy.state(verify_outputs=False)
    return {k: legacy._path(v['copy']) for k, v in state['exclusions'].items()}


@pytest.mark.parametrize('mode', ['copy', 'move'])
def test_legacy_empty_keeps_exclusions_and_state_validates(tmp_path, mode):
    source, legacy, owner, snapshot, original, archive = _legacy(tmp_path, {'2', '3'}, mode)
    try:
        copies = _copies(legacy)
        before = legacy.state()
        snap = legacy.snapshot()
        assert (snap.purgeable_count, snap.copy_count) == (2, 2)
        done = legacy.purge(expected_count=2, confirmed=True)
        assert (done.removed, done.failed) == (2, 0)
        assert not any(p.exists() for p in copies.values())
        after = legacy.state()  # strict verification must not need the copies
        assert set(after['exclusions']) == set(before['exclusions'])
        for key, item in after['exclusions'].items():
            assert {k: v for k, v in item.items() if k != 'purged'} == before['exclusions'][key]
        assert after['archives'] == before['archives']
        snap = legacy.snapshot()
        assert (snap.purgeable_count, snap.copy_count, snap.missing_count, snap.corrupt_count) == (0, 0, 0, 0)
        assert snap.excluded_pages == 2
        view, catalog = legacy.project(snapshot)
        assert '2' not in {w.work_id for w in view.works}
        catalog.close()
        with pytest.raises(ValueError):
            legacy.download_proof(SimpleNamespace(work_id='3', author_id='92', page_count=2), 0)
        rescan = source.scan()
        assert not {p.key for g in rescan.groups for p in g.items} & {'2:1', '3:1'}
        source.close(); owner.close()
        from pixivvault_nicegui.legacy_quarantine import LegacyQuarantine
        owner = ManagedPixivDownloads(tmp_path / 'managed', object(), resolve_record=lambda _: None)
        reopened = LegacyQuarantine(original, snapshot, owner=owner, source_guard=lambda: None)
        try:
            assert reopened.state() == after and reopened.snapshot().purgeable_count == 0
        finally:
            reopened.close()
    finally:
        source.close(); owner.close()


def test_legacy_no_confirmation_or_wrong_count_removes_nothing(tmp_path):
    source, legacy, owner, *_ = _legacy(tmp_path, {'2'}, 'copy')
    try:
        copies = _copies(legacy)
        state = legacy.state()
        for confirmed, count in ((False, 1), (True, 0), (True, 2), (True, '1')):
            with pytest.raises((ValueError, DuplicateInspectionError)):
                legacy.purge(expected_count=count, confirmed=confirmed)
            with pytest.raises((ValueError, DuplicateInspectionError, ManagedDownloadError)):
                source.empty_trash(expected_count=count, confirmed=confirmed)
        assert all(p.exists() for p in copies.values()) and legacy.state() == state
    finally:
        source.close(); owner.close()


@pytest.mark.parametrize('mode', ['copy', 'move'])
def test_legacy_crash_after_commit_before_delete_resumes(tmp_path, monkeypatch, mode):
    source, legacy, owner, snapshot, original, _ = _legacy(tmp_path, {'2'}, mode)
    try:
        copies = _copies(legacy)
        monkeypatch.setattr(legacy, '_delete_copy', lambda *a, **k: (_ for _ in ()).throw(PowerLoss()))
        with pytest.raises(PowerLoss):
            legacy.purge(expected_count=1, confirmed=True)
        monkeypatch.undo()
        assert all(p.exists() for p in copies.values())
        source.close(); owner.close()
        from pixivvault_nicegui.legacy_quarantine import LegacyQuarantine
        owner = ManagedPixivDownloads(tmp_path / 'managed', object(), resolve_record=lambda _: None)
        reopened = LegacyQuarantine(original, snapshot, owner=owner, source_guard=lambda: None)
        source = ManagedDuplicateInspection(owner, legacy=reopened)
        reopened.state()  # strict validation with the copy still present and the item purged
        assert reopened.snapshot().purgeable_count == 0
        source.quarantine_snapshot()  # resume
        assert not any(p.exists() for p in copies.values())
        assert reopened.state()['exclusions']
    finally:
        source.close(); owner.close()


def test_inspection_total_is_managed_plus_legacy_and_mismatch_is_refused(tmp_path):
    source, legacy, owner, *_ = _legacy(tmp_path, {'2', '3'}, 'copy')
    try:
        snap = source.quarantine_snapshot()
        assert snap.purgeable_count == 2
        with pytest.raises((ValueError, DuplicateInspectionError, ManagedDownloadError)):
            source.empty_trash(expected_count=3, confirmed=True)
        assert legacy.snapshot().purgeable_count == 2
        done = source.empty_trash(expected_count=2, confirmed=True)
        assert (done.removed, done.failed) == (2, 0)
        assert source.quarantine_snapshot().purgeable_count == 0
    finally:
        source.close(); owner.close()


def _png(color=(10, 20, 30), size=(16, 12)):
    from io import BytesIO
    from PIL import Image
    stream = BytesIO()
    Image.new('RGB', size, color).save(stream, 'PNG')
    return stream.getvalue()


def test_legacy_redownload_of_purged_page_skips_it_and_still_succeeds(tmp_path):
    """Item 3: like the original, an excluded page is just not fetched; the work still updates."""
    from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
    source, legacy, owner, *_ = _legacy(tmp_path, {'3'}, 'copy')
    calls = []

    class Reader:
        def verify_identity(self):
            calls.append('verify'); self._verified_identity = '7'; return self._verified_identity

        def get_artwork_original_pages(self, wid):
            return ('https://i.pximg.net/p0', 'https://i.pximg.net/p1')

        def get_image_bytes(self, url):
            calls.append(url); return _png((90, 80, 70), (20, 14))

    owner.client = Reader()
    owner.resolve_record = lambda wid: RemoteBookmark(wid, 'illustration', 'show', 'Generated', '92', 'Generated',
        (), 'https://i.pximg.net/t', '2026-11-01T00:00:00Z', 2, 0, 0)
    try:
        before = legacy.state()['exclusions']
        legacy.purge(expected_count=1, confirmed=True)
        relative = owner.run(SimpleNamespace(kind='pixiv-illust', target_id='3'), Context())
        assert 'https://i.pximg.net/p0' not in calls and 'https://i.pximg.net/p1' in calls
        assert len(owner.saved_receipts(verify_pages=True)) >= 1
        assert owner.visible_source_pages('3') == (1,)  # the excluded page stays hidden
        assert set(legacy.state()['exclusions']) == set(before)
        with pytest.raises(ValueError, match='purged'):
            legacy.download_proof(SimpleNamespace(work_id='3', author_id='92', page_count=2), 0)
        assert owner.run(SimpleNamespace(kind='pixiv-illust', target_id='3'), Context()) == relative
    finally:
        source.close(); owner.close()


def _intent(record_, entry, mode, date):
    from pixivvault_nicegui.managed_download_requests import ManagedAuthorIntent
    if entry is None:
        return ManagedAuthorIntent(record_, 'follow', date, None, mode)
    return ManagedAuthorIntent(record_, 'follow', date, entry['sha256'], mode, entry['relative_receipt'])


@pytest.fixture
def generations(tmp_path):
    """Four works saved as author generations, so each can later be updated."""
    from test_managed_quarantine import Client as Remote
    remote = Remote()
    remote.date = '2026-11-16'
    remote.get_artwork_update_revision = lambda wid, author: dict(
        author_id='24' if wid in ('123', '124') else '25',
        page_count=3 if wid == '123' else 1, illustration_type=0, update_date=remote.date)
    writer = ManagedPixivDownloads(tmp_path / 'managed', remote, resolve_record=record)
    for wid in ('123', '124', '223', '224'):
        writer.run(SimpleNamespace(kind='pixiv-illust', target_id=wid, job_id=wid.ljust(32, '0')), Context(),
                   intent=_intent(record(wid), None, 'new', remote.date))
    source = ManagedDuplicateInspection(writer)
    try:
        yield writer, source, remote
    finally:
        source.close(); writer.close()


@pytest.mark.parametrize('mode', ['copy', 'move'])
def test_update_after_empty_skips_the_purged_page_and_keeps_everything_consistent(generations, mode):
    writer, source, remote = generations
    kwargs = dict(mode='move', confirmed_move=True) if mode == 'move' else {}
    source.execute(plan_for(source, '123:2'), operation_id='d' * 32, **kwargs)
    assert writer.quarantine.purge(expected_count=1, confirmed=True).removed == 1
    exclusions = writer._generations.state_snapshot()['exclusions']
    remote.images.clear()
    remote.date = '2026-11-17'
    first = writer._generations.current('123')
    relative = writer.run(SimpleNamespace(kind='pixiv-illust', target_id='123', job_id='e' * 32),
                          Context(), intent=_intent(record('123'), first, 'updated', remote.date))
    assert relative.startswith('generations/') and relative != first['relative_receipt']
    assert not any(url.endswith('/123/1.png') for url in remote.images)  # the excluded page is not fetched
    assert len(remote.images) == 2
    assert writer._generations.state_snapshot()['exclusions'] == exclusions
    assert writer.visible_source_pages('123') == (0, 2)
    assert len(writer.saved_receipts(verify_pages=True)) == 4
    entry = writer._generations.current('123')
    assert writer.measure_active_receipt_bytes(entry['relative_receipt'], entry['sha256'])[0] == '24'
    assert writer.quarantine.snapshot().purgeable_count == 0
    restarted = _reopen(writer, remote)
    try:
        assert len(restarted.saved_receipts(verify_pages=True)) == 4
        assert restarted.visible_source_pages('123') == (0, 2)
    finally:
        restarted.close()


def test_capacity_measurement_survives_an_emptied_move(owned):
    """Item 1: a purged page counts as 0 bytes instead of failing the author's measurement."""
    writer, source, _ = owned
    entry = writer._generations.current('123')
    full = writer.measure_active_receipt_bytes(entry['relative_receipt'], entry['sha256'])[1]
    plan = plan_for(source, '123:2')
    item = plan.items[0]
    page_bytes = len(writer.read_saved_page(item.relative_receipt, 1, item.receipt_sha256))
    source.execute(plan, operation_id='f' * 32, mode='move', confirmed_move=True)
    assert writer.measure_active_receipt_bytes(entry['relative_receipt'], entry['sha256'])[1] == full
    assert writer.quarantine.purge(expected_count=1, confirmed=True).removed == 1
    author, total = writer.measure_active_receipt_bytes(entry['relative_receipt'], entry['sha256'])
    assert (author, total) == ('24', full - page_bytes)


class _FailingLegacy:
    """A legacy store whose purge fails with an I/O error."""

    def __init__(self, count=1):
        from pixivvault_nicegui.managed_quarantine import QuarantineSnapshot
        self.snap = QuarantineSnapshot('ready', count, 10, count, None, 'r', 0, 0, 0, count, 10)

    def snapshot(self):
        return self.snap

    def finish_purges(self):
        return 0

    def purge(self, *, expected_count, confirmed):
        raise OSError('synthetic legacy failure')

    def close(self):
        pass


def test_managed_done_then_legacy_failure_returns_a_partial_result(owned):
    writer, source, _ = owned
    source.execute(plan_for(source, '123:2'), operation_id='1' * 32)
    source.legacy = _FailingLegacy(1)
    assert source.quarantine_snapshot().purgeable_count == 2
    done = source.empty_trash(expected_count=2, confirmed=True)
    assert (done.removed, done.failed) == (1, 1)
    assert not _copy(writer, '1' * 32).exists()
    assert source.quarantine_snapshot().purgeable_count == 1  # re-running only has the legacy item left


def test_managed_interrupted_midway_reports_what_was_done_and_converges(owned, monkeypatch):
    writer, source, _ = owned
    source.execute(plan_for(source, '123:2'), operation_id='2' * 32)
    source.execute(plan_for(source, '224:1'), operation_id='3' * 32)
    real = writer._generations.purge_quarantine
    calls = []

    def once(operation_id, **kw):
        calls.append(operation_id)
        if len(calls) == 2:
            raise OSError('synthetic commit failure')
        return real(operation_id, **kw)

    monkeypatch.setattr(writer._generations, 'purge_quarantine', once)
    done = source.empty_trash(expected_count=2, confirmed=True)
    assert (done.removed, done.failed) == (1, 1)
    monkeypatch.undo()
    assert source.quarantine_snapshot().purgeable_count == 1
    again = source.empty_trash(expected_count=1, confirmed=True)
    assert (again.removed, again.failed) == (1, 0)
    _strict(writer)


class _CountingLegacy(_FailingLegacy):
    def __init__(self, count=1):
        super().__init__(count)
        self.purge_calls = 0

    def purge(self, *, expected_count, confirmed):
        self.purge_calls += 1
        return super().purge(expected_count=expected_count, confirmed=confirmed)


def test_managed_refusal_stops_everything_and_never_reaches_legacy(owned, monkeypatch):
    """Recheck item 1: a count mismatch refuses the whole call, as in c9c77c7."""
    writer, source, _ = owned
    op = source.execute(plan_for(source, '123:2'), operation_id='7' * 32).operation_id
    legacy = source.legacy = _CountingLegacy(1)

    def refuse(**kw):
        raise ManagedDownloadError('trash content changed')

    monkeypatch.setattr(writer.quarantine, 'purge', refuse)
    with pytest.raises(ManagedDownloadError):
        source.empty_trash(expected_count=2, confirmed=True)
    assert legacy.purge_calls == 0 and _copy(writer, op).exists()


def test_stand_in_page_stays_hidden_when_the_legacy_store_is_not_bound(tmp_path):
    """Recheck item 2: a start without the old-DB snapshot must not show the black stand-in."""
    from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
    from pixivvault_nicegui.managed_downloads import ManagedPageQuarantined
    source, legacy, owner, *_ = _legacy(tmp_path, {'3'}, 'copy')

    class Reader:
        def verify_identity(self):
            self._verified_identity = '7'
            return self._verified_identity

        def get_artwork_original_pages(self, wid):
            return ('https://i.pximg.net/p0', 'https://i.pximg.net/p1')

        def get_image_bytes(self, url):
            return _png((90, 80, 70), (20, 14))

    owner.client = Reader()
    maker = lambda wid: RemoteBookmark(wid, 'illustration', 'show', 'Generated', '92', 'Generated',
        (), 'https://i.pximg.net/t', '2026-11-01T00:00:00Z', 2, 0, 0)
    owner.resolve_record = maker
    try:
        legacy.purge(expected_count=1, confirmed=True)
        relative = owner.run(SimpleNamespace(kind='pixiv-illust', target_id='3'), Context())
        digest = owner._generations.current('3')['sha256']
        unbound = ManagedPixivDownloads(tmp_path / 'managed', object(), resolve_record=maker)
        try:
            assert unbound.legacy_quarantine is None
            assert unbound.visible_source_pages('3') == (1,)
            with pytest.raises(ManagedPageQuarantined):
                unbound.read_saved_page(relative, 0, digest)
            with pytest.raises(ManagedPageQuarantined):
                unbound.assert_page_available('3', 0)
            assert len(unbound.read_saved_page(relative, 1, digest)) > 0
            snapshot = unbound.receipt_snapshot() if hasattr(unbound, 'receipt_snapshot') else None
            if snapshot is not None:
                assert snapshot.pages['3'] == (1,)
        finally:
            unbound.close()
    finally:
        source.close(); owner.close()


@pytest.mark.asyncio
async def test_ui_dialog_keeps_the_original_shape_and_partial_wording(owned):
    writer, source, _ = owned
    source.execute(plan_for(source, '123:2'), operation_id='8' * 32)
    source.legacy = _FailingLegacy(1)
    client = Client(ui.page('/_f_trash_empty_ui_shape'))
    try:
        with client:
            view = DuplicateView(source)
            view.set_active(True)
            await view.open_trash()
            inside = set(view.trash_details.descendants())
            for element in (view.trash_copy_count, view.trash_copy_bytes, view.trash_excluded,
                            view.trash_restore_rows, view.trash_refresh_button, view.trash_open_button):
                assert element in inside
            for element in (view.trash_title, view.trash_confirm_count, view.trash_empty_button):
                assert element not in inside
            view.trash_confirm_count.set_value('2')
            await view.empty_trash()
            assert view.notice.text == 'ごみ箱: 1件を完全に削除しました（除外の記録は残ります）・失敗 1件'
    finally:
        client.delete()


def _format_cases():
    return ((95, '95B'), (1536, '1.5KB'), (5 * 1024 * 1024, '5.0MB'), (0, '0B'))


@pytest.mark.asyncio
async def test_ui_wording_matches_the_original_dialog_and_entry_link(owned):
    writer, source, _ = owned
    op = source.execute(plan_for(source, '123:2'), operation_id='5' * 32).operation_id
    from pixivvault_nicegui.ui.duplicate_view import format_trash_size
    for value, text in _format_cases():
        assert format_trash_size(value) == text
    size = _copy(writer, op).stat().st_size
    client = Client(ui.page('/_f_trash_empty_ui_wording'))
    try:
        with client:
            view = DuplicateView(source)
            view.set_active(True)
            assert view.trash_button.text == 'ごみ箱'
            await view.refresh_trash_summary()
            assert view.trash_button.text == f'ごみ箱 1件・{format_trash_size(size)}'
            await view.open_trash()
            assert view.trash_title.text == f'ごみ箱 1件・{format_trash_size(size)}'
            assert 'KiB' not in view.trash_title.text and ' B' not in view.trash_title.text
            labels = [element.text for element in view.trash_dialog.descendants() if hasattr(element, 'text')]
            assert not any('消えるのは隔離コピーだけです' in label for label in labels)
            assert any('空にすると元に戻せません。件数を入力すると「空にする」が押せます。' == label for label in labels)
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_ui_title_and_body_agree_when_a_copy_is_damaged(owned):
    writer, source, _ = owned
    op = source.execute(plan_for(source, '123:2'), operation_id='6' * 31 + '0').operation_id
    _copy(writer, op).write_bytes(b'damaged')
    client = Client(ui.page('/_f_trash_empty_ui_damaged'))
    try:
        with client:
            view = DuplicateView(source)
            view.set_active(True)
            await view.open_trash()
            assert view.trash_title.text.startswith('ごみ箱 1件・')
            body = view.trash_copy_count.text + view.trash_status.text
            assert '1件' in view.trash_status.text and 'ごみ箱の件数' in view.trash_status.text, body
    finally:
        client.delete()


# ---- UI (original dialog wording and typed-count gate) -----------------------

@pytest.mark.asyncio
async def test_ui_empty_button_needs_the_exact_typed_count(owned):
    writer, source, _ = owned
    op = source.execute(plan_for(source, '123:2'), operation_id='6' * 32).operation_id
    client = Client(ui.page('/_f_trash_empty_ui'))
    try:
        with client:
            view = DuplicateView(source)
            view.set_active(True)
            await view.open_trash()
            assert view.trash_title.text.startswith('ごみ箱 1件・')
            assert not view.trash_empty_button.enabled
            for wrong in ('', '0', '2', 'abc'):
                view.trash_confirm_count.set_value(wrong)
                assert not view.trash_empty_button.enabled
                await view.empty_trash()  # a forced call must still do nothing
                assert _copy(writer, op).exists()
            view.trash_confirm_count.set_value('1')
            assert view.trash_empty_button.enabled
            await view.empty_trash()
            assert not _copy(writer, op).exists()
            assert view.notice.text == 'ごみ箱: 1件を完全に削除しました（除外の記録は残ります）'
            assert not view.trash_dialog.value and not view.busy
            await view.open_trash()
            assert view.trash_title.text.startswith('ごみ箱 0件・')
            assert not view.trash_empty_button.enabled
        assert writer.visible_source_pages('123') == (0, 2)
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_ui_changed_content_is_refused_without_deleting(owned):
    writer, source, _ = owned
    op = source.execute(plan_for(source, '123:2'), operation_id='7' * 32).operation_id
    client = Client(ui.page('/_f_trash_empty_ui_changed'))
    try:
        with client:
            view = DuplicateView(source)
            view.set_active(True)
            await view.open_trash()
            view.trash_confirm_count.set_value('1')
            source.execute(plan_for(source, '223:1'), operation_id='8' * 32)  # trash grows meanwhile
            await view.empty_trash()
            assert _copy(writer, op).exists() and _copy(writer, '8' * 32, '223', 1).exists()
            assert '空にできませんでした' in view.notice.text
    finally:
        client.delete()
