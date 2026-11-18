"""Saved-works ceiling: the generation index scales past 10,000 works.

Before: 10,000 entries AND a 2 MiB index file (about 6,000 works with fingerprints), and every
call re-verified every receipt (13 s per call at 10,000). Now: verified once, appended to a
hash-chained journal, folded into the index in bulk, ceiling 1,000,000.
Only synthetic receipts and images.
"""
import hashlib
import io
import json
import time

import pytest
from PIL import Image

from pixivvault_nicegui import managed_generations as mg
from pixivvault_nicegui.managed_downloads import IDENTITY, MARKER, ManagedDownloadError

PNG = None


def png():
    global PNG
    if PNG is None:
        buffer = io.BytesIO()
        Image.new('RGB', (2, 2), (1, 2, 3)).save(buffer, 'PNG')
        PNG = buffer.getvalue()
    return PNG


def receipt_bytes(wid, author='1000', with_page=True):
    data = png()
    return json.dumps({
        'version': 1, 'source': 'pixiv-original', 'work_id': wid, 'author_id': author, 'author_name': 'a',
        'title': 't', 'tags': ['a'], 'created_at': '2026-01-01T00:00:00+00:00',
        'saved_at': '2026-01-02T00:00:00+00:00', 'illustration_type': 0, 'x_restrict': 0, 'page_count': 1,
        'files': [{'name': 'page-1.png', 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                   'format': 'png', 'width': 2, 'height': 2}]}, sort_keys=True).encode()


def make_root(tmp_path):
    root = tmp_path / 'managed'
    root.mkdir()
    (root / MARKER).write_text(json.dumps(IDENTITY))
    return root


def make_generation(root, wid, suffix=None):
    name = f'work-{wid}-' + (suffix or ('%032x' % int(wid)))
    folder = root / 'generations' / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'page-1.png').write_bytes(png())
    raw = receipt_bytes(wid)
    (folder / 'receipt.json').write_bytes(raw)
    return f'generations/{name}/receipt.json', hashlib.sha256(raw).hexdigest()


def publish(g, root, wid, previous=None, fingerprint='c' * 64):
    relative, digest = make_generation(root, wid)
    return g.publish(wid, relative, digest, '2026-01-01T00:00:00+00:00', *(previous or (None, None)),
                     fingerprint)


def seed_index(root, count, version=2, start=100000):
    """An index of `count` receipts written the way the old code wrote it (receipt-only)."""
    entries = {}
    for number in range(count):
        wid = str(start + number)
        relative, digest = make_generation(root, wid)
        entries[wid] = {'work_id': wid, 'relative_receipt': relative, 'sha256': digest,
                        'update_date': '2026-01-01T00:00:00+00:00'}
        if version >= 2:
            entries[wid]['accepted_fingerprint'] = 'b' * 64
    (root / 'active-generations.json').write_bytes(
        json.dumps({'version': version, 'entries': entries}, sort_keys=True, separators=(',', ':')).encode())
    return entries


@pytest.fixture
def root(tmp_path):
    return make_root(tmp_path)


def test_ceiling_is_far_above_the_old_10000_and_the_byte_limit_no_longer_binds_first():
    assert mg._MAX_ENTRIES >= 1_000_000
    assert mg._MAX_INDEX_BYTES >= mg._MAX_ENTRIES * 400  # the index file always fits the entry ceiling


def test_an_index_larger_than_the_old_2mib_limit_loads_and_keeps_publishing(root):
    old_limit = 2 * 1024 * 1024
    entries = seed_index(root, 7000)
    assert (root / 'active-generations.json').stat().st_size > old_limit
    g = mg.ManagedGenerations(root)
    assert g.entry_count() == 7000
    publish(g, root, '900001')
    assert g.entry_count() == 7001 and g.current('900001')['work_id'] == '900001'
    assert g.current('100003') == entries['100003']


