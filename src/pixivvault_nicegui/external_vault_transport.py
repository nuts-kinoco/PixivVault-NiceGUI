"""Isolated Shaft/OAuth/search transport. Never borrows a Cookie session.

The request callable is explicit and can be entirely synthetic. No network
happens on construction. Profiles are explicit deployment inputs; credentials
are never put in a repr, exception, log, UI status, or source defaults.
"""
from dataclasses import dataclass, field
from contextlib import contextmanager
from datetime import datetime
import hashlib
import hmac
import json
import math
import threading
from urllib.parse import urlencode, urlsplit
from .external_vault import (VaultError, _FirstPayoutGateBusy, _SharedGateBusy, TOKEN_FIELDS,
    remaining, normalize_data)

ACCOUNT='https://pixshaft.com/v1/account/nana7mi'
INVALID='https://pixshaft.com/v1/account/nana7mi/invalid'
ONLINE='https://pixshaft.com/v1/account/online'
OAUTH='https://oauth.secure.pixiv.net/auth/token'
SEARCH='https://app-api.pixiv.net/v1/search/illust'
RANKING='https://app-api.pixiv.net/v1/illust/ranking'
APP_READ_ROUTES=(SEARCH,RANKING)

def ranking_params(*,mode='day',date=None,offset=0):
    if mode not in ('day','week','month','week_rookie','day_r18','week_r18'):
        raise VaultError('invalid-ranking')
    if type(offset) is not int or not 0<=offset<=30000:raise VaultError('invalid-ranking')
    params={'mode':mode,'filter':'for_ios'}
    if date is not None:
        try:
            if type(date) is not str or len(date)!=8 or not date.isascii() or not date.isdecimal():raise ValueError()
            parsed=datetime.strptime(date,'%Y%m%d')
        except ValueError:raise VaultError('invalid-ranking') from None
        params['date']=parsed.strftime('%Y-%m-%d')
    if offset:params['offset']=offset
    return params

@dataclass(frozen=True,repr=False)
class VaultResponse:
    status: int
    body: dict=field(repr=False)
    headers: dict=field(default_factory=dict,repr=False)

@dataclass(frozen=True,repr=False)
class OAuthProfile:
    ua: str
    os: str
    os_version: str
    version: str
    salt: str
    client_id: str
    client_secret: str

class RateLimited(VaultError):
    def __init__(self,retry_at):super().__init__('rate-limited');self.retry_at=retry_at

class BorrowedRateLimited(VaultError):
    def __init__(self,retry_at):super().__init__('borrowed-rate-limited');self.retry_at=retry_at


def search_params(word,bookmark_min=0,*,sort='popular_desc',r18=False,exclude_ai=False,offset=0,start_date=None,end_date=None):
    if type(word) is not str or not word.strip() or len(word)>1024 or any(ord(c)<32 for c in word):raise VaultError('invalid-search')
    if type(bookmark_min) is not int or bookmark_min<0 or type(offset) is not int or offset<0 or sort not in ('popular_desc','date_desc','date_asc') or type(r18) is not bool or type(exclude_ai) is not bool:raise VaultError('invalid-search')
    # Original app-api route does not send r18: preserve its meaning/behavior.
    params={'word':word,'sort':sort,'search_target':'partial_match_for_tags','bookmark_num_min':bookmark_min,'filter':'for_ios'}
    dates={}
    for key,value in (('start_date',start_date),('end_date',end_date)):
        if value in (None,''):continue
        try:
            if type(value) is not str or len(value)!=10 or datetime.strptime(value,'%Y-%m-%d').strftime('%Y-%m-%d')!=value:raise ValueError()
        except (ValueError,TypeError):raise VaultError('invalid-search-date') from None
        dates[key]=value
    if len(dates)==2 and dates['start_date']>dates['end_date']:raise VaultError('invalid-search-date')
    params.update(dates)
    if exclude_ai:params['search_ai_type']=0
    if offset:params['offset']=offset
    return params


def canonical(payload):return json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')

def token_fields(raw):
    if type(raw) is not dict:return {}
    return {k:raw[k] for k in TOKEN_FIELDS if k in raw}

def online_fields(raw):
    result=token_fields(raw)
    if type(raw) is not dict:return result
    if 'scope' in raw:result['scope']=raw['scope']
    if type(raw.get('user')) is dict:result['user']={k:raw['user'][k] for k in ('id','is_premium','name','account') if k in raw['user']}
    return result

