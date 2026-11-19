from pixivvault_nicegui.discovery_learning import DiscoveryLearningService
from pixivvault_nicegui.learning import Judgment
from z_learning_support_20261118 import FakeReader, candidate, seed_candidates


def test_old_development_seed_migration_is_atomic_and_only_once(tmp_path):
    root = tmp_path / 'discovery'
    service = DiscoveryLearningService(root, FakeReader())
    try:
        seed_candidates(service, [candidate(0), candidate(1)])
        session = service._submit(service._open_session).result(10)
        service.submit_record(session.session_id, Judgment('1000', 'like', 'judged')).result(10)
        def old_state():
            db = service._database()
            with db._transaction() as conn:
                conn.execute('CREATE TABLE managed_local_seed(work_id TEXT, tags TEXT)')
                conn.execute("INSERT INTO managed_local_seed VALUES ('1001', 'local_only')")
                conn.execute("INSERT INTO settings(key,value) VALUES ('discovery_search_cursor','2') ON CONFLICT(key) DO UPDATE SET value='2'")
        service._submit(old_state).result(10)
    finally:
        service.close()
    reopened = DiscoveryLearningService(root, FakeReader())
    try:
        def inspect():
            db = reopened._database()
            table = db.conn.execute("SELECT 1 FROM sqlite_master WHERE name='managed_local_seed'").fetchone()
            candidates = [r[0] for r in db.conn.execute('SELECT work_id FROM managed_discovery_candidate')]
            return table, candidates, db.get_setting('discovery_search_cursor', None), db.get_judged_work_ids()
        table, candidates, cursor, judged = reopened._submit(inspect).result(10)
        assert table is None and candidates == ['1000'] and cursor is None and judged == {'1000'}
        seed_candidates(reopened, [candidate(2)])
    finally:
        reopened.close()
    again = DiscoveryLearningService(root, FakeReader())
    try:
        assert set(again._submit(lambda: (again._database(), tuple(again._candidates))[1]).result(10)) == {'1000', '1002'}
    finally:
        again.close()