def test_calls_do_not_reverify_every_receipt(root):
    seed_index(root, 3000)
    g = mg.ManagedGenerations(root)
    started = time.perf_counter()
    for _ in range(200):
        g.current('100010')
        g.state_snapshot()
    assert time.perf_counter() - started < 3.0  # was seconds per call before


def test_publish_appends_to_a_journal_and_leaves_the_index_untouched(root):
    seed_index(root, 50)
    base = (root / 'active-generations.json').read_bytes()
    g = mg.ManagedGenerations(root)
    for number in range(5):
        publish(g, root, str(900000 + number))
    assert (root / 'active-generations.json').read_bytes() == base
    assert (root / mg._JOURNAL).stat().st_size > 0
    reopened = mg.ManagedGenerations(root)
    assert reopened.entries() == g.entries() and len(g.entries()) == 55
    assert reopened.state_snapshot()['revision'] == g.state_snapshot()['revision']


def test_revision_changes_with_each_publish_and_is_order_free(root, tmp_path):
    seed_index(root, 3)
    g = mg.ManagedGenerations(root)
    first = g.state_snapshot()['revision']
    publish(g, root, '900001')
    second = g.state_snapshot()['revision']
    assert first != second
    (tmp_path / 'x').mkdir()
    other = make_root(tmp_path / 'x')
    seed_index(other, 3)
    h = mg.ManagedGenerations(other)
    publish(h, other, '900001')
    assert h.state_snapshot()['revision'] == second  # same set of entries, same revision


def test_a_snapshot_never_moves_under_its_holder(root):
    seed_index(root, 3)
    g = mg.ManagedGenerations(root)
    snapshot = g.state_snapshot()
    publish(g, root, '900001')
    assert '900001' not in snapshot['entries'] and len(snapshot['entries']) == 3
    assert '900001' in g.state_snapshot()['entries']
    value = snapshot['entries'].get('100001')
    value['sha256'] = 'x'
    assert snapshot['entries'].get('100001')['sha256'] != 'x'  # reads are copies


def test_work_state_token_formula_is_unchanged(root):
    entries = seed_index(root, 2)
    g = mg.ManagedGenerations(root)
    expected = hashlib.sha256(json.dumps({'entry': entries['100001'], 'exclusions': {}}, sort_keys=True,
                                         ensure_ascii=True, separators=(',', ':')).encode('ascii')).hexdigest()
    assert g.work_state_token('100001') == expected


def test_a_torn_final_append_is_ignored_and_repaired_by_the_next_publish(root):
    seed_index(root, 5)
    g = mg.ManagedGenerations(root)
    publish(g, root, '900001')
    journal = root / mg._JOURNAL
    committed = journal.read_bytes()
    journal.write_bytes(committed + b'{"chain":"deadbeef","entry":{"work_i')  # crash mid-append
    reopened = mg.ManagedGenerations(root)
    assert reopened.entry_count() == 6 and reopened.current('900001') is not None
    publish(reopened, root, '900002')
    again = mg.ManagedGenerations(root)
    assert again.entry_count() == 7 and journal.read_bytes().startswith(committed)
    assert b'deadbeef' not in journal.read_bytes()


@pytest.mark.parametrize('damage', ['edit_entry', 'swap_records', 'bad_json'])
def test_a_damaged_journal_is_rejected_never_skipped(root, damage):
    seed_index(root, 3)
    g = mg.ManagedGenerations(root)
    publish(g, root, '900001')
    publish(g, root, '900002')
    journal = root / mg._JOURNAL
    lines = journal.read_bytes().split(b'\n')
    if damage == 'edit_entry':
        lines[1] = lines[1].replace(b'900001', b'900009')
    elif damage == 'swap_records':
        lines[1], lines[2] = lines[2], lines[1]
    else:
        lines[1] = lines[1][:-5] + b'#####'
    journal.write_bytes(b'\n'.join(lines))
    with pytest.raises(ManagedDownloadError, match='index rejected'):
        mg.ManagedGenerations(root)