def _uid(value):
    try:result=int(value)
    except (ValueError,TypeError):return None
    return result if result>0 and type(value) is not bool else None

def build_online_payload(account_uid,account):
    safe=online_fields(account);user=dict(safe.get('user',{}));uid=_uid(user.get('id')) or _uid(account_uid)
    if uid is None:raise VaultError('account-identity-unavailable')
    user['id']=uid;safe['user']=user
    return {'uid':uid,'account':safe,'premiumAgeMs':0}

def quota_retry_at(body,now,default=None):
    values=[]
    for quota in body.get('quotas',[]) if type(body.get('quotas')) is list else []:
        if type(quota) is not dict:continue
        value=quota.get('resetsAt')
        exhausted=(quota.get('remaining')==0 or
            (type(quota.get('used')) in (int,float) and type(quota.get('max')) in (int,float)
             and quota['used']>=quota['max']))
        if exhausted and type(value) in (int,float) and math.isfinite(value) and value/1000>now:values.append(value/1000)
    return max(values) if values else now+2100 if default is None else default


class ShaftTransport:
    def __init__(self,request,*,oauth_profiles,shaft_user_agent='PixivVault',clock=None):
        from .vault_socket_fence import SocketFence
        self._socket_fence=SocketFence()
        self.request=request;self.profiles=tuple(oauth_profiles);self.shaft_user_agent=shaft_user_agent
        self.clock=clock or (lambda:datetime.now().astimezone());self._online=[];self._online_lock=threading.RLock();self._online_generation=0;self._session=threading.local()
        # A rejected app-client pair is not a rejected borrowed account. Keep
        # this ordering hint in this transport/process only, never in the vault.
        self._oauth_profile_lock=threading.RLock();self._oauth_rejected_profiles=set()
        # Real direct Pixiv HTTP cannot operate outside an explicitly sealed
        # Windows-user STOP owner. Synthetic injected callable transport tests
        # remain isolated from production ApplicationServices binding.
        self._pixiv_stop_bound=False
        self._pixiv_shared_stop=None
        self._premium_authorized=None
        self._budget=None
        self.borrowed_429_scope='shared'
        if not self.profiles or any(not isinstance(p,OAuthProfile) for p in self.profiles):raise VaultError('profile-unavailable')

    def bind_shared_pixiv_stop(self,scope):
        from .managed_network_stop_shared_scope import ManagedUserNetworkStopScope
        if scope is not None and type(scope) is not ManagedUserNetworkStopScope:
            raise VaultError('shared-stop-owner-invalid')
        if scope is not None:scope._check_owner()
        self._pixiv_shared_stop=scope
        self._pixiv_stop_bound=True
    def bind_premium_authorization(self,require_enabled):
        if not callable(require_enabled):raise VaultError('premium-authorization-unavailable')
        self._premium_authorized=require_enabled
    def bind_request_budget(self,reserve):self._budget=reserve
    def set_borrowed_429_scope(self,scope):
        if scope not in ('shared','account'):raise VaultError('invalid-quota-policy')
        if scope=='account' and self._budget is None:raise VaultError('premium-budget-unavailable')
        self.borrowed_429_scope=scope
    @contextmanager
    def borrowed_account(self,account):
        previous=getattr(self._session,'account',None);self._session.account=account
        try:yield
        finally:self._session.account=previous
    @property
    def revocation_unverified(self):
        """Read-only, process-local shutdown failure; restart is required."""
        return self._socket_fence.revocation_unverified
    def inspect_network_stop(self):
        """Local observation only: no initialization, clearing, or request."""
        scope=self._pixiv_shared_stop
        if not self._pixiv_stop_bound or scope is None:
            return {'state':'unavailable','status_code':None}
        try:
            scope._check_owner()
            state=scope.gate.snapshot()
        except Exception:
            # Never expose paths, credentials, or arbitrary exception text.
            return {'state':'unavailable','status_code':None}
        return {'state':'stopped' if state.blocked else 'clear',
                'status_code':state.status_code if state.blocked else None}
    @contextmanager
    def session_guard(self,valid):
        previous=getattr(self._session,'valid',None);old_epoch=getattr(self._session,'socket_epoch',None)
        epoch=self._socket_fence.snapshot() if old_epoch is None else old_epoch
        self._session.valid=valid;self._session.socket_epoch=epoch
        try:
            self._check_session()
            yield
        finally:self._session.valid=previous;self._session.socket_epoch=old_epoch
    def _check_session(self):
        valid=getattr(self._session,'valid',None)
        if valid is not None and not valid():raise VaultError('stale-session')
        if self.revocation_unverified:raise VaultError('socket-shutdown-unverified')
        epoch=getattr(self._session,'socket_epoch',None)
        if epoch is not None:
            from .vault_socket_fence import SocketFenceError
            try:self._socket_fence.require_healthy(epoch)
            except SocketFenceError:
                if self.revocation_unverified:raise VaultError('socket-shutdown-unverified') from None
                raise VaultError('stale-session') from None
    @contextmanager
    def oauth_send_guard(self,before_send):
        """Local durability callback, invoked only at an OAuth HTTP send."""
        previous=getattr(self._session,'before_oauth_send',None)
        self._session.before_oauth_send=before_send
        try:yield
        finally:self._session.before_oauth_send=previous
    def _before_oauth_send(self):
        callback=getattr(self._session,'before_oauth_send',None)
        if callback is not None:callback()
    @contextmanager
    def oauth_result_guard(self,on_known_result,*,is_rejected=None):
        """Observe a resolved OAuth result before ancillary account work."""
        previous=getattr(self._session,'oauth_result_known',None)
        previous_rejected=getattr(self._session,'oauth_refresh_rejected',None)
        self._session.oauth_result_known=on_known_result
        self._session.oauth_refresh_rejected=is_rejected
        try:yield
        finally:
            self._session.oauth_result_known=previous
            self._session.oauth_refresh_rejected=previous_rejected
    def _oauth_result_known(self,token=None):
        callback=getattr(self._session,'oauth_result_known',None)
        if callback is not None:
            if token is None:callback()
            else:callback(token)
    def _request(self,method,url,headers,body=None,*,reserve=None):
        """Admit Pixiv and Shaft requests through the sealed shared STOP.

        Only direct Pixiv responses can latch Pixiv STOP. Shaft responses
        still drive the existing payout and report retry/quotas.
        """
        if self._premium_authorized is not None:self._premium_authorized()
        elif type(self.request) is UrlopenVaultRequest:raise VaultError('premium-disabled')
        from .managed_network_stop_gate import (
            ManagedNetworkGateBusy, ManagedNetworkGateStorageError,
            ManagedNetworkStopped, STOP_STATUSES,
        )
        from .pixiv_read_client import HTTPStatusRejected, PixivReadError
        self._check_session()
        route=url.split('?',1)[0]
        direct_pixiv=route in (*APP_READ_ROUTES,OAUTH)
        if not direct_pixiv and route not in (ACCOUNT,INVALID,ONLINE):
            raise VaultError('destination-rejected')
        # These are deterministic local refusals, before any attempt to emit
        # HTTP. A malformed request must not leave an in-flight marker that
        # would permanently poison the user's unrelated Native/CLI GETs.
        if ((route in APP_READ_ROUTES and (method!='GET' or body is not None)) or
                (route==OAUTH and (method!='POST' or type(body) is not bytes))):
            raise VaultError('pixiv-method-rejected')
        if (type(headers) is not dict or
                any(type(k) is not str or k.lower()=='cookie' for k in headers)):
            raise VaultError('cookie-rejected')
        if (isinstance(self.request,UrlopenVaultRequest) and
                getattr(self._session,'socket_epoch',None) is None):
            raise VaultError('session-required')
        scope=self._pixiv_shared_stop
        if not self._pixiv_stop_bound:
            # A directly constructed real transport must never become an
            # alternate unowned Pixiv network entry. Fake injected callable
            # transports are usable without real HTTP in standalone tests.
            if isinstance(self.request,UrlopenVaultRequest):
                raise VaultError('shared-stop-migration-required')
            if reserve is not None:reserve()
            return self._request_unfenced(method,url,headers,body)
        if scope is None:
            raise VaultError('shared-stop-migration-required')
        status_seen=None
        send_attempted=False

        def observe_status(status):
            nonlocal status_seen
            if type(status) is int and 100<=status<=599:
                status_seen=status

        def observe_send():
            nonlocal send_attempted
            if route==OAUTH:self._before_oauth_send()
            send_attempted=True

        class _NonfatalCarrier(PixivReadError):
            """Recognized by the existing shared-gate nonfatal cleanup."""
            def __init__(self, vault_error):
                self.original=vault_error
                Exception.__init__(self,'bounded Pixiv response nonfatal')

        try:
            scope._check_owner()
            with scope.gate.request():
                try:
                    if reserve is not None:reserve()
                    result=self._request_unfenced(method,url,headers,body,
                        observe_status=observe_status,observe_send=observe_send)
                    if not direct_pixiv:
                        return result
                    # A complete 408/425/5xx HTTP response does not establish
                    # whether the already-sent Pixiv operation took effect.
                    # Keep the shared gate's ordinary-error marker cleanup,
                    # but prevent a second OAuth profile or GET by returning
                    # the same uncertain-outcome code as body failures.
                    if (isinstance(self.request,UrlopenVaultRequest)
                            and route!=OAUTH and send_attempted
                            and (result.status in (408,425) or
                                 500<=result.status<=599)):
                        raise VaultError('pixiv-outcome-uncertain')
                except VaultError as error:
                    if not direct_pixiv and not self.revocation_unverified:
                        raise _NonfatalCarrier(error) from None
                    if direct_pixiv and status_seen in STOP_STATUSES and not (route==OAUTH and status_seen in (400,403)):
                        # Header status always overrides body, timeout and
                        # even cleanup faults, before the durable gate unlock.
                        raise HTTPStatusRejected(status_seen) from None
                    # An ordinary error completes the bounded transport's
                    # in-flight marker, but the request may have been sent
                    # without a usable HTTP result (including a status line
                    # read before header parsing failed). Preserve that
                    # uncertainty for retry policy at the caller: never send
                    # another OAuth profile or repeat this operation when
                    # its result is unknown. A pre-send error retains its
                    # original code and existing retry policy.
                    # A failed socket shutdown/close has *not* proven that
                    # the owner stopped, so keep that uncertainty durable.
                    if (isinstance(self.request,UrlopenVaultRequest)
                            and not self.revocation_unverified):
                        if (send_attempted
                                and error.code!='stale-session'):
                            error=VaultError('pixiv-outcome-uncertain')
                        raise _NonfatalCarrier(error) from None
                    raise
                if route==OAUTH and result.status in (401,429):
                    self._oauth_result_known()
                body_text=json.dumps(result.body).lower()
                challenge=any(word in body_text for word in ('captcha','challenge','login_required','login required'))
                account_quota=(result.status==429 and route in APP_READ_ROUTES
                    and self.borrowed_429_scope=='account' and not challenge)
                if result.status in STOP_STATUSES and not account_quota and not (route==OAUTH and result.status in (400,403) and not challenge):
                    raise HTTPStatusRejected(result.status)
                return result
        except _NonfatalCarrier as carrier:
            raise carrier.original from None
        except ManagedNetworkGateBusy:
            # A different reader owns the short-lived process/thread GET lock.
            # This is not a latched 401/403/429 and must not be reported as one.
            raise _SharedGateBusy() from None
        except (ManagedNetworkGateStorageError,ManagedNetworkStopped,HTTPStatusRejected):
            raise VaultError('pixiv-network-stopped') from None

    def _request_unfenced(self,method,url,headers,body=None,*,observe_status=None,observe_send=None):
        self._check_session()
        if url.split('?',1)[0] not in (ACCOUNT,INVALID,ONLINE,OAUTH,*APP_READ_ROUTES):raise VaultError('destination-rejected')
        if any(k.lower()=='cookie' for k in headers):raise VaultError('cookie-rejected')
        try:
            epoch=getattr(self._session,'socket_epoch',None)
            fenced=getattr(self.request,'fenced_request',None)
            if epoch is not None:
                if not callable(fenced):raise VaultError('transport-fence-unavailable')
                extras=(dict(observe_status=observe_status,observe_send=observe_send)
                        if isinstance(self.request,UrlopenVaultRequest) else {})
                if not isinstance(self.request,UrlopenVaultRequest) and url.split('?',1)[0]==OAUTH:
                    self._before_oauth_send()
                response=fenced(method,url,headers=headers,body=body,timeout=10,
                    fence=self._socket_fence,epoch=epoch,validate=self._check_session,
                    **extras)
            else:
                if isinstance(self.request,UrlopenVaultRequest):raise VaultError('session-required')
                if url.split('?',1)[0]==OAUTH:self._before_oauth_send()
                response=self.request(method,url,headers=headers,body=body,timeout=10)
            if not isinstance(response,VaultResponse) or type(response.body) is not dict:raise ValueError()
            return response
        except VaultError:raise
        except Exception:raise VaultError('transport-failed') from None
    def _signed(self,url,payload,secret,*,reserve=None):
        body=canonical(payload)
        return self._request('POST',url,{'X-Shaft-Sign':hmac.new(secret.encode('utf-8'),body,hashlib.sha256).hexdigest(),
            'Content-Type':'application/json','User-Agent':self.shaft_user_agent},body,reserve=reserve)
    def _refresh_oauth(self,refresh_token,slot=None):
        digest=(hashlib.sha256(refresh_token.encode('utf-8')).hexdigest()
                if type(refresh_token) is str else None)
        rejected=getattr(self._session,'oauth_refresh_rejected',None)
        if digest is not None and (
                (slot is not None and digest in slot.get('rejected_refresh_hashes',[]))
                or (rejected is not None and rejected(digest))):
            # Known invalid_grant from this vault, including its other UIDs.
            # Still allow the bounded ACCOUNT re-fetch to supply a new token.
            raise VaultError('refresh-token-rejected')
        with self._oauth_profile_lock:
            profiles=sorted(enumerate(self.profiles),key=lambda item:item[0] in self._oauth_rejected_profiles)
        for profile_index,profile in profiles:
            body=urlencode({'grant_type':'refresh_token','refresh_token':refresh_token,'client_id':profile.client_id,
                'client_secret':profile.client_secret,'include_policy':'true'}).encode('ascii')
            try:response=self._request('POST',OAUTH,{'User-Agent':profile.ua,'Content-Type':'application/x-www-form-urlencoded'},body)
            except VaultError as error:
                # A shared STOP/storage fault after send can surface only
                # while releasing the gate. Do not send the same OAuth
                # refresh through a second profile after that outcome.
                if error.code in ('stale-session','pixiv-outcome-uncertain','pixiv-network-stopped','oauth-send-fence-failed','oauth-result-fence-failed'):raise
                continue
            error=response.body.get('error');errors=response.body.get('errors')
            system=errors.get('system') if isinstance(errors,dict) else None
            client_codes=[part.get('code') for part in (response.body,error,system) if isinstance(part,dict)]
            if response.status in (400,403) and any(str(code)=='918' for code in client_codes):
                with self._oauth_profile_lock:self._oauth_rejected_profiles.add(profile_index)
            if response.status==400 and 'invalid_grant' in json.dumps(response.body):
                if slot is not None and digest is not None:
                    history=slot.setdefault('rejected_refresh_hashes',[])
                    if digest not in history:history.append(digest)
                self._oauth_result_known()
                raise VaultError('invalid-grant')
            if 200<=response.status<300 and token_fields(response.body).get('access_token'):
                token=token_fields(response.body)
                # Reuse the vault's token-field validation before declaring
                # success known. Malformed credential replies stay fenced.
                try:normalize_data({'secret_key':'local-validation','shaft_uids':[1],'slots':{'1':token}})
                except VaultError:pass
                else:self._oauth_result_known(token)
                return token,online_fields(response.body)
            if isinstance(self.request,UrlopenVaultRequest) and 200<=response.status<300:
                raise VaultError('pixiv-outcome-uncertain')
            # A complete HTTP error reply is a known rejected refresh. Keep only
            # genuinely missing transport outcomes pending across restarts.
            self._oauth_result_known()
        raise VaultError('oauth-failed')
    def fetch_account(self,request_uid,secret,slot,now):
        # Match the original product: request a Shaft payout first. Only
        # refresh the credential returned by an expired/short payout below.
        # The service's durable unknown-send fence remains authoritative.
        for attempt in range(2):
            if slot.get('payout_not_before',0)>now:raise RateLimited(slot['payout_not_before'])
            reserve=(lambda:self._budget('payout',request_uid)) if self._budget is not None else None
            try:
                response=self._signed(ACCOUNT,{'uid':request_uid},secret,reserve=reserve)
            except _SharedGateBusy:
                if attempt==0:
                    raise _FirstPayoutGateBusy() from None
                raise
            if response.status==429:raise RateLimited(quota_retry_at(response.body,now))
            if not 200<=response.status<300:raise VaultError('payout-failed')
            body=response.body;account=body.get('account',{});token=token_fields(account)
            slot['payout_not_before']=quota_retry_at(body,now,default=0)
            expiry=body.get('expiresAt');expiry=expiry if type(expiry) in (int,float) and math.isfinite(expiry) else None
            if expiry is None:
                duration=token.get('expires_in')
                if type(duration) in (int,float) and math.isfinite(duration) and duration>0:expiry=(now+duration)*1000
            left=None if expiry is None else expiry/1000-now;threshold=max(300,remaining(slot,now) or 0)
            # Top-level uid is the requester; never report it as the borrower.
            user=account.get('user') if type(account) is dict else None
            borrowed_uid=_uid(user.get('id')) if type(user) is dict else None
            if type(user) is dict and user.get('is_premium') is False:raise VaultError('not-premium')
            if body.get('expired') or (left is not None and left<=threshold):
                refresh=account.get('refresh_token') if type(account) is dict else None
                if not refresh:
                    if not attempt:continue
                    raise VaultError('expired-payout')
                try:token,online=self._refresh_oauth(refresh,slot)
                except VaultError as error:
                    if error.code in ('stale-session','pixiv-outcome-uncertain','pixiv-network-stopped','oauth-send-fence-failed','oauth-result-fence-failed','rejected-history-unavailable'):raise
                    if error.code=='invalid-grant':self._report_invalid(secret,borrowed_uid,refresh,slot,now)
                    if not attempt:continue
                    raise VaultError('expired-payout') from None
                duration=token.get('expires_in')
                expiry=(now+duration)*1000 if type(duration) in (int,float) and math.isfinite(duration) and duration>0 else None
                borrowed_uid=_uid(online.get('user',{}).get('id')) or borrowed_uid
                if borrowed_uid is not None:
                    self.queue_online(secret,borrowed_uid,online,now)
                    self.online_tick(now)
            if not token.get('access_token'):raise VaultError('invalid-payout')
            token.update(token_expires_at_ms=expiry,account_uid=borrowed_uid,payout_not_before=slot['payout_not_before'])
            return token
        raise VaultError('expired-payout')
    def _report_invalid(self,secret,account_uid,refresh,slot,now):
        if _uid(account_uid) is None:return
        digest=hashlib.sha256(refresh.encode('utf-8')).hexdigest()
        if digest==slot.get('reported_refresh_hash') or slot.get('backoff_until',0)>now:return
        try:self._signed(INVALID,{'uid':account_uid,'refreshTokenHash':digest},secret)
        except VaultError as error:
            if error.code in ('stale-session','pixiv-outcome-uncertain','pixiv-network-stopped',
                              'pixiv-network-busy','shared-stop-migration-required'):raise
        slot['reported_refresh_hash']=digest
    def queue_online(self,secret,uid,account,now):
        with self._online_lock:generation=self._online_generation
        self._check_session()
        try:payload=build_online_payload(uid,account)
        except VaultError:return
        if not payload['account'].get('access_token') or not payload['account'].get('refresh_token'):return
        with self._online_lock:
            if generation!=self._online_generation:raise VaultError('stale-session')
            self._online.append({'secret':secret,'payload':payload,'due':now,'attempt':0,'waited':0,'generation':generation})
    def clear_pending(self):
        with self._online_lock:
            self._online_generation+=1;self._online.clear()
        from .vault_socket_fence import SocketFenceError
        try:self._socket_fence.invalidate()
        except SocketFenceError:raise VaultError('socket-shutdown-unverified') from None
    def online_tick(self,now):
        # Claim a batch under the lock, then release it during network I/O.
        # Lock/close invalidates the generation and suppresses late retries.
        with self._online_lock:
            if self.revocation_unverified:
                self._online_generation+=1;self._online.clear()
                return 900
            pending=self._online;self._online=[]
        for task in pending:
            with self._online_lock:
                if task['generation']!=self._online_generation:continue
                if task['due']>now:
                    self._online.append(task);continue
            task['attempt']+=1;delay=None;retry=False
            try:
                previous=getattr(self._session,'valid',None)
                def current_task():
                    if previous is not None and not previous():return False
                    with self._online_lock:return task['generation']==self._online_generation
                with self.session_guard(current_task):
                    response=self._signed(ONLINE,task['payload'],task['secret'])
                status=response.status
                if 200<=status<300:continue
                if status==429:
                    quotas=response.body.get('quotas',[]);quotas=quotas if type(quotas) is list else []
                    exhausted=[q for q in quotas if type(q) is dict and (q.get('remaining')==0 or (type(q.get('used')) in (int,float) and type(q.get('max')) in (int,float) and q['used']>=q['max']))]
                    if any(q.get('scope') in ('uid_5h','uid_weekly') for q in exhausted):continue
                    seconds=[]
                    header=response.headers.get('Retry-After',response.headers.get('retry-after'))
                    try:seconds.append(float(int(str(header).strip())))
                    except (ValueError,TypeError):pass
                    server=response.body.get('serverTime');base=server/1000 if type(server) in (int,float) and math.isfinite(server) and server>0 else now
                    resets=[q['resetsAt']/1000-base for q in exhausted if type(q.get('resetsAt')) in (int,float) and math.isfinite(q['resetsAt'])]
                    if resets:seconds.append(min(resets))
                    delay=max(seconds) if seconds else None
                    if delay is None or not 0<delay<=60 or task['waited']+delay>120:continue
                    task['waited']+=delay;retry=True
                else:retry=status in (408,425) or status>=500
            except VaultError as error:
                if error.code in ('pixiv-network-busy','pixiv-network-stopped','shared-stop-migration-required'):
                    # No HTTP attempt took place. Keep the pending report and
                    # the original retry budget for a later admitted send.
                    task['attempt']-=1
                    task['due']=now+(30 if error.code=='pixiv-network-busy' else 900)
                    with self._online_lock:
                        if task['generation']==self._online_generation and not self.revocation_unverified:
                            self._online.append(task)
                    continue
                retry=True
            if retry and task['attempt']<5:
                task['due']=now+(delay if delay is not None else (30,60,120,240,480)[task['attempt']-1])
                with self._online_lock:
                    if task['generation']==self._online_generation and not self.revocation_unverified:
                        self._online.append(task)
        with self._online_lock:
            return min((max(0,t['due']-now) for t in self._online),default=900)
    def search(self,access_token,params):
        return self._app_read(SEARCH,access_token,params)
    def ranking(self,access_token,*,mode='day',date=None,offset=0):
        return self._app_read(RANKING,access_token,ranking_params(mode=mode,date=date,offset=offset))
    def _app_read(self,route,access_token,params):
        if route not in APP_READ_ROUTES:raise VaultError('destination-rejected')
        if self._budget is not None:self._budget('borrow',getattr(self._session,'account',None) or 'account:unknown')
        profile=self.profiles[0];stamp=self.clock().isoformat(timespec='seconds')
        headers={'User-Agent':profile.ua,'app-os':profile.os,'app-os-version':profile.os_version,'app-version':profile.version,
            'accept-language':'ja','x-client-time':stamp,'x-client-hash':hashlib.md5((stamp+profile.salt).encode('utf-8')).hexdigest(),
            'Authorization':'Bearer '+access_token}
        response=self._request('GET',route+'?'+urlencode(params),headers)
        if response.status==429 and any(word in json.dumps(response.body).lower() for word in ('captcha','challenge','login_required','login required')):
            raise VaultError('pixiv-network-stopped')
        if response.status==429 and self.borrowed_429_scope=='account':
            now=self.clock().timestamp();seconds=None
            value=response.headers.get('Retry-After',response.headers.get('retry-after'))
            try:seconds=float(value)
            except (ValueError,TypeError):
                try:
                    from email.utils import parsedate_to_datetime
                    seconds=parsedate_to_datetime(value).timestamp()-now
                except Exception:pass
            if seconds is None or not math.isfinite(seconds) or seconds<0:seconds=300
            raise BorrowedRateLimited(min(1e12-1,now+max(1,seconds)))
        if response.status in (401,403,429):raise VaultError('pixiv-network-stopped')
        if response.status==400 and 'invalid_grant' in json.dumps(response.body):raise VaultError('invalid-grant')
        if response.status in (400,404):raise VaultError('premium-read-rejected')
        if not 200<=response.status<300:raise VaultError('search-failed')
        return response.body


