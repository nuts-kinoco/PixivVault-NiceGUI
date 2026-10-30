"""One-time read-only legacy discovery fork into an empty managed sidecar.

Only the named discovery/count/cache tables are copied, never authentication,
file paths, models, jobs or a writable source connection. Copy uses one target
transaction; a completed seed is immutable and is never re-applied on restart.
"""
from pathlib import Path
import hashlib
import json
import sqlite3

TABLES = ('discovery_preference','discovery_author_preference','discovery_swipe_history',
          'discovery_view_history','discovery_population','discovery_population_meta',
          'discovery_population_seen','discovery_hidden_works','discovery_hidden_authors',
          'discovery_boosted_authors','following_users','bookmark_remote_cache','superlike_info_cache')

def _digest(path):
    with path.open('rb') as handle:return hashlib.file_digest(handle,'sha256').hexdigest()

def read_seed(path, target):
    path=Path(path)
    if (not path.is_absolute() or path.resolve(strict=True)!=path or not path.is_file()
            or path.is_symlink() or path.is_junction() or path.stat().st_nlink!=1):
        raise ValueError('standalone regular legacy snapshot required')
    if any(Path(str(path)+suffix).exists() for suffix in ('-wal','-shm','-journal')):
        raise ValueError('legacy snapshot must be closed and standalone')
    before=_digest(path)
    conn=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True)
    conn.row_factory=sqlite3.Row
    try:
        conn.execute('PRAGMA query_only=ON')
        if conn.execute('PRAGMA quick_check').fetchone()[0]!='ok':
            raise ValueError('invalid legacy snapshot integrity')
        tables={}
        for name in TABLES:
            kind=conn.execute('SELECT type FROM sqlite_master WHERE name=?',(name,)).fetchone()
            if kind is None or kind[0]!='table':raise ValueError('legacy discovery table unavailable: '+name)
            target_columns=tuple(r['name'] for r in target.conn.execute(f'PRAGMA table_info("{name}")'))
            source_columns={r['name'] for r in conn.execute(f'PRAGMA table_info("{name}")')}
            if not target_columns or not set(target_columns)<=source_columns:
                raise ValueError('legacy discovery schema mismatch: '+name)
            columns=','.join('"'+c+'"' for c in target_columns)
            rows=[tuple(r) for r in conn.execute(f'SELECT {columns} FROM "{name}"')]
            if name in ('discovery_preference','discovery_author_preference'):
                for values in rows:
                    for column,value in zip(target_columns,values):
                        if (column.endswith('_count') or column.startswith('seed_')) and value is not None and (type(value) is not int or value<0):
                            raise ValueError('invalid legacy discovery counter')
            tables[name]=(target_columns,rows)
        required={'work_id','title','user_id','content_type','work_meta','is_deleted'}
        if not required<={r['name'] for r in conn.execute('PRAGMA table_info(works)')}:
            raise ValueError('legacy work metadata schema mismatch')
        known=tuple(str(r[0]) for r in conn.execute("SELECT work_id FROM works WHERE content_type<>'novel'"))
        population_size=conn.execute('SELECT COUNT(*) FROM works WHERE is_deleted=0').fetchone()[0]
        saved=tuple(str(r[0]) for r in conn.execute("SELECT work_id FROM works WHERE is_deleted=0 AND work_meta IS NOT NULL AND work_meta<>'' AND content_type<>'novel'"))
        history=[]
        for row in conn.execute("""SELECT h.*, COALESCE(w.title,c.title,h.work_id) AS title,
                COALESCE(h.author_id,w.user_id,c.user_id) AS resolved_author,
                COALESCE(c.user_name,a.display_name,'') AS author_name,
                COALESCE(h.thumbnail_url,c.thumbnail_url) AS resolved_thumbnail
                FROM discovery_swipe_history h LEFT JOIN works w ON w.work_id=h.work_id
                LEFT JOIN superlike_info_cache c ON c.work_id=h.work_id
                LEFT JOIN discovery_author_preference a ON a.author_id=h.author_id"""):
            tags=json.loads(row['tags'] or '[]')
            if not isinstance(tags,list) or any(not isinstance(t,str) for t in tags):
                raise ValueError('invalid legacy judgment tags')
            if row['verdict'] not in ('like','superLike','skip','hate'):
                raise ValueError('invalid legacy judgment verdict')
            history.append({'work_id':str(row['work_id']),'title':row['title'],
                  'author_id':row['resolved_author'],'author_name':row['author_name'],
                  'thumbnail_url':row['resolved_thumbnail'],'tags':tags,'source':row['source'] or 'legacy'})
        settings=[tuple(r) for r in conn.execute("SELECT key,value FROM settings WHERE substr(key,1,10)='discovery_'")]
    finally:conn.close()
    if before!=_digest(path) or any(Path(str(path)+s).exists() for s in ('-wal','-shm','-journal')):
        raise ValueError('legacy snapshot changed while reading')
    return {'path':str(path),'sha256':before,'tables':tables,'history':history,'saved_ids':saved,'known_ids':known,'population_size':population_size,'settings':settings}

def seed_once(db, source):
    with db._transaction() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS managed_discovery_seed (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')
        conn.execute('CREATE TABLE IF NOT EXISTS managed_discovery_legacy_history (work_id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
    existing=db.conn.execute('SELECT payload FROM managed_discovery_seed WHERE id=1').fetchone()
    if existing:
        marker=json.loads(existing[0])
        if source is not None and str(Path(source)).casefold()!=marker['source_path'].casefold():
            raise ValueError('managed discovery was already seeded from another source')
        return marker
    if source is None:return None
    settings_used=db.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='managed_discovery_setting_receipt'").fetchone()
    if settings_used and db.conn.execute('SELECT 1 FROM managed_discovery_setting_receipt LIMIT 1').fetchone():
        raise ValueError('legacy seed requires an unused discovery ledger')
    # A seed may never overwrite/rebase an already-used managed ledger.
    if any(db.conn.execute(f'SELECT 1 FROM "{name}" LIMIT 1').fetchone() for name in TABLES):
        raise ValueError('legacy seed requires a new empty discovery ledger')
    if db.conn.execute('SELECT 1 FROM managed_discovery_candidate LIMIT 1').fetchone():
        raise ValueError('legacy seed requires an empty candidate ledger')
    packet=read_seed(source,db)
    marker={'format':'pixivvault-discovery-seed-v1','source_path':packet['path'],
            'source_sha256':packet['sha256'],'saved_ids':packet['saved_ids'],
            'known_ids':packet['known_ids'],'population_size':packet['population_size'],
            'row_counts':{name:len(rows) for name,(_,rows) in packet['tables'].items()}}
    with db._transaction() as conn:
        for name,(columns,rows) in packet['tables'].items():
            names=','.join('"'+c+'"' for c in columns)
            conn.executemany(f'INSERT INTO "{name}" ({names}) VALUES ({",".join("?" for _ in columns)})',rows)
        conn.execute('CREATE TABLE IF NOT EXISTS learning_adapter_revision (work_id TEXT PRIMARY KEY, revision INTEGER NOT NULL)')
        conn.execute('INSERT INTO learning_adapter_revision SELECT work_id,1 FROM discovery_swipe_history')
        conn.executemany('INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',packet['settings'])
        conn.executemany('INSERT INTO managed_discovery_legacy_history VALUES (?,?)',
            [(c['work_id'],json.dumps(c,ensure_ascii=False,sort_keys=True)) for c in packet['history']])
        conn.execute('INSERT INTO managed_discovery_seed VALUES (1,?)',(json.dumps(marker,sort_keys=True),))
    return marker
