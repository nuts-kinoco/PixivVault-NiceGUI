"""Opt-in Pixiv bookmark/follow mutations, separate from the GET-only reader.

No mutation occurs on construction. Every operation has an explicit account,
target, action and operation ID; a POST is sent at most once. The existing
LivePixivReadClient owns the frozen, read-only Cookie jar and authenticated GET
identity. This writer never persists a Cookie, logs a token, or closes reader.

Original Pixiv endpoints are preserved, but live write acceptance remains
UNVERIFIED. Callers must not silently retry an indeterminate result.
"""
from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import http.client
import inspect
import json
import math
import re
import socket
import ssl
from threading import Event, RLock, Thread, Timer, current_thread
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

from .bookmark_write_evidence import inspect_add_response, positive_id, successful_api_response
from .vault_socket_fence import SocketFence, SocketFenceError
from .owned_https import OwnedHTTPSPool, OwnedHTTPSHandler as _OwnedHTTPSHandler
from .pixiv_read_client import (
    API_ORIGIN, LivePixivReadClient, _NoRedirects, _JSON_HEADERS, MAX_HEADER_BYTES,
    _identity_from_home,
)


MAX_POST_BYTES = 131072
MAX_CSRF_HTML = 8 * 1024 * 1024  # GET reader enforces this independently.
MAX_MUTATION_SECONDS = 10.0
_OP_ID = re.compile(r'[A-Za-z0-9._:-]{1,128}\Z')
_TOKEN = re.compile(r'[A-Za-z0-9_-]{32,128}\Z')
_NEXT = re.compile(r'<script\s+id="__NEXT_DATA__"\s+type="application/json">(.*?)</script>', re.S)
_DIRECT_TOKEN = re.compile(r'<meta\s+name="csrf-token"\s+content="([A-Za-z0-9_-]{32,128})"\s*/?>')
_GLOBAL_META = re.compile(r'<meta\s+id="meta-global-data"\s+content="([^\"]{1,131072})"\s*/?>')
_LEGACY_JSON_TOKEN = re.compile(r'"token"\s*:\s*"([a-f0-9]{32,64})"')


class WriteValidationError(ValueError):
    def __init__(self):super().__init__('Pixiv write request invalid')


class WriteBusy(RuntimeError):
    def __init__(self):super().__init__('Pixiv write operation already pending')


class WriteOperationConflict(RuntimeError):
    def __init__(self):super().__init__('Pixiv write operation requires reconciliation')


@dataclass(frozen=True, slots=True, repr=False)
class WriteIntent:
    operation_id: str
    account_id: str
    target_id: str
    action: str  # bookmark_add/bookmark_delete/follow/unfollow
    work_kind: str = 'illust'
    author_id: str | None = None
    restrict: int = 0  # 0=public, 1=private; preserved for bookmark/follow.
    tags: tuple[str, ...] = ()
    comment: str = ''

    def __repr__(self):return 'WriteIntent(<redacted>)'


@dataclass(frozen=True, slots=True, repr=False)
class WriteHTTPResponse:
    status: int
    body: bytes

    def __repr__(self):return 'WriteHTTPResponse(<redacted>)'


@dataclass(frozen=True, slots=True, repr=False)
class WriteResult:
    operation_id: str
    account_id: str
    target_id: str
    action: str
    work_kind: str
    status: str  # confirmed/rejected/indeterminate
    reason: str
    created_bookmark_id: str | None = None
    restrict: int | None = None

    @property
    def confirmed(self):return self.status == 'confirmed'

    @property
    def requires_reconciliation(self):return self.status == 'indeterminate'

    def matches(self,intent:WriteIntent) -> bool:
        """A late UI completion may update only its original selected intent."""
        return (isinstance(intent,WriteIntent)
                and self.operation_id==intent.operation_id
                and self.account_id==intent.account_id
                and self.target_id==intent.target_id
                and self.action==intent.action
                and self.work_kind==intent.work_kind)

    def __repr__(self):return f'WriteResult(status={self.status!r})'


