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
from .external_vault import VaultError, TOKEN_FIELDS, remaining

ACCOUNT='https://pixshaft.com/v1/account/nana7mi'
INVALID='https://pixshaft.com/v1/account/nana7mi/invalid'
ONLINE='https://pixshaft.com/v1/account/online'
OAUTH='https://oauth.secure.pixiv.net/auth/token'
SEARCH='https://app-api.pixiv.net/v1/search/illust'

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


def search_params(word,bookmark_min=0,*,sort='popular_desc',r18=False,exclude_ai=False,offset=0):
    if type(word) is not str or not word.strip() or len(word)>1024 or any(ord(c)<32 for c in word):raise VaultError('invalid-search')
    if type(bookmark_min) is not int or bookmark_min<0 or type(offset) is not int or offset<0 or sort not in ('popular_desc','date_desc','date_asc') or type(r18) is not bool or type(exclude_ai) is not bool:raise VaultError('invalid-search')
    # Original app-api route does not send r18: preserve its meaning/behavior.
    params={'word':word,'sort':sort,'search_target':'partial_match_for_tags','bookmark_num_min':bookmark_min,'filter':'for_ios'}
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

def quota_retry_at(body,now):
    values=[]
    for quota in body.get('quotas',[]) if type(body.get('quotas')) is list else []:
        if type(quota) is not dict:continue
        value=quota.get('resetsAt')
        if quota.get('remaining')==0 and type(value) in (int,float) and math.isfinite(value) and value/1000>now:values.append(value/1000)
    return min(values) if values else now+2100


class ShaftTransport:
    def __init__(self,request,*,oauth_profiles,shaft_user_agent='PixivVault',clock=None):
        from .vault_socket_fence import SocketFence
        self._socket_fence=SocketFence()
        self.request=request;self.profiles=tuple(oauth_profiles);self.shaft_user_agent=shaft_user_agent
        self.clock=clock or (lambda:datetime.now().astimezone());self._online=[];self._online_lock=threading.RLock();self._online_generation=0;self._session=threading.local()
        if not self.profiles or any(not isinstance(p,OAuthProfile) for p in self.profiles):raise VaultError('profile-unavailable')
    @property
    def revocation_unverified(self):
        """Read-only, process-local shutdown failure; restart is required."""
        return self._socket_fence.revocation_unverified
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
    def _request(self,method,url,headers,body=None):
        self._check_session()
        if url.split('?',1)[0] not in (ACCOUNT,INVALID,ONLINE,OAUTH,SEARCH):raise VaultError('destination-rejected')
        if any(k.lower()=='cookie' for k in headers):raise VaultError('cookie-rejected')
        try:
            epoch=getattr(self._session,'socket_epoch',None)
            fenced=getattr(self.request,'fenced_request',None)
            if epoch is not None:
                if not callable(fenced):raise VaultError('transport-fence-unavailable')
                response=fenced(method,url,headers=headers,body=body,timeout=10,
                    fence=self._socket_fence,epoch=epoch,validate=self._check_session)
            else:
                if isinstance(self.request,UrlopenVaultRequest):raise VaultError('session-required')
                response=self.request(method,url,headers=headers,body=body,timeout=10)
            if not isinstance(response,VaultResponse) or type(response.body) is not dict:raise ValueError()
            return response
        except VaultError:raise
        except Exception:raise VaultError('transport-failed') from None
    def _signed(self,url,payload,secret):
        body=canonical(payload)
        return self._request('POST',url,{'X-Shaft-Sign':hmac.new(secret.encode('utf-8'),body,hashlib.sha256).hexdigest(),
            'Content-Type':'application/json','User-Agent':self.shaft_user_agent},body)
    def _refresh_oauth(self,refresh_token):
        for profile in self.profiles:
            body=urlencode({'grant_type':'refresh_token','refresh_token':refresh_token,'client_id':profile.client_id,
                'client_secret':profile.client_secret,'include_policy':'true'}).encode('ascii')
            try:response=self._request('POST',OAUTH,{'User-Agent':profile.ua,'Content-Type':'application/x-www-form-urlencoded'},body)
            except VaultError as error:
                if error.code=='stale-session':raise
                continue
            if response.status==400 and 'invalid_grant' in json.dumps(response.body):raise VaultError('invalid-grant')
            if 200<=response.status<300 and token_fields(response.body).get('access_token'):return token_fields(response.body),online_fields(response.body)
        raise VaultError('oauth-failed')
    def fetch_account(self,request_uid,secret,slot,now):
        for attempt in range(2):
            response=self._signed(ACCOUNT,{'uid':request_uid},secret)
            if response.status==429:raise RateLimited(quota_retry_at(response.body,now))
            if not 200<=response.status<300:raise VaultError('payout-failed')
            body=response.body;account=body.get('account',{});token=token_fields(account)
            expiry=body.get('expiresAt');expiry=expiry if type(expiry) in (int,float) and math.isfinite(expiry) else None
            left=None if expiry is None else expiry/1000-now;threshold=max(30,remaining(slot,now) or 0)
            borrowed_uid=_uid(body.get('uid'))
            if body.get('expired') or (left is not None and left<=threshold):
                refresh=account.get('refresh_token') if type(account) is dict else None
                if not refresh:
                    if not attempt:continue
                    raise VaultError('expired-payout')
                try:token,online=self._refresh_oauth(refresh)
                except VaultError as error:
                    if error.code=='stale-session':raise
                    if error.code=='invalid-grant' and borrowed_uid is not None:
                        digest=hashlib.sha256(refresh.encode('utf-8')).hexdigest()
                        if digest!=slot.get('reported_refresh_hash') and slot.get('backoff_until',0)<=now:
                            # Report actual payout account, never request UID; only invalid_grant.
                            try:self._signed(INVALID,{'uid':borrowed_uid,'refreshTokenHash':digest},secret)
                            except VaultError as error:
                                if error.code=='stale-session':raise
                            slot['reported_refresh_hash']=digest
                    if not attempt:continue
                    raise VaultError('expired-payout') from None
                duration=token.get('expires_in')
                expiry=(now+duration)*1000 if type(duration) in (int,float) and math.isfinite(duration) and duration>0 else None
                if borrowed_uid is not None:
                    self.queue_online(secret,borrowed_uid,online,now)
                    self.online_tick(now)
            if not token.get('access_token'):raise VaultError('invalid-payout')
            token.update(token_expires_at_ms=expiry,account_uid=borrowed_uid)
            return token
        raise VaultError('expired-payout')
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
            except VaultError:retry=True
            if retry and task['attempt']<5:
                task['due']=now+(delay if delay is not None else (30,60,120,240,480)[task['attempt']-1])
                with self._online_lock:
                    if task['generation']==self._online_generation and not self.revocation_unverified:
                        self._online.append(task)
        with self._online_lock:
            return min((max(0,t['due']-now) for t in self._online),default=900)
    def search(self,access_token,params):
        profile=self.profiles[0];stamp=self.clock().isoformat(timespec='seconds')
        headers={'User-Agent':profile.ua,'app-os':profile.os,'app-os-version':profile.os_version,'app-version':profile.version,
            'accept-language':'ja','x-client-time':stamp,'x-client-hash':hashlib.md5((stamp+profile.salt).encode('utf-8')).hexdigest(),
            'Authorization':'Bearer '+access_token}
        response=self._request('GET',SEARCH+'?'+urlencode(params),headers)
        if response.status==400 and 'invalid_grant' in json.dumps(response.body):raise VaultError('invalid-grant')
        if not 200<=response.status<300:raise VaultError('search-failed')
        return response.body