def test_compaction_folds_the_journal_into_the_index(root, monkeypatch):
    monkeypatch.setattr(mg, '_COMPACT_RECORDS', 3)
    seed_index(root, 4)
    g = mg.ManagedGenerations(root)
    before = (root / 'active-generations.json').read_bytes()
    for number in range(3):
        publish(g, root, str(900000 + number))
    assert not (root / mg._JOURNAL).exists()
    assert (root / 'active-generations.json').read_bytes() != before
    assert json.loads((root / 'active-generations.json').read_bytes())['version'] == 2
    publish(g, root, '900010')  # a new journal starts on the new base
    reopened = mg.ManagedGenerations(root)
    assert reopened.entry_count() == 8 and reopened.entries() == g.entries()


def test_a_crash_between_compaction_and_journal_removal_is_safe(root, monkeypatch):
    seed_index(root, 3)
    g = mg.ManagedGenerations(root)
    publish(g, root, '900001')
    publish(g, root, '900002')
    stale = (root / mg._JOURNAL).read_bytes()
    g._compact(g._loaded)  # the new index now contains both
    (root / mg._JOURNAL).write_bytes(stale)  # the unlink never happened
    reopened = mg.ManagedGenerations(root)
    assert reopened.entry_count() == 5
    publish(reopened, root, '900003')  # replaces the stale journal with a fresh one
    assert mg.ManagedGenerations(root).entry_count() == 6


def test_a_journal_with_entries_missing_from_its_base_is_rejected(root):
    seed_index(root, 3)
    g = mg.ManagedGenerations(root)
    publish(g, root, '900001')
    journal = (root / mg._JOURNAL).read_bytes()
    (root / 'active-generations.json').write_bytes((root / 'active-generations.json').read_bytes() + b'\n')
    (root / mg._JOURNAL).write_bytes(journal)
    with pytest.raises(ManagedDownloadError, match='index rejected'):
        mg.ManagedGenerations(root)


def test_a_second_instance_picks_up_appended_records_without_a_full_reload(root, monkeypatch):
    seed_index(root, 20)
    first = mg.ManagedGenerations(root)
    second = mg.ManagedGenerations(root)
    publish(first, root, '900001')
    loads = []
    original = second._load_full
    monkeypatch.setattr(second, '_load_full', lambda: loads.append(1) or original())
    assert second.current('900001')['work_id'] == '900001'
    publish(first, root, '900002')
    assert second.current('900002') is not None
    assert loads == [1]  # one full load for the first sight of the journal, then only the tail


def test_entry_ceiling_refuses_with_a_storage_capacity_code_and_changes_nothing(root, monkeypatch):
    monkeypatch.setattr(mg, '_MAX_ENTRIES', 6)
    seed_index(root, 5)
    g = mg.ManagedGenerations(root)
    assert g.capacity()['headroom'] == 1
    publish(g, root, '900001')
    assert g.capacity()['headroom'] == 0
    with pytest.raises(ManagedDownloadError, match='limit exceeded') as caught:
        publish(g, root, '900002')
    assert caught.value.reason_code == 'storage_capacity'
    assert g.entry_count() == 6 and mg.ManagedGenerations(root).entry_count() == 6


def test_index_byte_ceiling_is_reported_by_capacity_and_refused_with_the_same_code(root, monkeypatch):
    seed_index(root, 5)
    used = (root / 'active-generations.json').stat().st_size
    monkeypatch.setattr(mg, '_MAX_INDEX_BYTES', used + 6 * mg._ENTRY_BYTES_ESTIMATE)
    g = mg.ManagedGenerations(root)
    assert g.capacity()['headroom'] == 6  # bytes bind before the entry count
    saved = 0
    with pytest.raises(ManagedDownloadError, match='size exceeded') as caught:
        for number in range(20):
            publish(g, root, str(900000 + number))
            saved += 1
    assert 2 <= saved <= 6 and caught.value.reason_code == 'storage_capacity'
    assert mg.ManagedGenerations(root).entry_count() == 5 + saved


