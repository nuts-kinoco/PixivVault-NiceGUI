"""Encrypted, conservative request reservations; no server quota is invented."""
import hashlib
import json
import os
import threading
import time
from uuid import uuid4
from .external_vault import VaultError
from .managed_downloads import _safe_path,_read_bounded,_write,_fsync_dir
from .fs_paths import native_io_path

class PremiumRequestLimit(VaultError):
    def __init__(self,code,retry_at):super().__init__(code);self.retry_at=retry_at

class PremiumRequestBudget:
    # Original nana7mi_vault.acquire_nana7mi_slot / pixiv_client._request:
    # global minimum interval 0.3s. 200/min is a LOCAL upper bound implied by
    # that cadence, not a claim about Pixiv's undocumented account quota.
    def __init__(self,vault,*,payout_limit=10,payout_window=18000,borrow_limit=200,
                 borrow_window=60,min_interval=.3,sleep=time.sleep):
        self.vault=vault;self.path=vault.root/'premium-request-budget.enc'
        self.payout_limit=payout_limit;self.payout_window=payout_window
        self.borrow_limit=borrow_limit;self.borrow_window=borrow_window
        self.min_interval=min_interval;self.sleep=sleep;self._lock=threading.RLock()
    def _load(self):
        _safe_path(self.path,file=True)
        if not native_io_path(self.path).exists():return {'events':{},'cooldowns':{},'last_at':0}
        try:
            state=self.vault.crypto.decrypt(_read_bounded(self.path,262144),self.vault._key)
            if set(state)!={'events','cooldowns','last_at'} or type(state['events']) is not dict or type(state['cooldowns']) is not dict:raise ValueError()
            if len(state['events'])>128 or len(state['cooldowns'])>128:raise ValueError()
            for name,values in state['events'].items():
                if type(name) is not str or type(values) is not list or len(values)>max(200,self.borrow_limit,self.payout_limit):raise ValueError()
                if any(type(x) not in (float,int) or not 0<=x<1e12 for x in values):raise ValueError()
            if any(type(x) not in (float,int) or not 0<=x<1e12 for x in [state['last_at'],*state['cooldowns'].values()]):raise ValueError()
            return state
        except Exception:raise VaultError('premium-budget-unavailable') from None
    def _save(self,state):
        temporary=self.vault.root/('premium-budget-next-'+uuid4().hex)
        try:
            _write(temporary,self.vault.crypto.encrypt(state,self.vault._key))
            _safe_path(self.path,file=True)
            os.replace(native_io_path(temporary),native_io_path(self.path));_fsync_dir(self.vault.root)
        except Exception:raise VaultError('premium-budget-unavailable') from None
        finally:
            if native_io_path(temporary).exists():native_io_path(temporary).unlink()
    @staticmethod
    def account(slot):
        uid=slot.get('account_uid')
        return 'account:'+str(int(uid)) if type(uid) in (int,float) and uid>0 else 'account:unknown'
    def _trim(self,state,now):
        state['events']={k:[x for x in values if x>now-(self.payout_window if k.startswith('payout:') else self.borrow_window)]
                         for k,values in state['events'].items()}
        state['events']={k:v for k,v in state['events'].items() if v}
        state['cooldowns']={k:v for k,v in state['cooldowns'].items() if v>now}
    def blocked_until(self,slot):
        with self._lock:
            state=self._load();now=self.vault.clock();self._trim(state,now)
            account=self.account(slot);events=state['events'].get(account,[])
            limit=events[0]+self.borrow_window if len(events)>=self.borrow_limit else 0
            return max(state['cooldowns'].get(account,0),limit)
    def reserve(self,kind,owner):
        self.vault._require_premium()
        with self._lock:
            state=self._load();now=self.vault.clock();self._trim(state,now)
            if kind=='payout':name='payout:'+str(owner);limit=self.payout_limit;window=self.payout_window
            elif kind=='borrow':
                name=owner;limit=self.borrow_limit;window=self.borrow_window
                gap=state['last_at']+self.min_interval-now
                if gap>0:
                    self.sleep(min(self.min_interval,gap));self.vault._require_premium();now=self.vault.clock();self._trim(state,now)
            else:raise VaultError('premium-budget-unavailable')
            events=state['events'].setdefault(name,[])
            until=max(state['cooldowns'].get(name,0),events[0]+window if len(events)>=limit else 0)
            if until>now:raise PremiumRequestLimit('payout-limit' if kind=='payout' else 'account-cooldown',until)
            events.append(now)
            if kind=='borrow':state['last_at']=now
            self._save(state)
    def cooldown(self,slot,until):
        with self._lock:
            state=self._load();now=self.vault.clock();self._trim(state,now)
            key=self.account(slot);state['cooldowns'][key]=max(now,until,state['cooldowns'].get(key,0));self._save(state)
    def counts(self,uid,slot):
        with self._lock:
            state=self._load();self._trim(state,self.vault.clock())
            return len(state['events'].get('payout:'+str(uid),[])),len(state['events'].get(self.account(slot),[]))
    def summary(self,data):
        with self._lock:
            state=self._load();now=self.vault.clock();self._trim(state,now)
            result=[]
            for index,uid in enumerate(data['shaft_uids']):
                slot=data['slots'][str(uid)];account=self.account(slot);events=state['events'].get(account,[])
                until=max(state['cooldowns'].get(account,0),events[0]+self.borrow_window if len(events)>=self.borrow_limit else 0,
                    slot.get('backoff_until',0),slot.get('payout_not_before',0))
                result.append(dict(index=index+1,payouts=len(state['events'].get('payout:'+str(uid),[])),
                    payout_limit=self.payout_limit,borrowed=len(events),borrow_limit=self.borrow_limit,wait=max(0,until-now)))
            return tuple(result)
