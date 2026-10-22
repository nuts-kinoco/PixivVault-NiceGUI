import sqlite3
import pytest

from pixivvault_nicegui.learning import Judgment
from pixivvault_nicegui.learning_repository import LearningRepository
from pixivvault_nicegui.legacy.database import Database


def new_db(tmp_path):
    return Database(tmp_path / "learning.db", sandbox_root=tmp_path)


def test_adapter_init_rejects_transaction_without_committing_caller_changes(tmp_path):
    db = new_db(tmp_path)
    try:
        db.conn.execute("BEGIN IMMEDIATE")
        db.conn.execute("CREATE TABLE uncommitted_marker (id INTEGER)")
        db.conn.execute("INSERT INTO uncommitted_marker VALUES (1)")
        with pytest.raises(ValueError, match="active transaction"):
            LearningRepository(db)
        assert db.conn.in_transaction
        assert db.conn.execute("SELECT id FROM uncommitted_marker").fetchone()[0] == 1
        db.conn.execute("ROLLBACK")
        assert not db.conn.in_transaction
        assert db.conn.execute("SELECT name FROM sqlite_master WHERE name='uncommitted_marker'").fetchone() is None
        LearningRepository(db)  # Schema installation succeeds outside a transaction.
    finally:
        db.close()


def test_path_confinement_and_no_environment_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("PIXIVVAULT_DB_PATH", str(tmp_path / "forbidden.db"))
    with pytest.raises((ValueError, TypeError)):
        Database(None, sandbox_root=tmp_path)
    with pytest.raises(ValueError):
        Database("relative.db", sandbox_root=tmp_path)
    with pytest.raises(ValueError):
        Database(tmp_path.parent / "outside.db", sandbox_root=tmp_path)
    assert not (tmp_path / "forbidden.db").exists()


@pytest.mark.parametrize("verdict,column", [
    ("skip", "skip_count"), ("hate", "hate_count"),
    ("like", "like_count"), ("superLike", "superlike_count")
])
def test_original_learning_counters_and_undo(tmp_path, verdict, column):
    db = new_db(tmp_path)
    try:
        meta = {"one": {"tags": ["ＦＯＯ", "foo", "猫"], "author_id": "author"}}
        ledger = LearningRepository(db, meta)
        item = Judgment("one", verdict, "op-one")
        ledger.record(item)
        assert db.get_discovery_swipe_history()[0]["verdict"] == verdict
        prefs = db.get_discovery_preferences()
        assert "foo" in prefs and prefs["foo"][column] == 1
        authors = db.get_discovery_author_preferences()
        # Legacy hate without reason updates tags only; author shown_count still increases.
        assert authors["author"][column] == (0 if verdict == "hate" else 1)
        assert authors["author"]["shown_count"] == 1
        with pytest.raises(ValueError):
            ledger.record(item)
        ledger.revert(item, "undo-one")
        assert db.get_discovery_swipe_history() == []
        assert not db.get_discovery_preferences()
        ledger.record(Judgment("one", verdict, "op-two"))
        assert db.get_discovery_preferences()["foo"][column] == 1
    finally:
        db.close()


def test_rejudge_reopen_and_failure_rollback(tmp_path):
    db = new_db(tmp_path)
    try:
        ledger = LearningRepository(db, {"w": {"tags": ["Tag"], "author_id": "u"}})
        ledger.record(Judgment("w", "like", "a"))
        ledger.record(Judgment("w", "skip", "b"))
        counts = db.get_discovery_preferences()["tag"]
        assert counts["like_count"] == 0 and counts["skip_count"] == 1
        # Trigger a failure inside the existing transaction (after counter updates).
        db.conn.execute("""CREATE TRIGGER reject_history BEFORE UPDATE ON discovery_swipe_history
                           BEGIN SELECT RAISE(ABORT, 'blocked'); END""")
        with pytest.raises(sqlite3.DatabaseError):
            ledger.record(Judgment("w", "superLike", "c"))
        assert db.get_discovery_preferences()["tag"]["skip_count"] == 1
        db.conn.execute("DROP TRIGGER reject_history")
        ledger.record(Judgment("w", "superLike", "c"))
        assert db.get_discovery_preferences()["tag"]["superlike_count"] == 1
        assert db.get_discovery_preferences()["tag"]["skip_count"] == 0
    finally:
        db.close()
    reopened = new_db(tmp_path)
    try:
        assert reopened.get_discovery_swipe_history()[0]["verdict"] == "superLike"
        assert reopened.undo_discovery_judgment("w")
        assert reopened.get_discovery_swipe_history() == []
    finally:
        reopened.close()