def _safe_id(value):
    # Do not accept the evidence helper's leading-zero variant for routing.
    if type(value) is not str:return None
    result=positive_id(value)
    return result if result and result[0] != '0' else None


def _validate(intent):
    if (not isinstance(intent,WriteIntent) or type(intent.operation_id) is not str
            or _OP_ID.fullmatch(intent.operation_id) is None
            or _safe_id(intent.account_id) is None or _safe_id(intent.target_id) is None
            or intent.action not in ('bookmark_add','bookmark_delete','follow','unfollow')
            or type(intent.restrict) is not int or intent.restrict not in (0,1)
            or type(intent.work_kind) is not str or intent.work_kind not in ('illust','novel')
            or type(intent.tags) is not tuple or len(intent.tags)>10
            or any(type(t) is not str or not 0<len(t)<=64 or any(ord(c)<32 for c in t)
                   for t in intent.tags)
            or type(intent.comment) is not str or len(intent.comment)>1000
            or any(ord(c)<32 and c not in '\n\t' for c in intent.comment)):
        raise WriteValidationError()
    if intent.action.startswith('bookmark'):
        if _safe_id(intent.author_id) is None:
            raise WriteValidationError()
    elif intent.tags or intent.comment or intent.work_kind != 'illust':
        raise WriteValidationError()
    if intent.action in ('unfollow','bookmark_delete') and (intent.tags or intent.comment):
        raise WriteValidationError()


def _json_packet(body, *, limit=MAX_POST_BYTES):
    if type(body) is not bytes or len(body)>limit:
        return None
    def unique(pairs):
        output={}
        for key,value in pairs:
            if key in output:raise ValueError('duplicate')
            output[key]=value
        return output
    try:
        value=json.loads(body,object_pairs_hook=unique,
                         parse_constant=lambda _v: (_ for _ in ()).throw(ValueError('constant')))
    except (ValueError,UnicodeError,RecursionError):
        return None
    return value if type(value) is dict else None


def _csrf_from_home(html):
    if type(html) is not bytes or not 0<len(html)<=MAX_CSRF_HTML:
        raise WriteValidationError()
    try:
        text=html.decode('utf-8')
        matches=list(_NEXT.finditer(text))
        if len(matches)==1:
            outer=_json_packet(matches[0].group(1).encode('utf-8'),limit=MAX_CSRF_HTML)
            inner=((outer or {}).get('props') or {}).get('pageProps') or {}
            serialized=inner.get('serverSerializedPreloadedState')
            if type(serialized) is str and len(serialized)<=MAX_CSRF_HTML:
                decoded=_json_packet(serialized.encode(),limit=MAX_CSRF_HTML)
                api=(decoded or {}).get('api')
                token=api.get('token') if type(api) is dict else None
                if type(token) is str and _TOKEN.fullmatch(token):return token
        if matches:
            raise WriteValidationError()
        # Original Flet fallback variants, only after the GET reader separately
        # verifies the same authenticated account in this exact homepage.
        tokens=set()
        meta=_GLOBAL_META.findall(text)
        if len(meta)==1:
            loaded=_json_packet(unescape(meta[0]).encode('utf-8'),limit=MAX_CSRF_HTML)
            token=(loaded or {}).get('token')
            if type(token) is str and _TOKEN.fullmatch(token):tokens.add(token)
        tokens.update(_LEGACY_JSON_TOKEN.findall(text))
        found=_DIRECT_TOKEN.findall(text)
        tokens.update(found)
        if len(tokens)==1:return next(iter(tokens))
    except (ValueError,TypeError,UnicodeError,RecursionError):
        pass
    raise WriteValidationError()


