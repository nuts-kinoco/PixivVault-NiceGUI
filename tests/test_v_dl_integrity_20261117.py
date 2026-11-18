"""Checker findings D1-D3 and improvements (a)(b)(c)(e): the journalled generation index must never
lose or revert a saved work, whichever build opens the root next."""
import hashlib
import importlib.util
import json
import os
import threading
import time
from pathlib import Path

import pytest

from pixivvault_nicegui import managed_generations as mg
from pixivvault_nicegui.managed_downloads import ManagedDownloadError
from test_v_dl_cap_20261117 import (journal_of, make_generation, make_legacy, make_root, mark_of, png,
                                    publish, receipt_bytes, root, seed_index)  # noqa: F401

FIXTURE = Path(__file__).parent / 'fixtures' / 'managed_generations_v4_reader.py'


def old_reader():
    """The generation index code as it was before journals (what 'rolling the app back' runs)."""
    spec = importlib.util.spec_from_file_location('pixivvault_nicegui.managed_generations_v4_reader', FIXTURE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def update_work(g, root, wid, suffix):
    old = g.current(wid)
    relative, digest = make_generation(root, wid, suffix=suffix)
    g.publish(wid, relative, digest, '2026-03-03T00:00:00+00:00', old['sha256'], old['relative_receipt'], 'e' * 64)
    return relative


# ---- D1: a build that does not know the journal must refuse the root -------------------------------

def test_d1_the_index_is_version_5_as_soon_as_a_journal_exists_and_the_old_code_refuses_it(root):
    seed_index(root, 3)
    g = mg.ManagedGenerations(root)
    publish(g, root, '900001')
    packet = json.loads((root / 'active-generations.json').read_bytes())
    assert packet['version'] == 5 and packet['version'] not in (1, 2, 3, 4)
    with pytest.raises(ManagedDownloadError):
        old_reader().ManagedGenerations(root)          # fails closed; it cannot see "no 900001"


def test_d1_new_old_new_round_trip_never_reverts_an_updated_work(root):
    seed_index(root, 3)
    g = mg.ManagedGenerations(root)
    publish(g, root, '900001')
    updated = update_work(g, root, '100001', 'a' * 32)
    with pytest.raises(ManagedDownloadError):
        old_reader().ManagedGenerations(root)          # the rolled-back build is refused, writes nothing
    again = mg.ManagedGenerations(root)
    assert again.current('100001')['relative_receipt'] == updated and again.current('900001') is not None
    assert again.entry_count() == 4


def write_legacy_journal(root, records, base_digest):
    """A journal as the first builds wrote it (index still v2): header bound to the index digest."""
    header = json.dumps({'base': base_digest, 'kind': mg._JOURNAL_KIND, 'version': 1}, sort_keys=True,
                        separators=(',', ':')).encode() + b'\n'
    chain, lines = hashlib.sha256(header).hexdigest(), []
    for seq, entry in enumerate(records, 1):
        canonical = json.dumps(entry, sort_keys=True, ensure_ascii=True, separators=(',', ':'))
        chain = hashlib.sha256((chain + canonical).encode()).hexdigest()
        lines.append(json.dumps({'chain': chain, 'entry': entry, 'seq': seq}, sort_keys=True,
                                ensure_ascii=True, separators=(',', ':')).encode() + b'\n')
    (root / 'active-generations.journal').write_bytes(header + b''.join(lines))


def entry_for(root, wid, suffix, fingerprint='c' * 64):
    relative, digest = make_generation(root, wid, suffix=suffix)
    return {'work_id': wid, 'relative_receipt': relative, 'sha256': digest,
            'update_date': '2026-03-03T00:00:00+00:00', 'accepted_fingerprint': fingerprint}


def test_d1_a_legacy_journal_is_adopted_only_when_it_is_the_index_or_newer_than_nothing(root):
    entries = seed_index(root, 2)
    base_digest = hashlib.sha256((root / 'active-generations.json').read_bytes()).hexdigest()
    fresh = entry_for(root, '900001', '1' * 32)
    write_legacy_journal(root, [fresh], base_digest)
    g = mg.ManagedGenerations(root)
    assert g.current('900001') == fresh and g.entry_count() == 3     # bound to this index: replayed
    publish(g, root, '900002')                                        # the next save folds it into v5
    assert json.loads((root / 'active-generations.json').read_bytes())['version'] == 5
    assert not (root / 'active-generations.journal').exists()
    assert mg.ManagedGenerations(root).entry_count() == 4


def test_d1_a_legacy_journal_older_than_the_index_must_match_it_entry_for_entry(root):
    entries = seed_index(root, 2)
    old_entry = dict(entries['100000'])
    newer = entry_for(root, '100000', '2' * 32)                       # the index moved on to a newer version
    packet = {'version': 2, 'entries': {**entries, '100000': newer}}
    (root / 'active-generations.json').write_bytes(
        json.dumps(packet, sort_keys=True, separators=(',', ':')).encode())
    write_legacy_journal(root, [old_entry], 'f' * 64)                 # journal of an earlier index
    with pytest.raises(ManagedDownloadError, match='index rejected'):  # never reverts 100000
        mg.ManagedGenerations(root)
    write_legacy_journal(root, [newer], 'f' * 64)                     # identical to the index: harmless
    g = mg.ManagedGenerations(root)
    assert g.current('100000')['relative_receipt'] == newer['relative_receipt']


# ---- D2: a journal that vanishes, empties or is cut short is refused -------------------------------

def damaged(root, how):
    seed_index(root, 3)
    g = mg.ManagedGenerations(root)
    for number in range(3):
        publish(g, root, str(900001 + number))
    journal, mark = journal_of(root), mark_of(root)
    lines = journal.read_bytes().split(b'\n')
    if how == 'delete_journal':
        journal.unlink()
    elif how == 'delete_mark':
        mark.unlink()
    elif how == 'empty_to_header':
        journal.write_bytes(lines[0] + b'\n')
    elif how == 'drop_last_record':
        journal.write_bytes(b'\n'.join(lines[:-2]) + b'\n')
    elif how == 'cut_in_the_middle':
        journal.write_bytes(b'\n'.join([lines[0], lines[2], lines[3]]) + b'\n')
    elif how == 'zero_length':
        journal.write_bytes(b'')


@pytest.mark.parametrize('how', ['delete_journal', 'delete_mark', 'empty_to_header', 'drop_last_record',
                                 'cut_in_the_middle', 'zero_length'])
def test_d2_a_missing_emptied_or_shortened_journal_is_refused_not_read_as_fewer_works(root, how):
    damaged(root, how)
    with pytest.raises(ManagedDownloadError, match='index rejected'):
        mg.ManagedGenerations(root)


def test_d2_only_a_torn_last_line_is_forgiven_and_so_is_a_mark_one_save_behind(root):
    seed_index(root, 3)
    g = mg.ManagedGenerations(root)
    publish(g, root, '900001')
    behind = mark_of(root).read_bytes()
    publish(g, root, '900002')
    journal = journal_of(root)
    mark_of(root).write_bytes(behind)                    # crash between the append and the mark: 1 behind
    assert mg.ManagedGenerations(root).entry_count() == 5
    journal.write_bytes(journal.read_bytes() + b'{"chain":"x')          # and a torn line on top
    assert mg.ManagedGenerations(root).entry_count() == 5


def journal_records(root):
    return max(0, journal_of(root).read_bytes().count(b"\n") - 1)


def mark_records(root):
    return json.loads(mark_of(root).read_bytes())['records']


def test_d2_a_mark_several_saves_behind_is_recoverable_and_the_next_save_catches_it_up(root):
    seed_index(root, 3)
    g = mg.ManagedGenerations(root)
    publish(g, root, '900001')
    behind = mark_of(root).read_bytes()
    publish(g, root, '900002')
    publish(g, root, '900003')
    mark_of(root).write_bytes(behind)                     # two saves ahead of the mark (the old failure state)
    reopened = mg.ManagedGenerations(root)
    assert reopened.entry_count() == 6                    # nothing lost, nothing refused
    publish(reopened, root, '900004')
    assert mark_records(root) == journal_records(root) == 4
    assert mg.ManagedGenerations(root).entry_count() == 7


def test_d2_a_mark_that_is_not_on_the_journals_chain_is_still_damage(root):
    seed_index(root, 3)
    g = mg.ManagedGenerations(root)
    publish(g, root, '900001')
    publish(g, root, '900002')
    mark = json.loads(mark_of(root).read_bytes())
    mark.update(records=1, chain='0' * 64)
    mark_of(root).write_bytes(json.dumps(mark, sort_keys=True, separators=(',', ':')).encode())
    with pytest.raises(ManagedDownloadError, match='index rejected'):
        mg.ManagedGenerations(root)


def test_d2_every_fold_gets_a_new_journal_and_the_expectation_follows_the_index(root, monkeypatch):
    monkeypatch.setattr(mg, '_JOURNAL_RETAIN_SECONDS', 0.0)
    monkeypatch.setattr(mg, '_COMPACT_RECORDS', 2)
    seed_index(root, 2)
    g = mg.ManagedGenerations(root)
    seen = set()
    for number in range(7):
        publish(g, root, str(900001 + number))
        seen.add(json.loads((root / 'active-generations.json').read_bytes())['journal']['id'])
    assert len(seen) >= 3
    assert len(list(root.glob('active-generations-*.journal'))) <= 2          # older ones are swept
    assert mg.ManagedGenerations(root).entry_count() == 9


# ---- D3: opening while another instance folds the journal ------------------------------------------

def test_d3_a_reader_never_sees_a_spurious_rejection_while_a_writer_saves_and_folds(root, monkeypatch):
    monkeypatch.setattr(mg, '_COMPACT_RECORDS', 4)
    seed_index(root, 5)
    writer = mg.ManagedGenerations(root)
    publish(writer, root, '900000')
    errors, stop, reads = [], threading.Event(), [0]

    def read():
        while not stop.is_set():
            try:
                reader = mg.ManagedGenerations(root)       # a fresh open, as a second process would
                reader.entry_count()
                other = mg.ManagedGenerations(root)
                other.current('900000')
                reads[0] += 1
            except Exception as error:                     # noqa: BLE001
                errors.append(repr(error))
    thread = threading.Thread(target=read)
    thread.start()
    try:
        for number in range(1, 60):
            publish(writer, root, str(900000 + number))
    finally:
        stop.set()
        thread.join(30)
    assert errors == [] and reads[0] > 0
    assert mg.ManagedGenerations(root).entry_count() == 5 + 60


# ---- (a) a store with no index (outputs/ only) is verified once, not on every call -----------------

def test_a_an_outputs_only_store_is_verified_once_and_still_refuses_an_unindexed_generation(root, monkeypatch):
    make_legacy(root, '800001')
    make_legacy(root, '800002')
    g = mg.ManagedGenerations(root)
    loads = []
    original = g._load_full
    monkeypatch.setattr(g, '_load_full', lambda: loads.append(1) or original())
    for _ in range(5):
        g.entries()
        g.current('800001')
    make_legacy(root, '800003')
    assert g.entry_count() == 3 and g.current('800003') is not None
    assert loads == []                                       # no re-read at all after opening
    make_generation(root, '900001')                         # a sealed generation without any index
    with pytest.raises(ManagedDownloadError, match='index missing'):
        g.entries()


# ---- (b) silent corruption that keeps size and time ----------------------------------------------

def writer_with_works(tmp_path, count):
    from test_v_dl_cap_20261117 import _writer_with_works
    return _writer_with_works(tmp_path, count)


def test_b_same_size_same_time_rewrite_is_caught_when_the_bytes_are_read_and_after_the_recheck_interval(
        tmp_path, monkeypatch):
    from pixivvault_nicegui import managed_downloads as downloads
    writer = writer_with_works(tmp_path, 2)
    try:
        first = writer.catalog_snapshot()
        relative, receipt, digest = first.receipts[0]
        page = (writer.root / relative).parent / receipt['files'][0]['name']
        stat = page.stat()
        data = bytearray(page.read_bytes())
        data[-5] ^= 0xFF                                    # same length, flipped byte
        page.write_bytes(bytes(data))
        os.utime(page, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        with pytest.raises(ManagedDownloadError):
            writer.read_saved_page(relative, 0, digest)     # reading always hashes the bytes
        monkeypatch.setattr(downloads, '_RECEIPT_RECHECK_SECONDS', 0.0)
        with pytest.raises(ManagedDownloadError):
            writer.catalog_snapshot()                       # and the periodic full re-verification
    finally:
        writer.close()


def test_b_a_reopened_store_verifies_in_full_again(tmp_path):
    from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads
    writer = writer_with_works(tmp_path, 1)
    client, resolver = writer.client, writer.resolve_record
    first = writer.catalog_snapshot()
    relative, receipt, _ = first.receipts[0]
    page = (writer.root / relative).parent / receipt['files'][0]['name']
    stat = page.stat()
    writer.close()
    data = bytearray(page.read_bytes())
    data[-5] ^= 0xFF
    page.write_bytes(bytes(data))
    os.utime(page, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    reopened = ManagedPixivDownloads(tmp_path / 'managed', client, resolve_record=resolver)
    try:
        with pytest.raises(ManagedDownloadError):
            reopened.catalog_snapshot()
    finally:
        reopened.close()


# ---- (c) the journal never grows past what the next open reads ----------------------------------

def test_c_a_journal_at_its_ceiling_with_a_failing_fold_stops_saving_with_a_capacity_code(root, monkeypatch):
    seed_index(root, 3)
    g = mg.ManagedGenerations(root)
    publish(g, root, '900001')
    size = journal_of(root).stat().st_size
    monkeypatch.setattr(mg, '_COMPACT_RECORDS', 10 ** 9)
    monkeypatch.setattr(mg, '_COMPACT_BYTES', 10 ** 12)
    monkeypatch.setattr(mg, '_MAX_JOURNAL_BYTES', size + mg._JOURNAL_SLACK + 700)
    monkeypatch.setattr(g, '_compact', lambda loaded: (_ for _ in ()).throw(OSError('disk')))
    saved = 0
    with pytest.raises(ManagedDownloadError, match='journal full') as caught:
        for number in range(2, 50):
            publish(g, root, str(900000 + number))
            saved += 1
    assert caught.value.reason_code == 'storage_capacity' and saved >= 1
    assert journal_of(root).stat().st_size <= mg._MAX_JOURNAL_BYTES
    assert mg.ManagedGenerations(root).entry_count() == 3 + 1 + saved     # nothing saved is lost


def test_c_a_journal_at_its_ceiling_is_folded_and_saving_goes_on(root, monkeypatch):
    seed_index(root, 3)
    g = mg.ManagedGenerations(root)
    publish(g, root, '900001')
    monkeypatch.setattr(mg, '_COMPACT_RECORDS', 10 ** 9)
    monkeypatch.setattr(mg, '_COMPACT_BYTES', 10 ** 12)
    monkeypatch.setattr(mg, '_MAX_JOURNAL_BYTES', journal_of(root).stat().st_size + mg._JOURNAL_SLACK + 700)
    for number in range(2, 12):
        publish(g, root, str(900000 + number))
    assert g.entry_count() == 14 and mg.ManagedGenerations(root).entry_count() == 14


# ---- (e) an ID / bookmark save (no intent, lands in outputs/) is refused before it is downloaded ----

def test_e_a_save_without_intent_is_refused_up_front_when_the_store_is_full(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
    from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads
    monkeypatch.setattr(mg, '_MAX_ENTRIES', 2)
    downloaded = []

    class Context:
        def checkpoint(self): pass
        def progress(self, *_): pass

    class Client:
        def verify_identity(self): return '99'
        def get_artwork_original_pages(self, wid): return (f'https://i.pximg.net/synthetic/{wid}/1.png',)
        def get_image_bytes(self, url):
            downloaded.append(url)
            return png()

    def record(wid):
        return RemoteBookmark(wid, 'illustration', 'show', 'T' + wid, '25', 'A', (), '', '2026-10-29T00:00:00Z', 1, 0, 0)
    writer = ManagedPixivDownloads(tmp_path / 'managed', Client(), resolve_record=record)
    try:
        def save(wid):
            return writer.run(SimpleNamespace(kind='pixiv-illust', target_id=wid, job_id='%032x' % int(wid)), Context())
        save('501'), save('502')
        downloaded.clear()
        with pytest.raises(ManagedDownloadError) as caught:
            save('503')
        assert caught.value.reason_code == 'storage_capacity' and downloaded == []   # nothing was fetched
        assert writer._generations.entry_count() == 2
        save('501')                                       # an update of a saved work is still allowed
    finally:
        writer.close()


def test_d3_a_fold_waits_for_another_process_that_has_the_index_open_for_a_moment(root):
    seed_index(root, 3)
    g = mg.ManagedGenerations(root)
    publish(g, root, '900001')
    holder = open(root / 'active-generations.json', 'rb')           # a reader mid-read (Windows: no replace)
    threading.Timer(0.4, holder.close).start()
    g._compact(g._loaded)                                            # used to fail with PermissionError
    assert mg.ManagedGenerations(root).entry_count() == 4


# ---- checker round 2 ------------------------------------------------------------------------------

def test_r2_1_failing_mark_writes_never_leave_the_journal_two_ahead(root, monkeypatch):
    """Fault injection: the mark cannot be written N times in a row. Every state stays openable."""
    seed_index(root, 3)
    g = mg.ManagedGenerations(root)
    publish(g, root, '900001')
    real = g._write_mark
    broken = {'on': False}

    def write_mark(*args, **kwargs):
        if broken['on']:
            raise OSError('mark held by another process')
        return real(*args, **kwargs)
    monkeypatch.setattr(g, '_write_mark', write_mark)
    broken['on'] = True
    publish(g, root, '900002')                            # the save itself commits; only the mark lags
    for number in range(3, 7):                            # further saves are refused, not appended
        with pytest.raises(ManagedDownloadError):
            publish(g, root, str(900000 + number))
        assert journal_records(root) - mark_records(root) <= 1
        assert mg.ManagedGenerations(root).entry_count() == 5      # still opens, nothing lost
    broken['on'] = False
    publish(g, root, '900010')                            # the mark is caught up first, then appended
    assert journal_records(root) == mark_records(root) == 3
    assert mg.ManagedGenerations(root).entry_count() == 6


def test_r2_1_a_crash_after_the_append_before_the_mark_is_recovered_by_the_next_save(root, monkeypatch):
    seed_index(root, 3)
    g = mg.ManagedGenerations(root)
    publish(g, root, '900001')
    monkeypatch.setattr(g, '_write_mark', lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):                # the process dies between the two writes
        publish(g, root, '900002')
    assert journal_records(root) == 2 and mark_records(root) == 1
    survivor = mg.ManagedGenerations(root)
    assert survivor.entry_count() == 5 and survivor.current('900002') is not None
    publish(survivor, root, '900003')
    assert journal_records(root) == mark_records(root) == 3


def test_r2_1_a_mark_held_open_by_another_process_for_a_while_does_not_wedge_the_root(root):
    seed_index(root, 3)
    g = mg.ManagedGenerations(root)
    publish(g, root, '900001')
    holder = open(mark_of(root), 'rb')                    # plain open: Windows cannot replace it
    threading.Timer(2.5, holder.close).start()
    publish(g, root, '900002')                            # the mark lags, the save stands
    for number in range(3, 6):
        try:
            publish(g, root, str(900000 + number))
        except ManagedDownloadError:
            pass
    assert mg.ManagedGenerations(root).entry_count() >= 5        # never a rejected root
    holder.close()
    publish(g, root, '900099')
    assert journal_records(root) == mark_records(root)
    assert mg.ManagedGenerations(root).current('900099') is not None


# ---- item 4: the first version upgrade and routine folds ---------------------------------------------

def test_r2_4_a_reader_that_keeps_the_index_open_no_longer_blocks_the_fold(root):
    seed_index(root, 3)
    g = mg.ManagedGenerations(root)
    publish(g, root, '900001')
    reader = mg._open_shared(root / 'active-generations.json')    # a reader mid-read, shared-delete
    try:
        started = time.monotonic()
        g._compact(g._loaded, 0.2)                                # replaces the index at once
        assert time.monotonic() - started < 0.5
        assert reader.read(1) == b'{'                             # and the reader still has its snapshot
    finally:
        reader.close()
    assert mg.ManagedGenerations(root).entry_count() == 4


def test_r2_4_the_first_version_upgrade_waits_out_a_plain_holder_and_succeeds(root):
    seed_index(root, 3)
    g = mg.ManagedGenerations(root)
    holder = open(root / 'active-generations.json', 'rb')         # an old-style reader
    threading.Timer(1.2, holder.close).start()
    started = time.monotonic()
    publish(g, root, '900001')                                    # v2 -> v5 cannot be skipped
    assert 1.0 < time.monotonic() - started < 4.5
    assert json.loads((root / 'active-generations.json').read_bytes())['version'] == 5


def test_r2_4_a_routine_fold_that_is_blocked_gives_up_quickly_and_keeps_the_fence_short(root, monkeypatch):
    monkeypatch.setattr(mg, '_COMPACT_RECORDS', 1)
    seed_index(root, 3)
    g = mg.ManagedGenerations(root)
    publish(g, root, '900001')
    holder = open(root / 'active-generations.json', 'rb')
    try:
        started = time.monotonic()
        publish(g, root, '900002')                                # the fold is optional: the save wins
        assert time.monotonic() - started < 1.5
        assert g._loaded.fold_failed is True
    finally:
        holder.close()
    publish(g, root, '900003')
    assert g._loaded.fold_failed is False
    assert mg.ManagedGenerations(root).entry_count() == 6


# ---- item 5: a journal that cannot be folded is a capacity fact the download must hear about ------------

def test_r2_5_assert_room_and_capacity_see_a_journal_that_cannot_be_folded(root, monkeypatch):
    seed_index(root, 3)
    g = mg.ManagedGenerations(root)
    publish(g, root, '900001')
    monkeypatch.setattr(mg, '_COMPACT_RECORDS', 10 ** 9)
    monkeypatch.setattr(mg, '_COMPACT_BYTES', 10 ** 12)
    monkeypatch.setattr(mg, '_MAX_JOURNAL_BYTES', journal_of(root).stat().st_size + mg._JOURNAL_SLACK + 1500)
    monkeypatch.setattr(g, '_compact', lambda loaded, patience=5.0: (_ for _ in ()).throw(OSError('disk')))
    before = g.capacity()['headroom']
    for number in range(2, 20):
        try:
            publish(g, root, str(900000 + number))
        except ManagedDownloadError as error:
            assert error.reason_code == 'storage_capacity'
            break
    assert g.capacity()['headroom'] == 0 < before
    with pytest.raises(ManagedDownloadError, match='journal full') as caught:
        g.assert_room('999999')                                   # said before the download, not after
    assert caught.value.reason_code == 'storage_capacity'
    g.assert_room('100001')                                       # an update of a saved work is not blocked here


# ---- item 6: an emptied pointer with sealed works, and the pointer a cancelled first save leaves --------------

def test_r2_6_a_cancelled_first_save_leaves_a_root_that_still_opens(root):
    g = mg.ManagedGenerations(root)
    g.prepare_for_publication()
    assert json.loads((root / 'active-generations.json').read_bytes())['version'] == 5
    make_generation(root, '7001')                                 # sealed, never published: a harmless orphan
    assert mg.ManagedGenerations(root).entry_count() == 0
    with pytest.raises(ManagedDownloadError):
        old_reader().ManagedGenerations(root)                     # and an older build is refused


def test_r2_6_an_older_index_with_entries_still_tolerates_unindexed_orphans(root):
    seed_index(root, 2)
    make_generation(root, '7001')
    assert mg.ManagedGenerations(root).entry_count() == 2


# ---- (d) a damaged work is named ---------------------------------------------------------------------------

def test_r2_d_the_failure_names_the_damaged_work(tmp_path, monkeypatch):
    from pixivvault_nicegui import managed_downloads as downloads
    writer = writer_with_works(tmp_path, 2)
    try:
        monkeypatch.setattr(downloads, '_RECEIPT_RECHECK_SECONDS', 0.0)
        first = writer.catalog_snapshot()
        relative, receipt, _ = first.receipts[1]
        page = (writer.root / relative).parent / receipt['files'][0]['name']
        page.write_bytes(page.read_bytes() + b'\0')
        with pytest.raises(ManagedDownloadError) as caught:
            writer.catalog_snapshot()
        assert caught.value.work_id == receipt['work_id'] and caught.value.relative_receipt == relative
    finally:
        writer.close()


# ---- checker round 3 -------------------------------------------------------------------------------

def old_code_first_save_stopped_after_the_rename(root):
    """What the pre-journal code left when the first save stopped: prepare, rename, no publish."""
    (root / 'active-generations.json').write_bytes(b'{"entries":{},"version":2}')
    return make_generation(root, '555')


def test_r3_r1_an_orphan_left_by_the_older_first_save_is_not_a_reason_to_refuse_the_root(root):
    old_code_first_save_stopped_after_the_rename(root)
    g = mg.ManagedGenerations(root)                      # used to be refused outright
    assert g.entry_count() == 0 and g.current('555') is None
    publish(g, root, '556')                              # and the work can be saved (and the orphan stays inert)
    again = mg.ManagedGenerations(root)
    assert sorted(again.entries()) == ['556'] and json.loads((root / 'active-generations.json').read_bytes())['version'] == 5


def test_r3_r1_an_orphan_next_to_an_update_that_the_older_code_could_not_publish(root):
    entries = seed_index(root, 2)
    g = mg.ManagedGenerations(root)
    relative, digest = make_generation(root, '100000', suffix='9' * 32)    # the update's new folder, never published
    assert g.current('100000')['relative_receipt'] == entries['100000']['relative_receipt']
    assert mg.ManagedGenerations(root).entry_count() == 2


def test_r3_r1_evidence_of_a_lost_journal_still_refuses(root):
    old_code_first_save_stopped_after_the_rename(root)
    (root / 'active-generations.journal.deleted').write_bytes(b'x')        # a trace of the journal of the old builds
    with pytest.raises(ManagedDownloadError, match='index rejected'):
        mg.ManagedGenerations(root)


def test_r3_d4_a_mark_that_is_already_current_on_disk_is_not_written_again(root, monkeypatch):
    seed_index(root, 3)
    first = mg.ManagedGenerations(root)
    second = mg.ManagedGenerations(root)
    publish(first, root, '900001')
    assert second.current('900001') is not None                           # second's full read (after the upgrade)
    publish(first, root, '900002')
    assert second.current('900002') is not None                           # now it takes in just the tail
    assert second._loaded.mark_records == second._loaded.journal_records
    calls = []
    real = second._write_mark
    monkeypatch.setattr(second, '_write_mark', lambda *a, **k: calls.append(a) or real(*a, **k))
    publish(second, root, '900003')
    assert len(calls) == 1                                                # only the one after its own append
    assert journal_records(root) == mark_records(root) == 3


def test_r3_d4_a_save_does_not_fail_for_a_mark_it_never_needed_to_write(root, monkeypatch):
    seed_index(root, 3)
    first = mg.ManagedGenerations(root)
    second = mg.ManagedGenerations(root)
    publish(first, root, '900001')
    assert second.current('900001') is not None
    publish(first, root, '900002')
    assert second.current('900002') is not None                           # the tail path
    broken = {'on': True}
    real = second._write_mark
    monkeypatch.setattr(second, '_write_mark',
                        lambda *a, **k: (_ for _ in ()).throw(OSError('held')) if broken['on'] else real(*a, **k))
    publish(second, root, '900003')                                       # nothing to catch up: it succeeds
    assert second.current('900003') is not None


def test_r3_obs_a_retired_journal_is_kept_from_the_fold_not_from_its_last_append(root):
    seed_index(root, 3)
    g = mg.ManagedGenerations(root)
    publish(g, root, '900001')
    old_journal, old_mark = journal_of(root), mark_of(root)
    long_ago = time.time() - 3600
    os.utime(old_journal, (long_ago, long_ago))
    os.utime(old_mark, (long_ago, long_ago))                              # idle for an hour before the fold
    g._compact(g._loaded)
    g._compact(g._loaded)                                                 # a second fold right after
    assert old_journal.exists() and old_mark.exists()                     # not swept at once


def test_r3_obs_headroom_matches_what_assert_room_and_publish_allow(root, monkeypatch):
    seed_index(root, 3)
    g = mg.ManagedGenerations(root)
    publish(g, root, '900001')
    monkeypatch.setattr(mg, '_COMPACT_RECORDS', 10 ** 9)
    monkeypatch.setattr(mg, '_COMPACT_BYTES', 10 ** 12)
    monkeypatch.setattr(mg, '_MAX_JOURNAL_BYTES', journal_of(root).stat().st_size + mg._JOURNAL_SLACK + 2000)
    monkeypatch.setattr(g, '_compact', lambda loaded, patience=5.0: (_ for _ in ()).throw(OSError('disk')))
    g._loaded.fold_failed = True
    promised = g.capacity()['headroom']
    saved = 0
    for number in range(2, 40):
        try:
            g.assert_room(str(900000 + number))
            publish(g, root, str(900000 + number))
        except ManagedDownloadError:
            break
        saved += 1
    assert saved == promised and g.capacity()['headroom'] == 0


@pytest.mark.skipif(os.name != 'nt', reason='share modes are a Windows matter')
def test_r3_obs_an_append_waits_out_a_reader_that_holds_the_journal_without_sharing_write(root):
    import ctypes
    from ctypes import wintypes
    seed_index(root, 3)
    g = mg.ManagedGenerations(root)
    publish(g, root, '900001')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                   wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    kernel.CreateFileW.restype = wintypes.HANDLE
    handle = kernel.CreateFileW(str(journal_of(root)), 0x80000000, 0x1, None, 3, 0x80, None)   # share READ only
    assert handle not in (None, wintypes.HANDLE(-1).value)
    threading.Timer(0.25, lambda: kernel.CloseHandle(handle)).start()
    started = time.monotonic()
    publish(g, root, '900002')
    assert 0.15 < time.monotonic() - started < 2.0
    assert mg.ManagedGenerations(root).entry_count() == 5
