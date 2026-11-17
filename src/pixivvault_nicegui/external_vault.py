"""Encrypted, explicitly owned Shaft sidecar and borrowed-account session.

Wire compatibility: original nana7mi_vault.py at 3ac3aa0. No main Cookie
client, automatic discovery, network on construction, or plaintext fallback.
"""
from __future__ import annotations
import base64
from dataclasses import dataclass
import copy
from contextlib import nullcontext
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
from .managed_downloads import ManagedDownloadError, _safe_path, _read_bounded, _write, _fsync_dir
from .fs_paths import native_io_path
from .ugoira_storage import _owner_lock
from .external_vault_rejections import (RejectedRefreshArchive, RejectionHistoryError,
    ARCHIVE_FIELD, SEND_RESERVE, WRITE_RESERVE, valid_pointer, payload_size)
from .external_vault_result_retention import KnownResultRetention, MAX_NORMAL_RESULTS

LIMIT = 2 * 1024 * 1024
VERIFY = b'nana7mi-vault-verify-v1'
ENCRYPT = b'nana7mi-vault-encrypt-v1'
TOKEN_FIELDS = ('access_token', 'refresh_token', 'expires_in', 'token_type')
MANAGED_FIELDS = ('backoff_until','payout_not_before','last_fetch_at','last_refresh_attempt_at','consecutive_failures','reported_refresh_hash','rejected_refresh_hashes','account_uid','token_expires_at_ms','token_fetched_at')
KNOWN_RESULT_SLOT_FIELDS = frozenset(TOKEN_FIELDS + (
    'token_expires_at_ms','token_fetched_at','account_uid',
    'rejected_refresh_hashes','oauth_send_pending'))

class VaultError(ValueError):
    def __init__(self, code='unavailable'):
        self.code=code
        super().__init__('external vault: '+code)

VAULT_INTEGRITY_ERRORS = frozenset(('invalid-ciphertext', 'invalid-generation', 'invalid-vault'))

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
    if ARCHIVE_FIELD in data:
        if not valid_pointer(data[ARCHIVE_FIELD]):raise VaultError('invalid-vault')
        output[ARCHIVE_FIELD]=data[ARCHIVE_FIELD]
    for uid in uids:
        raw=slots.get(str(uid),data if data.get('shaft_uid')==uid else {})
        if type(raw) is not dict:raise VaultError('invalid-vault')
        slot={k:raw[k] for k in TOKEN_FIELDS+MANAGED_FIELDS if k in raw}
        # Local-only write-ahead fence. Never accept this field from an OAuth reply.
        if 'oauth_send_pending' in raw:
            if type(raw['oauth_send_pending']) is not bool:raise VaultError('invalid-vault')
            slot['oauth_send_pending']=raw['oauth_send_pending']
        rejected=slot.get('rejected_refresh_hashes',[])
        if (type(rejected) is not list or any(type(value) is not str or len(value)!=64
                or any(c not in '0123456789abcdef' for c in value) for value in rejected)):
            raise VaultError('invalid-vault')
        for key in ('access_token','refresh_token','token_type','reported_refresh_hash'):
            if key in slot and type(slot[key]) is not str:raise VaultError('invalid-vault')
        for key in ('expires_in','backoff_until','payout_not_before','last_fetch_at','last_refresh_attempt_at','consecutive_failures','account_uid','token_expires_at_ms','token_fetched_at'):
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


def periodic_interval_seconds(hours):
    """元 scheduler.py の読み方: 壊れた値は既定の24時間、1時間未満は1時間に切り上げる。"""
    if hours is None:return None
    try:value=int(hours)
    except (TypeError,ValueError):value=24
    return max(1,value)*3600


def periodic_due_in(slot,now,interval):
    """最後の更新から設定間隔までの残り秒。更新の記録が無いスロットは対象にしない（安全側）。"""
    stamp=max(slot.get('last_fetch_at') or 0,slot.get('token_fetched_at') or 0)
    if not stamp:return None
    return max(0,stamp+interval-now)


def keeper_plan(data,now,interval_hours=None):
    """interval_hours が None のときは従来どおり有効期限だけで決める。

    指定時は「有効期限に余裕があっても、最後の更新から設定間隔が過ぎた」スロットも更新対象にする
    （元アプリの定期更新）。対象になったスロットも proactive_wait（35分の下限と backoff）を通る。
    """
    interval=periodic_interval_seconds(interval_hours)
    eligible=[];waiting=[];order=[]
    for uid in data['shaft_uids']:
        slot=data['slots'][str(uid)]; left=remaining(slot,now)
        priority=(float('-inf') if left is None else left,slot.get('last_refresh_attempt_at',0),uid)
        order.append(priority)
        due_in=periodic_due_in(slot,now,interval) if interval is not None and left is not None and left>600 else None
        if left is not None and left>600 and due_in!=0:
            waiting.append(min(left-600,900) if due_in is None else min(left-600,due_in,900))
        else:
            wait=proactive_wait(slot,now)
            if wait:waiting.append(min(wait,900))
            else:eligible.append(priority)
    if eligible:return min(eligible)[2],True,300
    return min(order)[2],False,min(waiting)


@dataclass(frozen=True, slots=True, repr=False)
class _BorrowedPageSession:
    owner: object
    epoch: int
    requester_uid: int
    account_uid: int | None
    token_digest: bytes
    operation: str
    query: tuple

    def __repr__(self):return '<BorrowedPageSession>'


