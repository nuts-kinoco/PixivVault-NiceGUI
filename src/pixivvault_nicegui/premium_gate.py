"""Local activation permission, separate from token custody and Pixiv identity."""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import threading
import time
from uuid import uuid4

from .external_vault import VaultError, VAULT_INTEGRITY_ERRORS
from .external_vault_autounlock import _dpapi
from .managed_downloads import _safe_path,_read_bounded,_write,_fsync_dir
from .fs_paths import native_io_path
from .premium_gate_verifier import SALT,VERIFIER,ITERATIONS

class PremiumGateError(VaultError):pass

class PremiumAuthorization:
    def __init__(self,vault,*,protect=None,clock=time.time,verifier=None):
        self.vault=vault;self.root=vault.root;self.protect=protect or _dpapi;self.clock=clock
        self._verifier=verifier or (SALT,VERIFIER,ITERATIONS)
        self._binding=hashlib.sha256(b'PV-premium-gate-v1\0'+str(self.root).casefold().encode()).digest()
        self._verifier_id=hashlib.sha256(self._verifier[0]+self._verifier[1]).hexdigest()
        self._lock=threading.RLock();self._enabled=False;self._remembered=False
        self._pending=False;self._material=None;self._closed=False;self._error=None
        self._prompting=False;self._epoch=0
        self.startup_suppressed = os.environ.get('PIXIVVAULT_PREMIUM_STARTUP', '').strip().lower() in ('off', '0')
        if not self.startup_suppressed:
            self._restore()
    def _path(self,name):
        p=self.root/('premium-'+name);_safe_path(p,file=True);return p
    def _atomic(self,name,data):
        self.vault._ensure_writable(self.vault._revision)
        p=self._path(name);temporary=self.root/('premium-next-'+uuid4().hex)
        _write(temporary,data)
        try:os.replace(native_io_path(temporary),native_io_path(p));_fsync_dir(self.root)
        finally:
            if native_io_path(temporary).exists():native_io_path(temporary).unlink()
    def _write_protected(self,name,body):
        raw=json.dumps(body,separators=(',',':')).encode()
        sealed=self.protect(raw,self._binding)
        self._atomic(name,sealed)
        if self._read_protected(name)!=body:raise PremiumGateError('premium-save-unconfirmed')
    def _read_protected(self,name):
        p=self._path(name)
        if not native_io_path(p).exists():return None
        raw=self.protect(_read_bounded(p,32768),self._binding,decrypt=True)
        result=json.loads(raw)
        if type(result) is not dict:raise PremiumGateError('premium-record-invalid')
        return result
    def _restore(self, *, require_open=False):
        try:
            fence=self._path('activation-state')
            try:
                state=_read_bounded(fence,16)
            except FileNotFoundError:
                # A missing activation record is not authorization to modify files.
                return
            if state == b'off\n':
                # 明示的な OFF 記録がある場合だけ、旧版が残した保存済みの鍵を取り除く。
                # 記録が無い場合は従来どおりファイルに触れない。
                self._remove_material()
                return
            if state != b'on\n':
                return
            proof=self._read_protected('activation.dpapi')
            if proof!={'purpose':'pv-premium-permission-v1','verifier':self._verifier_id,'enabled':True}:return
            self._enabled=self._remembered=True
            self._load_material()
            try:
                opened = self.restore_vault()
                if require_open and not opened:
                    self._enabled=self._remembered=False
            except VaultError as error:
                if error.code!='unlock-failed':raise
                self._material=None
                self._enabled=self._remembered=False;self._error='premium-material-stale'
        except VaultError as error:
            self._enabled=self._remembered=False
            self._error=error.code if error.code in VAULT_INTEGRITY_ERRORS else 'premium-restore-unconfirmed'
        except Exception:
            self._enabled=self._remembered=False;self._error='premium-restore-unconfirmed'
    def _attempts(self):
        p=self._path('attempts.json')
        if not native_io_path(p).exists():return dict(failures=0,next_at=0)
        try:
            state=json.loads(_read_bounded(p,2048))
            if (set(state)!={'failures','next_at'} or type(state['failures']) is not int or not 0<=state['failures']<=1000
                    or type(state['next_at']) not in (float,int) or not 0<=state['next_at']<1e12):raise ValueError()
            return state
        except Exception:raise PremiumGateError('premium-attempt-state-unavailable') from None
    def status(self):
        with self._lock:
            try:wait=max(0,self._attempts()['next_at']-self.clock())
            except PremiumGateError:wait=900
            return dict(enabled=self._enabled and not self._closed,remembered=self._remembered,
                pending=self._pending,retry_after=wait,error=self._error)
    def require_enabled(self):
        with self._lock:
            if self._closed or not self._enabled:raise PremiumGateError('premium-disabled')
    def begin_prompt(self):
        with self._lock:
            if self._closed or self._enabled or self._prompting:return None
            self._prompting=True;return self._epoch
    def end_prompt(self):
        with self._lock:self._prompting=False;self._pending=False
    def authenticate(self,password,*,expected_epoch=None):
        """Only a boolean outcome escapes; plaintext is neither stored nor logged."""
        with self._lock:
            if self._closed:raise PremiumGateError('premium-disabled')
            if expected_epoch is not None and expected_epoch!=self._epoch:raise PremiumGateError('stale-session')
            self._pending=False
            state=self._attempts();now=self.clock()
            if state['next_at']>now:raise PremiumGateError('premium-password-wait')
            salt,expected,rounds=self._verifier
            candidate=hashlib.pbkdf2_hmac('sha256',str(password).encode('utf-8'),salt,rounds)
            if not hmac.compare_digest(candidate,expected):
                count=min(1000,state['failures']+1)
                self._atomic('attempts.json',json.dumps(dict(failures=count,next_at=now+min(900,2**min(count-1,10)))).encode())
                raise PremiumGateError('premium-password-rejected')
            self._atomic('attempts.json',b'{"failures":0,"next_at":0}')
            self._pending=False;self._error=None
            try:
                self._load_material()
                if self.vault.status()['open']:
                    self._capture_material()
                elif self.vault.status()['initialized']:
                    if self._material is not None:
                        try:self._restore_key()
                        except VaultError as error:
                            if error.code!='unlock-failed':raise
                            self._material=None
                    if self._material is None:
                        try:self.vault.unlock(password)
                        except VaultError as error:
                            if error.code!='unlock-failed':raise
                            # Only a password mismatch permits the separate
                            # legacy maintenance flow. Corruption cannot enable borrowing.
                            self._error='premium-legacy-unlock-required'
                        else:self._capture_material()
                elif self._material is None:
                    if not self.vault.crypto.available:raise PremiumGateError('crypto-unavailable')
                    salt=os.urandom(16);verify,key=self.vault.crypto.derive(password,salt)
                    self._material=dict(salt=base64.b64encode(salt).decode(),verify=base64.b64encode(verify).decode(),key=base64.b64encode(key).decode())
            except VaultError as error:
                if error.code in VAULT_INTEGRITY_ERRORS:
                    self._enabled=self._remembered=False;self._material=None
                    self._error=error.code
                    self._atomic('activation-state',b'off\n')
                raise
            if self._error=='premium-legacy-unlock-required':
                # Not yet fully verified: keep the earlier failure count so that
                # wrong vault passwords in unlock_legacy keep escalating.
                self._atomic('attempts.json',json.dumps(dict(failures=state['failures'],next_at=0)).encode())
            # Password is only a transient parameter; keep derived material.
            self._pending=True
            return True
    def unlock_legacy(self,vault_password,*,expected_epoch=None):
        """Second step when the vault password differs from the activation one.

        Only valid after authenticate() reported premium-legacy-unlock-required.
        A wrong vault password shares the activation backoff; no plaintext is kept.
        """
        with self._lock:
            if self._closed:raise PremiumGateError('premium-disabled')
            if expected_epoch is not None and expected_epoch!=self._epoch:raise PremiumGateError('stale-session')
            if not self._pending or self._error!='premium-legacy-unlock-required':
                raise PremiumGateError('premium-confirmation-required')
            state=self._attempts();now=self.clock()
            if state['next_at']>now:raise PremiumGateError('premium-password-wait')
            try:self.vault.unlock(vault_password)
            except VaultError as error:
                if error.code=='unlock-failed':
                    count=min(1000,state['failures']+1)
                    self._atomic('attempts.json',json.dumps(dict(failures=count,next_at=now+min(900,2**min(count-1,10)))).encode())
                    raise PremiumGateError('premium-vault-password-rejected') from None
                if error.code in VAULT_INTEGRITY_ERRORS:
                    self._enabled=self._remembered=False;self._material=None;self._pending=False
                    self._error=error.code
                    self._atomic('activation-state',b'off\n')
                raise
            self._capture_material()
            self._error=None
            self._atomic('attempts.json',b'{"failures":0,"next_at":0}')
    def confirm(self,remember,*,expected_epoch=None):
        with self._lock:
            if self._error in VAULT_INTEGRITY_ERRORS:
                self._pending=False
                raise PremiumGateError(self._error)
            if not self._pending or self._closed or type(remember) is not bool:raise PremiumGateError('premium-confirmation-required')
            if expected_epoch is not None and expected_epoch!=self._epoch:raise PremiumGateError('stale-session')
            # Saved permission is independent of the key-custody record.
            if not remember:self._remove_material()
            self._atomic('activation-state',b'off\n')
            if remember and self._material is not None:self._write_protected('material.dpapi',self._material)
            self._write_protected('activation.dpapi',dict(purpose='pv-premium-permission-v1',verifier=self._verifier_id,enabled=remember))
            if remember:self._atomic('activation-state',b'on\n')
            self._enabled=True;self._remembered=remember;self._pending=False
    def _load_material(self):
        record=self._read_protected('material.dpapi')
        if record is None:return
        try:
            if set(record)!={'salt','verify','key'}:raise ValueError()
            decoded={k:base64.b64decode(v,validate=True) for k,v in record.items()}
            if len(decoded['salt'])!=16 or len(decoded['verify'])!=32 or len(decoded['key'])!=32:raise ValueError()
        except Exception:raise PremiumGateError('premium-material-invalid') from None
        self._material=record
    def _capture_material(self):
        with self.vault._mutex:
            if self.vault._key is None:raise PremiumGateError('locked')
            self._material=dict(salt=self.vault._meta['salt'],verify=self.vault._meta['verify'],key=base64.b64encode(self.vault._key).decode())
    def _remove_material(self):
        path=self._path('material.dpapi')
        # ファイルが無いときは書き込みロックを取らない（別インスタンスの保持と競合させない）。
        if native_io_path(path).exists():
            self.vault._ensure_writable(self.vault._revision)
            native_io_path(path).unlink();_fsync_dir(self.root)
    def _restore_key(self):
        if self._material is not None:self.vault.unlock_derived(self._material)
    def resume_saved(self):
        """Reopen borrowing only from the existing protected startup permission."""
        with self._lock:
            if self._closed or self._enabled or self._prompting:
                return False
            if not (self._remembered or self.startup_suppressed):
                return False
            self._remembered = False
            self._error = None
            self._restore(require_open=True)
            if not self._enabled:
                return False
            if not self.vault.status().get('oauth_review_required'):
                self.vault.start_keeper()
            return True

    def restore_vault(self):
        self.require_enabled()
        if self.vault.status()['initialized'] and not self.vault.status()['open']:
            if self._material is None:self._error='premium-legacy-unlock-required';return False
            self._restore_key()
        return self.vault.status()['open']
    def initialize_vault(self,uids,secret):
        with self._lock:
            self.require_enabled()
            if self._material is None:raise PremiumGateError('premium-material-unavailable')
            self.vault.initialize_derived(self._material,uids,secret)
    def remember_local_unlock(self):
        with self._lock:
            self.require_enabled();self._capture_material()
            self._error=None
    def cancel(self):
        with self._lock:self._pending=False
    def disable(self, *, session_only=False):
        with self._lock:
            self._epoch+=1
            self._enabled=False;self._pending=False
            if session_only:
                # The header pauses borrowing in this process. Durable revocation
                # is still used by the recovery and failure paths.
                self._material=None
                self.vault.lock()
                return
            self._remembered=False
            try:
                # OFF fence is authoritative even if DPAPI subsequently fails.
                self._atomic('activation-state',b'off\n')
                self._write_protected('activation.dpapi',dict(purpose='pv-premium-permission-v1',verifier=self._verifier_id,enabled=False))
            finally:
                try:self._remove_material()
                finally:
                    self._material=None
                    if self.vault.status()['initialized']:self.vault.lock_explicitly()
                    else:self.vault.lock()
    def close(self):
        with self._lock:self._closed=True;self._enabled=False;self._pending=False;self._material=None;self._epoch+=1
