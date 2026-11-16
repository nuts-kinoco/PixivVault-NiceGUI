"""Generated fixtures only: the original ledger/source images stay unchanged."""
from io import BytesIO
import hashlib
import json
from pathlib import Path
import sqlite3
from zipfile import ZipFile

from PIL import Image
import pytest

from pixivvault_nicegui.legacy_ledger_rebuild import (
    LedgerRebuildError, rebuild_undecidable_ledger,
    publish_rebuilt_ledger, load_rebuilt_ledger,
)
from pixivvault_nicegui import legacy_ledger_rebuild as rebuild_module
from pixivvault_nicegui.managed_downloads import IDENTITY, MARKER


def fixture(tmp_path, *, rows=None, manual=False, ledger=True):
    images = tmp_path / 'images'
    images.mkdir()
    managed = tmp_path / 'managed'
    managed.mkdir()
    (managed / MARKER).write_text(json.dumps(IDENTITY), encoding='utf-8')
    db = tmp_path / 'immutable-copy.db'
    with sqlite3.connect(db) as conn:
        conn.executescript('''
            CREATE TABLE works (work_id TEXT PRIMARY KEY, user_id TEXT, title TEXT,
                                page_count INTEGER, content_type TEXT, is_deleted INTEGER);
            CREATE TABLE following_users (user_id TEXT PRIMARY KEY, name TEXT);
            INSERT INTO following_users VALUES ('9', '合成作者');
        ''')
        conn.executemany('INSERT INTO works VALUES (?,?,?,?,?,?)', rows or [
            ('1', '9', '題名', 1, 'illust', 0),
            ('2', '9', '題名', 3, 'illust', 0),
        ])
        if ledger:
            conn.executescript('''
                CREATE TABLE file_ledger (
                    work_id TEXT, page INTEGER, status TEXT, store_path TEXT,
                    entry_name TEXT, reason TEXT, ambiguous_group TEXT,
                    resolved_by TEXT, updated_at TEXT, PRIMARY KEY(work_id,page));
            ''')
            conn.execute('INSERT INTO file_ledger VALUES (?,?,?,?,?,?,?,?,?)',
                         ('1', 1, 'present' if manual else 'unsure', 'old-place',
                          'old.png', 'old-reason', '9:題名' if manual else '9:stale',
                          'manual' if manual else None, 'old-time'))
    return db, images, managed


def png():
    stream = BytesIO()
    Image.new('RGB', (2, 2), 'green').save(stream, format='PNG')
    return stream.getvalue()


def store(images, names=('題名.png',), *, zipped=False, author='9', folder='合成作者'):
    path = images / (f'{folder}({author})' + ('.zip' if zipped else ''))
    if zipped:
        with ZipFile(path, 'x') as archive:
            for name in names:
                archive.writestr(name, png())
    else:
        path.mkdir()
        for name in names:
            target = path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(png())
    return path


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def run(paths, **kwargs):
    db, images, managed = paths
    return rebuild_undecidable_ledger(undecidable_db=db, images_root=images,
                                      managed_root=managed, **kwargs)


@pytest.mark.parametrize('zipped', [False, True])
def test_rebuild_adds_current_candidate_and_omits_stale_rows_without_source_writes(tmp_path, zipped):
    paths = fixture(tmp_path)
    source = store(paths[1], zipped=zipped)
    before_db = paths[0].read_bytes()
    before_images = hashes(paths[1])
    result = run(paths)
    assert len(result.groups) == 1
    group = result.groups[0]
    assert group['ambiguous_group'] == '9:題名'
    assert '同じ作者に同じ題名' in group['reason']
    assert [c['work_id'] for c in group['candidates']] == ['1', '2']
    assert all(c['author_name'] == '合成作者' for c in group['candidates'])
    assert group['candidates'][0]['pages'] == [
        {'page': 1, 'store_path': str(source), 'entry_name': '題名.png'}]
    assert result.scanned_groups == 2 and result.skipped_multi == 0
    assert result.unreadable == ()
    assert result.generation_path.is_relative_to(paths[2])
    saved = json.loads(result.generation_path.read_text(encoding='utf-8'))
    assert saved['groups'][0]['ambiguous_group'] == '9:題名'
    assert paths[0].read_bytes() == before_db and hashes(paths[1]) == before_images
    assert not any(Path(str(paths[0]) + suffix).exists()
                   for suffix in ('-wal', '-shm', '-journal'))