def _write_response_body(response, owner, limit):
    headers = response.headers
    if sum(len(str(key)) + len(str(value)) for key, value in headers.items()) > MAX_HEADER_BYTES:
        raise ValueError('oversized headers')
    if headers.get('Content-Encoding', 'identity').strip().lower() not in ('', 'identity'):
        raise ValueError('encoded response')
    declared = headers.get('Content-Length')
    if declared is not None and (re.fullmatch(r'[0-9]{1,12}', declared) is None
                                 or int(declared) > limit):
        raise ValueError('oversized response')
    read1 = getattr(response, 'read1', None)
    if not callable(read1):
        read1 = getattr(getattr(response, 'fp', None), 'read1', None)
    if callable(read1):
        data = bytearray()
        while True:
            owner.check()
            chunk = read1(min(16384, limit + 1 - len(data)))
            owner.check()
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > limit:
                raise ValueError('oversized response')
        data = bytes(data)
    else:
        # Compatibility for explicitly injected opener responses. Real urllib
        # HTTPResponse/HTTPError bodies use read1; the owned socket deadline is
        # also active for every real response.
        owner.check()
        data = response.read(limit + 1)
        owner.check()
    if len(data) > limit or (declared is not None and len(data) != int(declared)):
        raise ValueError('incomplete or oversized response')
    return data


class _OwnedWriteTransport(OwnedHTTPSPool):
    def __call__(self, request, timeout, limit, deadline):
        parsed, origin = urlsplit(request.full_url), urlsplit(API_ORIGIN)
        if (request.get_method() != 'POST' or parsed.scheme != 'https'
                or parsed.netloc != origin.netloc or parsed.username or parsed.password
                or parsed.fragment or parsed.query or parsed.path not in (
                    '/bookmark_add.php', '/ajax/illusts/bookmarks/add',
                    '/ajax/illusts/bookmarks/delete', '/ajax/novels/bookmarks/add',
                    '/ajax/novels/bookmarks/delete')
                or type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0
                or type(deadline) not in (int, float) or not math.isfinite(deadline)
                or type(limit) is not int or not 0 < limit <= MAX_POST_BYTES):
            raise TimeoutError('write outcome unknown')
        try:
            with self.session(
                    timeout=min(timeout, MAX_MUTATION_SECONDS),
                    deadline=min(deadline, time.monotonic() + MAX_MUTATION_SECONDS),
                    host=parsed.hostname, port=parsed.port or 443) as owner:
                opener = build_opener(ProxyHandler({}), _NoRedirects(), _OwnedHTTPSHandler(owner))
                try:
                    response = opener.open(request, timeout=owner.remaining())
                except HTTPError as error:
                    response = error
                owner.response(response)
                owner.check()
                body = _write_response_body(response, owner, limit)
                owner.check()
                return WriteHTTPResponse(response.getcode(), body)
        except Exception:
            raise TimeoutError('write outcome unknown') from None


def _default_write_transport(request: Request, timeout: float, limit: int,
                             deadline: float) -> WriteHTTPResponse:
    """One standalone bounded POST; app writers retain their own transport."""
    transport = _OwnedWriteTransport()
    try:
        return transport(request, timeout, limit, deadline)
    finally:
        transport.close()


WriteTransport=Callable[[Request,float,int,float],WriteHTTPResponse]