def test_rejudgment_supersedes_old_undo_and_preserves_other_works(tmp_path):
    db = new_db(tmp_path)
    try:
        ledger = LearningRepository(db, {"a": {"tags": ["one"]}, "b": {"tags": ["two"]}})
        first = Judgment("a", "like", "a-first")
        second = Judgment("b", "skip", "b-first")
        latest = Judgment("a", "superLike", "a-second")
        ledger.record(first)
        ledger.record(second)
        ledger.record(latest)
        ledger.revert(latest, "undo-latest")
        with pytest.raises(ValueError, match="not latest"):
            ledger.revert(first, "undo-old")
        assert {row["work_id"] for row in db.get_discovery_swipe_history()} == {"b"}
        ledger.revert(second, "undo-second")
        assert db.get_discovery_swipe_history() == []
    finally:
        db.close()


def test_failed_rejudgment_keeps_prior_stack_and_operation_id(tmp_path):
    db = new_db(tmp_path)
    try:
        ledger = LearningRepository(db, {"a": {"tags": ["tag"]}})
        first = Judgment("a", "like", "first")
        latest = Judgment("a", "skip", "second")
        ledger.record(first)
        db.conn.execute("""CREATE TRIGGER reject_update BEFORE UPDATE ON discovery_swipe_history
                           BEGIN SELECT RAISE(ABORT, 'fail'); END""")
        with pytest.raises(sqlite3.DatabaseError):
            ledger.record(latest)
        assert ledger._stack == [first]
        assert "second" not in ledger._used
        assert db.get_discovery_preferences()["tag"]["like_count"] == 1
        db.conn.execute("DROP TRIGGER reject_update")
        ledger.revert(first, "undo-first")
        ledger.record(latest)
        assert db.get_discovery_preferences()["tag"]["skip_count"] == 1
    finally:
        db.close()


def test_metadata_snapshot_survives_nested_mutation(tmp_path):
    db = new_db(tmp_path)
    try:
        meta = {"a": {"tags": ["first"], "author_id": "original"}}
        ledger = LearningRepository(db, meta)
        meta["a"]["tags"].append("later")
        meta["a"]["author_id"] = "changed"
        ledger.record(Judgment("a", "like", "once"))
        assert set(db.get_discovery_preferences()) == {"first"}
        assert set(db.get_discovery_author_preferences()) == {"original"}
    finally:
        db.close()


def test_stale_undo_never_erases_other_connections_rejudgment(tmp_path):
    a_db = new_db(tmp_path)
    b_db = new_db(tmp_path)
    try:
        meta = {"w": {"tags": ["tag"], "author_id": "author"}}
        a = LearningRepository(a_db, meta)
        b = LearningRepository(b_db, meta)
        first = Judgment("w", "like", "a-like")
        a.record(first)
        b.record(Judgment("w", "superLike", "b-super"))
        with pytest.raises(ValueError, match="changed|conflict"):
            a.revert(first, "a-undo")
        assert a._stack == [first]
        assert "a-undo" not in a._used
        assert a_db.get_discovery_swipe_history()[0]["verdict"] == "superLike"
        assert a_db.get_discovery_preferences()["tag"]["superlike_count"] == 1
        assert a_db.get_discovery_preferences()["tag"]["like_count"] == 0
    finally:
        a_db.close()
        b_db.close()