def test_manual_resolution_preserved_and_entire_group_excluded(tmp_path):
    paths = fixture(tmp_path, manual=True)
    store(paths[1])
    before = paths[0].read_bytes()
    result = run(paths)
    assert result.groups == ()
    saved = json.loads(result.generation_path.read_text(encoding='utf-8'))
    assert saved['manual_rows'] == [{
        'work_id': '1', 'page': 1, 'status': 'present', 'store_path': 'old-place',
        'entry_name': 'old.png', 'reason': 'old-reason', 'ambiguous_group': '9:題名',
        'resolved_by': 'manual', 'updated_at': 'old-time'}]
    assert paths[0].read_bytes() == before


def test_next_scan_adds_and_resolves_candidates_but_retains_old_generation(tmp_path):
    paths = fixture(tmp_path)
    first = run(paths)
    assert first.groups == ()
    directory = store(paths[1])
    second = run(paths)
    assert len(second.groups) == 1
    second_bytes = second.generation_path.read_bytes()
    (directory / '題名(1).png').write_bytes(png())
    for page in range(1, 4):
        (directory / f'題名(2)_{page}.png').write_bytes(png())
    third = run(paths)
    assert third.groups == ()
    assert len({r.generation_path for r in (first, second, third)}) == 3
    assert second.generation_path.read_bytes() == second_bytes
    assert len(second.groups) == 1


@pytest.mark.parametrize(('counts', 'names', 'expected', 'skipped'), [
    ((1, 3), (), 0, 0),
    ((1, 3), ('題名_1.png', '題名_2.png', '題名_3.png'), 1, 0),
    ((1, 3), ('題名_1.png', '題名_3.png'), 0, 0),
    ((1, 1), ('題名.png',), 0, 1),
    ((1, 3), ('題名.gif',), 0, 0),
])
def test_original_exact_page_set_and_multi_candidate_rules(tmp_path, counts, names, expected, skipped):
    paths = fixture(tmp_path, rows=[
        (str(i), '9', '題名', count, 'illust', 0)
        for i, count in enumerate(counts, 1)])
    store(paths[1], names)
    result = run(paths)
    assert len(result.groups) == expected
    assert result.skipped_multi == skipped


@pytest.mark.parametrize(('second_type', 'deleted'), [('novel', 0), ('ugoira', 0), ('illust', 1)])
def test_non_illust_and_deleted_work_cannot_make_duplicate_title(tmp_path, second_type, deleted):
    paths = fixture(tmp_path, rows=[('1', '9', '題名', 1, 'illust', 0),
                                  ('2', '9', '題名', 3, second_type, deleted)])
    store(paths[1])
    assert run(paths).groups == ()


def test_nested_author_store_and_sanitized_title_collision_match_original(tmp_path):
    paths = fixture(tmp_path, rows=[('1', '9', 'A/B', 1, 'illust', 0),
                                  ('2', '9', 'A:B', 3, 'illust', 0)])
    wrapper = paths[1] / '☆ブックマーク(99)'
    wrapper.mkdir()
    store(wrapper, ('A_B.png',))
    assert run(paths).groups[0]['ambiguous_group'] == '9:A_B'


def test_broken_zip_skips_whole_author_with_typed_diagnostic(tmp_path):
    paths = fixture(tmp_path)
    store(paths[1])
    (paths[1] / 'broken(9).zip').write_bytes(b'not a zip')
    result = run(paths)
    assert result.groups == ()
    assert result.unreadable == ('9:BadZipFile',)


def test_missing_ledger_can_build_first_generation(tmp_path):
    paths = fixture(tmp_path, ledger=False)
    store(paths[1])
    assert len(run(paths).groups) == 1


