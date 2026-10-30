"""Original saved recommendation sections with an explicit private preference overlay.

Frozen SQLite copies are read-only. Remote acquisition is explicit and injected.
Original references: recommend_tab.py 513-611, 962, 1515, 2273, 2794, 2967;
database.py 2471-2569. Hide preferences never become learning judgments.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
import json
import re
import sqlite3
from .failed_records_repository import immutable_copy_signature, MAX_TOTAL_UTF8_BYTES
from .failed_records_state import MAX_RECORDS
from .managed_downloads import _safe_path, _write, _read_bounded
from .ugoira_storage import _owner_lock

ORDER=('trend_added','trend','certain','boosted','superlike')
LABELS={'trend_added':'追加した傾向','trend':'好みの傾向','certain':'好みの傾向から',
        'boosted':'もっと見る登録中の作者の新着','superlike':'あなたがSuperLikeした作品'}
MARKER='.pixivvault-recommendations.json'
IDENTITY=b'{"application":"pixivvault-nicegui","purpose":"recommendation-preferences","version":1}\n'

class RecommendationError(ValueError):
    def __init__(self):super().__init__('recommendation_state_unavailable_or_changed')

def identifier(value):
    if type(value) is not str or re.fullmatch(r'[1-9][0-9]{0,19}',value) is None:raise RecommendationError()
    return value

def text(value):
    if value is None:return ''
    if type(value) is not str or len(value)>65536:raise RecommendationError()
    return value

@dataclass(frozen=True)
class RecommendationWork:
    work_id:str
    title:str=''
    author_id:str=''
    author_name:str=''
    thumbnail_url:str=''
    page_count:int|None=None
    illust_type:int|None=None
    tags:tuple[str,...]=()
    create_date:str=''
    x_restrict:int|None=None
    def __post_init__(self):
        identifier(self.work_id)
        if self.author_id:identifier(self.author_id)
        text(self.title);text(self.author_name)
        text(self.thumbnail_url);text(self.create_date)
        if self.thumbnail_url:
            from .bookmark_remote_adapter import thumbnail_url
            try:thumbnail_url(self.thumbnail_url)
            except ValueError:raise RecommendationError() from None
        if self.page_count is not None and (type(self.page_count) is not int or not 1<=self.page_count<=1000):
            raise RecommendationError()
        for value in (self.illust_type,self.x_restrict):
            if value is not None and (type(value) is not int or value not in (0,1,2)):
                raise RecommendationError()
        if type(self.tags) not in (tuple,list) or len(self.tags)>1000:raise RecommendationError()
        for value in self.tags:text(value)
        object.__setattr__(self,'tags',tuple(self.tags))

    @classmethod
    def from_record(cls,row,*,author_id='',author_name=''):
        """Keep supplied read metadata; a missing count/type stays unknown."""
        if isinstance(row,cls):return row
        if type(row) is not dict:raise RecommendationError()
        return cls(identifier(str(row.get('id',row.get('work_id','')))),
            text(row.get('title')),text(str(row.get('user_id') or row.get('author_id') or author_id or '')),
            text(row.get('user_name') or row.get('author_name') or author_name),
            text(row.get('thumbnail_url')),row.get('page_count'),
            row.get('illust_type',row.get('type')),row.get('tags') or (),
            text(row.get('create_date')),row.get('x_restrict'))

@dataclass(frozen=True)
class RecommendationGroup:
    key:str
    title:str
    works:tuple[RecommendationWork,...]
    page:int=1
    total:int=0
    exhausted:bool=False
    def __post_init__(self):
        text(self.key);text(self.title)
        if len({w.work_id for w in self.works})!=len(self.works):raise RecommendationError()

@dataclass(frozen=True)
class RecommendationSnapshot:
    sections:tuple[tuple[str,tuple[RecommendationGroup,...]],...]=()
    hidden_works:frozenset[str]=frozenset()
    hidden_authors:frozenset[str]=frozenset()
    order:tuple[str,...]=ORDER
    collapsed:frozenset[str]=frozenset()
    unavailable:tuple[str,...]=('trend','trend_added')
    boosted:tuple[tuple[str,str],...]=()
    judged:frozenset[str]=frozenset()
    added_tags:tuple[tuple[str,str],...]=()
    search_mode:str='all'


def _rows(conn,sql):
    result=[];size=0
    for row in conn.execute(sql):
        size+=sum(len(v.encode('utf-8')) for v in row if isinstance(v,str))
        if len(result)>=MAX_RECORDS or size>MAX_TOTAL_UTF8_BYTES:raise RecommendationError()
        result.append(row)
    return result

class FrozenRecommendationSource:
    """Explicit standalone legacy DB copy; no schema creation, migration, or GET."""
    def __init__(self,path):
        self.path=Path(path)
        _safe_path(self.path,file=True)
        self.signature=immutable_copy_signature(self.path)
    def load(self):
        if immutable_copy_signature(self.path)!=self.signature:raise RecommendationError()
        conn=sqlite3.connect(self.path.as_uri()+'?mode=ro&immutable=1',uri=True)
        try:
            conn.execute('PRAGMA query_only=ON')
            conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,MAX_TOTAL_UTF8_BYTES)
            conn.execute('BEGIN')
            tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            sections=[];unavailable=['trend','trend_added']
            if {'discovery_swipe_history','superlike_info_cache'}<=tables:
                columns={r[1] for r in conn.execute('PRAGMA table_info(superlike_info_cache)')}
                # Older standalone copies contain only the four text fields.
                # Reading optional columns must not migrate or write that copy.
                optional=','.join('c.'+name if name in columns else 'NULL'
                                  for name in ('thumbnail_url','page_count','illust_type'))
                rows=_rows(conn,"SELECT h.work_id,c.title,c.user_id,c.user_name,"+optional+" FROM discovery_swipe_history h LEFT JOIN superlike_info_cache c ON c.work_id=h.work_id WHERE h.verdict='superLike' AND (h.recommend_dismissed IS NULL OR h.recommend_dismissed=0) ORDER BY h.judged_at DESC")
                works=tuple(RecommendationWork(identifier(r[0]),text(r[1]),text(r[2]),text(r[3]),
                    thumbnail_url=text(r[4]),page_count=r[5],illust_type=r[6]) for r in rows)
                sections.append(('superlike',(RecommendationGroup('superlike',LABELS['superlike'],works),)))
            else:unavailable.append('superlike')
            for kind in ('certain','boosted'):
                rows=(_rows(conn,"SELECT payload_json FROM recommend_section_carryover WHERE section='"+kind+"'") if 'recommend_section_carryover' in tables else [])
                if not rows:unavailable.append(kind);continue
                payload=json.loads(rows[0][0]);groups=[]
                if type(payload) is not list:raise RecommendationError()
                for group in payload:
                    works=[];seen=set()
                    for row in group['works']:
                        wid=identifier(str(row.get('id',row.get('work_id',''))))
                        if wid in seen:continue
                        seen.add(wid)
                        works.append(RecommendationWork.from_record(row,
                            author_id=group.get('author_id') or '',
                            author_name=group.get('display_name') or ''))
                    key=text(group.get('tag') if kind=='certain' else group.get('author_id'))
                    title=text(group.get('display') if kind=='certain' else group.get('display_name'))
                    groups.append(RecommendationGroup(key,title,tuple(works),int(group.get('page',1)),int(group.get('total',len(works))),bool(group.get('exhausted',False))))
                sections.append((kind,tuple(groups)))
            hidden_work=(frozenset(identifier(r[0]) for r in _rows(conn,'SELECT work_id FROM discovery_hidden_works')) if 'discovery_hidden_works' in tables else frozenset())
            hidden_author=(frozenset(identifier(r[0]) for r in _rows(conn,'SELECT author_id FROM discovery_hidden_authors')) if 'discovery_hidden_authors' in tables else frozenset())
            settings=dict(_rows(conn,"SELECT key,value FROM settings WHERE key IN ('recommend_section_order','recommend_section_collapsed','recommend_trend_added_tags','discovery_search_mode')")) if 'settings' in tables else {}
            order=ORDER;collapsed=frozenset()
            try:
                candidate=json.loads(settings.get('recommend_section_order','null'))
                if type(candidate) is list and len(candidate)==len(ORDER) and set(candidate)==set(ORDER):order=tuple(candidate)
                elif type(candidate) is list and len(candidate)==len(ORDER)-1 and set(candidate)==set(ORDER)-{'certain'}:order=tuple(candidate)+('certain',)
                candidate=json.loads(settings.get('recommend_section_collapsed','[]'))
                if type(candidate) is list:collapsed=frozenset(x for x in candidate if x in ORDER)
            except (ValueError,TypeError):pass
            boosted=tuple((identifier(r[0]),text(r[1])) for r in _rows(conn,'SELECT author_id,display_name FROM discovery_boosted_authors ORDER BY boosted_at DESC')) if 'discovery_boosted_authors' in tables else ()
            judged=frozenset(identifier(r[0]) for r in _rows(conn,'SELECT work_id FROM discovery_swipe_history')) if 'discovery_swipe_history' in tables else frozenset()
            added=json.loads(settings.get('recommend_trend_added_tags','[]'))
            if type(added) is not list:raise RecommendationError()
            added=tuple((text(r['tag']),text(r.get('display') or r['tag'])) for r in added if type(r) is dict and r.get('tag'))
            mode=settings.get('discovery_search_mode','all')
            if mode not in ('all','safe','r18'):mode='all'
            result=RecommendationSnapshot(tuple(sections),hidden_work,hidden_author,order,collapsed,tuple(unavailable),boosted,judged,added,mode)
        finally:conn.close()
        if immutable_copy_signature(self.path)!=self.signature:raise RecommendationError()
        return result

    def trend_rows(self):
        """Exact original trend calculation over the immutable legacy counts only."""
        from .discovery_ranking import trend_rows, GENERIC_EXTRA_SETTING_KEY
        if immutable_copy_signature(self.path)!=self.signature:raise RecommendationError()
        conn=sqlite3.connect(self.path.as_uri()+'?mode=ro&immutable=1',uri=True)
        try:
            conn.execute('PRAGMA query_only=ON')
            conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,MAX_TOTAL_UTF8_BYTES)
            conn.execute('BEGIN')
            tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'discovery_preference' not in tables:raise RecommendationError()
            columns=('superlike_count','like_count','skip_count','hate_count','shown_count',
                     'open_count','bookmark_count','seed_saved','seed_bookmarked')
            prefs={}
            for row in _rows(conn,'SELECT tag,display_tag,'+','.join(columns)+' FROM discovery_preference'):
                counts={k:(v or 0) for k,v in zip(columns,row[2:])}
                counts['display_tag']=row[1] or row[0];prefs[row[0]]=counts
            settings=dict(conn.execute('SELECT key,value FROM settings WHERE key=?',(GENERIC_EXTRA_SETTING_KEY,))) if 'settings' in tables else {}
            total=conn.execute('SELECT COUNT(*) FROM works').fetchone()[0] if 'works' in tables else 0
            result=tuple(trend_rows(prefs,total_works=total,get_setting=lambda k,d=None:settings.get(k,d)))
        finally:conn.close()
        if immutable_copy_signature(self.path)!=self.signature:raise RecommendationError()
        return result

class RecommendationPreferences:
    """Dedicated marked root; explicit overlay, revision CAS, one process owner."""
    def __init__(self,root):
        self.root=Path(root)
        if not self.root.is_absolute():raise RecommendationError()
        _safe_path(self.root)
        self.root.mkdir(exist_ok=True)
        if not any(self.root.iterdir()):_write(self.root/MARKER,IDENTITY)
        if _read_bounded(self.root/MARKER,4096)!=IDENTITY:raise RecommendationError()
        allowed={MARKER,'.writer.lock','preferences.sqlite3','preferences.sqlite3-journal'}
        if any(p.name not in allowed for p in self.root.iterdir()):raise RecommendationError()
        for p in self.root.iterdir():_safe_path(p,file=True)
        self._owner=_owner_lock(self.root);self._owner.__enter__()
        self._mutex=RLock();self.closed=False
        try:
            self.db=sqlite3.connect(self.root/'preferences.sqlite3',check_same_thread=False)
            self.db.execute('PRAGMA synchronous=FULL')
            with self.db:
                self.db.execute('CREATE TABLE IF NOT EXISTS identity(id INTEGER PRIMARY KEY,revision INTEGER NOT NULL)')
                self.db.execute('INSERT OR IGNORE INTO identity VALUES(1,0)')
                self.db.execute('CREATE TABLE IF NOT EXISTS overrides(kind TEXT NOT NULL,target TEXT NOT NULL,hidden INTEGER NOT NULL,reason TEXT,PRIMARY KEY(kind,target))')
                self.db.execute('CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        except BaseException:
            if hasattr(self,'db'):self.db.close()
            self._owner.__exit__(None,None,None);raise
    def snapshot(self):
        with self._mutex:
            if self.closed:raise RecommendationError()
            revision=self.db.execute('SELECT revision FROM identity WHERE id=1').fetchone()[0]
            overrides={(r[0],r[1]):bool(r[2]) for r in self.db.execute('SELECT kind,target,hidden FROM overrides')}
            settings=dict(self.db.execute('SELECT key,value FROM settings'))
            return revision,overrides,settings
    def apply(self,kind,target,value,*,expected_revision,reason=None):
        if kind in ('work','author'):
            identifier(target)
            if type(value) is not bool or reason not in (None,'genre','style'):raise RecommendationError()
        elif kind=='recommend_section_order':
            if len(value)!=len(ORDER) or set(value)!=set(ORDER):raise RecommendationError()
        elif kind=='recommend_section_collapsed':
            if not set(value)<=set(ORDER):raise RecommendationError()
        else:raise RecommendationError()
        with self._mutex:
            if self.closed:raise RecommendationError()
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                if self.db.execute('SELECT revision FROM identity WHERE id=1').fetchone()[0]!=expected_revision:raise RecommendationError()
                if kind in ('work','author'):
                    self.db.execute('INSERT OR REPLACE INTO overrides VALUES(?,?,?,?)',(kind,target,int(value),reason))
                else:self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(kind,json.dumps(list(value))))
                self.db.execute('UPDATE identity SET revision=revision+1 WHERE id=1')
    def store_acquisition(self,value,revision):
        # Internal normalized cache/registration envelope, never the original DB.
        encoded=json.dumps(value,ensure_ascii=False,allow_nan=False)
        if len(encoded.encode('utf-8'))>MAX_TOTAL_UTF8_BYTES:raise RecommendationError()
        with self._mutex:
            if self.closed:raise RecommendationError()
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                if self.db.execute('SELECT revision FROM identity WHERE id=1').fetchone()[0]!=revision:raise RecommendationError()
                self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('recommend_acquisition_state',encoded))
                self.db.execute('UPDATE identity SET revision=revision+1 WHERE id=1')
    def close(self):
        with self._mutex:
            if self.closed:return
            self.db.close();self.closed=True;self._owner.__exit__(None,None,None)

class RecommendationController:
    def __init__(self,source,preferences=None,*,acquisition=None,trend_provider=None,judged_provider=None):
        self.source=source;self.preferences=preferences;self._mutex=RLock()
        self._snapshot=None
        from .recommendation_acquisition import RecommendationAcquisition
        self.acquisition=RecommendationAcquisition(self,acquisition,trend_provider,judged_provider)
    def refresh(self):
        # Publish only a fully loaded snapshot; errors keep the last valid one.
        value=self.source.load()
        if not isinstance(value,RecommendationSnapshot):raise RecommendationError()
        with self._mutex:self._snapshot=value
        return self.snapshot()
    def snapshot(self):
        with self._mutex:
            source=self._snapshot
            if source is None:raise RecommendationError()
            revision,overrides,settings=(self.preferences.snapshot() if self.preferences else (None,{},{}))
            works=set(source.hidden_works);authors=set(source.hidden_authors)
            for (kind,target),hidden in overrides.items():
                values=works if kind=='work' else authors
                values.add(target) if hidden else values.discard(target)
            order=tuple(json.loads(settings['recommend_section_order'])) if 'recommend_section_order' in settings else source.order
            collapsed=frozenset(json.loads(settings['recommend_section_collapsed'])) if 'recommend_section_collapsed' in settings else source.collapsed
            groups={kind:tuple(RecommendationGroup(g.key,g.title,tuple(w for w in g.works if w.work_id not in works and w.author_id not in authors),g.page,g.total,g.exhausted) for g in rows) for kind,rows in source.sections}
            state=dict(revision=revision,sections=groups,order=order,collapsed=collapsed,hidden_works=tuple(sorted(works)),hidden_authors=tuple(sorted(authors)),unavailable=source.unavailable,writable=self.preferences is not None)
            return self.acquisition.decorate(state,source,settings)
    def hide(self,kind,target,hidden,revision,reason=None):
        if self.preferences is None:raise RecommendationError()
        self.preferences.apply(kind,target,hidden,expected_revision=revision,reason=reason)
        return self.snapshot()
    def configure(self,kind,value,revision):
        if self.preferences is None:raise RecommendationError()
        self.preferences.apply(kind,'',value,expected_revision=revision)
        return self.snapshot()
    def update(self,revision):return self.acquisition.update(revision)
    def add_trend(self,tag,display,revision):return self.acquisition.add_trend(tag,display,revision)
    def remove_trend(self,tag,revision):return self.acquisition.remove_trend(tag,revision)
    def next_page(self,kind,tag,revision):return self.acquisition.next_page(kind,tag,revision)
    def boost(self,author_id,name,enabled,revision):return self.acquisition.boost(author_id,name,enabled,revision)
    def more_author(self,author_id,limit,revision):return self.acquisition.more_author(author_id,limit,revision)
    def close(self):
        if self.preferences:self.preferences.close()


class EmptyRecommendationSource:
    """A newly configured product, with no invented saved recommendations."""
    def load(self):
        return RecommendationSnapshot(unavailable=('certain','boosted','superlike'))


class DiscoveryRecommendationSource:
    """Read only the explicit discovery sidecar's public recommendation projection."""
    def __init__(self,discovery):
        self.discovery=discovery
    def load(self):
        value=self.discovery.recommendation_snapshot()
        mode=value.get('search_mode','all')
        if mode not in ('all','safe','r18'):raise RecommendationError()
        candidates={str(row.get('work_id',row.get('id',''))):row for row in value['works']}
        selected=value.get('superlike_order',tuple(sorted(value['superlike_ids'])))
        works=[]
        for wid in selected:
            identifier(wid)
            row=candidates.get(wid,{})
            works.append(RecommendationWork.from_record(dict(row,id=wid,work_id=wid)))
        return RecommendationSnapshot(
            sections=(('superlike',(RecommendationGroup('superlike',LABELS['superlike'],tuple(works)),)),),
            unavailable=('certain','boosted'),
            judged=frozenset(identifier(str(v)) for v in value['judged_ids']), search_mode=mode)


class CombinedRecommendationSource:
    """Keep frozen legacy settings/carryover while overlaying explicit new judgments."""
    def __init__(self,legacy,current):
        self.legacy=legacy;self.current=current
    def load(self):
        old=self.legacy.load();new=self.current.load()
        sections=dict(old.sections)
        current=dict(new.sections).get('superlike',())
        previous=sections.get('superlike',())
        works=[w for g in current for w in g.works]
        seen={w.work_id for w in works}
        works.extend(w for g in previous for w in g.works if w.work_id not in new.judged and w.work_id not in seen)
        sections['superlike']=(RecommendationGroup('superlike',LABELS['superlike'],tuple(works)),)
        return RecommendationSnapshot(tuple(sections.items()),old.hidden_works,old.hidden_authors,
            old.order,old.collapsed,tuple(k for k in old.unavailable if k!='superlike'),
            old.boosted,old.judged|new.judged,old.added_tags,new.search_mode)