class ExternalVaultService:
    """Owns a new sidecar, borrows transport, serializes mutation, fences lock.

    Immutable encrypted generations are committed by an atomic CURRENT pointer.
    A single-writer file lock covers the service lifetime. Missing crypto prevents
    any creation/import, while existing ciphertext can still be identified safely.
    """
    supports_page_sessions=True

    def __init__(self,root,*,transport=None,crypto=None,clock=time.time,maintenance=None,read_busy_timeout=20):
        self.root=Path(root).absolute();self.transport=transport;self.crypto=crypto or LegacyVaultCrypto();self.clock=clock
        self._rejections=RejectedRefreshArchive(self.root)
        self._known_result_retention=KnownResultRetention(self.root)
        self._known_result_retention_state='unexamined'
        self._request_budget=None;self._availability_reason=None;self._paging_owner=object()
        self._maintenance_policy=maintenance
        self._read_busy_timeout=max(0,read_busy_timeout)
        self._mutex=threading.RLock();self._busy=threading.Lock();self._data=None;self._key=None;self._meta=None
        self._auto_unlock_save_failed=False
        self._unlock_intent_lock=threading.RLock();self._unlock_intent_epoch=0
        self._epoch=0;self._round=0;self._closed=False;self._revision=None;self._owner=None;self._pages={};self._keeper=None;self._keeper_stop=threading.Event()
        self._owner_writable=False
        self._inspection_error=None
        try:
            self._inspect_root()
        except VaultError as error:
            self._inspection_error=error.code
        except PermissionError:
            self._inspection_error='vault-permission-denied'
        except OSError:
            self._inspection_error='vault-io-error'
        except ManagedDownloadError:
            self._inspection_error='vault-path-rejected'
        if self._revision is not None and self._inspection_error is None:
            owner=_owner_lock(self.root,create=False)
            owner.__enter__()
            try:
                previous=self._revision
                self._inspect_root()
                if self._revision!=previous:raise VaultError('generation-conflict')
            except BaseException:
                owner.__exit__(None,None,None)
                raise
            self._owner=owner

    def _inspect_root(self):
        _safe_path(self.root)
        try:
            entries=tuple(self.root.iterdir())
        except FileNotFoundError:
            self._revision=None
            return
        if not entries:
            self._revision=None
            return
        marker=self.root/'EXTERNAL_VAULT_V1'
        try:
            value=_read_bounded(marker,128)
        except FileNotFoundError:
            raise VaultError('foreign-root') from None
        if value not in (b'PixivVault external vault sidecar v1\n',b'PixivVault external vault sidecar v2\n'):
            raise VaultError('foreign-root')
        self._revision=self._pointer()
        generations=self.root/'generations'
        _safe_path(generations)
        if self._revision is None:
            if any(p.name not in ('EXTERNAL_VAULT_V1', '.writer.lock', 'generations', 'premium-activation-state', 'premium-activation.dpapi', 'premium-material.dpapi', 'premium-attempts.json') for p in entries):
                raise VaultError('invalid-generation')
            try:
                if any(generations.iterdir()):raise VaultError('invalid-generation')
            except FileNotFoundError:
                pass

    def _ensure_writable(self, expected):
        if self._inspection_error is not None:raise VaultError(self._inspection_error)
        if self._owner is not None and self._owner_writable:
            if self._pointer()!=expected:raise VaultError('generation-conflict')
            return
        if self._owner is not None:
            self._owner.__exit__(None,None,None)
            self._owner=None
        self._inspect_root()
        if self._revision!=expected:raise VaultError('generation-conflict')
        self.root.mkdir(parents=True,exist_ok=True)
        owner=_owner_lock(self.root,create=True)
        owner.__enter__()
        try:
            self._inspect_root_after_lock(expected)
        except BaseException:
            owner.__exit__(None,None,None)
            raise
        self._owner=owner
        self._owner_writable=True

    def _inspect_root_after_lock(self, expected):
        marker=self.root/'EXTERNAL_VAULT_V1'
        try:
            value=_read_bounded(marker,128)
        except FileNotFoundError:
            # Only a pristine root plus our own lock can be initialized.
            if expected is not None or any(p.name != '.writer.lock' for p in self.root.iterdir()):
                raise VaultError('foreign-root') from None
            _write(marker,b'PixivVault external vault sidecar v1\n')
        else:
            if value not in (b'PixivVault external vault sidecar v1\n',b'PixivVault external vault sidecar v2\n'):
                raise VaultError('foreign-root')
        if self._pointer()!=expected:raise VaultError('generation-conflict')
        generations=self.root/'generations'
        _safe_path(generations)
        generations.mkdir(exist_ok=True)
        if expected is None and any(generations.iterdir()):raise VaultError('invalid-generation')

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
        try:
            raw=_read_bounded(path,64)
        except FileNotFoundError:
            return None
        try:
            value=raw.decode('ascii')
        except UnicodeDecodeError:
            raise VaultError('invalid-generation') from None
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
        except PermissionError:raise VaultError('vault-permission-denied') from None
        except FileNotFoundError:raise VaultError('invalid-generation') from None
        except OSError:raise VaultError('vault-io-error') from None
        except Exception:raise VaultError('invalid-generation') from None
        return revision,meta,salt,verify,encrypted
    def _commit(self,data,key,meta,expected):
        self._ensure_writable(expected)
        if self._pointer()!=expected:raise VaultError('generation-conflict')
        # Preserve authenticated CURRENT's inherited ON before omitting its
        # legacy key from a new generation. An explicit standalone OFF wins.
        # Failed migration must leave CURRENT/data untouched, not silently OFF.
        if expected is not None and self._auto_meta().get('autounlock'):
            try:
                state=self._disk_auto()
                if state['state']=='legacy':
                    self._write_auto(True,key=key,inherited=True)
                    self._auto_unlock_save_failed=False
            except Exception:
                self._auto_unlock_save_failed=True
                raise VaultError('autounlock-migration-unconfirmed') from None
        meta=dict(meta);meta.pop('autounlock',None)
        self._compact_rejections(data,key)
        if ARCHIVE_FIELD in data:
            try:self._rejections.read(data,key,self.crypto,LIMIT)
            except RejectionHistoryError as error:raise VaultError(error.code) from None
        encrypted=self.crypto.encrypt(data,key)
        generation=uuid.uuid4().hex;directory=self.root/'generations'/generation
        _safe_path(directory);directory.mkdir();_write(directory/'vault.enc',encrypted)
        _write(directory/'settings.json',json.dumps(meta,separators=(',',':')).encode('ascii'))
        # Metadata-only commits must not orphan a pending result's authenticated
        # base. Keep a small, authenticated origin link in this new generation,
        # never copy/rewrite the immutable result files or trust plain settings.
        if expected is not None and any(s.get('oauth_send_pending',False) for s in data['slots'].values()):
            previous=_read_bounded(self.root/'generations'/expected/'vault.enc',LIMIT+28)
            if normalize_data(self.crypto.decrypt(previous,key))==normalize_data(data):
                origin,source=self._known_result_origin(expected,previous,key)
                link=self.crypto.encrypt({'purpose':'oauth-known-result-origin-v1',
                    'generation':generation,'source_sha256':hashlib.sha256(encrypted).hexdigest(),
                    'base':origin,'base_sha256':hashlib.sha256(source).hexdigest()},key)
                _write(directory/'known-result-origin.enc',link)
        _fsync_dir(directory)
        temporary=self.root/('CURRENT.'+generation)
        _write(temporary,generation.encode('ascii'))
        if self._pointer()!=expected:raise VaultError('generation-conflict')
        if ARCHIVE_FIELD in data:self._mark_archive_version()
        _safe_path(self.root/'CURRENT',file=True)
        os.replace(temporary,self.root/'CURRENT');_fsync_dir(self.root)
        self._revision=generation
    def _mark_archive_version(self):
        # Older candidates must refuse this root rather than ignore its
        # external rejection proofs on downgrade.
        marker=self.root/'EXTERNAL_VAULT_V1'
        observed=_read_bounded(marker,128)
        if observed==b'PixivVault external vault sidecar v2\n':return
        if observed!=b'PixivVault external vault sidecar v1\n':raise VaultError('foreign-root')
        temporary=self.root/('EXTERNAL_VAULT_V1.'+uuid.uuid4().hex)
        _write(temporary,b'PixivVault external vault sidecar v2\n')
        _safe_path(marker,file=True)
        os.replace(temporary,marker);_fsync_dir(self.root)
    def _compact_rejections(self,data,key,reserve=False,force=False,extra_digests=()):
        try:
            self._rejections.compact(data,key,self.crypto,LIMIT,
                reserve=SEND_RESERVE if reserve else WRITE_RESERVE,force=force,extra_digests=extra_digests)
            if reserve and payload_size(data)>LIMIT-SEND_RESERVE:
                raise VaultError('vault-too-large')
        except RejectionHistoryError as error:
            raise VaultError(error.code) from None
    def initialize(self,password,uid_text,secret_key):
        with self._mutex:
            if self._inspection_error is not None:raise VaultError(self._inspection_error)
            if self._closed:raise VaultError('closed')
            self._require_transport_healthy()
            if self._pointer() is not None:raise VaultError('already-initialized')
            if not self.crypto.available:raise CryptoUnavailable()
            data=normalize_data({'secret_key':secret_key,'shaft_uids':parse_uid_text(uid_text)})
            salt=os.urandom(16);verify,key=self.crypto.derive(password,salt)
            meta={'version':1,'salt':base64.b64encode(salt).decode(),'verify':base64.b64encode(verify).decode()}
            self._commit(data,key,meta,None);self._data=data;self._key=key;self._meta=meta;self._epoch+=1
    def initialize_derived(self,material,uid_text,secret_key):
        """Initialize using authenticated, DPAPI-custodied derived key material."""
        self._require_premium()
        with self._mutex:
            if self._inspection_error is not None:raise VaultError(self._inspection_error)
            if self._closed or self._pointer() is not None:raise VaultError('already-initialized')
            if not self.crypto.available:raise CryptoUnavailable()
            try:
                salt=base64.b64decode(material['salt'],validate=True)
                verify=base64.b64decode(material['verify'],validate=True)
                key=base64.b64decode(material['key'],validate=True)
                if len(salt)!=16 or len(verify)!=32 or len(key)!=32:raise ValueError()
            except Exception:raise VaultError('invalid-key-material') from None
            data=normalize_data({'secret_key':secret_key,'shaft_uids':parse_uid_text(uid_text)})
            meta={'version':1,'salt':material['salt'],'verify':material['verify']}
            self._commit(data,key,meta,None);self._data=data;self._key=key;self._meta=meta;self._epoch+=1
    def unlock_derived(self,material):
        """Local-only activation; does not enable borrowing or start a keeper."""
        epoch=self.unlock_request_epoch()
        with self._mutex:
            if self._closed:raise VaultError('closed')
            self._require_transport_healthy()
            revision,meta,salt,verify,encrypted=self._load()
            try:
                key=base64.b64decode(material['key'],validate=True)
                if (len(key)!=32 or not hmac.compare_digest(meta['salt'],material['salt'])
                        or not hmac.compare_digest(meta['verify'],material['verify'])):raise ValueError()
            except Exception:raise VaultError('unlock-failed') from None
            # Matching derived material is already authenticated. A damaged
            # ciphertext or payload must not become a password mismatch.
            data=normalize_data(self.crypto.decrypt(encrypted,key))
            self._ensure_writable(revision)
            with self._unlock_intent_lock:
                if epoch!=self._unlock_intent_epoch:raise VaultError('stale-session')
                self._require_transport_healthy()
                self._data=data;self._key=key;self._meta=meta;self._revision=revision;self._epoch+=1
    def _require_premium(self):
        gate=getattr(self,'premium_authorization',None)
        if gate is not None:gate.require_enabled()
    def enable_request_budget(self,**options):
        from .premium_request_budget import PremiumRequestBudget
        self._request_budget=PremiumRequestBudget(self,**options)
        bind=getattr(self.transport,'bind_request_budget',None)
        if callable(bind):bind(self._request_budget.reserve)
    def premium_pool_status(self):
        with self._mutex:
            if self._data is None or self._request_budget is None:return ()
            return self._request_budget.summary(self._data)
    def import_legacy(self,password,*,salt,verify,ciphertext,activate=True):
        """Explicit bytes only: never discovers or changes the original vault."""
        with self._mutex:
            if self._inspection_error is not None:raise VaultError(self._inspection_error)
            if type(activate) is not bool:raise VaultError('invalid-option')
            if self._closed or self._pointer() is not None:raise VaultError('already-initialized')
            self._require_transport_healthy()
            if not self.crypto.available:raise CryptoUnavailable()
            actual,key=self.crypto.derive(password,salt)
            if not hmac.compare_digest(actual,verify):raise VaultError('unlock-failed')
            data=normalize_data(self.crypto.decrypt(ciphertext,key))
            meta={'version':1,'salt':base64.b64encode(salt).decode(),'verify':base64.b64encode(verify).decode()}
            self._commit(data,key,meta,None);self._data=data;self._key=key;self._meta=meta;self._epoch+=1
            if not activate:self.lock()
    def unlock_request_epoch(self):
        with self._unlock_intent_lock:return self._unlock_intent_epoch
    def _invalidate_unlock_requests(self):
        with self._unlock_intent_lock:self._unlock_intent_epoch+=1
    def _auto_meta(self):
        revision=self._pointer()
        if revision is None:raise VaultError('not-initialized')
        directory=self.root/'generations'/revision;_safe_path(directory)
        meta=json.loads(_read_bounded(directory/'settings.json',4096))
        if len(base64.b64decode(meta['salt'],validate=True))!=16:raise VaultError('invalid-generation')
        return meta
    def _auto_record(self):
        from .external_vault_autounlock import AutoUnlockRecord
        return AutoUnlockRecord(self.root)
    def _disk_auto(self):
        meta=self._auto_meta();record=self._auto_record().read(meta['salt'])
        if record is not None:return {**record,'state':'on' if record['enabled'] else 'off'}
        legacy=bool(meta.get('autounlock'))
        return {'enabled':legacy,'inherited':legacy,'state':'legacy' if legacy else 'off','key':''}
    def _write_auto(self,enabled,*,key=None,inherited=False):
        meta=self._auto_meta()
        return self._auto_record().write(meta['salt'],enabled=enabled,key=key,inherited=inherited)
    def unlock(self,password,*,auto_unlock=None,expected_epoch=None):
        if auto_unlock is not None and type(auto_unlock) is not bool:raise VaultError('invalid-option')
        epoch=self.unlock_request_epoch() if expected_epoch is None else expected_epoch
        self._unlock_password(password,remember=True,auto_unlock=auto_unlock,expected_epoch=epoch)
    def _unlock_password(self,password,*,remember=False,auto_unlock=None,expected_epoch=None):
        epoch=self.unlock_request_epoch() if expected_epoch is None else expected_epoch
        with self._mutex:
            if self._closed:raise VaultError('closed')
            self._require_transport_healthy()
            if not self.crypto.available:raise CryptoUnavailable()
            revision,meta,salt,verify,encrypted=self._load()
            actual,key=self.crypto.derive(password,salt)
            if not hmac.compare_digest(actual,verify):raise VaultError('unlock-failed')
            data=normalize_data(self.crypto.decrypt(encrypted,key))
            self._ensure_writable(revision)
            self._require_transport_healthy()
            with self._unlock_intent_lock:
                if epoch!=self._unlock_intent_epoch:raise VaultError('stale-session')
                self._auto_unlock_save_failed=False
                if remember:
                    try:
                        state=self._disk_auto()
                        if auto_unlock is not None:
                            self._write_auto(auto_unlock,key=key if auto_unlock else None)
                        elif state['state']=='legacy':
                            self._write_auto(True,key=key,inherited=True)
                    except Exception:self._auto_unlock_save_failed=True
                self._require_transport_healthy()
                for slot in data['slots'].values():slot['consecutive_failures']=0
                self._data=data;self._key=key;self._meta=meta;self._revision=revision;self._epoch+=1
    def reopen_saved_state(self,password):
        """Reauthenticate CURRENT locally; never resolve pending or start work."""
        if not self._busy.acquire(False):raise VaultError('busy')
        try:
            with self._mutex:
                if self._closed:raise VaultError('closed')
                self._require_transport_healthy()
                # Reuse the existing cancellation/epoch fence before reloading.
                # A wrong password leaves the session locked, not on stale data.
                self.lock()
                self._unlock_password(password)
        finally:self._busy.release()
    def configure_auto_unlock(self,enabled,*,binding=None,expected_epoch=None):
        if type(enabled) is not bool:raise VaultError('invalid-option')
        if not enabled:self._invalidate_unlock_requests()
        epoch=self.unlock_request_epoch() if expected_epoch is None else expected_epoch
        with self._mutex:
            if self._closed:raise VaultError('closed')
            if enabled:
                self._require_transport_healthy()
                if self._data is None:raise VaultError('locked')
            with self._unlock_intent_lock:
                if epoch!=self._unlock_intent_epoch:raise VaultError('stale-session')
                try:self._write_auto(enabled,key=self._key if enabled else None)
                except Exception:
                    self._auto_unlock_save_failed=True
                    raise VaultError('autounlock-write-unconfirmed') from None
                self._auto_unlock_save_failed=False
    def _known_result_origin(self,generation,source,key):
        path=self.root/'generations'/generation/'known-result-origin.enc'
        _safe_path(path,file=True)
        if not native_io_path(path).exists():return generation,source
        link=self.crypto.decrypt(_read_bounded(path,4096),key)
        if (type(link) is not dict or set(link)!={'purpose','generation','source_sha256','base','base_sha256'}
                or link['purpose']!='oauth-known-result-origin-v1'
                or link['generation']!=generation or link['source_sha256']!=hashlib.sha256(source).hexdigest()
                or type(link['base']) is not str or len(link['base'])!=32
                or any(c not in '0123456789abcdef' for c in link['base'])):
            raise VaultError('known-result-unavailable')
        original=_read_bounded(self.root/'generations'/link['base']/'vault.enc',LIMIT+28)
        if (link['base_sha256']!=hashlib.sha256(original).hexdigest()
                or normalize_data(self.crypto.decrypt(original,key))!=normalize_data(self.crypto.decrypt(source,key))):
            raise VaultError('known-result-unavailable')
        return link['base'],original
    def _save_known_result(self,epoch,data,uid,*,defer=False):
        """Journal an observed OAuth result before CURRENT; no inferred outcome."""
        with self._mutex:
            self._require_transport_healthy()
            if self._closed or self._data is None or epoch!=self._epoch:raise VaultError('stale-session')
            if self._pointer()!=self._revision:raise VaultError('generation-conflict')
            if not self._data['slots'][str(uid)].get('oauth_send_pending'):raise VaultError('known-result-unavailable')
            candidate=normalize_data(data)
            if candidate['slots'][str(uid)].get('oauth_send_pending') is not False:raise VaultError('known-result-unavailable')
            source=_read_bounded(self.root/'generations'/self._revision/'vault.enc',LIMIT+28)
            result=self.crypto.encrypt(candidate,self._key)
            proof=self.crypto.encrypt({'purpose':'oauth-known-result-v1','base':self._revision,'uid':uid,
                'source_sha256':hashlib.sha256(source).hexdigest(),
                'result_sha256':hashlib.sha256(result).hexdigest()},self._key)
            record=(self._revision,result,proof)
            if not defer:self._store_known_result(epoch,record)
            return record
    def _store_known_result(self,epoch,record):
        # A deferred record remains bound to the observed request's original
        # pending generation, even when a later primary/follow-up save failed.
        # It never authorizes recovery of a different CURRENT by itself.
        with self._mutex:
            self._require_transport_healthy()
            if self._closed or self._data is None or epoch!=self._epoch:raise VaultError('stale-session')
            base,result,proof=record
            parent=self.root/'known-results';_safe_path(parent);native_io_path(parent).mkdir(exist_ok=True);_safe_path(parent)
            directory=parent/base;_safe_path(directory);native_io_path(directory).mkdir()
            # Exclusive writes preserve interrupted evidence. Neither file alone
            # authorizes recovery; both must authenticate and match CURRENT.
            _write(directory/'result.enc',result);_write(directory/'proof.enc',proof)
            _fsync_dir(directory);_fsync_dir(parent)
    def _can_record_normal_result(self):
        try:
            self._collect_completed_records()
            allowed=self._known_result_retention.can_record(self._key,self.crypto)
            self._known_result_retention_state='recording' if allowed else 'limited'
            return allowed
        except Exception:
            # An unreadable accounting index must not cause unbounded normal
            # retention or block primary publication / failed-result evidence.
            self._known_result_retention_state='unavailable'
            return False
    def _collect_completed_records(self):
        policy=self._maintenance_policy
        if policy is None or not policy.allows(self.root):return ()
        with self._mutex:
            if self._data is None or self._closed:return ()
            if any(s.get('oauth_send_pending') for s in self._data['slots'].values()):return ()
            try:return self._known_result_retention.collect_completed(self._key,self.crypto)
            except Exception:
                self._known_result_retention_state='unavailable'
                return ()
    def recover_known_result(self,password):
        """Explicit authenticated local recovery; never clears the shared STOP."""
        if not self._busy.acquire(False):raise VaultError('busy')
        try:
            with self._mutex:
                self.lock()
                try:
                    self._unlock_password(password)
                    pending=[uid for uid in self._data['shaft_uids']
                             if self._data['slots'][str(uid)].get('oauth_send_pending')]
                    if len(pending)!=1:raise VaultError('known-result-unavailable')
                    uid=pending[0];current=self._revision
                    try:
                        source=_read_bounded(self.root/'generations'/current/'vault.enc',LIMIT+28)
                        base,source=self._known_result_origin(current,source,self._key)
                        directory=self.root/'known-results'/base;_safe_path(directory)
                        result=_read_bounded(directory/'result.enc',LIMIT+28)
                        proof=self.crypto.decrypt(_read_bounded(directory/'proof.enc',LIMIT+28),self._key)
                        expected={'purpose':'oauth-known-result-v1','base':base,'uid':uid,
                            'source_sha256':hashlib.sha256(source).hexdigest(),
                            'result_sha256':hashlib.sha256(result).hexdigest()}
                        if type(proof) is not dict or type(proof.get('uid')) is not int or proof!=expected:
                            raise VaultError('known-result-unavailable')
                        base_data=normalize_data(self.crypto.decrypt(source,self._key))
                        recorded=self.crypto.decrypt(result,self._key)
                        candidate=normalize_data(recorded)
                        # Records are written normalized. Reject unexpected fields
                        # rather than silently discard them before comparison.
                        if (recorded!=candidate
                                or candidate['shaft_uids']!=base_data['shaft_uids']
                                or candidate['secret_key']!=base_data['secret_key']
                                or candidate['slots'][str(uid)].get('oauth_send_pending') is not False
                                or any(candidate['slots'][str(other)].get('oauth_send_pending',False)
                                       !=base_data['slots'][str(other)].get('oauth_send_pending',False)
                                       # pending contains only uid here. Compare
                                       # every other registered slot, not that singleton.
                                       for other in self._data['shaft_uids'] if other!=uid)):
                            raise VaultError('known-result-unavailable')
                        # unlock resets in-memory failures; the authenticated
                        # base generation, not self._data, defines this contract.
                        for other in base_data['shaft_uids']:
                            before=base_data['slots'][str(other)]
                            after=candidate['slots'][str(other)]
                            if other==uid:
                                before={k:v for k,v in before.items() if k not in KNOWN_RESULT_SLOT_FIELDS}
                                after={k:v for k,v in after.items() if k not in KNOWN_RESULT_SLOT_FIELDS}
                            if before!=after:raise VaultError('known-result-unavailable')
                        self._rejections.read(candidate,self._key,self.crypto,LIMIT,
                            required_head=base_data.get(ARCHIVE_FIELD))
                    except Exception:
                        raise VaultError('known-result-unavailable') from None
                    self._commit(candidate,self._key,self._meta,current)
                    self._data=candidate;self._pages.clear();self._round=0;self._epoch+=1
                    try:
                        self._known_result_retention.release_failed(base,
                            hashlib.sha256(_read_bounded(directory/'proof.enc',LIMIT+28)).hexdigest(),
                            self._key,self.crypto)
                    except Exception:self._known_result_retention_state='unavailable'
                except Exception:
                    self.lock()
                    raise
        finally:self._busy.release()
    def update_config(self,*,secret_key='',uid_text=''):
        """Update an opened vault from explicit text; no file import or fetch."""
        if not self._busy.acquire(False):raise VaultError('busy')
        try:
            with self._mutex:
                self._require_transport_healthy()
                if self._closed or self._data is None:raise VaultError('locked')
                # A configuration change must not erase an unresolved sent request.
                if any(s.get('oauth_send_pending',False) for s in self._data['slots'].values()):
                    raise VaultError('pixiv-outcome-uncertain')
                if secret_key is not None and type(secret_key) is not str:raise VaultError('invalid-secret-key')
                if uid_text is not None and type(uid_text) is not str:raise VaultError('invalid-uid-list')
                change_secret=bool(secret_key and secret_key.strip())
                change_uids=bool(uid_text and uid_text.lstrip('\ufeff').strip())
                if not change_secret and not change_uids:raise VaultError('no-changes')
                current=copy.deepcopy(self._data)
                uids=parse_uid_text(uid_text) if change_uids else list(current['shaft_uids'])
                removed=[uid for uid in current['shaft_uids'] if uid not in uids]
                removed_history=tuple(digest for uid in removed
                    for digest in current['slots'][str(uid)].get('rejected_refresh_hashes',[]))
                candidate_input={
                    'secret_key':secret_key if change_secret else current['secret_key'],
                    'shaft_uids':uids,
                    'slots':{str(uid):current['slots'].get(str(uid),{}) for uid in uids},
                }
                if ARCHIVE_FIELD in current:candidate_input[ARCHIVE_FIELD]=current[ARCHIVE_FIELD]
                candidate=normalize_data(candidate_input)
                if removed:
                    # Size the final requested settings, while archiving proof
                    # from removed UIDs without retaining their old payload.
                    self._compact_rejections(candidate,self._key,force=True,extra_digests=removed_history)
                # The busy lock excludes searches and keeper work. Clear queued
                # online work before committing so old credentials cannot be sent
                # after the change, and a failed fence leaves CURRENT unchanged.
                if hasattr(self.transport,'clear_pending'):self.transport.clear_pending()
                self._require_transport_healthy()
                self._commit(candidate,self._key,self._meta,self._revision)
                self._data=candidate;self._pages.clear();self._round=0;self._epoch+=1
        finally:self._busy.release()
    def try_auto_unlock(self,*,binding=None):
        # Explicit app startup call. Migration must be confirmed before use.
        epoch=self.unlock_request_epoch()
        with self._mutex:
            if self._closed or not self.crypto.available or self._revision is None:return False
            if self._transport_unverified():self._keeper_stop.set();return False
            try:
                revision,meta,salt,verify,encrypted=self._load()
                state=self._disk_auto()
                if not state['enabled']:return False
                if state['state']=='legacy':
                    key=self.crypto.unwrap_key(base64.b64decode(meta['autounlock'],validate=True),binding=binding)
                else:key=base64.b64decode(state['key'],validate=True)
                data=normalize_data(self.crypto.decrypt(encrypted,key))
                with self._unlock_intent_lock:
                    if epoch!=self._unlock_intent_epoch:return False
                    if state['state']=='legacy':self._write_auto(True,key=key,inherited=True)
                    if self._transport_unverified():self._keeper_stop.set();return False
                    for slot in data['slots'].values():slot['consecutive_failures']=0
                    self._data=data;self._key=key;self._meta=meta;self._revision=revision;self._epoch+=1
                    self._auto_unlock_save_failed=False
                    return True
            except Exception:
                self._auto_unlock_save_failed=True
                return False
    def lock_explicitly(self):
        self._invalidate_unlock_requests()
        with self._mutex:
            if self._closed:raise VaultError('closed')
            failure=None
            try:self.lock()
            except Exception as error:failure=error
            try:self._write_auto(False)
            except Exception:
                self._auto_unlock_save_failed=True
                raise VaultError('autounlock-revoke-failed') from None
            if failure is not None:raise failure
    def lock(self):
        self._invalidate_unlock_requests()
        with self._mutex:
            self._keeper_stop.set();self._data=None;self._key=None;self._meta=None;self._pages.clear();self._epoch+=1
            self._auto_unlock_save_failed=False
            if hasattr(self.transport,'clear_pending'):self.transport.clear_pending()
            # Do not claim confirmed lock/close if a custom transport reported
            # poison but returned normally from its cancellation callback.
            self._require_transport_healthy()
    def registered_uids(self):
        """UIDs of the opened vault in registration order; None while locked."""
        with self._mutex:
            return None if self._data is None else list(self._data['shaft_uids'])
    def status(self):
        with self._mutex:
            poisoned=self._transport_unverified()
            if poisoned:self._keeper_stop.set()
            try:
                auto=self._disk_auto() if self._revision is not None else {'enabled':False,'inherited':False,'state':'off'}
            except Exception:auto={'enabled':None,'inherited':False,'state':'unknown'}
            slots=() if self._data is None else tuple(self._data['slots'][str(uid)] for uid in self._data['shaft_uids'])
            active=any(s.get('consecutive_failures',0)<3 for s in slots)
            return {'inspection_error':self._inspection_error, 'has_artifact':self._inspection_error is not None or self._revision is not None, 'initialized':self._revision is not None,'crypto_available':bool(self.crypto.available),
                'transport_available':self.transport is not None and not poisoned,
                'oauth_review_required':any(s.get('oauth_send_pending',False) for s in slots),
                'socket_shutdown_unverified':poisoned,
                'open':self._data is not None and not poisoned,'closed':self._closed,
                'auto_unlock':auto['enabled'],
                'auto_unlock_state':auto['state'],'auto_unlock_inherited':auto['inherited'],
                'auto_unlock_epoch':self.unlock_request_epoch(),
                'auto_unlock_save_failed':self._auto_unlock_save_failed,
                'known_result_retention':self._known_result_retention_state,
                'known_result_limit':MAX_NORMAL_RESULTS,
                'mode':'unavailable' if poisoned else ('nana7mi' if active else 'fallback'),'busy':self._busy.locked(),
                'slots':() if poisoned else tuple({'index':i+1,'remaining_seconds':remaining(s,self.clock()),'failures':s.get('consecutive_failures',0),
                    'retry_after':proactive_wait(s,self.clock())} for i,s in enumerate(slots))}
    def _snapshot(self):
        with self._mutex:
            self._require_transport_healthy()
            if self._closed or self._data is None:raise VaultError('locked')
            return self._epoch,copy.deepcopy(self._data)
    def inspect_recovery(self):
        """Explicit local diagnosis; grants no permission to resume traffic."""
        inspect=getattr(self.transport,'inspect_network_stop',None)
        shared=inspect() if callable(inspect) else {'state':'unavailable','status_code':None}
        status=self.status()
        return {'shared_stop':shared,'vault_open':status['open'],
                'oauth_review_required':status['oauth_review_required'],
                'socket_shutdown_unverified':status['socket_shutdown_unverified']}
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
        self._availability_reason=None
        for step in range(len(uids)):
            index=(self._round+step)%len(uids);uid=uids[index];s=data['slots'][str(uid)];left=remaining(s,now)
            if not s.get('access_token') or s.get('consecutive_failures',0)>=3:continue
            if self._request_budget is not None and self._request_budget.blocked_until(s)>now:
                self._availability_reason='borrow-accounts-cooling-down';continue
            if left is None or left>120:fresh.append((uid,index))
            elif left>0:last.append((left,uid,index))
        if fresh:uid,index=fresh[0]
        elif last:_,uid,index=max(last)
        else:return None
        self._round=(index+1)%len(uids);return uid
    def _refresh(self,epoch,data,uid=None,proactive=False):
        self._require_premium()
        if self.transport is None:raise VaultError('transport-unavailable')
        now=self.clock()
        self._collect_completed_records()
        if uid is None:uid=keeper_plan(data,now)[0]
        if uid not in data['shaft_uids']:raise VaultError('unknown-slot')
        # Fail closed globally until a separately reviewed explicit resolution.
        if any(s.get('oauth_send_pending',False) for s in data['slots'].values()):
            raise VaultError('pixiv-outcome-uncertain')
        slot=data['slots'][str(uid)]
        oldleft=remaining(slot,now)
        wait=proactive_wait(slot,now) if proactive else max(0,slot.get('backoff_until',0)-now)
        if wait:raise VaultError('backoff')
        # Preflight immutable transport capabilities before the durable
        # uncertainty marker. A missing method cannot have sent OAuth bytes;
        # falsely persisting it would lock the vault on every restart.
        guard=getattr(self.transport,'session_guard',None)
        clear_pending=getattr(self.transport,'clear_pending',None)
        fetch_account=getattr(self.transport,'fetch_account',None)
        if not all(callable(value) for value in (guard,clear_pending,fetch_account)):
            raise VaultError('transport-fence-unavailable')
        # Enter the local-only session fence before publishing the pending
        # marker. A rejected guard cannot have sent an OAuth request; marking
        # it uncertain would falsely immobilize the vault across restarts.
        # The real ShaftTransport guard checks local validity/socket epochs
        # only, so no network bytes can be emitted by __enter__.
        send_guard=getattr(self.transport,'oauth_send_guard',None)
        result_guard=getattr(self.transport,'oauth_result_guard',None)
        fetch_started=False
        marker_error=None
        known_record=None
        record_deferred=False
        record_reserved=False
        record_written=False
        normal_result=False
        def release_failed_reservation():
            if not record_reserved or known_record is None:return
            try:
                self._known_result_retention.release_failed(known_record[0],
                    hashlib.sha256(known_record[2]).hexdigest(),self._key,self.crypto)
            except Exception:self._known_result_retention_state='unavailable'
        def preserve_deferred():
            nonlocal record_deferred
            if known_record is None or not record_deferred:return
            record_deferred=False  # Exactly one attempt; partial evidence stays.
            try:self._store_known_result(epoch,known_record)
            except Exception:self._known_result_retention_state='unavailable'
        def before_oauth_send():
            nonlocal marker_error
            try:
                self._compact_rejections(data,self._key,reserve=True)
                slot['oauth_send_pending']=True
                # Keep the published snapshot separate from the mutable result:
                # a failed final commit must retain the durable pending state.
                self._publish(epoch,copy.deepcopy(data))
            except Exception as error:
                marker_error=error
                raise VaultError('oauth-send-fence-failed') from None
        def oauth_result_known(token=None):
            nonlocal marker_error,known_record,record_deferred,record_reserved,record_written,normal_result
            if not slot.get('oauth_send_pending',False):return
            normal_result=False
            if token is not None:
                # Persist the validated credentials with the resolved marker.
                # A post-CURRENT exception must not lose a rotated token.
                duration=token.get('expires_in')
                lifetime=duration if type(duration) in (int,float) and math.isfinite(duration) and duration>0 else None
                # Preserve the existing short-payout rejection/old-token rule.
                if lifetime is None or lifetime>max(30,oldleft or 0):
                    normal_result=True
                    slot.update({k:token[k] for k in TOKEN_FIELDS if k in token})
                    slot['token_expires_at_ms']=(now+lifetime)*1000 if lifetime is not None else None
                    slot['token_fetched_at']=now
                    slot.pop('account_uid',None)
            slot['oauth_send_pending']=False
            # This journal is insurance for a failed publish, not a prerequisite
            # for persisting an observed result. Capture before compaction so
            # unrelated slots still exactly match the pending base generation.
            record_deferred=normal_result and not self._can_record_normal_result()
            known_record=None;record_reserved=False;record_written=False
            try:
                known_record=self._save_known_result(epoch,data,uid,defer=True)
                if normal_result and not record_deferred:
                    try:
                        # Reserve before any normal record bytes are written.
                        # A failed completion mark cannot bypass the cap.
                        self._known_result_retention.reserve_normal(known_record[0],
                            hashlib.sha256(known_record[2]).hexdigest(),self._key,self.crypto)
                        record_reserved=True
                    except Exception:
                        record_deferred=True
                        self._known_result_retention_state='unavailable'
                if not record_deferred:
                    self._store_known_result(epoch,known_record);record_written=True
            except Exception:
                # A partial record remains immutable and outside the normal cap.
                release_failed_reservation()
            try:
                self._compact_rejections(data,self._key)
                self._publish(epoch,copy.deepcopy(data))
            except Exception as error:
                slot['oauth_send_pending']=True
                marker_error=error
                raise VaultError('oauth-result-fence-failed') from None
        try:
            def current_session():
                with self._mutex:return not self._closed and self._data is not None and epoch==self._epoch
            def already_rejected(digest):
                if any(digest in s.get('rejected_refresh_hashes',[]) for s in data['slots'].values()):return True
                try:return self._rejections.contains(digest,data,self._key,self.crypto,LIMIT)
                except RejectionHistoryError as error:raise VaultError(error.code) from None
            with (guard(current_session),
                  send_guard(before_oauth_send) if callable(send_guard) else nullcontext(),
                  result_guard(oauth_result_known,is_rejected=already_rejected) if callable(result_guard) else nullcontext()):
                slot['last_refresh_attempt_at']=now
                # The original keeper charges its 35-minute proactive interval
                # on every attempt, including rejected/failed payouts.
                if proactive:slot['last_fetch_at']=now
                # Production ShaftTransport calls back after DNS/connect/TLS
                # and immediately before OAuth request emission. Account
                # checks and pre-send failures cannot poison the vault.
                # Opaque older injected transports retain a conservative fence.
                if not callable(send_guard):before_oauth_send()
                fetch_started=True
                result=fetch_account(uid,data['secret_key'],slot,now)
            if type(result) is not dict:raise VaultError('invalid-response')
            incoming={k:result[k] for k in TOKEN_FIELDS+MANAGED_FIELDS if k in result}
            if not incoming.get('access_token'):raise VaultError('invalid-response')
            candidate=copy.deepcopy(data)
            candidate['slots'][str(uid)].update(incoming)
            try:normalize_data(candidate)
            except VaultError:raise VaultError('invalid-token-fields') from None
            newleft=remaining(incoming,now)
            if newleft is not None and newleft<=max(30,oldleft or 0):
                # A fully validated payout has a known outcome, even when its
                # lifetime is too short to replace the current token. Keep
                # rejecting the payout but do not label this observed response
                # as an uncertain in-flight OAuth request across restarts.
                slot['oauth_send_pending']=False
                raise VaultError('short-payout')
            slot.update(incoming);slot.update(last_fetch_at=now,token_fetched_at=now,consecutive_failures=0,backoff_until=0)
            # A complete account result also resolves conservative transports.
            slot['oauth_send_pending']=False
        except Exception as error:
            preserve_deferred()
            release_failed_reservation()
            if marker_error is not None:
                # Marker persistence failed: no profile retry or second
                # publish may hide an ambiguous send/result-state commit.
                if isinstance(marker_error,VaultError):raise marker_error
                raise VaultError('vault-persistence-failed') from None
            if not fetch_started:
                # Guard entry failed before any transport operation.
                if isinstance(error,VaultError):raise
                raise VaultError('refresh-failed') from None
            if isinstance(error,VaultError) and error.code=='invalid-token-fields':
                raise VaultError('invalid-response') from None
            slot['consecutive_failures']=slot.get('consecutive_failures',0)+1
            retry=getattr(error,'retry_at',None)
            if type(retry) in (int,float) and math.isfinite(retry):slot['backoff_until']=max(now,retry)
            try:self._publish(epoch,data)
            except Exception:
                # A local publish failure cannot turn a sent/blocked Pixiv
                # request into a generic keeper error eligible for the next
                # automatic tick. Preserve the original abortive classification
                # even when its diagnostic state could not be saved.
                if (isinstance(error,VaultError) and
                        error.code in ('pixiv-outcome-uncertain','pixiv-network-stopped')):
                    raise VaultError(error.code) from None
                raise
            raise VaultError(error.code if isinstance(error,VaultError) else 'refresh-failed') from None
        try:self._publish(epoch,data)
        except Exception:
            preserve_deferred()
            release_failed_reservation()
            raise
        if normal_result and known_record is not None and record_reserved and record_written:
            # Only fully completed normal refreshes enter the bounded index.
            # Rejections, partial writes and failed refreshes remain unclassified
            # and preserved, outside this normal-retention cap.
            try:
                self._known_result_retention.mark_success(known_record[0],
                    hashlib.sha256(known_record[2]).hexdigest(),self._key,self.crypto)
                if self._maintenance_policy is not None and self._maintenance_policy.allows(self.root):
                    self._known_result_retention.confirm_generation(known_record[0],self._key,self.crypto)
                    self._collect_completed_records()
            except Exception:self._known_result_retention_state='unavailable'
    def refresh(self,uid=None,*,proactive=False):
        if not self._busy.acquire(False):raise VaultError('busy')
        try:
            epoch,data=self._snapshot();self._refresh(epoch,data,uid,proactive)
            return self.status()
        finally:self._busy.release()
    def keeper_tick(self,*,_stop=None):
        if not self._busy.acquire(False):raise VaultError('busy')
        try:
            with self._mutex:
                # A worker may have passed the loop check before local reopen.
                # Validate its own event after admission, not a replacement's.
                if _stop is not None and (_stop is not self._keeper_stop or _stop.is_set()):
                    raise VaultError('keeper-stopped')
                epoch,data=self._snapshot()
            if any(s.get('oauth_send_pending',False) for s in data['slots'].values()):
                raise VaultError('pixiv-outcome-uncertain')
            uid,fetch,wait=keeper_plan(data,self.clock(),self._refresh_interval_hours())
            if hasattr(self.transport,'online_tick'):wait=min(wait,self.transport.online_tick(self.clock()))
            with self._mutex:self._require_transport_healthy()
            if fetch:self._refresh(epoch,data,uid,proactive=True)
            return self._keeper_next_wait(wait) if fetch else wait
        finally:self._busy.release()
    def set_refresh_interval_provider(self,provider):
        """設定の「借用アカウント自動更新間隔（時間）」を返す呼び出し。None を返すか例外なら周期更新は行わない。"""
        self._refresh_interval_provider=provider
    def _refresh_interval_hours(self):
        provider=getattr(self,'_refresh_interval_provider',None)
        if provider is None:return None
        try:return provider()
        except Exception:return None
    def _keeper_next_wait(self,default=300):
        with self._mutex:
            if self._closed or self._data is None:return default
            _,ready,wait=keeper_plan(self._data,self.clock(),self._refresh_interval_hours())
            return min(default,1 if ready else wait)
    def start_keeper(self,*,expected_epoch=None):
        self._require_premium()
        with self._mutex, self._unlock_intent_lock:
            if expected_epoch is not None and expected_epoch!=self._unlock_intent_epoch:raise VaultError('stale-session')
            self._require_transport_healthy()
            if self._closed or self._data is None:raise VaultError('locked')
            if any(s.get('oauth_send_pending',False) for s in self._data['slots'].values()):
                raise VaultError('pixiv-outcome-uncertain')
            if self._keeper is not None and self._keeper.is_alive() and not self._keeper_stop.is_set():return
            self._keeper_stop=threading.Event();stop=self._keeper_stop
            def run():
                while not stop.is_set():
                    try:wait=self.keeper_tick(_stop=stop)
                    except VaultError as error:
                        # A sent Pixiv request with no observed status may
                        # have succeeded. Do not replay it on the next
                        # automatic keeper tick; only an explicit later
                        # operation can start another keeper.
                        if error.code in ('keeper-stopped','pixiv-outcome-uncertain','pixiv-network-stopped',
                                'vault-persistence-failed','vault-too-large','rejected-history-unavailable',
                                'generation-conflict','socket-shutdown-unverified'):return
                        wait=self._keeper_next_wait()
                    except Exception:wait=300
                    if stop.wait(max(1,min(wait,900))):return
            self._keeper=threading.Thread(target=run,name='pixivvault-external-token-keeper',daemon=True);self._keeper.start()
    def search(self,word,bookmark_min=0,*,sort='popular_desc',r18=False,exclude_ai=False,offset=0,start_date=None,end_date=None,page_session=None,with_page_session=False):
        from .external_vault_transport import search_params
        params=search_params(word,bookmark_min,sort=sort,r18=r18,exclude_ai=exclude_ai,offset=offset,start_date=start_date,end_date=end_date)
        return self._app_read('search',params,page_session=page_session,with_page_session=with_page_session)
    def ranking(self,*,mode='day',date=None,offset=0,page_session=None,with_page_session=False):
        from .external_vault_transport import ranking_params
        ranking_params(mode=mode,date=date,offset=offset)
        return self._app_read('ranking',dict(mode=mode,date=date,offset=offset),page_session=page_session,with_page_session=with_page_session)
    def _app_read(self,operation,params,*,page_session=None,with_page_session=False):
        self._require_premium()
        with self._mutex:
            self._require_transport_healthy()
            if self._closed or self._data is None:raise VaultError('locked')
            wait_epoch=self._epoch;stop=self._keeper_stop;was_stopped=stop.is_set()
        deadline=time.monotonic()+self._read_busy_timeout
        while True:
            with self._mutex:
                self._require_transport_healthy()
                if self._closed or self._data is None:raise VaultError('locked')
                if self._epoch!=wait_epoch:raise VaultError('stale-session')
                if not was_stopped and stop.is_set():raise VaultError('keeper-stopped')
            if self._busy.acquire(False):break
            delay=min(0.05,deadline-time.monotonic())
            if delay<=0:raise VaultError('busy')
            time.sleep(delay)
        try:
            self._require_premium()
            epoch,data=self._snapshot()
            if epoch!=wait_epoch:raise VaultError('stale-session')
            if self.transport is None:raise VaultError('transport-unavailable')
            query=tuple(sorted((k,v) for k,v in params.items() if k!='offset'))
            pinned=page_session is not None
            if pinned:
                if (type(page_session) is not _BorrowedPageSession
                        or page_session.owner is not self._paging_owner
                        or page_session.epoch!=epoch or page_session.operation!=operation
                        or page_session.query!=query):raise VaultError('stale-page')
                uid=page_session.requester_uid;slot=data['slots'].get(str(uid),{})
                account=slot.get('account_uid');token=slot.get('access_token','')
                same=(account==page_session.account_uid if page_session.account_uid is not None else
                      hmac.compare_digest(hashlib.sha256(token.encode()).digest(),page_session.token_digest))
                left=remaining(slot,self.clock())
                if (not token or not same or slot.get('consecutive_failures',0)>=3
                        or (left is not None and left<=0)
                        or (self._request_budget is not None and self._request_budget.blocked_until(slot)>self.clock())):
                    raise VaultError('paging-account-unavailable')
            else:uid=self._select(data)
            refreshed=False;payout_errors=[]
            try:
                if uid is None:
                    if self._availability_reason:raise VaultError(self._availability_reason)
                    # At most one pass over configured request UIDs. A payout
                    # quota applies to its requester, not the whole borrowed pool.
                    candidates=sorted(data['shaft_uids'],key=lambda u:(data['slots'][str(u)].get('last_refresh_attempt_at',0),u))
                    last_error=None
                    for candidate in candidates:
                        if data['slots'][str(candidate)].get('backoff_until',0)>self.clock():continue
                        try:self._refresh(epoch,data,candidate)
                        except VaultError as error:
                            if error.code not in ('rate-limited','payout-limit','backoff','expired-payout','not-premium'):raise
                            last_error=error.code;payout_errors.append(error.code)
                        refreshed=True
                        next_epoch,data=self._snapshot()
                        if next_epoch!=epoch:raise VaultError('stale-session')
                        uid=self._select(data)
                        if uid is not None:break
                    if uid is None and last_error is not None:
                        error=VaultError('borrow-accounts-unavailable')
                        # Retain a bounded cause without changing the pool-level
                        # availability code used by other readers and STOP policy.
                        error.reason='expired-payout' if 'expired-payout' in payout_errors else last_error
                        raise error
                if uid is None:raise VaultError('token-unavailable')
                attempts=max(2,len(data['shaft_uids']))
                for attempt in range(attempts):
                    slot=data['slots'][str(uid)]
                    try:
                        guard=getattr(self.transport,'session_guard',None)
                        if not callable(guard) or not callable(getattr(self.transport,'clear_pending',None)):
                            raise VaultError('transport-fence-unavailable')
                        def current_session():
                            with self._mutex:return not self._closed and self._data is not None and epoch==self._epoch
                        account_guard=getattr(self.transport,'borrowed_account',None)
                        account=(self._request_budget.account(slot) if self._request_budget is not None else 'account:unknown')
                        with guard(current_session),account_guard(account) if callable(account_guard) else nullcontext():
                            result=(self.transport.search(slot['access_token'],params) if operation=='search'
                                    else self.transport.ranking(slot['access_token'],**params))
                        if type(result) is not dict or type(result.get('illusts')) is not list:raise VaultError('invalid-response')
                        slot['consecutive_failures']=0;self._publish(epoch,data)
                        self._cache_pages(epoch,result['illusts'])
                        if with_page_session or pinned:
                            account=slot.get('account_uid')
                            account=account if type(account) is int and account>0 else None
                            session=page_session if pinned else _BorrowedPageSession(
                                self._paging_owner,epoch,uid,account,
                                hashlib.sha256(slot['access_token'].encode()).digest(),operation,query)
                            result=dict(result,_borrow_session=session)
                        if operation=='search':
                            result=dict(result,_borrow_warning='expired-payout' if 'expired-payout' in payout_errors else '')
                        return result
                    except Exception as error:
                        if isinstance(error,VaultError) and error.code=='borrowed-rate-limited':
                            if self._request_budget is None:raise VaultError('premium-budget-unavailable') from None
                            self._request_budget.cooldown(slot,error.retry_at)
                            if pinned:raise VaultError('paging-account-unavailable') from None
                            next_uid=self._select(data)
                            if next_uid is None:raise VaultError('borrow-accounts-cooling-down') from None
                            uid=next_uid;continue
                        if isinstance(error,VaultError) and error.code=='account-cooldown':
                            if pinned:raise VaultError('paging-account-unavailable') from None
                            next_uid=self._select(data)
                            if next_uid is None:raise VaultError('borrow-accounts-cooling-down') from None
                            uid=next_uid;continue
                        slot['consecutive_failures']=slot.get('consecutive_failures',0)+1
                        invalid=isinstance(error,VaultError) and error.code=='invalid-grant'
                        if invalid:slot['token_expires_at_ms']=self.clock()*1000
                        self._publish(epoch,data)
                        if pinned and invalid:raise VaultError('paging-account-unavailable') from None
                        if not invalid or attempt>=attempts-1:
                            code=(error.code if isinstance(error,VaultError) else
                                  'transport-failed' if isinstance(error,(TimeoutError,ConnectionError)) else 'search-failed')
                            raise VaultError(code) from None
                        next_uid=self._select(data)
                        if next_uid is None and not refreshed:
                            self._refresh(epoch,data,uid);refreshed=True
                            next_epoch,data=self._snapshot()
                            if next_epoch!=epoch:raise VaultError('stale-session')
                            next_uid=self._select(data)
                        if next_uid is None:raise VaultError('token-unavailable')
                        uid=next_uid
            except VaultError as error:
                if 'expired-payout' in payout_errors:error.warning='expired-payout'
                raise
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
