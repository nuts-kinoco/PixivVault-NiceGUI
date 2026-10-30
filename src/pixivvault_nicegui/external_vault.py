"""Encrypted, explicitly owned Shaft sidecar and borrowed-account session.

Wire compatibility: original nana7mi_vault.py at 3ac3aa0. No main Cookie
client, automatic discovery, network on construction, or plaintext fallback.
"""
from __future__ import annotations
import base64
import copy
import hashlib
import hmac
import importlib.util
import json
import math
import os
from pathlib import Path
import threading
import time
import uuid
from .managed_downloads import _safe_path, _read_bounded, _write, _fsync_dir
from .ugoira_storage import _owner_lock

LIMIT = 2 * 1024 * 1024
VERIFY = b'nana7mi-vault-verify-v1'
ENCRYPT = b'nana7mi-vault-encrypt-v1'
TOKEN_FIELDS = ('access_token', 'refresh_token', 'expires_in', 'token_type')
MANAGED_FIELDS = ('backoff_until','last_fetch_at','last_refresh_attempt_at','consecutive_failures','reported_refresh_hash','account_uid','token_expires_at_ms','token_fetched_at')

class VaultError(ValueError):
    def __init__(self, code='unavailable'):
        self.code=code
        super().__init__('external vault: '+code)

class CryptoUnavailable(VaultError):
    def __init__(self):super().__init__('crypto-unavailable')

