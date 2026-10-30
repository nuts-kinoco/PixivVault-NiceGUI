"""Explicit managed discovery ledger and original four-source candidate pipeline.

No ambient database, client, account discovery or network call at construction.
The caller owns the GET reader and supplies already accepted following/saved
snapshots. All learning SQL, cursor rotation and source reads serialize on the
existing FIFO learning worker; accepted judgments retain its CAS/Undo contract.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from copy import deepcopy
from dataclasses import replace
from .learning_service import LearningService
from .legacy.database import normalize_tag
from .bookmark_remote_adapter import RemoteBookmark, positive_id, thumbnail_url
from . import discovery_engine, discovery_ranking

SOURCES = ('following', 'ranking', 'related_author', 'tag_search')
TREND_MIN_JUDGMENTS = 20

@dataclass(frozen=True)
class DiscoveryBatch:
    artworks: tuple
    records: tuple
    frames: dict
    succeeded: tuple
    failed: tuple
    descriptions: dict | None = None
    @property
    def nothing_and_failed(self): return not self.artworks and bool(self.failed)

class _SourceDatabase:
    def __init__(self, db, following, saved):
        self.db, self.following, self.saved = db, following, saved
    def __getattr__(self, name): return getattr(self.db, name)
    def get_following_users(self, **kwargs): return deepcopy(self.following)
    def get_work(self, work_id):
        return {'work_id': work_id} if str(work_id) in self.saved else self.db.get_work(work_id)

class _Reader:
    """Original lane names are advisory; bounded reader owns actual admission."""
    def __init__(self, reader, is_current=None): self.reader = reader; self.is_current=is_current
    def __getattr__(self, name):
        target = getattr(self.reader, "search_recommendation_illusts" if name == "search_illusts" and hasattr(self.reader, "search_recommendation_illusts") else name)
        def call(*args, **kwargs):
            if self.is_current is not None and not self.is_current():raise RuntimeError('discovery acquisition stopped')
            kwargs.pop('lane', None)
            return target(*args, **kwargs)
        return call

class DiscoveryLearningService(LearningService):
    marker_name = '.pixivvault-managed-discovery.json'
    def __init__(self, root, reader, *, following=None, saved_ids=(), seed_readonly_db=None):
        super().__init__(root, ())
        if reader is None: raise ValueError('explicit discovery reader required')
        self._marker = {'format':'pixivvault-nicegui-managed-discovery-v1', 'database':'learning.sqlite3'}
        self.reader = reader
        self.following = following
        self.saved_ids = saved_ids
        self.seed_readonly_db = seed_readonly_db
        self._seed_saved = frozenset()
        self._seed_known = frozenset()
        self._seed_population_size = 0
        self._legacy_history = {}
        self._candidates = {}
        self._loaded = False
        self.frames = {}
        self._candidate_fetch_future = None
        self._candidate_fetch_claims = []
        self._initial_task = None
        self._initial_claims = []
        self._initial_finished = False
        self.initial_load_status = "idle"

    def _database(self):
        db = super()._database()
        if not self._loaded:
            with db._transaction() as conn:
                conn.execute('CREATE TABLE IF NOT EXISTS managed_discovery_candidate (work_id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            from .discovery_seed import seed_once
            seed=seed_once(db,self.seed_readonly_db)
            self._seed_saved=frozenset(seed['saved_ids']) if seed else frozenset()
            self._seed_known=frozenset(seed['known_ids']) if seed else frozenset()
            self._seed_population_size=seed['population_size'] if seed else 0
            for row in db.conn.execute('SELECT payload FROM managed_discovery_legacy_history ORDER BY rowid'):
                candidate=json.loads(row[0])
                self._legacy_history[candidate['work_id']]=candidate
                self._metadata[candidate['work_id']]={'tags':candidate['tags'],'author_id':candidate['author_id'],
                    'author_name':candidate['author_name'],'source':candidate['source'],'thumbnail_url':candidate['thumbnail_url']}
                try:self._record_for(candidate)
                except (ValueError,TypeError):pass
                else:self._accept_metadata(candidate)
            rows = db.conn.execute('SELECT payload FROM managed_discovery_candidate ORDER BY rowid').fetchall()
            for row in rows:
                candidate = json.loads(row[0])
                self._accept_metadata(candidate)
            self._loaded = True
        return db

    @staticmethod
    def _record_for(candidate):
        wid = positive_id(candidate.get('work_id'))
        aid = positive_id(candidate.get('author_id'))
        if not wid or not aid: raise ValueError('invalid discovery identity')
        tags = candidate.get('tags') or []
        if not isinstance(tags, (list, tuple)) or any(not isinstance(x, str) for x in tags):
            raise ValueError('invalid discovery tags')
        thumb = thumbnail_url(candidate.get('thumbnail_url'))
        if not thumb: raise ValueError('discovery thumbnail unavailable')
        DiscoveryLearningService._description_for(candidate)
        return RemoteBookmark(wid, 'illustration', 'show', str(candidate.get('title') or ''), aid,
                              str(candidate.get('author_name') or ''), tuple(tags), thumb, '', 1, None, 0,
                              page_count_known=False)

    @staticmethod
    def _description_for(candidate):
        """Only already-present GET fields: None means never acquired."""
        value = candidate.get('description')
        if value is None:
            return None
        if type(value) is not str or len(value) > 131072:
            raise ValueError('invalid discovery description')
        return value

    @classmethod
    def _artwork_for(cls,candidate):
        work=cls._record_for(candidate).as_artwork()
        description=cls._description_for(candidate)
        return replace(work, description=description) if description is not None else work

    def _accept_metadata(self, candidate):
        record = self._record_for(candidate)
        self._candidates[record.work_id] = deepcopy(candidate)
        self._metadata[record.work_id] = {'tags':record.tags,'author_id':record.author_id,
                'author_name':record.author_name,'source':candidate['source'], 'thumbnail_url':record.thumbnail}
        self.artworks = tuple(self._artwork_for(c) for c in self._candidates.values())
        for session in self._sessions.values():
            session.repository.artwork_metadata[record.work_id] = deepcopy(self._metadata[record.work_id])

    async def initialize_once(self, refresh_context_callback=None, *, is_current=None):
        """Configured app startup and first tab share original initial_load.

        Construction performs no GET. The app explicitly invokes this lifecycle
        entry once configured; views attach to it. A cancelled waiter cannot
        cancel app-owned work. No claims remaining stops before the next phase.
        Empty/failure is an initial result; the original active-view restock
        policy, or explicit retry, owns recovery instead of repeating startup.
        """
        import asyncio
        if self._close_future is not None:raise RuntimeError('learning service is closed')
        claim=is_current if is_current is not None else lambda:True
        if self._initial_task is None or (self._initial_task.done() and not self._initial_finished):
            self._initial_claims=[claim]
            self._initial_task=asyncio.create_task(self._initialize_worker(refresh_context_callback))
            self._initial_task.add_done_callback(self._initial_settled)
        elif not self._initial_task.done():
            self._initial_claims.append(claim)
        return await asyncio.shield(self._initial_task)

    def _initial_current(self):
        return self._close_future is None and any(claim() for claim in tuple(self._initial_claims))

    def _initial_settled(self,task):
        if task is self._initial_task:self._initial_claims=[]
        if not task.cancelled():task.exception()  # Observe eventual failure after a cancelled UI waiter.

    async def _initialize_worker(self,refresh_context_callback):
        self.initial_load_status='running'
        try:
            if not self._initial_current():
                self.initial_load_status='stopped';return None
            # Original discovery_tab computes its initial preference/trend cache
            # before scheduling initial source acquisition (fixed L4786-4788).
            await self.trend_snapshot()
            if not self._initial_current():
                self.initial_load_status='stopped';return None
            if refresh_context_callback is not None:await refresh_context_callback()
            if not self._initial_current():
                self.initial_load_status='stopped';return None
            result=await self.fetch_candidates(is_current=self._initial_current)
            if result is None:
                self.initial_load_status='stopped';return None
            self._initial_finished=True
            self.initial_load_status='failed' if result.nothing_and_failed else 'complete'
            return result
        except Exception:
            self._initial_finished=True
            self.initial_load_status='failed'
            raise

    async def fetch_candidates(self, *, exclude_ids=(), recent_author_ids=(), is_current=None):
        # Multiple active views share one accepted source acquisition. Cancelling
        # a waiter cannot cancel the FIFO worker or start another concurrent GET.
        claim=is_current if is_current is not None else lambda:True
        with self._lifecycle_lock:
            if self._close_future is not None:raise RuntimeError('learning service is closed')
            if self._candidate_fetch_future is None or self._candidate_fetch_future.done():
                self._candidate_fetch_claims=[claim]
                self._candidate_fetch_future=self._executor.submit(self._fetch_claimed,tuple(exclude_ids),tuple(recent_author_ids))
            else:
                self._candidate_fetch_claims.append(claim)
            future=self._candidate_fetch_future
        future.add_done_callback(self._release_fetch_claims)
        return await self._wait(future)

    def _release_fetch_claims(self,future):
        with self._lifecycle_lock:
            if self._candidate_fetch_future is future:
                self._candidate_fetch_claims=[]

    def _fetch_claimed(self,exclude_ids,recent_author_ids):
        if not self._fetch_has_claim():return None
        return self._fetch_candidates(exclude_ids,recent_author_ids,is_current=self._fetch_has_claim)

    def _fetch_has_claim(self):
        with self._lifecycle_lock:claims=tuple(self._candidate_fetch_claims)
        return any(claim() for claim in claims)

    async def refill_snapshot(self):
        """Local, fresh candidate/history snapshot; never opens a new UI session."""
        def read():
            db=self._database()
            saved=self.saved_ids() if callable(self.saved_ids) else self.saved_ids
            excluded=set(str(x) for x in saved)|self._seed_saved|set(db.get_judged_work_ids())|set(db.get_hidden_work_ids())
            hidden=set(db.get_hidden_author_ids())
            ordered,frames=discovery_ranking.rank_candidates(
                [c for wid,c in self._candidates.items() if wid not in excluded and c.get('author_id') not in hidden],
                db.get_discovery_preferences(),db.get_discovery_author_preferences(),
                get_setting=db.get_setting,normalize=normalize_tag,total_works=self._population_size(saved))
            records=tuple(self._record_for(c) for c in ordered)
            all_records=tuple(self._record_for(c) for c in self._candidates.values())
            return DiscoveryBatch(tuple(self._artwork_for(c) for c in ordered),records,frames,(),(),
                {c['work_id']:self._description_for(c) for c in ordered}),all_records
        return await self._wait(self._submit(read))

    def _fetch_candidates(self, exclude_ids, recent_author_ids, *, is_current=None):
        db = self._database()
        self.reader.require_identity()
        following = self.following() if callable(self.following) else self.following
        saved = self.saved_ids() if callable(self.saved_ids) else self.saved_ids
        saved = frozenset(str(x) for x in saved) | self._seed_saved
        # Only an unknown source uses the imported legacy rows. An explicitly
        # acquired empty snapshot is authoritative and stays empty.
        following = (tuple(db.get_following_users(sort_by="follow_order", sort_order="asc"))
                     if following is None else tuple(following))
        facade = _SourceDatabase(db, following, saved | self._seed_known)
        judged = db.get_judged_work_ids()
        hidden_authors = set(db.get_hidden_author_ids())
        excluded = set(exclude_ids) | saved | self._seed_known | set(judged) | set(db.get_hidden_work_ids())
        pending = [c for wid,c in self._candidates.items() if wid not in excluded and c.get('author_id') not in hidden_authors]
        result = discovery_engine.get_next_batch(n=30, sources=SOURCES,
                    exclude_ids=excluded | {c['work_id'] for c in pending}, db=facade, client=_Reader(self.reader,is_current))
        valid, rejected = [], False
        for candidate in result.items:
            if candidate.get('author_id') in hidden_authors: continue
            try: self._record_for(candidate)
            except (ValueError, TypeError): rejected = True; continue
            valid.append(candidate)
        # Persist metadata before a UI can accept a judgment. Never create a works
        # row: acquiring/queuing an image is not proof of a managed saved receipt.
        with db._transaction() as conn:
            for candidate in valid:
                conn.execute('INSERT INTO managed_discovery_candidate VALUES (?,?) ON CONFLICT(work_id) DO NOTHING',
                    (candidate['work_id'], json.dumps(candidate,ensure_ascii=False,sort_keys=True)))
        for candidate in valid:
            if candidate['work_id'] not in self._candidates: self._accept_metadata(candidate)
        unique = {c['work_id']:c for c in pending+valid}
        ordered, frames = discovery_ranking.rank_candidates(list(unique.values()),
             db.get_discovery_preferences(), db.get_discovery_author_preferences(),
             get_setting=db.get_setting, normalize=normalize_tag, total_works=self._population_size(saved),
             recent_author_ids=recent_author_ids)
        records = tuple(self._record_for(c) for c in ordered)
        return DiscoveryBatch(tuple(self._artwork_for(c) for c in ordered), records, frames,
                     tuple(result.ok), tuple(result.failed) + (('invalid_metadata',) if rejected else ()),
                     {c['work_id']:self._description_for(c) for c in ordered})

    async def description_snapshot(self):
        """Re-read cached candidate text only; never asks the network reader."""
        def read():
            self._database()
            return {wid:self._description_for(candidate)
                    for wid,candidate in self._candidates.items()}
        return await self._wait(self._submit(read))

    def candidate_records(self):
        return self._submit(lambda: tuple(self._record_for(c) for c in self._candidates.values())).result()

    async def trend_snapshot(self):
        return await self._wait(self._submit(self._trend_snapshot))

    def _trend_snapshot(self):
        db = self._database()
        count = len(db.get_judged_work_ids())
        saved = self.saved_ids() if callable(self.saved_ids) else self.saved_ids
        rows = discovery_ranking.trend_rows(db.get_discovery_preferences(),total_works=self._population_size(saved),
                  get_setting=db.get_setting) if count >= TREND_MIN_JUDGMENTS else []
        return {'judged':count,'rows':tuple(rows),'ready':count >= TREND_MIN_JUDGMENTS}

    def trend_rows(self):
        """Synchronous recommendation worker API, never call from this worker."""
        return self._submit(self._trend_snapshot).result()['rows']

    def _open_session(self):
        snapshot = super()._open_session()
        db = self._database()
        saved = self.saved_ids() if callable(self.saved_ids) else self.saved_ids
        saved = frozenset(str(x) for x in saved) | self._seed_saved
        hidden_authors = set(db.get_hidden_author_ids())
        hidden_works = set(db.get_hidden_work_ids())
        ordered, self.frames = discovery_ranking.rank_candidates(
            [c for wid,c in self._candidates.items() if wid not in snapshot.judged_ids and wid not in saved and wid not in hidden_works and c.get('author_id') not in hidden_authors],
            db.get_discovery_preferences(), db.get_discovery_author_preferences(),
            get_setting=db.get_setting, normalize=normalize_tag, total_works=self._population_size(saved))
        remainder = [c for c in self._candidates.values() if c['work_id'] in snapshot.judged_ids]
        self.artworks = tuple(self._record_for(c).as_artwork() for c in ordered + remainder)
        return snapshot

    def _review_snapshot(self, session_id):
        result = self._session(session_id).repository.review_snapshot()
        saved = self.saved_ids() if callable(self.saved_ids) else self.saved_ids
        saved = frozenset(str(x) for x in saved) | self._seed_saved
        for row in result['rows']:
            candidate = self._candidates.get(row['work_id']) or self._legacy_history.get(row['work_id'])
            if candidate is None: raise ValueError('unknown discovery history identity')
            row['title'] = candidate.get('title') or row['work_id']
            # Projection from the caller's verified managed-receipt inventory;
            # never mutate a legacy works row or treat queue acceptance as saved.
            row['work_meta'] = ({'legacy_saved_snapshot':True} if row['work_id'] in self._seed_saved else {'verified_managed_receipt':True}) if row['work_id'] in saved else None
            row['is_deleted'] = False
        return result

    def recommendation_snapshot(self):
        def read():
            db = self._database()
            with db.lock:
                rows = db.conn.execute('SELECT work_id,verdict,recommend_dismissed FROM discovery_swipe_history ORDER BY judged_at DESC, work_id').fetchall()
            return {'works':tuple(deepcopy(c) for c in (self._legacy_history | self._candidates).values()),
                    'search_mode':(db.get_setting('discovery_search_mode','all') or 'all'),
                    'hated_ids':frozenset(r['work_id'] for r in rows if r['verdict']=='hate'),
                    'author_seed_bookmarked':{aid:counts['seed_bookmarked'] for aid,counts in db.get_discovery_author_preferences().items()},
                    'judged_ids':frozenset(r['work_id'] for r in rows),
                    'superlike_ids':frozenset(r['work_id'] for r in rows if r['verdict']=='superLike' and not r['recommend_dismissed']),
                    'superlike_order':tuple(r['work_id'] for r in rows if r['verdict']=='superLike' and not r['recommend_dismissed'])}
        return self._submit(read).result()

    async def rank_pending(self, work_ids, recent_author_ids=()):
        def rank():
            db=self._database()
            saved=self.saved_ids() if callable(self.saved_ids) else self.saved_ids
            ordered,frames=discovery_ranking.rank_candidates(
                [self._candidates[wid] for wid in work_ids],db.get_discovery_preferences(),
                db.get_discovery_author_preferences(),get_setting=db.get_setting,
                normalize=normalize_tag,total_works=self._population_size(saved),recent_author_ids=tuple(recent_author_ids))
            return tuple(c['work_id'] for c in ordered),frames
        return await self._wait(self._submit(rank))

    def _population_size(self, saved):
        return self._seed_population_size + len(set(str(x) for x in saved) - self._seed_known)

    async def resolve_history_candidate(self, work_id):
        def resolve():
            db=self._database()
            if work_id in self._candidates:return
            if work_id not in self._legacy_history:raise ValueError('unknown history work')
            raw=self.reader.get_discovery_work(work_id)
            if str(raw.get('id'))!=work_id:raise ValueError('history metadata identity mismatch')
            candidate={'work_id':work_id,'title':raw.get('title'),'author_id':raw.get('user_id'),
                'author_name':raw.get('user_name'),'thumbnail_url':raw.get('thumbnail_url'),
                'tags':raw.get('tags'),'source':self._legacy_history[work_id]['source']}
            # This detail GET already occurs for the explicit history
            # resolution. Preserve its description here instead of issuing a
            # second request from the summary button.
            if raw.get('description') is not None:
                candidate['description']=self._description_for(raw)
            self._record_for(candidate)
            with db._transaction() as conn:
                conn.execute('INSERT INTO managed_discovery_candidate VALUES (?,?) ON CONFLICT(work_id) DO NOTHING',
                    (work_id,json.dumps(candidate,ensure_ascii=False,sort_keys=True)))
            self._accept_metadata(candidate)
        return await self._wait(self._submit(resolve))

    async def settings_snapshot(self):
        from .discovery_settings import read_settings
        return await self._wait(self._submit(lambda:read_settings(self._database())))

    def submit_settings_update(self,expected_revision,values,operation_id):
        from .discovery_settings import update_settings
        accepted=deepcopy(dict(values))
        return self._submit(lambda:update_settings(self._database(),expected_revision,accepted,operation_id))

    def search_mode(self):
        def read():
            value=self._database().get_setting('discovery_search_mode','all') or 'all'
            return value if value in ('all','safe','r18') else 'all'
        return self._submit(read).result()