def test_interrupted_scan_preserves_previous_published_result(tmp_path):
    paths = fixture(tmp_path)
    store(paths[1])
    previous = run(paths)
    before = hashes(paths[2])
    calls = 0
    def cancelled():
        nonlocal calls
        calls += 1
        if calls == 4:
            raise RuntimeError('synthetic cancellation')
    with pytest.raises(RuntimeError, match='synthetic cancellation'):
        run(paths, check_cancelled=cancelled)
    assert calls == 4 and hashes(paths[2]) == before
    assert len(previous.groups) == 1 and previous.generation_path.is_file()


def test_reject_source_sidecar_and_overlapping_or_unowned_output(tmp_path):
    paths = fixture(tmp_path)
    store(paths[1])
    before = hashes(paths[1])
    with pytest.raises(LedgerRebuildError):
        rebuild_undecidable_ledger(undecidable_db=paths[0], images_root=paths[1],
                                    managed_root=paths[1])
    unowned = tmp_path / 'unowned'
    unowned.mkdir()
    with pytest.raises(LedgerRebuildError):
        rebuild_undecidable_ledger(undecidable_db=paths[0], images_root=paths[1],
                                    managed_root=unowned)
    assert list(unowned.iterdir()) == []
    Path(str(paths[0]) + '-wal').write_bytes(b'synthetic sidecar')
    with pytest.raises(LedgerRebuildError):
        run(paths)
    assert hashes(paths[1]) == before
    assert list(paths[2].iterdir()) == [paths[2] / MARKER]


def test_output_write_failure_retains_previous_generation_and_source(tmp_path, monkeypatch):
    paths = fixture(tmp_path)
    store(paths[1])
    previous = run(paths)
    before = previous.generation_path.read_bytes()
    db_before = paths[0].read_bytes()
    images_before = hashes(paths[1])
    def fail_write(path, data):
        raise OSError('synthetic output failure')
    monkeypatch.setattr(rebuild_module, '_write', fail_write)
    with pytest.raises(LedgerRebuildError):
        run(paths)
    assert previous.generation_path.read_bytes() == before
    assert paths[0].read_bytes() == db_before and hashes(paths[1]) == images_before


def test_interruption_after_new_file_write_leaves_old_result_unchanged(tmp_path, monkeypatch):
    paths = fixture(tmp_path)
    store(paths[1])
    previous = run(paths)
    before = previous.generation_path.read_bytes()
    interrupted = False
    write = rebuild_module._write
    def record_write(path, data):
        nonlocal interrupted
        write(path, data)
        interrupted = True
    def checkpoint():
        if interrupted:
            raise RuntimeError('synthetic late cancellation')
    monkeypatch.setattr(rebuild_module, '_write', record_write)
    with pytest.raises(RuntimeError, match='synthetic late cancellation'):
        run(paths, check_cancelled=checkpoint)
    assert previous.generation_path.read_bytes() == before
    assert len(previous.groups) == 1
    assert len(list((paths[2] / 'legacy-ledger').glob('*/ledger.json'))) == 2
    assert not (paths[2] / 'legacy-ledger' / 'CURRENT').exists()


def test_source_inventory_change_aborts_before_output_publication(tmp_path, monkeypatch):
    paths = fixture(tmp_path)
    directory = store(paths[1])
    previous = run(paths)
    before = hashes(paths[2])
    calculate = rebuild_module._author_candidates
    def change_inventory(*args):
        result = calculate(*args)
        (directory / 'new-file.png').write_bytes(png())
        return result
    monkeypatch.setattr(rebuild_module, '_author_candidates', change_inventory)
    with pytest.raises(LedgerRebuildError):
        run(paths)
    assert hashes(paths[2]) == before and previous.generation_path.is_file()


def test_db_identity_change_aborts_before_output_publication(tmp_path, monkeypatch):
    paths = fixture(tmp_path)
    store(paths[1])
    signature = rebuild_module.immutable_copy_signature
    calls = 0
    def changed_signature(path):
        nonlocal calls
        calls += 1
        value = signature(path)
        return value if calls == 1 else (*value[:3], b'changed')
    monkeypatch.setattr(rebuild_module, 'immutable_copy_signature', changed_signature)
    with pytest.raises(LedgerRebuildError):
        run(paths)
    assert calls == 2 and list(paths[2].iterdir()) == [paths[2] / MARKER]