class LegacyVaultCrypto:
    """Original PBKDF2 + HKDF + AES-GCM format; imports remain optional."""
    @property
    def available(self):return importlib.util.find_spec('cryptography') is not None
    def derive(self,password,salt):
        if type(password) is not str or not password or type(salt) is not bytes or len(salt)<16:raise VaultError('invalid-password-input')
        stretched=hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),salt,600_000,32)
        prk=hmac.new(salt,stretched,hashlib.sha256).digest()
        return tuple(hmac.new(prk,info+b'\x01',hashlib.sha256).digest() for info in (VERIFY,ENCRYPT))
    def _device_key(self,binding=None):
        material=(str(uuid.getnode()).encode() if binding is None else binding)+b'PixivVault-nana7mi-autounlock-2026-08-22'
        prk=hmac.new(bytes(32),material,hashlib.sha256).digest()
        return hmac.new(prk,b'nana7mi-vault-autounlock-v1'+b'\x01',hashlib.sha256).digest()
    def wrap_key(self,key,*,binding=None):
        nonce=os.urandom(12);return nonce+self._aes(self._device_key(binding)).encrypt(nonce,key,None)
    def unwrap_key(self,wrapped,*,binding=None):
        if len(wrapped)!=60:raise VaultError('autounlock-failed')
        try:key=self._aes(self._device_key(binding)).decrypt(wrapped[:12],wrapped[12:],None)
        except Exception:raise VaultError('autounlock-failed') from None
        if len(key)!=32:raise VaultError('autounlock-failed')
        return key
    def _aes(self,key):
        try:from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError:raise CryptoUnavailable() from None
        return AESGCM(key)
    def encrypt(self,data,key):
        raw=json.dumps(data,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
        if len(raw)>LIMIT:raise VaultError('vault-too-large')
        nonce=os.urandom(12)
        return nonce+self._aes(key).encrypt(nonce,raw,None)
    def decrypt(self,data,key):
        if len(data)<28 or len(data)>LIMIT+28:raise VaultError('invalid-ciphertext')
        try:return json.loads(self._aes(key).decrypt(data[:12],data[12:],None).decode('utf-8'))
        except CryptoUnavailable:raise
        except Exception:raise VaultError('invalid-ciphertext') from None


def parse_uid_text(text):
    if type(text) is not str:raise VaultError('invalid-uid-list')
    values=[]
    for no,line in enumerate(text.lstrip('\ufeff').splitlines(),1):
        line=line.split('#',1)[0].strip()
        if not line:continue
        try:uid=int(line)
        except ValueError:raise VaultError('invalid-uid-line-'+str(no)) from None
        if uid<=0:raise VaultError('invalid-uid-line-'+str(no))
        if uid not in values:values.append(uid)
    if not values or len(values)>10:raise VaultError('uid-count')
    return values


def normalize_data(data):
    if type(data) is not dict:raise VaultError('invalid-vault')
    data=copy.deepcopy(data)
    secret=data.get('secret_key')
    if type(secret) is not str or not secret:raise VaultError('invalid-vault')
    uids=data.get('shaft_uids')
    if uids is None:uids=[data.get('shaft_uid')] if data.get('shaft_uid') else []
    if type(uids) is not list or not 1<=len(uids)<=10 or any(type(u) is not int or u<=0 for u in uids) or len(set(uids))!=len(uids):raise VaultError('invalid-vault')
    slots=data.get('slots',{})
    if type(slots) is not dict:raise VaultError('invalid-vault')
    output={'secret_key':secret,'shaft_uids':uids,'slots':{}}
    for uid in uids:
        raw=slots.get(str(uid),data if data.get('shaft_uid')==uid else {})
        if type(raw) is not dict:raise VaultError('invalid-vault')
        slot={k:raw[k] for k in TOKEN_FIELDS+MANAGED_FIELDS if k in raw}
        for key in ('access_token','refresh_token','token_type','reported_refresh_hash'):
            if key in slot and type(slot[key]) is not str:raise VaultError('invalid-vault')
        for key in ('expires_in','backoff_until','last_fetch_at','last_refresh_attempt_at','consecutive_failures','account_uid','token_expires_at_ms','token_fetched_at'):
            if key in slot and slot[key] is not None and (type(slot[key]) not in (int,float) or not math.isfinite(slot[key]) or slot[key]<0):raise VaultError('invalid-vault')
        slot.setdefault('consecutive_failures',0)
        output['slots'][str(uid)]=slot
    return output


def remaining(slot,now):
    expiry=slot.get('token_expires_at_ms')
    return None if not expiry else expiry/1000-now


def proactive_wait(slot,now):
    last=slot.get('last_fetch_at',0)
    return max(0,slot.get('backoff_until',0)-now,(last+2100-now) if last else 0)


def keeper_plan(data,now):
    eligible=[]
    for uid in data['shaft_uids']:
        slot=data['slots'][str(uid)]; left=remaining(slot,now)
        eligible.append((float('-inf') if left is None else left,slot.get('last_refresh_attempt_at',0),uid))
    _,_,uid=min(eligible)
    slot=data['slots'][str(uid)];left=remaining(slot,now)
    if left is not None and left>600:return uid,False,min(left-600,900)
    wait=proactive_wait(slot,now)
    return (uid,False,min(wait,900)) if wait else (uid,True,300)


class ExternalVaultService:
    """Owns a new sidecar, borrows transport, serializes mutation, fences lock.

    Immutable encrypted generations are committed by an atomic CURRENT pointer.
    A single-writer file lock covers the service lifetime. Missing crypto prevents
    any creation/import, while existing ciphertext can still be identified safely.
    """
    def __init__(self,root,*,transport=None,crypto=None,clock=time.time):
        self.root=Path(root).absolute();self.transport=transport;self.crypto=crypto or LegacyVaultCrypto();self.clock=clock
        self._mutex=threading.RLock();self._busy=threading.Lock();self._data=None;self._key=None;self._meta=None
        self._epoch=0;self._round=0;self._closed=False;self._revision=None;self._owner=None;self._pages={};self._keeper=None;self._keeper_stop=threading.Event()
        _safe_path(self.root)
        if self.root.exists() and any(self.root.iterdir()) and not (self.root/'EXTERNAL_VAULT_V1').is_file():raise VaultError('foreign-root')
        self.root.mkdir(parents=True,exist_ok=True)
        self._owner=_owner_lock(self.root,create=True);self._owner.__enter__()
        try:
            marker=self.root/'EXTERNAL_VAULT_V1'
            if not marker.exists():_write(marker,b'PixivVault external vault sidecar v1\n')
            elif _read_bounded(marker,128)!=b'PixivVault external vault sidecar v1\n':raise VaultError('foreign-root')
            generations=self.root/'generations';_safe_path(generations);generations.mkdir(exist_ok=True)
            self._revision=self._pointer()
        except Exception:self._owner.__exit__(None,None,None);self._owner=None;raise
    def _transport_unverified(self):
        # Injected synthetic transports without this explicitly exposed state
        # retain their existing contract. Production ShaftTransport owns the
        # sticky socket fence and publishes its status under an RLock.
        return bool(getattr(self.transport,'revocation_unverified',False))
    def _require_transport_healthy(self):
        if self._transport_unverified():
            self._keeper_stop.set()
            raise VaultError('socket-shutdown-unverified')
    def _pointer(self):
        path=self.root/'CURRENT'
        if not path.exists():return None
        value=_read_bounded(path,64).decode('ascii')
        if len(value)!=32 or any(c not in '0123456789abcdef' for c in value):raise VaultError('invalid-generation')
        return value
    def _load(self):
        revision=self._pointer()
        if revision is None:raise VaultError('not-initialized')
        directory=self.root/'generations'/revision;_safe_path(directory)
        try:
            meta=json.loads(_read_bounded(directory/'settings.json',4096))
            salt=base64.b64decode(meta['salt'],validate=True);verify=base64.b64decode(meta['verify'],validate=True)
            if meta.get('version')!=1 or len(salt)!=16 or len(verify)!=32:raise ValueError()
            encrypted=_read_bounded(directory/'vault.enc',LIMIT+28)
        except Exception:raise VaultError('invalid-generation') from None
        return revision,meta,salt,verify,encrypted
    def _commit(self,data,key,meta,expected):
        if self._pointer()!=expected:raise VaultError('generation-conflict')
        encrypted=self.crypto.encrypt(data,key)
        generation=uuid.uuid4().hex;directory=self.root/'generations'/generation
        _safe_path(directory);directory.mkdir();_write(directory/'vault.enc',encrypted)
        _write(directory/'settings.json',json.dumps(meta,separators=(',',':')).encode('ascii'))
        _fsync_dir(directory)
        temporary=self.root/('CURRENT.'+generation)
        _write(temporary,generation.encode('ascii'))
        if self._pointer()!=expected:raise VaultError('generation-conflict')
        _safe_path(self.root/'CURRENT',file=True)
        os.replace(temporary,self.root/'CURRENT');_fsync_dir(self.root)
        self._revision=generation
    def initialize(self,password,uid_text,secret_key):
        with self._mutex:
            if self._closed:raise VaultError('closed')
            self._require_transport_healthy()
            if self._pointer() is not None:raise VaultError('already-initialized')
            if not self.crypto.available:raise CryptoUnavailable()
            data=normalize_data({'secret_key':secret_key,'shaft_uids':parse_uid_text(uid_text)})
            salt=os.urandom(16);verify,key=self.crypto.derive(password,salt)
            meta={'version':1,'salt':base64.b64encode(salt).decode(),'verify':base64.b64encode(verify).decode()}
            self._commit(data,key,meta,None);self._data=data;self._key=key;self._meta=meta;self._epoch+=1
    def import_legacy(self,password,*,salt,verify,ciphertext):
        """Explicit bytes only: never discovers or changes the original vault."""
        with self._mutex:
            if self._closed or self._pointer() is not None:raise VaultError('already-initialized')
            self._require_transport_healthy()
            if not self.crypto.available:raise CryptoUnavailable()
            actual,key=self.crypto.derive(password,salt)
            if not hmac.compare_digest(actual,verify):raise VaultError('unlock-failed')
            data=normalize_data(self.crypto.decrypt(ciphertext,key))
            meta={'version':1,'salt':base64.b64encode(salt).decode(),'verify':base64.b64encode(verify).decode()}
            self._commit(data,key,meta,None);self._data=data;self._key=key;self._meta=meta;self._epoch+=1
    def unlock(self,password):
        with self._mutex:
            if self._closed:raise VaultError('closed')
            self._require_transport_healthy()
            if not self.crypto.available:raise CryptoUnavailable()
            revision,meta,salt,verify,encrypted=self._load()
            actual,key=self.crypto.derive(password,salt)
            if not hmac.compare_digest(actual,verify):raise VaultError('unlock-failed')
            data=normalize_data(self.crypto.decrypt(encrypted,key))
            self._require_transport_healthy()
            for slot in data['slots'].values():slot['consecutive_failures']=0
            self._data=data;self._key=key;self._meta=meta;self._revision=revision;self._epoch+=1
    def configure_auto_unlock(self,enabled,*,binding=None):
        if type(enabled) is not bool:raise VaultError('invalid-option')
        with self._mutex:
            self._require_transport_healthy()
            if self._closed or self._data is None:raise VaultError('locked')
            meta=dict(self._meta)
            if enabled:meta['autounlock']=base64.b64encode(self.crypto.wrap_key(self._key,binding=binding)).decode('ascii')
            else:meta.pop('autounlock',None)
            self._commit(self._data,self._key,meta,self._revision);self._meta=meta
    def try_auto_unlock(self,*,binding=None):
        # Explicit application call; no network and no constructor auto-unlock.
        with self._mutex:
            if self._closed or not self.crypto.available or self._revision is None:return False
            if self._transport_unverified():self._keeper_stop.set();return False
            try:
                revision,meta,salt,verify,encrypted=self._load()
                wrapped=base64.b64decode(meta.get('autounlock',''),validate=True)
                if not wrapped:return False
                key=self.crypto.unwrap_key(wrapped,binding=binding)
                data=normalize_data(self.crypto.decrypt(encrypted,key))
            except Exception:return False
            if self._transport_unverified():self._keeper_stop.set();return False
            for slot in data['slots'].values():slot['consecutive_failures']=0
            self._data=data;self._key=key;self._meta=meta;self._revision=revision;self._epoch+=1
            return True
    def lock(self):
        with self._mutex:
            self._keeper_stop.set();self._data=None;self._key=None;self._meta=None;self._pages.clear();self._epoch+=1
            if hasattr(self.transport,'clear_pending'):self.transport.clear_pending()
            # Do not claim confirmed lock/close if a custom transport reported
            # poison but returned normally from its cancellation callback.
            self._require_transport_healthy()
    def status(self):
        with self._mutex:
            poisoned=self._transport_unverified()
            if poisoned:self._keeper_stop.set()
            slots=() if self._data is None else tuple(self._data['slots'][str(uid)] for uid in self._data['shaft_uids'])
            active=any(s.get('consecutive_failures',0)<3 for s in slots)
            return {'initialized':self._revision is not None,'crypto_available':bool(self.crypto.available),
                'transport_available':self.transport is not None and not poisoned,
                'socket_shutdown_unverified':poisoned,
                'open':self._data is not None and not poisoned,'closed':self._closed,
                'auto_unlock':bool(self._meta and self._meta.get('autounlock')),
                'mode':'unavailable' if poisoned else ('nana7mi' if active else 'fallback'),'busy':self._busy.locked(),
                'slots':() if poisoned else tuple({'index':i+1,'remaining_seconds':remaining(s,self.clock()),'failures':s.get('consecutive_failures',0),
                    'retry_after':proactive_wait(s,self.clock())} for i,s in enumerate(slots))}
    def _snapshot(self):
        with self._mutex:
            self._require_transport_healthy()
            if self._closed or self._data is None:raise VaultError('locked')
            return self._epoch,copy.deepcopy(self._data)
    def _publish(self,epoch,data):
        with self._mutex:
            self._require_transport_healthy()
            if self._closed or epoch!=self._epoch or self._data is None:
                # lock already invalidated its own epoch; an old result must
                # not clear pending work from a newly unlocked session.
                raise VaultError('stale-session')
            self._commit(data,self._key,self._meta,self._revision);self._data=data
    def _select(self,data):
        now=self.clock();uids=data['shaft_uids'];fresh=[];last=[]
        for step in range(len(uids)):
            index=(self._round+step)%len(uids);uid=uids[index];s=data['slots'][str(uid)];left=remaining(s,now)
            if not s.get('access_token') or s.get('consecutive_failures',0)>=3:continue
            if left is None or left>120:fresh.append((uid,index))
            elif left>0:last.append((left,uid,index))
        if fresh:uid,index=fresh[0]
        elif last:_,uid,index=max(last)
        else:return None
        self._round=(index+1)%len(uids);return uid
    def _refresh(self,epoch,data,uid=None,proactive=False):
        if self.transport is None:raise VaultError('transport-unavailable')
        now=self.clock()
        if uid is None:uid=keeper_plan(data,now)[0]
        if uid not in data['shaft_uids']:raise VaultError('unknown-slot')
        slot=data['slots'][str(uid)]
        wait=proactive_wait(slot,now) if proactive else max(0,slot.get('backoff_until',0)-now)
        if wait:raise VaultError('backoff')
        slot['last_refresh_attempt_at']=now
        try:
            def current_session():
                with self._mutex:return not self._closed and self._data is not None and epoch==self._epoch
            guard=getattr(self.transport,'session_guard',None)
            if not callable(guard) or not callable(getattr(self.transport,'clear_pending',None)):
                raise VaultError('transport-fence-unavailable')
            with guard(current_session):
                result=self.transport.fetch_account(uid,data['secret_key'],slot,now)
            if type(result) is not dict:raise VaultError('invalid-response')
            incoming={k:result[k] for k in TOKEN_FIELDS+MANAGED_FIELDS if k in result}
            if not incoming.get('access_token'):raise VaultError('invalid-response')
            candidate=copy.deepcopy(data)
            candidate['slots'][str(uid)].update(incoming)
            try:normalize_data(candidate)
            except VaultError:raise VaultError('invalid-token-fields') from None
            oldleft=remaining(slot,now);newleft=remaining(incoming,now)
            if newleft is not None and newleft<=max(30,oldleft or 0):raise VaultError('short-payout')
            slot.update(incoming);slot.update(last_fetch_at=now,token_fetched_at=now,consecutive_failures=0,backoff_until=0)
        except Exception as error:
            if isinstance(error,VaultError) and error.code=='invalid-token-fields':
                raise VaultError('invalid-response') from None
            slot['consecutive_failures']=slot.get('consecutive_failures',0)+1
            retry=getattr(error,'retry_at',None)
            if type(retry) in (int,float) and math.isfinite(retry):slot['backoff_until']=max(now,retry)
            self._publish(epoch,data)
            raise VaultError(error.code if isinstance(error,VaultError) else 'refresh-failed') from None
        self._publish(epoch,data)
    def refresh(self,uid=None,*,proactive=False):
        if not self._busy.acquire(False):raise VaultError('busy')
        try:
            epoch,data=self._snapshot();self._refresh(epoch,data,uid,proactive)
            return self.status()
        finally:self._busy.release()
    def keeper_tick(self):
        epoch,data=self._snapshot();uid,fetch,wait=keeper_plan(data,self.clock())
        if hasattr(self.transport,'online_tick'):wait=min(wait,self.transport.online_tick(self.clock()))
        if fetch:self.refresh(uid,proactive=True)
        return wait
    def start_keeper(self):
        with self._mutex:
            self._require_transport_healthy()
            if self._closed or self._data is None:raise VaultError('locked')
            if self._keeper is not None and self._keeper.is_alive() and not self._keeper_stop.is_set():return
            self._keeper_stop=threading.Event();stop=self._keeper_stop
            def run():
                while not stop.is_set():
                    try:wait=self.keeper_tick()
                    except VaultError:wait=300
                    except Exception:wait=300
                    if stop.wait(max(1,min(wait,900))):return
            self._keeper=threading.Thread(target=run,name='pixivvault-external-token-keeper',daemon=True);self._keeper.start()
    def search(self,word,bookmark_min=0,*,sort='popular_desc',r18=False,exclude_ai=False,offset=0):
        from .external_vault_transport import search_params
        params=search_params(word,bookmark_min,sort=sort,r18=r18,exclude_ai=exclude_ai,offset=offset)
        if not self._busy.acquire(False):raise VaultError('busy')
        try:
            epoch,data=self._snapshot()
            if self.transport is None:raise VaultError('transport-unavailable')
            uid=self._select(data);refreshed=False
            if uid is None:
                self._refresh(epoch,data);refreshed=True
                next_epoch,data=self._snapshot()
                if next_epoch!=epoch:raise VaultError('stale-session')
                uid=self._select(data)
            if uid is None:raise VaultError('token-unavailable')
            for attempt in range(2):
                slot=data['slots'][str(uid)]
                try:
                    guard=getattr(self.transport,'session_guard',None)
                    if not callable(guard) or not callable(getattr(self.transport,'clear_pending',None)):
                        raise VaultError('transport-fence-unavailable')
                    def current_session():
                        with self._mutex:return not self._closed and self._data is not None and epoch==self._epoch
                    with guard(current_session):
                        result=self.transport.search(slot['access_token'],params)
                    if type(result) is not dict or type(result.get('illusts')) is not list:raise VaultError('invalid-response')
                    slot['consecutive_failures']=0;self._publish(epoch,data)
                    self._cache_pages(epoch,result['illusts']);return result
                except Exception as error:
                    slot['consecutive_failures']=slot.get('consecutive_failures',0)+1
                    invalid=isinstance(error,VaultError) and error.code=='invalid-grant'
                    if invalid:slot['token_expires_at_ms']=self.clock()*1000
                    self._publish(epoch,data)
                    if not invalid or attempt:raise VaultError(error.code if isinstance(error,VaultError) else 'search-failed') from None
                    next_uid=self._select(data)
                    if next_uid is None and not refreshed:
                        self._refresh(epoch,data,uid);refreshed=True
                        next_epoch,data=self._snapshot()
                        if next_epoch!=epoch:raise VaultError('stale-session')
                        next_uid=self._select(data)
                    if next_uid is None:raise VaultError('token-unavailable')
                    uid=next_uid
        finally:self._busy.release()
    def _cache_pages(self,epoch,rows):
        from .pixiv_read_client import _validate_destination,MAX_ARTWORK_PAGES
        from .bookmark_remote_adapter import positive_id
        ready={}
        for raw in rows:
            if type(raw) is not dict:continue
            wid=positive_id(raw.get('id'));count=raw.get('page_count')
            if wid is None or type(count) is not int or not 1<=count<=MAX_ARTWORK_PAGES or raw.get('type')=='ugoira':continue
            pages=raw.get('meta_pages')
            if type(pages) is list and len(pages)==count:
                urls=[p.get('image_urls',{}).get('large') or p.get('image_urls',{}).get('original') for p in pages if type(p) is dict and type(p.get('image_urls')) is dict]
            elif count==1:
                image=raw.get('image_urls',{});single=raw.get('meta_single_page',{})
                if type(image) is not dict or type(single) is not dict:continue
                urls=[image.get('large') or single.get('original_image_url')]
            else:continue
            try:
                if len(urls)!=count or len(set(urls))!=count:continue
                for url in urls:_validate_destination(url,'image')
            except Exception:continue
            ready[wid]=tuple(urls)
        with self._mutex:
            self._require_transport_healthy()
            if epoch!=self._epoch or self._closed or self._data is None:raise VaultError('stale-session')
            self._pages.update(ready)
    def get_artwork_pages(self,work_id):
        with self._mutex:
            self._require_transport_healthy()
            if self._data is None or self._closed:raise VaultError('locked')
            pages=self._pages.get(str(work_id))
            if not pages:raise VaultError('search-page-metadata-unavailable')
            return pages
    def ranking_preferences(self):
        with self._mutex:
            path=self.root/'ranking-preferences.json'
            if not path.exists():return {}
            try:
                value=json.loads(_read_bounded(path,65536))
                return value if type(value) is dict else {}
            except Exception:return {}
    def save_ranking_preferences(self,tags,filters):
        from .personal_ranking import normalize_personal_filters
        if type(tags) not in (list,tuple) or any(type(t) is not str or len(t)>256 for t in tags):raise VaultError('invalid-preferences')
        value={'tags':list(dict.fromkeys(t.strip() for t in tags if t.strip())),'filters':normalize_personal_filters(filters)}
        raw=json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8')
        if len(raw)>65536:raise VaultError('invalid-preferences')
        with self._mutex:
            if self._closed:raise VaultError('closed')
            destination=self.root/'ranking-preferences.json';_safe_path(destination,file=True)
            temporary=self.root/('ranking-preferences.'+uuid.uuid4().hex)
            _write(temporary,raw);os.replace(temporary,destination);_fsync_dir(self.root)
    def close(self):
        with self._mutex:
            if self._closed:return
            # Owner lock stays held until shutdown has really been verified;
            # a failed lock propagates and cannot mark this vault closed.
            self.lock();self._closed=True
            if self._owner is not None:self._owner.__exit__(None,None,None);self._owner=None