class UrlopenVaultRequest:
    """Direct HTTPS only, with a registered raw-socket duplicate before TLS."""
    def fenced_request(self,method,url,*,headers,body,timeout,fence,epoch,validate):
        import http.client, socket, ssl
        from .vault_socket_fence import SocketFenceError
        parsed=urlsplit(url)
        if (parsed.scheme!='https' or parsed.username or parsed.password or parsed.port
                or parsed.fragment or url.split('?',1)[0] not in (ACCOUNT,INVALID,ONLINE,OAUTH,SEARCH)):
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
            connection.request(method,path,body=body,headers=headers)
            response=connection.getresponse()
            payload=response.read(2*1024*1024+1)
            if len(payload)>2*1024*1024:raise VaultError('response-too-large')
            decoded=json.loads(payload.decode('utf-8'))
            if type(decoded) is not dict:raise VaultError('invalid-response')
            return VaultResponse(response.status,decoded,dict(response.getheaders()))
        except SocketFenceError:raise VaultError('stale-session') from None
        except VaultError:raise
        except Exception:raise VaultError('transport-failed') from None
        finally:
            cleanup_owned(connection,raw,registration,response)


def default_transport():
    """Original public app profiles, dedicated bounded transport; no I/O yet."""
    from .external_public_profiles import OAUTH_PROFILES, _USER_AGENT
    profiles=tuple(OAuthProfile(**{k:p[k] for k in OAuthProfile.__dataclass_fields__}) for p in OAUTH_PROFILES)
    return ShaftTransport(UrlopenVaultRequest(),oauth_profiles=profiles,shaft_user_agent=_USER_AGENT)