def test_canonical_path_spelling_cannot_hide_output_image_overlap(tmp_path):
    paths = fixture(tmp_path)
    (paths[1] / MARKER).write_text(json.dumps(IDENTITY), encoding='utf-8')
    anchor = tmp_path / 'anchor'
    anchor.mkdir()
    before = hashes(paths[1])
    with pytest.raises(LedgerRebuildError):
        rebuild_undecidable_ledger(undecidable_db=paths[0], images_root=paths[1],
                                    managed_root=anchor / '..' / 'images')
    assert hashes(paths[1]) == before


def publish(paths, result):
    return publish_rebuilt_ledger(result, undecidable_db=paths[0], images_root=paths[1],
                                  managed_root=paths[2])


def load(paths):
    return load_rebuilt_ledger(undecidable_db=paths[0], images_root=paths[1],
                               managed_root=paths[2])


def test_published_ledger_survives_new_read_and_unpublished_scan_stays_invisible(tmp_path):
    paths = fixture(tmp_path)
    assert load(paths) is None
    first = run(paths)
    assert load(paths) is None
    publish(paths, first)
    assert load(paths) == ()
    store(paths[1])
    second = run(paths)
    assert len(second.groups) == 1 and load(paths) == ()
    before_db = paths[0].read_bytes()
    before_images = hashes(paths[1])
    publish(paths, second)
    assert load(paths) == second.groups
    assert first.generation_path.is_file() and second.generation_path.is_file()
    assert paths[0].read_bytes() == before_db and hashes(paths[1]) == before_images


@pytest.mark.parametrize('after_replace', [False, True])
def test_publish_failure_is_old_or_verified_new_result(tmp_path, monkeypatch, after_replace):
    paths = fixture(tmp_path)
    first = run(paths)
    publish(paths, first)
    store(paths[1])
    second = run(paths)
    replace = rebuild_module.os.replace
    def fail_replace(source, target):
        if after_replace:
            replace(source, target)
        raise OSError('synthetic publication interruption')
    monkeypatch.setattr(rebuild_module.os, 'replace', fail_replace)
    if after_replace:
        assert publish(paths, second) is None
        assert load(paths) == second.groups
    else:
        with pytest.raises(LedgerRebuildError):
            publish(paths, second)
        assert load(paths) == first.groups
    assert first.generation_path.is_file() and second.generation_path.is_file()


def test_generation_tamper_before_or_after_publish_is_rejected(tmp_path):
    paths = fixture(tmp_path)
    store(paths[1])
    first = run(paths)
    publish(paths, first)
    second = run(paths)
    second.generation_path.write_bytes(b'{}')
    with pytest.raises(LedgerRebuildError):
        publish(paths, second)
    assert load(paths) == first.groups
    first.generation_path.write_bytes(b'{}')
    with pytest.raises(LedgerRebuildError):
        load(paths)


def test_different_source_or_pointer_traversal_never_falls_back(tmp_path):
    paths = fixture(tmp_path)
    store(paths[1])
    first = run(paths)
    publish(paths, first)
    other_db = tmp_path / 'other-copy.db'
    other_db.write_bytes(paths[0].read_bytes())
    with pytest.raises(LedgerRebuildError):
        load((other_db, paths[1], paths[2]))
    other_images = tmp_path / 'other-images'
    other_images.mkdir()
    with pytest.raises(LedgerRebuildError):
        load((paths[0], other_images, paths[2]))
    current = paths[2] / 'legacy-ledger' / 'CURRENT.json'
    pointer = json.loads(current.read_text())
    pointer['generation'] = '../outside'
    current.write_text(json.dumps(pointer), encoding='utf-8')
    with pytest.raises(LedgerRebuildError):
        load(paths)
    with pytest.raises(LedgerRebuildError):
        publish(paths, first)


def test_load_missing_root_is_none_but_existing_unowned_root_is_rejected(tmp_path):
    paths = fixture(tmp_path)
    missing = tmp_path / 'not-created'
    assert load((paths[0], paths[1], missing)) is None
    assert not missing.exists()
    missing.mkdir()
    with pytest.raises(LedgerRebuildError):
        load((paths[0], paths[1], missing))