class PixivWriteClient:
    """One-use operation IDs, exact-target locks and conservative outcome ledger."""

    def __init__(self,reader:LivePixivReadClient,*,transport:WriteTransport|None=None):
        if not isinstance(reader,LivePixivReadClient):
            raise WriteValidationError()
        if transport is not None and not callable(transport):
            raise WriteValidationError()
        self._reader=reader
        self._owned_transport=_OwnedWriteTransport() if transport is None else None
        self._transport=transport if transport is not None else self._owned_transport
        self._lock=RLock()
        self._pending_ops=set()
        self._pending_targets=set()
        self._reconciling=set()
        self._results={}
        self._blocked=set()
        self._confirmed={}
        self._closed=False
        self._epoch=0

    def _csrf_token(self, expected_account_id):
        # A single bounded GET, explicitly avoiding the reader's optional GET
        # retry policy. The shared GET-only reader is never changed or closed.
        deadline=time.monotonic()+MAX_MUTATION_SECONDS
        homepage=self._reader._get_once('home',API_ORIGIN+'/',deadline=deadline)
        if _identity_from_home(homepage)!=expected_account_id:
            raise WriteValidationError()
        return _csrf_from_home(homepage)

    def _bookmark_receipt(self,intent):
        # A verified work+author pair is mandatory, not a caller-provided
        # bookmarkData ID that could belong to another account/artwork.
        try:
            body=(self._reader.get_artwork_metadata_raw(intent.target_id,intent.author_id)
                  if intent.work_kind=='illust'
                  else self._reader.get_novel_payload(intent.target_id,intent.author_id))
            if (type(body) is not dict or str(body.get('id')) != intent.target_id
                    or str(body.get('userId'))!=intent.author_id):return None
            bookmark=body.get('bookmarkData')
            if type(bookmark) is not dict:return None
            return _safe_id(bookmark.get('id'))
        except Exception:
            return None

    def _request(self,intent,csrf,bookmark_id):
        headers=dict(_JSON_HEADERS)
        headers.update({'X-CSRF-Token':csrf,'Origin':API_ORIGIN,'X-Requested-With':'XMLHttpRequest'})
        if intent.action.startswith('bookmark'):
            kind='illusts' if intent.work_kind=='illust' else 'novels'
            operation='add' if intent.action=='bookmark_add' else 'delete'
            url=f'{API_ORIGIN}/ajax/{kind}/bookmarks/{operation}'
            referer=(f'{API_ORIGIN}/artworks/{intent.target_id}' if intent.work_kind=='illust'
                     else f'{API_ORIGIN}/novel/show.php?id={intent.target_id}')
            headers['Referer']=referer
            if operation=='add':
                headers['Content-Type']='application/json; charset=utf-8'
                field='illust_id' if intent.work_kind=='illust' else 'novel_id'
                data=json.dumps({field:intent.target_id,'restrict':intent.restrict,
                                 'comment':intent.comment,'tags':list(intent.tags)},
                                ensure_ascii=False,separators=(',',':')).encode('utf-8')
            else:
                headers['Content-Type']='application/x-www-form-urlencoded'
                data=urlencode({'bookmark_id':bookmark_id}).encode()
        else:
            url=API_ORIGIN+'/bookmark_add.php'
            headers['Referer']=f'{API_ORIGIN}/users/{intent.target_id}'
            headers['Content-Type']='application/x-www-form-urlencoded'
            values={'mode':'add' if intent.action=='follow' else 'delete',
                    'type':'user','user_id':intent.target_id}
            if intent.action=='follow':values['restrict']=intent.restrict
            values['format']='json'
            data=urlencode(values).encode()
        if len(data)>MAX_POST_BYTES:raise WriteValidationError()
        request=Request(url,data=data,headers=headers,method='POST')
        self._reader._ensure_open()
        self._reader._cookies.add_cookie_header(request)
        cookie_header=request.get_header('Cookie') or ''
        if (not cookie_header or len(cookie_header)>16384 or
                any(ord(c)<32 or ord(c)==127 for c in cookie_header)):
            raise WriteValidationError()
        if request.get_method()!='POST' or not url.startswith(API_ORIGIN+'/'):
            raise WriteValidationError()
        return request

    def confirmed_state(self,account_id:str,kind:str,target_id:str):
        with self._lock:
            return self._confirmed.get((account_id,kind,target_id))

    def invalidate_confirmed_state(self, account_id, kind, target_id):
        """Drop only stale cached state after the coordinator's explicit GET.

        This is not reconciliation: uncertain targets and used operation IDs
        remain fenced. Return whether a cached state was actually removed.
        """
        if (_safe_id(account_id) is None or _safe_id(target_id) is None
                or kind not in ('illust', 'novel', 'user')):
            raise WriteValidationError()
        key = (account_id, kind, target_id)
        with self._lock:
            if self._closed:
                raise WriteOperationConflict()
            if key in self._pending_targets or key in self._reconciling:
                raise WriteBusy()
            existed = key in self._confirmed
            self._confirmed.pop(key, None)
            return existed

    def result(self,operation_id:str):
        with self._lock:return self._results.get(operation_id)

    def close(self):
        """Invalidate results and revoke only this writer's POST sockets.

        Callers still await their operation workers to prove they have drained.
        The shared GET reader and injected transports remain caller-owned.
        """
        with self._lock:
            self._closed=True
            self._epoch+=1
        if self._owned_transport is not None:
            self._owned_transport.close()

    @property
    def shutdown_unverified(self):
        return bool(self._owned_transport is not None
                    and self._owned_transport.revocation_unverified)

    def cancel(self):
        """Invalidate late completions; cannot recall an already sent POST."""
        self.close()

    def _reconcile_observed(self,account_id,kind,target_id,*,read_state):
        """Internal serialized reconciliation of a verified bounded GET.

        Boolean observed state is recorded, but the original uncertain POST is
        never retroactively called a confirmed successful request.
        """
        key=(account_id,kind,target_id)
        with self._lock:
            if (self._closed or key not in self._blocked or key in self._pending_targets
                    or key in self._reconciling):
                return False
            self._reconciling.add(key)
            epoch=self._epoch
        try:
            if self._reader.require_identity()!=account_id or not callable(read_state):
                return False
            observed=read_state(account_id,kind,target_id)
            if type(observed) is not bool:return False
            with self._lock:
                if (self._closed or self._epoch!=epoch or key not in self._blocked
                        or key in self._pending_targets):
                    return False
                self._confirmed[key]=observed
                self._blocked.discard(key)
            return True
        except Exception:
            return False
        finally:
            with self._lock:
                self._reconciling.discard(key)

    def reconcile_from_reader(self,account_id,kind,target_id,*,author_id=None):
        """Clear an indeterminate target only after the existing bounded GETs.

        A bookmark's detail is pinned to its expected author and authenticated
        account. A complete followed-author snapshot must include both public
        and private lists. Missing/partial/malformed data does not unblock.
        This observes remote state, NOT a proof that the prior POST succeeded.
        """
        if (_safe_id(account_id) is None or _safe_id(target_id) is None
                or kind not in ('illust','novel','user')):
            return False
        if kind!='user' and _safe_id(author_id) is None:
            return False

        def observe(account, resource, target):
            if resource=='user':
                public=self._reader.get_following_users(account,'show')
                private=self._reader.get_following_users(account,'hide')
                if type(public) is not tuple or type(private) is not tuple:
                    return None
                ids=[]
                for row in (*public,*private):
                    if type(row) is not dict or _safe_id(row.get('user_id')) is None:
                        return None
                    ids.append(row['user_id'])
                return target in ids
            body=(self._reader.get_artwork_metadata_raw(target,author_id)
                  if resource=='illust' else
                  self._reader.get_novel_payload(target,author_id))
            if (type(body) is not dict or str(body.get('id'))!=target
                    or str(body.get('userId'))!=author_id
                    or 'bookmarkData' not in body):
                return None
            value=body['bookmarkData']
            if value is None:return False
            if type(value) is not dict or _safe_id(value.get('id')) is None:
                return None
            return True

        return self._reconcile_observed(account_id,kind,target_id,read_state=observe)

    def execute(self,intent:WriteIntent, *, before_dispatch:Callable[[],None]|None=None)->WriteResult:
        _validate(intent)
        if before_dispatch is not None and not callable(before_dispatch):
            raise WriteValidationError()
        kind='user' if intent.action in ('follow','unfollow') else intent.work_kind
        target_key=(intent.account_id,kind,intent.target_id)
        with self._lock:
            if self._closed:raise WriteOperationConflict()
            if intent.operation_id in self._pending_ops or target_key in self._pending_targets:
                raise WriteBusy()
            if (intent.operation_id in self._results or target_key in self._blocked
                    or target_key in self._reconciling):
                raise WriteOperationConflict()
            desired = intent.action in ('bookmark_add','follow')
            if self._confirmed.get(target_key) is desired:
                raise WriteOperationConflict()
            self._pending_ops.add(intent.operation_id)
            self._pending_targets.add(target_key)
            epoch=self._epoch

        status='indeterminate'
        reason='unknown_outcome'
        created=None
        try:
            # Verify the same explicit Cookie reader's established account.
            try:
                account=self._reader.require_identity()
                if (account != intent.account_id or
                        (self._reader._cookie_user_id is not None and
                         self._reader._cookie_user_id != account)):
                    raise WriteValidationError()
                bookmark_id=None
                if intent.action=='bookmark_delete':
                    bookmark_id=self._bookmark_receipt(intent)
                    if bookmark_id is None:
                        status,reason='indeterminate','unverified_bookmark_receipt'
                        return self._finish(intent,target_key,status,reason,created,epoch)
                csrf=self._csrf_token(intent.account_id)
                request=self._request(intent,csrf,bookmark_id)
            except Exception:
                status,reason='rejected','preflight_not_sent'
                return self._finish(intent,target_key,status,reason,created,epoch)

            # A tab/window may have gone away during bounded credential/CSRF
            # GETs. Do not dispatch a POST after an observed close/cancel.
            with self._lock:
                if self._closed or self._epoch!=epoch:
                    return self._finish(intent,target_key,
                        'indeterminate','writer_closed',None,epoch)
            if before_dispatch is not None:
                try:
                    acknowledged = before_dispatch()
                    if acknowledged is not None:
                        if inspect.iscoroutine(acknowledged):
                            acknowledged.close()
                        raise WriteValidationError()
                except Exception:
                    return self._finish(intent,target_key,
                        'rejected','preflight_not_sent',None,epoch)
                # Callback may itself observe app shutdown. Never call foreign
                # callbacks under our lock, and recheck our lifecycle afterwards.
                with self._lock:
                    if self._closed or self._epoch!=epoch:
                        return self._finish(intent,target_key,
                            'indeterminate','writer_closed',None,epoch)
            try:
                deadline=time.monotonic()+MAX_MUTATION_SECONDS
                response=self._transport(request,MAX_MUTATION_SECONDS,MAX_POST_BYTES,deadline)
                self._reader._ensure_open()
                if (time.monotonic()>deadline or not isinstance(response,WriteHTTPResponse)
                        or type(response.status) is not int):
                    raise TimeoutError()
            except Exception:
                return self._finish(intent,target_key,'indeterminate','transport_unknown',None,epoch)
            payload=_json_packet(response.body)
            if response.status==200 and payload is not None and payload.get('error') is True:
                status,reason='rejected','api_rejected'
            elif response.status in (400,401,403,404,422) and payload is not None and payload.get('error') is True:
                status,reason='rejected','api_rejected'
            elif successful_api_response(response.status,payload):
                if intent.action=='bookmark_add':
                    proof=inspect_add_response(response.status,payload)
                    if proof.authorizes_correlated_delete:
                        status,reason,created='confirmed','confirmed_bookmark_id',proof.created_bookmark_id
                    else:
                        status,reason='indeterminate','missing_or_ambiguous_bookmark_id'
                else:
                    status,reason='confirmed','accepted_response'
            else:
                status,reason='indeterminate','response_unknown'
            return self._finish(intent,target_key,status,reason,created,epoch)
        finally:
            with self._lock:
                self._pending_ops.discard(intent.operation_id)
                self._pending_targets.discard(target_key)

    def _finish(self,intent,key,status,reason,created,epoch):
        with self._lock:
            if self._closed or self._epoch!=epoch:
                status,reason,created='indeterminate','writer_closed',None
            result=WriteResult(intent.operation_id,intent.account_id,intent.target_id,
                intent.action,intent.work_kind,status,reason,created,
                intent.restrict if status=='confirmed' and intent.action in ('bookmark_add','follow') else None)
            self._results[intent.operation_id]=result
            if status=='confirmed':
                self._confirmed[key]=intent.action in ('bookmark_add','follow')
            if status=='indeterminate':
                self._confirmed.pop(key,None)
                self._blocked.add(key)
            return result
