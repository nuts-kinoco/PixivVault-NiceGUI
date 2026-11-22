"""Opt-in, app-owned persistence for the generated synthetic fixture only.

All filesystem and SQLite I/O belongs to one worker. Client sessions retain
Undo/operation receipts only until release; restart resumes from DB judged IDs.
Call close outside the UI event loop so accepted commands can drain.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
from threading import Lock
from uuid import uuid4

from .fixtures import generate_demo_artworks
from .learning import Judgment
from .learning_repository import LearningRepository
from .legacy.database import Database
from .models import Artwork

DATABASE_NAME = "learning.sqlite3"
MARKER_NAME = ".pixivvault-synthetic-learning.json"
_LOG = logging.getLogger(__name__)


def notify_shadow(callback, *args, route='observer'):
    """Observers are optional and cannot change the outcome of a learning action."""
    try:
        return callback(*args)
    except Exception as error:
        # Exception text may come from storage, providers or URLs.
        _LOG.warning('visual preference observer notification failed (%s, %s)', route, type(error).__name__)


def shadow_judgments(db, work_ids):
    """A read-only projection of committed labels, including Undo tombstones."""
    with db.lock:
        has_revisions = db.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='learning_adapter_revision'"
        ).fetchone()
        query = 'SELECT w.value, h.verdict, h.reason, h.bookmarked, '
        query += 'COALESCE(r.revision, 0)' if has_revisions else '0'
        query += (' FROM json_each(?) w LEFT JOIN discovery_swipe_history h ON h.work_id=w.value')
        if has_revisions:
            query += ' LEFT JOIN learning_adapter_revision r ON r.work_id=w.value'
        return {wid: {'judgment': 'superlike' if verdict == 'superLike' else verdict,
                      'reason': reason, 'bookmarked': bool(bookmarked), 'revision': revision}
                for wid, verdict, reason, bookmarked, revision in db.conn.execute(query, (json.dumps(work_ids),))}


@dataclass(frozen=True, slots=True)
class LearningSessionSnapshot:
    session_id: str
    judged_ids: frozenset[str]


@dataclass(slots=True)
class _Session:
    repository: LearningRepository
    receipts: dict[str, tuple] = field(default_factory=dict)
    review_undo: dict[str, tuple] = field(default_factory=dict)


class LearningService:
    """Serialize accepted work; cancelling an await cannot cancel its DB write."""

    def __init__(self, sandbox_root: Path, artworks: tuple[Artwork, ...], *, shadow_observer=None) -> None:
        self.sandbox_root = Path(sandbox_root)
        if not self.sandbox_root.is_absolute():
            raise ValueError("absolute learning sandbox path required")
        self.artworks = tuple(artworks)
        self.shadow_observer = shadow_observer
        if self.artworks != generate_demo_artworks(len(self.artworks)):
            raise ValueError("learning persistence accepts only the generated synthetic fixture")
        fingerprint = hashlib.sha256(json.dumps(
            [{key: getattr(item,key) for key in ('work_id','title','author','description','aspect_ratio','page_count','tags')} for item in self.artworks], ensure_ascii=False,
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        self._marker = {
            "format": "pixivvault-nicegui-synthetic-learning-v2",
            "database": DATABASE_NAME,
            "artwork_count": len(self.artworks),
            "fixture_sha256": fingerprint,
        }
        self._metadata = {
            item.work_id: {
                "tags": item.tags,
                "author_id": f"demo-author-{(index % 12) + 1:02d}",
                "author_name": item.author,
                "source": "synthetic",
            }
            for index, item in enumerate(self.artworks)
        }
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pixivvault-learning")
        self._lifecycle_lock = Lock()
        self._close_future: Future | None = None
        self._db: Database | None = None
        self._sessions: dict[str, _Session] = {}

    def _submit(self, function, *args) -> Future:
        with self._lifecycle_lock:
            if self._close_future is not None:
                raise RuntimeError("learning service is closed")
            return self._executor.submit(function, *args)

    @staticmethod
    async def _wait(future: Future):
        wrapped = asyncio.wrap_future(future)
        # Retrieve any eventual error if the UI waiter is cancelled. Shield
        # prevents cancellation from reaching a queued executor command.
        wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        return await asyncio.shield(wrapped)

    def _prepare_sandbox(self) -> Path:
        root = self.sandbox_root
        # Reject redirected roots before creating anything below them.
        if root.resolve(strict=False) != root:
            raise ValueError("learning sandbox must not use links or redirected paths")
        root.mkdir(parents=True, exist_ok=True)
        if not root.is_dir() or root.is_symlink() or root.is_junction():
            raise ValueError("learning sandbox must be a dedicated directory")
        marker_name = getattr(self, "marker_name", MARKER_NAME)
        allowed = {marker_name, DATABASE_NAME, DATABASE_NAME + "-wal",
                   DATABASE_NAME + "-shm", DATABASE_NAME + "-journal"}
        entries = list(root.iterdir())
        for entry in entries:
            if (entry.name not in allowed or entry.is_symlink() or entry.is_junction()
                    or not entry.is_file() or entry.stat().st_nlink != 1):
                raise ValueError("learning sandbox contains unmanaged or linked files")
        marker = root / marker_name
        created_marker = False
        if not marker.exists():
            if entries:
                raise ValueError("existing learning sandbox has no synthetic marker")
            try:
                with marker.open("x", encoding="utf-8") as output:
                    json.dump(dict(self._marker, instance_id=uuid4().hex), output,
                              ensure_ascii=False, sort_keys=True)
                created_marker = True
            except FileExistsError:
                pass  # Another app may have created the same valid marker.
        if marker.stat().st_size > 4096:
            raise ValueError("invalid synthetic learning marker")
        try:
            saved = json.loads(marker.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise ValueError("invalid synthetic learning marker") from exc
        instance_id = saved.get("instance_id") if isinstance(saved, dict) else None
        if (not isinstance(instance_id, str) or len(instance_id) != 32
                or any(char not in "0123456789abcdef" for char in instance_id)
                or saved != dict(self._marker, instance_id=instance_id)):
            raise ValueError("synthetic learning marker or fixture mismatch")
        path = root / DATABASE_NAME
        if not path.exists():
            if not created_marker:
                raise ValueError("learning database identity is missing")
            self._create_database_identity(path, saved)
        self._validate_database_identity(path, saved)
        return path

    @staticmethod
    def _identity_payload(marker: dict) -> str:
        return json.dumps(marker, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def _create_database_identity(cls, path: Path, marker: dict) -> None:
        # Never let sqlite3 create/adopt an already existing file. The new
        # identity is committed to the main DB before legacy enables WAL.
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        connection = sqlite3.connect(path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""CREATE TABLE learning_service_identity (
                id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)""")
            connection.execute("INSERT INTO learning_service_identity VALUES (1, ?)",
                               (cls._identity_payload(marker),))
            connection.commit()
        finally:
            connection.close()

    @classmethod
    def _validate_database_identity(cls, path: Path, marker: dict) -> None:
        # Identity never changes after exclusive creation. It lives in the
        # main file, so immutable inspection needs no WAL/shm writes or reads
        # of unrelated legacy tables and cannot initialize a foreign DB.
        connection = None
        try:
            connection = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
            kind = connection.execute("SELECT type FROM sqlite_master "
                                      "WHERE name='learning_service_identity'").fetchone()
            if kind != ("table",):
                raise ValueError("learning database identity is missing")
            rows = connection.execute("SELECT id, payload FROM learning_service_identity LIMIT 2").fetchall()
            if rows != [(1, cls._identity_payload(marker))]:
                raise ValueError("learning database identity or fixture mismatch")
        except sqlite3.DatabaseError as exc:
            raise ValueError("learning database identity is invalid") from exc
        finally:
            if connection is not None:
                connection.close()

    def _database(self) -> Database:
        if self._db is None:
            self._db = Database(self._prepare_sandbox(), sandbox_root=self.sandbox_root)
        return self._db

    async def open_session(self) -> LearningSessionSnapshot:
        return await self._wait(self._submit(self._open_session))

    def _open_session(self) -> LearningSessionSnapshot:
        db = self._database()
        repository = LearningRepository(db, self._metadata)
        # Fail closed: an unreadable history cannot become an empty snapshot.
        judged_ids = frozenset(db.get_judged_work_ids())
        if not judged_ids.issubset(self._metadata):
            raise ValueError("learning history contains non-fixture works")
        session_id = uuid4().hex
        self._sessions[session_id] = _Session(repository)
        return LearningSessionSnapshot(session_id, judged_ids)

    def _session(self, session_id: str) -> _Session:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise ValueError("learning session is closed or unknown") from exc

    @staticmethod
    def _replayed(session: _Session, operation_id: str, signature: tuple) -> bool:
        if not isinstance(operation_id, str) or not operation_id:
            raise ValueError("invalid operation_id")
        previous = session.receipts.get(operation_id)
        if previous is None:
            return False
        if previous != signature:
            raise ValueError("operation_id was already used for a different operation")
        return True

    def submit_record(self, session_id: str, judgment: Judgment) -> Future:
        """Synchronously accept a write before a UI background task can be cancelled."""
        return self._submit(self._record, session_id, judgment)

    async def record(self, session_id: str, judgment: Judgment) -> None:
        await self._wait(self.submit_record(session_id, judgment))

    def _record(self, session_id: str, judgment: Judgment) -> None:
        session = self._session(session_id)
        if not isinstance(judgment, Judgment) or judgment.work_id not in self._metadata:
            raise ValueError("judgment must refer to a synthetic fixture work")
        signature = ("record", judgment)
        if self._replayed(session, judgment.operation_id, signature):
            return
        session.repository.record(judgment)
        session.receipts[judgment.operation_id] = signature
        self._shadow_judgment('judgment', judgment.work_id)

    def submit_hate_reason(self, session_id: str, judgment: Judgment, reason: str) -> Future:
        """「合わない」の理由の後追い。保存済みの同じ判定にだけ付き、Futureは付けたかどうかを返す。"""
        return self._submit(self._hate_reason, session_id, judgment, reason)

    def _hate_reason(self, session_id: str, judgment: Judgment, reason: str) -> bool:
        session = self._session(session_id)
        if not isinstance(judgment, Judgment) or judgment.work_id not in self._metadata:
            raise ValueError("judgment must refer to a known work")
        changed = session.repository.set_hate_reason(judgment, reason)
        if changed:
            self._shadow_judgment('reason_change', judgment.work_id)
        return changed

    def submit_revert(self, session_id: str, judgment: Judgment, operation_id: str) -> Future:
        return self._submit(self._revert, session_id, judgment, operation_id)

    async def revert(self, session_id: str, judgment: Judgment, operation_id: str) -> None:
        await self._wait(self.submit_revert(session_id, judgment, operation_id))

    def _revert(self, session_id: str, judgment: Judgment, operation_id: str) -> None:
        session = self._session(session_id)
        if not isinstance(judgment, Judgment) or judgment.work_id not in self._metadata:
            raise ValueError("judgment must refer to a synthetic fixture work")
        signature = ("revert", judgment)
        if self._replayed(session, operation_id, signature):
            return
        session.repository.revert(judgment, operation_id)
        session.receipts[operation_id] = signature
        self._shadow_judgment('undo', judgment.work_id)

    async def review_snapshot(self, session_id: str):
        return await self._wait(self._submit(self._review_snapshot, session_id))

    def _review_snapshot(self, session_id):
        result = self._session(session_id).repository.review_snapshot()
        titles = {art.work_id: art.title for art in self.artworks}
        if any(row['work_id'] not in titles for row in result['rows']):
            raise ValueError('review contains non-fixture work')
        for row in result['rows']:
            row['title'] = row.get('title') or titles[row['work_id']]
        return result

    def submit_review_undo(self, session_id, work_id, expected_revision, operation_id):
        return self._submit(self._review_undo, session_id, work_id, expected_revision, operation_id)

    def _review_undo(self, session_id, work_id, expected_revision, operation_id):
        session = self._session(session_id)
        if work_id not in self._metadata: raise ValueError('unknown fixture work')
        signature = ('review-undo', work_id, expected_revision)
        if self._replayed(session, operation_id, signature): return
        saved = session.repository.review_undo(work_id, expected_revision)
        session.review_undo[operation_id] = saved
        session.receipts[operation_id] = signature
        self._shadow_judgment('undo', work_id)

    def submit_review_restore(self, session_id, undo_operation_id, operation_id):
        return self._submit(self._review_restore, session_id, undo_operation_id, operation_id)

    def _review_restore(self, session_id, undo_operation_id, operation_id):
        session = self._session(session_id)
        signature = ('review-restore', undo_operation_id)
        if self._replayed(session, operation_id, signature): return
        saved, revision = session.review_undo[undo_operation_id]
        session.repository.review_restore(saved, revision)
        session.review_undo.pop(undo_operation_id)
        session.receipts[operation_id] = signature
        self._shadow_judgment('restore', saved['work_id'])

    def _shadow_judgment(self, kind, work_id=None):
        observer = self.shadow_observer
        if observer is None:
            return

        def snapshot():
            if work_id is None:
                return {}
            return {'work_id': work_id, **shadow_judgments(self._database(), (work_id,))[work_id]}

        notify_shadow(lambda: observer.judgment_changed(kind, snapshot), route=kind)

    def release_session(self, session_id: str) -> None:
        """Queue cleanup after accepted commands, without blocking the UI."""
        with self._lifecycle_lock:
            if self._close_future is None:
                self._executor.submit(self._sessions.pop, session_id, None)

    def _close_worker(self) -> None:
        self._sessions.clear()
        if self._db is not None:
            try:
                self._db.close()
            finally:
                self._db = None

    def close(self) -> None:
        """Reject new work, drain, close DB on the worker, then join that worker."""
        with self._lifecycle_lock:
            if self._close_future is None:
                self._close_future = self._executor.submit(self._close_worker)
            closed = self._close_future
        try:
            closed.result()
        finally:
            self._executor.shutdown(wait=True, cancel_futures=False)

    async def record_view(self, work_id):
        def write():
            if work_id not in self._metadata or work_id.startswith('mock-'):
                raise ValueError('unknown learning work')
            self._database().record_discovery_open(work_id, self._metadata[work_id].get('thumbnail_url'))
        return await self._wait(self._submit(write))

    def _view_rows(self):
        rows = self._database().get_discovery_view_history()
        titles = {a.work_id:a.title for a in self.artworks}
        for row in rows:
            row['title'] = row.get('title') or titles.get(row['work_id']) or row['work_id']
        return rows

    async def view_history(self):
        return await self._wait(self._submit(self._view_rows))

    # --- 学習状況のリセット（元 gui.py:2183-2297 / database.reset_discovery_learning） ---
    async def judged_count(self):
        return await self._wait(self._submit(lambda: len(self._database().get_judged_work_ids())))

    async def judgment_export_rows(self):
        return await self._wait(self._submit(lambda: self._database().get_discovery_swipe_history_for_review()))

    def submit_reset(self) -> Future:
        """Accepted on the worker before awaiting, like review undo."""
        def reset_with_log():
            from .shared_events import emit_shared
            owner = getattr(self, '_event_services', None)
            try:
                result = self._reset()
            except Exception:
                emit_shared(owner, '学習状況: リセットに失敗しました。', level='error')
                raise
            emit_shared(owner, '学習状況: リセットしました', level='success')
            return result
        return self._submit(reset_with_log)

    async def reset_learning(self):
        return await self._wait(self.submit_reset())

    def _reset(self):
        result = self._database().reset_discovery_learning(keep_seed=True)
        self._shadow_judgment('reset')
        # 判定行が消えたあとも残る取り消し状態（Undoスタック・振り返りの取消済み台帳）は
        # 消えた履歴を指すので、全セッション分を破棄する。
        for session in self._sessions.values():
            session.repository.discard_undo_state()
            session.review_undo.clear()
        return result

    async def clear_view_history(self, expected):
        def clear():
            from .shared_events import emit_shared
            owner = getattr(self, '_event_services', None)
            try:
                if self._view_rows() != expected:
                    raise ValueError('history changed; refresh before clearing')
                self._database().clear_discovery_view_history()
            except Exception:
                emit_shared(owner, '閲覧履歴: 全件削除に失敗しました。', level='error')
                raise
            emit_shared(owner, '閲覧履歴: 全件削除しました', level='success')
        return await self._wait(self._submit(clear))
