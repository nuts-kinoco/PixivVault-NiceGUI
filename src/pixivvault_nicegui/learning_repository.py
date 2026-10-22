"""Learning persistence backed by the unchanged legacy judgment SQL."""
from __future__ import annotations

from threading import RLock
from copy import deepcopy
import json
from datetime import datetime

from .learning import Judgment
from .legacy.database import Database, normalize_tag, VALID_HATE_REASONS


class LearningRepository:
    """Session operation IDs guard retries; legacy history handles re-judgment."""

    def __init__(self, db: Database, artwork_metadata: dict[str, dict] | None = None):
        self.db = db
        # A caller mutating nested tags after construction must not alter later writes.
        self.artwork_metadata = deepcopy(artwork_metadata) if artwork_metadata is not None else {}
        self._used: set[str] = set()
        self._stack: list[Judgment] = []
        self._versions: dict[str, int | None] = {}
        self._lock = RLock()
        # Triggers fire inside the legacy write transaction, including on other
        # SQLite connections.  Never derive identity from the verdict itself (ABA).
        with self.db.lock:
            # executescript() implicitly commits an open transaction.  Schema
            # installation must never commit unrelated caller changes.
            if self.db.conn.in_transaction:
                raise ValueError("learning adapter schema requires no active transaction")
            self.db.conn.executescript("""
                CREATE TABLE IF NOT EXISTS learning_adapter_revision (
                    work_id TEXT PRIMARY KEY, revision INTEGER NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS learning_adapter_insert
                AFTER INSERT ON discovery_swipe_history BEGIN
                    INSERT INTO learning_adapter_revision VALUES (NEW.work_id, 1)
                    ON CONFLICT(work_id) DO UPDATE SET revision=revision+1;
                END;
                CREATE TRIGGER IF NOT EXISTS learning_adapter_update
                AFTER UPDATE ON discovery_swipe_history BEGIN
                    INSERT INTO learning_adapter_revision VALUES (NEW.work_id, 1)
                    ON CONFLICT(work_id) DO UPDATE SET revision=revision+1;
                END;
                CREATE TRIGGER IF NOT EXISTS learning_adapter_delete
                AFTER DELETE ON discovery_swipe_history BEGIN
                    INSERT INTO learning_adapter_revision VALUES (OLD.work_id, 1)
                    ON CONFLICT(work_id) DO UPDATE SET revision=revision+1;
                END;
            """)

    def _revision(self, work_id: str) -> int:
        row = self.db.conn.execute(
            "SELECT revision FROM learning_adapter_revision WHERE work_id=?", (str(work_id),)
        ).fetchone()
        return int(row[0]) if row else 0

    def record(self, judgment: Judgment) -> None:
        with self._lock:
            if not judgment.operation_id or judgment.operation_id in self._used:
                raise ValueError("duplicate operation_id")
            if judgment.choice not in ("skip", "hate", "like", "superLike"):
                raise ValueError("invalid verdict")
            metadata = self.artwork_metadata.get(judgment.work_id, {})
            reason = metadata.get("reason")
            if reason is not None and reason not in VALID_HATE_REASONS:
                raise ValueError(f"unknown hate reason: {reason}")
            if judgment.choice != "hate":
                reason = None
            display_tags = {}
            for raw in metadata.get("tags", ()):
                tag = normalize_tag(raw)
                if tag and tag not in display_tags:
                    display_tags[tag] = str(raw)
            tags = list(display_tags)
            work_id = str(judgment.work_id)
            author_id = str(metadata.get("author_id")) if metadata.get("author_id") else None
            source = metadata.get("source")
            with self.db._transaction():
                prev = self.db.conn.execute(
                    "SELECT verdict, reason, tags, author_id, source, opened, bookmarked "
                    "FROM discovery_swipe_history WHERE work_id=?", (work_id,)
                ).fetchone()
                if prev:
                    self.db._apply_judgment(
                        prev["verdict"], prev["reason"], json.loads(prev["tags"] or "[]"),
                        prev["author_id"], prev["source"],
                        bool(prev["opened"]), bool(prev["bookmarked"]), sign=-1,
                    )
                self.db._apply_judgment(
                    judgment.choice, reason, tags, author_id, source,
                    False, False, sign=+1,
                    display_tags=display_tags, display_name=metadata.get("author_name"),
                )
                self.db.conn.execute("""
                    INSERT INTO discovery_swipe_history
                        (work_id, verdict, judged_at, source, reason, tags, author_id, opened, bookmarked, thumbnail_url)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(work_id) DO UPDATE SET
                        verdict=excluded.verdict, judged_at=excluded.judged_at,
                        source=excluded.source, reason=excluded.reason,
                        tags=excluded.tags, author_id=excluded.author_id,
                        opened=excluded.opened, bookmarked=excluded.bookmarked,
                        thumbnail_url=COALESCE(excluded.thumbnail_url, discovery_swipe_history.thumbnail_url)
                """, (work_id, judgment.choice, datetime.now().isoformat(), source,
                      reason, json.dumps(tags, ensure_ascii=False), author_id, 0, 0, None))
                targets = {author_id}
                if prev and prev["author_id"]:
                    targets.add(prev["author_id"])
                for aid in targets:
                    self.db._recompute_consecutive_skips(aid)
                revision = self._revision(work_id)
            # Legacy SQL replaces the previous history for this work_id.
            # The replaced judgment can no longer be undone independently.
            self._stack = [entry for entry in self._stack if entry.work_id != judgment.work_id]
            self._versions[judgment.operation_id] = revision
            self._used.add(judgment.operation_id)
            self._stack.append(judgment)

    def revert(self, judgment: Judgment, operation_id: str) -> None:
        with self._lock:
            if not operation_id or operation_id in self._used:
                raise ValueError("duplicate operation_id")
            if not self._stack or self._stack[-1] != judgment:
                raise ValueError("judgment is not latest")
            # BEGIN IMMEDIATE serializes competing SQLite writers before the
            # generation check.  The original undo logic is reproduced here
            # because its own nested BEGIN would otherwise commit the guard.
            with self.db._transaction():
                expected = self._versions.get(judgment.operation_id)
                if expected is None or self._revision(judgment.work_id) != expected:
                    raise ValueError("judgment changed by another writer (conflict)")
                row = self.db.conn.execute(
                    "SELECT verdict, reason, tags, author_id, source, opened, bookmarked "
                    "FROM discovery_swipe_history WHERE work_id=?", (str(judgment.work_id),)
                ).fetchone()
                if row is None:
                    raise ValueError("judgment no longer exists")
                self.db._apply_judgment(
                    row["verdict"], row["reason"], json.loads(row["tags"] or "[]"),
                    row["author_id"], row["source"],
                    bool(row["opened"]), bool(row["bookmarked"]), sign=-1,
                )
                self.db.conn.execute(
                    "DELETE FROM discovery_swipe_history WHERE work_id=?", (str(judgment.work_id),)
                )
                self.db._recompute_consecutive_skips(row["author_id"])
                self.db._prune_empty_preference_rows()
            self._stack.pop()
            self._used.add(operation_id)
