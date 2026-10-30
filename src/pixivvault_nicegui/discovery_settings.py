"""Original public discovery keys only; FIFO caller provides serialization."""
from dataclasses import dataclass
import json
import math
from .learning_repository import LearningConflict
from .discovery_ranking import resolve_ratios

KEYS=('discovery_search_mode','discovery_ratio_subject','discovery_ratio_style')
DEFAULTS={'discovery_search_mode':'all','discovery_ratio_subject':'50','discovery_ratio_style':'20'}

@dataclass(frozen=True)
class DiscoverySettings:
    revision:int
    values:dict
    effective_ratios:dict

def _schema(db):
    with db._transaction() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS managed_discovery_settings_revision (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL)')
        conn.execute('INSERT OR IGNORE INTO managed_discovery_settings_revision VALUES (1,0)')
        conn.execute('CREATE TABLE IF NOT EXISTS managed_discovery_setting_receipt (operation_id TEXT PRIMARY KEY, signature TEXT NOT NULL, result TEXT NOT NULL)')
        for verb,prefix in (('INSERT','NEW'),('UPDATE','NEW'),('DELETE','OLD')):
            conn.execute(f"""CREATE TRIGGER IF NOT EXISTS managed_discovery_settings_{verb.lower()}
                AFTER {verb} ON settings WHEN {prefix}.key IN ('discovery_search_mode','discovery_ratio_subject','discovery_ratio_style')
                BEGIN UPDATE managed_discovery_settings_revision SET revision=revision+1 WHERE id=1; END""")

def _validate(values):
    if not isinstance(values,dict) or set(values)!=set(KEYS):raise ValueError('unsupported discovery settings')
    if values[KEYS[0]] not in ('all','safe','r18'):raise ValueError('invalid discovery search mode')
    result={KEYS[0]:values[KEYS[0]]}
    for key in KEYS[1:]:
        if isinstance(values[key],bool):raise ValueError('invalid exploration ratio')
        try:value=float(values[key])
        except (ValueError,TypeError):raise ValueError('invalid exploration ratio') from None
        if not math.isfinite(value) or value<0 or value>100:raise ValueError('invalid exploration ratio')
        result[key]=str(value)
    return result

def _snapshot_locked(conn):
    values=dict(DEFAULTS)
    values.update({r['key']:r['value'] for r in conn.execute('SELECT key,value FROM settings') if r['key'] in KEYS})
    # Preserve legacy resolution rules, including a blank ratio default.
    mode=values[KEYS[0]] or 'all'
    if mode not in ('all','safe','r18'):mode='all'
    values[KEYS[0]]=mode
    for key in KEYS[1:]:
        try:value=float(values[key])
        except (ValueError,TypeError):value=float(DEFAULTS[key])
        if not math.isfinite(value):value=float(DEFAULTS[key])
        values[key]=str(min(100,max(0,value)))
    ratios=resolve_ratios(lambda key,default=None:values.get(key,default))
    revision=conn.execute('SELECT revision FROM managed_discovery_settings_revision WHERE id=1').fetchone()[0]
    return DiscoverySettings(revision,values,ratios)

def read_settings(db):
    _schema(db)
    with db._transaction() as conn:return _snapshot_locked(conn)

def update_settings(db,expected_revision,values,operation_id):
    if type(expected_revision) is not int or expected_revision<0:raise ValueError('invalid settings revision')
    if not isinstance(operation_id,str) or not operation_id:raise ValueError('invalid settings operation')
    values=_validate(values)
    signature=json.dumps([expected_revision,values],sort_keys=True,separators=(',',':'))
    _schema(db)
    with db._transaction() as conn:
        receipt=conn.execute('SELECT signature,result FROM managed_discovery_setting_receipt WHERE operation_id=?',(operation_id,)).fetchone()
        if receipt:
            if receipt['signature']!=signature:raise ValueError('settings operation already used')
            saved=json.loads(receipt['result']);return DiscoverySettings(**saved)
        current=_snapshot_locked(conn)
        if current.revision!=expected_revision:raise LearningConflict('discovery settings changed in another view')
        conn.executemany('INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',values.items())
        saved=_snapshot_locked(conn)
        result=json.dumps({'revision':saved.revision,'values':saved.values,'effective_ratios':saved.effective_ratios},sort_keys=True)
        conn.execute('INSERT INTO managed_discovery_setting_receipt VALUES (?,?,?)',(operation_id,signature,result))
        return saved