def test_a_v1_index_is_upgraded_in_place_by_the_first_publish(root):
    seed_index(root, 3, version=1)
    g = mg.ManagedGenerations(root)
    assert g.entry_count() == 3
    publish(g, root, '900001')
    packet = json.loads((root / 'active-generations.json').read_bytes())
    assert packet['version'] == 2 and len(packet['entries']) == 4 and not (root / mg._JOURNAL).exists()
    assert mg.ManagedGenerations(root).entry_count() == 4


def test_replacing_a_work_updates_in_place_and_counts_once(root):
    entries = seed_index(root, 3)
    g = mg.ManagedGenerations(root)
    old = g.current('100001')
    relative, digest = make_generation(root, '100001', suffix='f' * 32)
    g.publish('100001', relative, digest, '2026-02-02T00:00:00+00:00', old['sha256'], old['relative_receipt'], 'd' * 64)
    assert g.entry_count() == 3 and g.current('100001')['relative_receipt'] == relative
    assert mg.ManagedGenerations(root).current('100001')['update_date'] == '2026-02-02T00:00:00+00:00'
    assert entries['100001']['relative_receipt'] != relative


def test_missing_index_with_generations_still_refuses_to_start(root):
    seed_index(root, 2)
    (root / 'active-generations.json').unlink()
    with pytest.raises(ManagedDownloadError, match='index missing'):
        mg.ManagedGenerations(root)


def test_tampering_with_an_unrelated_receipt_is_still_caught_at_open(root):
    seed_index(root, 3)
    receipt = next((root / 'generations').iterdir()) / 'receipt.json'
    receipt.write_bytes(receipt.read_bytes() + b' ')
    with pytest.raises(ManagedDownloadError, match='index rejected'):
        mg.ManagedGenerations(root)


# ---- the engine never turns a full store into an ordinary per-work failure ---------------

from test_t_dl_capacity_20261117 import CappedPort, run_engine, works  # noqa: E402
from pixivvault_nicegui.batch_runs import BatchRunLedger  # noqa: E402
from pixivvault_nicegui.managed_novel_jobs import ManagedNovelError  # noqa: E402


@pytest.fixture
def ledger(tmp_path):
    value = BatchRunLedger(tmp_path / 'runs', synchronous='NORMAL')
    yield value
    value.close()


class FullStorePort(CappedPort):
    def __init__(self, *, at, mode):
        super().__init__()
        self.at, self.mode = at, mode

    def enqueue(self, candidate, operation_id):
        if self.n + 1 >= self.at and self.mode == 'enqueue':
            raise ManagedNovelError(reason_code='storage_capacity')
        return super().enqueue(candidate, operation_id)

    def job_states(self, refs):
        if self.mode == 'job' and self.n >= self.at:
            return {ref.job_id: ('error', 'storage_capacity') for ref in refs}
        return super().job_states(refs)


@pytest.mark.parametrize('mode', ['enqueue', 'job'])
def test_a_full_store_fails_the_run_loudly_and_never_stamps_the_author(ledger, mode):
    port = FullStorePort(at=5, mode=mode)
    port.inventory = {'41': works('41', 30), '42': works('42', 3)}
    run_id = run_engine(port, ledger, ('41', '42'))
    assert ledger.run(run_id).state == 'failed'
    assert ledger.run(run_id).error_code in ('storage_capacity', 'saved_works_limit')
    assert [a.state for a in ledger.authors(run_id)] == ['failed', 'pending']


def test_services_port_headroom_is_the_smaller_of_count_and_index_bytes():
    from types import SimpleNamespace as NS
    from pixivvault_nicegui.batch_engine import ServicesPort
    port = ServicesPort(NS(managed_downloads=NS(_generations=NS(capacity=lambda: {'headroom': 7}))))
    assert port.saved_headroom() == 7


def test_novel_ledger_refusal_carries_the_capacity_code():
    from pixivvault_nicegui.job_failure import job_failure_code
    assert job_failure_code(ManagedNovelError(reason_code='storage_capacity')) == 'storage_capacity'
