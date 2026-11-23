import sqlite3

import pytest

from tools import d6_offline


def test_cache_uses_matching_bookmark_and_recommendation_rows(tmp_path):
    from pixivvault_nicegui.legacy.database import Database
    from pixivvault_nicegui.library_repository import ReadonlyLibraryRepository
    from pixivvault_nicegui.recommendation_state import FrozenRecommendationSource

    db_path = tmp_path / 'fixture.sqlite'
    db = Database(db_path, sandbox_root=tmp_path)
    db.conn.execute("INSERT INTO following_users(user_id,name) VALUES ('1','Fixture Author')")
    db.conn.execute("INSERT INTO works(work_id,title,user_id,page_count,content_type,tags) "
                    "VALUES ('2','Fixture Work','1',1,'illust','[]')")
    db.conn.commit()
    learning = [dict(work_id=str(n), title='Fixture Recommendation', author_id='3',
                     author_name='Fixture Author') for n in range(10, 23)]
    d6_offline.seed_caches(db, [('2', 'Fixture Work', 1, '[]', '1')], [('1', 'Fixture Author')],
                           learning)
    db.conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    db.conn.execute('PRAGMA journal_mode=DELETE')
    db.conn.close()

    with sqlite3.connect(db_path) as conn:
        assert conn.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        assert conn.execute('SELECT count(*) FROM bookmark_remote_cache').fetchone() == (1,)
    repository = ReadonlyLibraryRepository(db_path, immutable=True)
    try:
        remote, local = repository.bookmark_bundle()
        assert [row.title for row in remote] == [row.title for row in local] == ['Fixture Work']
    finally:
        repository.close()
    works = dict(FrozenRecommendationSource(db_path).load().sections)['superlike'][0].works
    assert len(works) == 12
    assert works[0].work_id == learning[-1]['work_id']


def test_fixture_reader_rejects_every_url_except_exact_mapped_fixture(tmp_path):
    image = tmp_path / 'fixture.png'
    image.write_bytes(b'fixture')
    url = d6_offline.thumbnail('12.png')
    reader = d6_offline.ImageFixture({url: image})
    assert reader.get_image_bytes(url) == b'fixture'
    for wrong in (url + '?q=x', d6_offline.thumbnail('13.png'), 'http://127.0.0.1/12.png'):
        with pytest.raises(ValueError):
            reader.get_image_bytes(wrong)
    assert reader.denials == 3
