from hashlib import sha256
from types import SimpleNamespace
import sqlite3
import pytest
from test_u11_zip_contract import generated_exports, settled, verify_archive
from pixivvault_nicegui.library_repository import ReadonlyLibraryRepository


def generated_repository(tmp_path, flag, *, column=True, author=True):
    path = tmp_path / 'frozen-legacy.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE works(work_id TEXT,title TEXT,page_count INTEGER,user_id TEXT,tags TEXT,is_deleted INTEGER)')
        db.execute('CREATE TABLE following_users(user_id TEXT,name TEXT' + (',is_zipped INTEGER' if column else '') + ')')
        if author:
            db.execute('INSERT INTO following_users VALUES (' + ('?,?,?' if column else '?,?') + ')',
                       ('456', 'Generated', int(flag)) if column else ('456', 'Generated'))
    return path, ReadonlyLibraryRepository(path, immutable=True)


@pytest.mark.parametrize('global_on', [False, True])
@pytest.mark.parametrize('override', [None, False, True])
@pytest.mark.parametrize('legacy_flag', [False, True])
def test_u11_inherit_uses_original_following_flag(tmp_path, global_on, override, legacy_flag):
    path, repo = generated_repository(tmp_path, legacy_flag)
    before = path.read_bytes()
    checks = []
    try:
        with generated_exports(tmp_path, global_on, override) as fixture:
            fixture.exports.s.library_repository = repo
            fixture.exports.s._assert_legacy_library_source = lambda: checks.append('check')
            fixture.exports.scan()
            jobs = settled(fixture.exports)
            # Flet core.py:1783-1789: author override first, then following flag, global OR unchanged.
            expected = global_on or (legacy_flag if override is None else override)
            assert len(jobs) == int(expected)
            assert not fixture.exports.scan_errors
            if expected:
                verify_archive(fixture.exports, fixture.managed, jobs[0])
            assert bool(checks) == (override is None)
            assert fixture.media.author_zip_setting('456') is override
    finally:
        repo.close()
    assert path.read_bytes() == before
    assert not any(tmp_path.glob('frozen-legacy.sqlite3-*'))


@pytest.mark.parametrize('column,author', [(False, True), (True, False)])
def test_u11_missing_legacy_flag_is_false_without_mutation(tmp_path, column, author):
    path, repo = generated_repository(tmp_path, True, column=column, author=author)
    before = path.read_bytes()
    try:
        assert repo.author_zip_flag('456') is False
    finally:
        repo.close()
    assert path.read_bytes() == before


def test_u11_changed_frozen_source_does_not_enqueue(tmp_path):
    path, repo = generated_repository(tmp_path, True)
    def changed():
        raise ValueError('generated source changed')
    try:
        with generated_exports(tmp_path, False, None) as fixture:
            fixture.exports.s.library_repository = repo
            fixture.exports.s._assert_legacy_library_source = changed
            fixture.exports.scan()
            assert not fixture.exports.jobs.snapshot()
            assert fixture.exports.scan_errors
    finally:
        repo.close()