class UrlopenVaultRequest:
    """Direct HTTPS only, with a registered raw-socket duplicate before TLS."""
    def fenced_request(self,method,url,*,headers,body,timeout,fence,epoch,validate,
                       observe_status=None,observe_send=None):
        import http.client, socket, ssl
        from .vault_socket_fence import SocketFenceError
        parsed=urlsplit(url)
        if (parsed.scheme!='https' or parsed.username or parsed.password or parsed.port
                or parsed.fragment or url.split('?',1)[0] not in (ACCOUNT,INVALID,ONLINE,OAUTH,*APP_READ_ROUTES)):
            raise VaultError('destination-rejected')
        if any(k.lower()=='cookie' for k in headers):raise VaultError('cookie-rejected')
        validate()
        host=parsed.hostname
        if not host:raise VaultError('destination-rejected')
        raw=None;registration=None;connection=None;response=None
        def cleanup_owned(conn, sock, reg, http_response=None):
            # Keep the entry visible until every request-owned alias closes.
            # An unsuccessful close is not proof that the owner has stopped:
            # retain its registered shutdown duplicate for subsequent lock.
            with fence.cleanup():
                close_error=None
                # HTTPResponse may retain fp and a separate socket alias even
                # after HTTPSConnection.close(), including on bounded-read
                # rejection. Close it while the fence still owns its duplicate.
                for owned in (http_response,conn,sock):
                    if owned is None:continue
                    try:owned.close()
                    except BaseException as error:
                        if close_error is None:close_error=error
                if close_error is not None:
                    fence.mark_close_unverified(close_error,registration=reg)
                    raise close_error
                if reg is not None:
                    try:reg.__exit__(None,None,None)
                    except BaseException as error:
                        fence.mark_close_unverified(error)
                        raise
        try:
            # Register each socket before connect or TLS. A failed address is
            # closed and unregistered before trying the next; an invalidated
            # epoch prevents any subsequent candidate from connecting.
            fence.require_healthy(epoch)
            addresses=socket.getaddrinfo(host,443,0,socket.SOCK_STREAM)
            last_error=None
            for family,kind,proto,_,address in addresses:
                validate()
                fence.require_healthy(epoch)
                candidate_socket=socket.socket(family,kind,proto)
                candidate_registration=None
                try:
                    candidate_socket.settimeout(timeout)
                    candidate=fence.register(candidate_socket,epoch)
                    candidate.__enter__();candidate_registration=candidate
                    validate()
                    candidate_socket.connect(address)
                except OSError as error:
                    last_error=error
                    cleanup_owned(None,candidate_socket,candidate_registration)
                    continue
                except BaseException:
                    cleanup_owned(None,candidate_socket,candidate_registration)
                    raise
                raw=candidate_socket;registration=candidate_registration;break
            if raw is None:raise last_error or OSError('address unavailable')
            fence.activate(raw,epoch)
            validate()
            tls=ssl.create_default_context()
            connection=http.client.HTTPSConnection(host,443,timeout=timeout,context=tls)
            connection.sock=tls.wrap_socket(raw,server_hostname=host)
            raw=None  # SSLSocket owns the original handle; the fence owns its duplicate.
            connection.auto_open=0  # no reconnect or unregistered second socket
            path=parsed.path or '/'
            if parsed.query:path+='?'+parsed.query
            if observe_send is not None:observe_send()
            connection.request(method,path,body=body,headers=headers)
            response=connection.getresponse()
            # This is the earliest reliable HTTP status observation. The
            # response body may be HTML, oversized, empty or unreadable; a
            # subsequent cleanup failure must not hide 401/403/429.
            if observe_status is not None:observe_status(response.status)
            payload=response.read(2*1024*1024+1)
            if len(payload)>2*1024*1024:raise VaultError('response-too-large')
            decoded=json.loads(payload.decode('utf-8'))
            if type(decoded) is not dict:raise VaultError('invalid-response')
            return VaultResponse(response.status,decoded,dict(response.getheaders()))
        except SocketFenceError:raise VaultError('stale-session') from None
        except VaultError:raise
        except Exception:
            # Invalidation may shut down the registered socket while
            # getresponse() is pending. Preserve the stale-session signal
            # so OAuth never advances to another profile after a lock/close.
            if fence.snapshot()!=epoch:
                raise VaultError('stale-session') from None
            raise VaultError('transport-failed') from None
        finally:
            cleanup_owned(connection,raw,registration,response)


def default_transport():
    """Original public app profiles, dedicated bounded transport; no I/O yet."""
    from .external_public_profiles import OAUTH_PROFILES, _USER_AGENT
    profiles=tuple(OAuthProfile(**{k:p[k] for k in OAuthProfile.__dataclass_fields__}) for p in OAUTH_PROFILES)
    return ShaftTransport(UrlopenVaultRequest(),oauth_profiles=profiles,shaft_user_agent=_USER_AGENT)
