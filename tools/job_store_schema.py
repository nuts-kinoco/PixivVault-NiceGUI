"""Independent read-only JobService v1/v2 native-checker schema contract."""
import json,re
APPLICATION_ID=0x50564A31
SCHEMA_VERSION=2


def _unique(pairs):
    value={}
    for key,item in pairs:
        if key in value:raise ValueError('duplicate job key')
        value[key]=item
    return value


def _job_schema(connection):
    version=connection.execute("PRAGMA user_version").fetchone()[0]
    if connection.execute("PRAGMA application_id").fetchone()[0]!=APPLICATION_ID or version not in (1,SCHEMA_VERSION):
        raise ValueError("managed database identity mismatch")
    expected={
        'jobs':[('job_id','TEXT',0,1),('ordinal','INTEGER',1,0),('payload','TEXT',1,0)],
        'operations':[('operation_id','TEXT',0,1),('job_id','TEXT',1,0),('fingerprint','TEXT',1,0)]}
    if version==2:expected['job_history']=[('job_id','TEXT',0,1),('payload','TEXT',1,0)]
    tables={row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if tables!=set(expected):raise ValueError("managed database schema mismatch")
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type IN ('view','trigger')").fetchone():
        raise ValueError("managed database schema mismatch")
    for table,columns in expected.items():
        actual=[(row[1],row[2].upper(),row[3],row[5]) for row in connection.execute('PRAGMA table_info('+table+')')]
        if actual!=columns:raise ValueError("managed database column mismatch")
    return version


def _job_payload(payload,job_id):
    if type(payload) is not str or len(payload)>8192:raise ValueError('invalid job payload')
    job=json.loads(payload,object_pairs_hook=_unique)
    keys={'job_id','kind','target_id','title','state','done','total','revision','attempt','error_code','result','paused_from','control_revision'}
    if type(job) is not dict or set(job)!=keys or type(job_id) is not str or job['job_id']!=job_id or re.fullmatch(r'[a-f0-9]{32}',job_id) is None:
        raise ValueError('invalid job state')
    for key,limit,empty in [('kind',32,False),('target_id',128,False),('title',256,True),('error_code',128,True)]:
        value=job[key]
        if type(value) is not str or len(value)>limit or (not empty and not value) or any(ord(c)<32 or ord(c)==127 for c in value):
            raise ValueError('invalid job field')
    if (type(job['state']) is not str or job['state'] not in {'queued','running','paused','stopping','stopped','done','error'}
        or any(type(job[k]) is not int or job[k]<1 for k in ('attempt','revision','control_revision'))
        or type(job['done']) is not int or type(job['total']) is not int or not 0<=job['done']<=job['total']
        or job['paused_from'] not in (None,'queued','running')):raise ValueError('invalid job state')
    value=job['result']
    if value is not None and (type(value) is not str or not 0<len(value)<=2048 or any(ord(c)<32 or ord(c)==127 for c in value)):
        raise ValueError('invalid job result')
    return job



def _job_bindings(connection,records):
    """Validate every operation row, not the existence of one good alias.

    Operation strings follow JobService._plain(maximum=128); there is no UUID
    or ASCII-only requirement. A missing v1 retired snapshot is not inferred.
    This read-only checker does not replay, repair or migrate that ledger.
    """
    rows=[]
    for operation,jid,raw in connection.execute('SELECT operation_id,job_id,fingerprint FROM operations ORDER BY operation_id'):
        if (type(operation) is not str or not 0<len(operation)<=128
                or any(ord(c)<32 or ord(c)==127 for c in operation)
                or type(jid) is not str or jid not in records or type(raw) is not str):
            raise ValueError('unverifiable job operation binding')
        fingerprint=json.loads(raw,object_pairs_hook=_unique)
        job=records[jid]
        if fingerprint!=[job[k] for k in ('kind','target_id','title')]:
            raise ValueError('job operation fingerprint mismatch')
        rows.append((operation,jid,raw))
    return tuple(rows)