@pytest.mark.parametrize("choices", [
    ("like", "like"),
    ("superLike", "like", "superLike"),
])
def test_revision_guard_rejects_same_value_and_aba(tmp_path, choices):
    a_db, b_db = new_db(tmp_path), new_db(tmp_path)
    try:
        meta = {"w": {"tags": ["tag"]}}
        a, b = LearningRepository(a_db, meta), LearningRepository(b_db, meta)
        original = Judgment("w", choices[0], "a")
        a.record(original)
        for index, verdict in enumerate(choices[1:]):
            b.record(Judgment("w", verdict, f"b-{index}"))
        before = [dict(row) for row in b_db.get_discovery_swipe_history()]
        counters = b_db.get_discovery_preferences()
        with pytest.raises(ValueError, match="conflict"):
            a.revert(original, "undo")
        assert [dict(row) for row in a_db.get_discovery_swipe_history()] == before
        assert a_db.get_discovery_preferences() == counters
        assert a._stack == [original]
        assert "undo" not in a._used
        # The current owner can still undo normally after the failed stale undo.
        b.revert(Judgment("w", choices[-1], f"b-{len(choices)-2}"), "b-undo")
        assert a_db.get_discovery_swipe_history() == []
    finally:
        a_db.close()
        b_db.close()


def test_external_legacy_writer_also_invalidates_adapter_undo(tmp_path):
    a_db, b_db = new_db(tmp_path), new_db(tmp_path)
    try:
        a = LearningRepository(a_db, {"w": {"tags": ["tag"]}})
        original = Judgment("w", "like", "a")
        a.record(original)
        b_db.record_discovery_judgment("w", "superLike", tags=["tag"])
        with pytest.raises(ValueError, match="conflict"):
            a.revert(original, "undo")
        assert b_db.get_discovery_preferences()["tag"]["superlike_count"] == 1
    finally:
        a_db.close()
        b_db.close()


@pytest.mark.parametrize("verdict,reason", [
    ("skip", None), ("like", None), ("superLike", None),
    ("hate", None), ("hate", "genre"), ("hate", "style"),
])
def test_adapter_matches_legacy_history_tag_author_and_undo(tmp_path, verdict, reason):
    adapter_root = tmp_path / "adapter"
    legacy_root = tmp_path / "legacy"
    adapter_root.mkdir()
    legacy_root.mkdir()
    adapter_db = Database(adapter_root / "learning.db", sandbox_root=adapter_root)
    legacy_db = Database(legacy_root / "learning.db", sandbox_root=legacy_root)
    try:
        meta = {"work": {"tags": ["ＦＯＯ", "foo", "猫"], "author_id": "author",
                         "author_name": "Author", "source": "ranking", "reason": reason}}
        ledger = LearningRepository(adapter_db, meta)
        first = Judgment("work", verdict, "first")
        ledger.record(first)
        legacy_db.record_discovery_judgment(
            "work", verdict, tags=meta["work"]["tags"], author_id="author",
            display_name="Author", source="ranking", reason=reason,
        )
        for getter in ("get_discovery_preferences", "get_discovery_author_preferences"):
            assert getattr(adapter_db, getter)() == getattr(legacy_db, getter)()
        fields = ("work_id", "verdict", "source", "reason", "tags", "author_id", "opened", "bookmarked")
        a_row = adapter_db.get_discovery_swipe_history()[0]
        l_row = legacy_db.get_discovery_swipe_history()[0]
        assert tuple(a_row[field] for field in fields) == tuple(l_row[field] for field in fields)
        second = Judgment("work", "superLike", "second")
        ledger.record(second)
        legacy_db.record_discovery_judgment(
            "work", "superLike", tags=meta["work"]["tags"],
            author_id="author", display_name="Author", source="ranking",
        )
        for getter in ("get_discovery_preferences", "get_discovery_author_preferences"):
            assert getattr(adapter_db, getter)() == getattr(legacy_db, getter)()
        ledger.revert(second, "undo")
        assert legacy_db.undo_discovery_judgment("work")
        for getter in ("get_discovery_preferences", "get_discovery_author_preferences"):
            assert getattr(adapter_db, getter)() == getattr(legacy_db, getter)()
        assert adapter_db.get_discovery_swipe_history() == legacy_db.get_discovery_swipe_history() == []
    finally:
        adapter_db.close()
        legacy_db.close()
