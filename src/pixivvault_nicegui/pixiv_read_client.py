"""Bounded, GET-only Pixiv reader for explicitly supplied Netscape cookies.

No network or application configuration is consulted at construction. This
module does not import the legacy application, persist cookies, or write data.
An optional injected transport lets all behavior be tested without HTTP.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
import io
import json
import math
from html.parser import HTMLParser
from http.cookiejar import CookieJar, DefaultCookiePolicy, MozillaCookieJar
from pathlib import Path
import re
from threading import RLock
import time
import warnings
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, quote, unquote, parse_qsl
from urllib.request import (
    HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener,
)

from .media_parity_policy import (
    MediaPayloadRejected, NovelDocument, parse_novel_detail,
)
from .owned_https import OwnedHTTPSHandler, OwnedHTTPSPool


COOKIE_LIMIT = 1024 * 1024
JSON_LIMIT = 2 * 1024 * 1024
HTML_LIMIT = 8 * 1024 * 1024
MAX_IDENTITY_META = 1
MAX_IDENTITY_SCRIPTS = 1
IMAGE_LIMIT = 20 * 1024 * 1024
MAX_HEADER_BYTES = 65536
MAX_URL_LENGTH = 4096
MAX_REQUEST_SECONDS = 10.0
MAX_OPERATION_SECONDS = 120.0
MAX_ROUTE_PAGES = 1100
MAX_BOOKMARKS_PER_ROUTE = 50000
MAX_ARTWORK_PAGES = 1000
MAX_AUTHOR_WORKS = 50000
MAX_FOLLOWED_AUTHORS = 10000
FOLLOWING_PAGE_SIZE = 24
BOOKMARK_PAGE_SIZE = 48
API_ORIGIN = "https://www.pixiv.net"
CDN_HOST = "i.pximg.net"
_ID = re.compile(r"[1-9][0-9]{0,19}\Z")
_SESSION_ID = re.compile(r"([1-9][0-9]{0,19})_[^\s;]+")
_BOOKMARK_PATH = re.compile(
    r"/ajax/user/([1-9][0-9]{0,19})/(illusts|novels)/bookmarks\Z"
)
_PAGES_PATH = re.compile(r"/ajax/illust/([1-9][0-9]{0,19})/pages\Z")
_AUTHOR_ALL_PATH = re.compile(r"/ajax/user/([1-9][0-9]{0,19})/profile/all\Z")
_AUTHOR_DETAILS_PATH = re.compile(r"/ajax/user/([1-9][0-9]{0,19})/profile/illusts\Z")
_FOLLOWING_PATH = re.compile(r"/ajax/user/([1-9][0-9]{0,19})/following\Z")
_DETAIL_PATH = re.compile(r"/ajax/illust/([1-9][0-9]{0,19})\Z")
_NOVEL_DETAIL_PATH = re.compile(r"/ajax/novel/([1-9][0-9]{0,19})\Z")
_ORIGINAL_IMAGE_PATH = re.compile(
    r"/img-original/img/[0-9]{4}/[0-9]{2}/[0-9]{2}/"
    r"[0-9]{2}/[0-9]{2}/[0-9]{2}/([1-9][0-9]{0,19})_p(0|[1-9][0-9]*)"
    r"\.(?:jpg|jpeg|png|gif|webp)\Z"
)
_JSON_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": API_ORIGIN + "/",
    "Accept": "application/json",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Accept-Encoding": "identity",
}


class PixivReadError(Exception):
    """Safe-to-display error; no destination, payload or credential included."""

    message = "Pixiv read failed"

    def __init__(self):
        super().__init__(self.message)


class CookieSourceRejected(PixivReadError):
    message = "Cookie source rejected"


class DestinationRejected(PixivReadError):
    message = "Destination rejected"


class ReadDeadlineExceeded(PixivReadError):
    message = "Read deadline exceeded"


_READ_DEADLINE = ContextVar('pixivvault_read_deadline', default=None)


@contextmanager
def read_deadline_scope(deadline):
    """Bound this operation's actual HTTP reads without mutating a shared client."""
    if (type(deadline) not in (int, float) or not math.isfinite(deadline)
            or time.monotonic() >= deadline):
        raise ReadDeadlineExceeded()
    inherited = _READ_DEADLINE.get()
    effective = deadline if inherited is None else min(deadline, inherited)
    token = _READ_DEADLINE.set(effective)
    try:
        yield
    finally:
        _READ_DEADLINE.reset(token)


class ResponseRejected(PixivReadError):
    message = "HTTP response rejected"


class HTTPStatusRejected(ResponseRejected):
    """HTTP status preserved for conservative failure classification only."""

    def __init__(self, status_code: int):
        if type(status_code) is not int or not 100 <= status_code <= 599:
            raise ValueError('invalid HTTP status')
        self.status_code = status_code
        super().__init__()


class ResponseLimitExceeded(PixivReadError):
    message = "Response limit exceeded"


class InvalidPayload(PixivReadError):
    message = "Pixiv payload invalid"


class ArtworkUnavailable(InvalidPayload):
    """A known per-artwork restriction/type, never a malformed API envelope."""
    message = "Pixiv artwork unavailable for this operation"


class ApiRejected(PixivReadError):
    message = "Pixiv API rejected the read"


class AuthenticationMissing(PixivReadError):
    message = "Pixiv identity not verified"


class ClientClosed(PixivReadError):
    message = "Pixiv client closed"


class TransportFailed(PixivReadError):
    message = "Pixiv transport failed"


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class _FrozenCookiePolicy(DefaultCookiePolicy):
    def set_ok(self, cookie, request):
        return False


def _identifier(value: object) -> str:
    if type(value) not in (str, int):
        raise InvalidPayload()
    result = str(value)
    if not _ID.fullmatch(result):
        raise InvalidPayload()
    return result


def _payload_unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidPayload()
        result[key] = value
    return result


def _payload_invalid_constant(_value):
    raise InvalidPayload()


def _discovery_record(row):
    """Normalize only supplied metadata; unknown counts/classification stay unknown."""
    if type(row) is not dict or row.get('isMasked',False) is not False:
        raise InvalidPayload()
    wid,aid=_identifier(row.get('id')),_identifier(row.get('userId'))
    title,name=row.get('title'),row.get('userName')
    if any(type(v) is not str or len(v)>512 or any(ord(c)<32 for c in v) for v in (title,name)):
        raise InvalidPayload()
    tags=row.get('tags',[])
    if type(tags) is dict:tags=tags.get('tags')
    if type(tags) is not list or len(tags)>100:raise InvalidPayload()
    tags=[t.get('tag') if type(t) is dict else t for t in tags]
    if any(type(t) is not str or len(t)>128 or any(ord(c)<32 for c in t) for t in tags):raise InvalidPayload()
    thumb=row.get('url','')
    if not thumb and type(row.get('urls')) is dict:
        thumb=row['urls'].get('small') or row['urls'].get('regular') or row['urls'].get('mini') or ''
    if type(thumb) is not str:raise InvalidPayload()
    if thumb:_validate_destination(thumb,'image')
    result=dict(id=wid,user_id=aid,title=title,user_name=name,tags=tags,
                thumbnail_url=thumb,is_masked=False)
    # Some already-fetched illustration detail payloads carry the complete
    # description. Keep it when supplied, without initiating a detail GET.
    # List/ranking payloads with no description remain explicitly unknown.
    if "description" in row and row["description"] is not None:
        body = row["description"]
        if type(body) is not str or len(body) > 131072:
            raise InvalidPayload()
        result["description"] = body
    for source,target in [('pageCount','page_count'),('illustType','illust_type'),('xRestrict','x_restrict'),
                          ('aiType','ai_type'),('bookmarkCount','bookmark_count'),('width','width'),('height','height')]:
        if source in row:
            value=row[source]
            if type(value) is not int or value<0:raise InvalidPayload()
            if source in ('illustType','xRestrict','aiType') and value not in (0,1,2):raise InvalidPayload()
            if source=='pageCount' and not 1<=value<=MAX_ARTWORK_PAGES:raise InvalidPayload()
            result[target]=value
    if 'illust_type' in result:result['type']=result['illust_type']
    for source,target in [('createDate','create_date'),('updateDate','update_date')]:
        if source in row:
            value=row[source]
            if type(value) is not str or len(value)>128 or any(ord(c)<32 for c in value):raise InvalidPayload()
            result[target]=value
    return result


def _read_cookies(path: str | Path) -> tuple[CookieJar, str | None]:
    try:
        if not isinstance(path, (str, Path)) or not str(path).strip():
            raise CookieSourceRejected()
        file = Path(path)
        if not file.is_absolute() or not file.is_file() or file.is_symlink():
            raise CookieSourceRejected()
        with file.open("rb") as stream:
            raw = stream.read(COOKIE_LIMIT + 1)
        if not raw or len(raw) > COOKIE_LIMIT:
            raise CookieSourceRejected()
        source = MozillaCookieJar()
        # Malformed Netscape cookies can cause library warnings with raw
        # cookie text. Do not let parser diagnostics escape to stderr.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            source._really_load(
                io.StringIO(raw.decode("utf-8-sig"), newline=None),
                "<memory>", ignore_discard=True, ignore_expires=False,
            )
        cookies = CookieJar(policy=_FrozenCookiePolicy())
        claimed_id: str | None = None
        now = time.time()
        for cookie in source:
            domain = cookie.domain.lower().lstrip(".")
            if domain not in {"pixiv.net", "www.pixiv.net"}:
                continue
            if cookie.is_expired(now):
                continue
            if not cookie.name or not cookie.value:
                continue
            if cookie.name == "PHPSESSID":
                match = _SESSION_ID.fullmatch(cookie.value)
                if match:
                    candidate = match.group(1)
                    if claimed_id is not None and claimed_id != candidate:
                        raise CookieSourceRejected()
                    claimed_id = candidate
            cookies.set_cookie(copy.copy(cookie))
        if not list(cookies):
            raise CookieSourceRejected()
        return cookies, claimed_id
    except CookieSourceRejected:
        raise
    except Exception:
        # The underlying cookie parser may include its filename or contents.
        raise CookieSourceRejected() from None


def _check_time(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise ReadDeadlineExceeded()


def _validate_destination(url: str, kind: str) -> None:
    if (type(url) is not str or not 0 < len(url) <= MAX_URL_LENGTH
            or any(ord(char) <= 32 or ord(char) == 127 for char in url)):
        raise DestinationRejected()
    try:
        parsed = urlsplit(url)
        allowed_host = CDN_HOST if kind in ("image", "ugoira_zip") else "www.pixiv.net"
        if (parsed.scheme != "https" or parsed.netloc != allowed_host
                or parsed.fragment or not parsed.path.startswith("/")
                or parsed.path.startswith("//") or "\\" in parsed.path):
            raise DestinationRejected()
        if kind == "ugoira_zip":
            if not re.fullmatch(r"/img-zip-ugoira/[A-Za-z0-9_./-]+\.zip", parsed.path) or ".." in parsed.path.split("/"):
                raise DestinationRejected()
        elif kind == "ugoira_meta":
            if not re.fullmatch(r"/ajax/illust/[1-9][0-9]{0,19}/ugoira_meta", parsed.path) or parsed.query:
                raise DestinationRejected()
        elif kind == "image":
            if not parsed.path or parsed.path == "/":
                raise DestinationRejected()
        elif kind == "ranking":
            parameters=parse_qsl(parsed.query,keep_blank_values=True,strict_parsing=True)
            if parsed.path!='/ranking.php' or len(parameters) not in (4,5):raise DestinationRejected()
            expected=['mode','content','format','p']+(['date'] if len(parameters)==5 else [])
            if [k for k,v in parameters]!=expected:raise DestinationRejected()
            values=dict(parameters)
            if (values['mode'] not in {'daily','weekly','monthly','rookie','daily_r18','weekly_r18'}
                    or values['content'] not in {'illust','manga','ugoira'} or values['format']!='json'
                    or not re.fullmatch(r'[1-9][0-9]*',values['p']) or parsed.query!=urlencode(parameters)):
                raise DestinationRejected()
            if 'date' in values:
                from datetime import datetime
                if not re.fullmatch(r'[0-9]{8}',values['date']):raise DestinationRejected()
                datetime.strptime(values['date'],'%Y%m%d')
        elif kind == "novel_ranking":
            # A different public JSON route. Never widen ranking.php's artwork
            # content allowlist: its contents format uses illust_id.
            parameters=parse_qsl(parsed.query,keep_blank_values=True,strict_parsing=True)
            expected=['mode','p']+(['date'] if len(parameters)==4 else [])+['lang']
            if (parsed.path!='/ajax/ranking/novel' or len(parameters) not in (3,4)
                    or [k for k,_ in parameters]!=expected):
                raise DestinationRejected()
            values=dict(parameters)
            if (values['mode'] not in {'daily','weekly','monthly','rookie','weekly_ai'}
                    or values['lang']!='ja'
                    or not re.fullmatch(r'[1-9][0-9]*', values['p'])
                    or parsed.query!=urlencode(parameters)):
                raise DestinationRejected()
            if 'date' in values:
                from datetime import datetime
                if not re.fullmatch(r'[0-9]{8}', values['date']):
                    raise DestinationRejected()
                datetime.strptime(values['date'],'%Y%m%d')
        elif kind in ("tag_search", "recommendation_search"):
            parameters=parse_qsl(parsed.query,keep_blank_values=True,strict_parsing=True)
            if [k for k,v in parameters]!=['word','order','mode','p','s_mode','type','lang']:raise DestinationRejected()
            values=dict(parameters);word=values['word']
            if (not word or len(word)>256 or any(ord(c)<32 or ord(c)==127 for c in word)
                    or parsed.path!='/ajax/search/artworks/'+quote(word,safe='')
                    or values['order']!='date_d' or values['mode'] not in ({'safe','r18'} if kind=='tag_search' else {'all','safe','r18'})
                    or not re.fullmatch(r'[1-9][0-9]*',values['p'])
                    or (kind=='tag_search' and values['p']!='1') or values['s_mode']!='s_tag' or values['type']!='all' or values['lang']!='ja'
                    or parsed.query!=urlencode(parameters)):
                raise DestinationRejected()
        elif kind == "related_authors":
            if (not re.fullmatch(r'/ajax/user/[1-9][0-9]{0,19}/recommends',parsed.path)
                    or not re.fullmatch(r'userNum=10&workNum=4&isR18=(true|false)&lang=ja',parsed.query)):
                raise DestinationRejected()
        elif kind == "home":
            if url != API_ORIGIN + "/":
                raise DestinationRejected()
        elif kind == "identity":
            if parsed.path != "/ajax/user/extra" or parsed.query:
                raise DestinationRejected()
        elif kind == "bookmarks":
            if not _BOOKMARK_PATH.fullmatch(parsed.path):
                raise DestinationRejected()
            # URL is built internally, but verify the complete query shape.
            if not re.fullmatch(
                r"tag=&offset=(0|[1-9][0-9]*)&limit=48&rest=(show|hide)&lang=ja",
                parsed.query,
            ):
                raise DestinationRejected()
        elif kind == "pages":
            if not _PAGES_PATH.fullmatch(parsed.path) or parsed.query:
                raise DestinationRejected()
        elif kind == "author_all":
            if not _AUTHOR_ALL_PATH.fullmatch(parsed.path) or parsed.query:
                raise DestinationRejected()
        elif kind == 'following':
            if not _FOLLOWING_PATH.fullmatch(parsed.path):
                raise DestinationRejected()
            if not re.fullmatch(
                    r'offset=(0|[1-9][0-9]*)&limit=24&rest=(show|hide)&lang=ja',
                    parsed.query):
                raise DestinationRejected()
        elif kind == "author_novel_details":
            if not re.fullmatch(r'/ajax/user/[1-9][0-9]{0,19}/profile/novels', parsed.path):
                raise DestinationRejected()
            parameters = parse_qsl(parsed.query, keep_blank_values=True)
            if (not 2 <= len(parameters) <= 49 or parameters[0] not in
                    [('is_first_page', '0'), ('is_first_page', '1')]
                    or any(k != 'ids[]' or not _ID.fullmatch(v) for k,v in parameters[1:])
                    or len({v for _,v in parameters[1:]}) != len(parameters)-1):
                raise DestinationRejected()
        elif kind == "author_details":
            if not _AUTHOR_DETAILS_PATH.fullmatch(parsed.path):
                raise DestinationRejected()
            # Internal construction: one bounded batch of unique IDs, in order.
            parameters = parse_qsl(parsed.query, keep_blank_values=True)
            if (len(parameters) < 3 or len(parameters) > BOOKMARK_PAGE_SIZE + 2
                    or parameters[:2] not in
                    ([('work_category', 'illustManga'), ('is_first_page', '1')],
                     [('work_category', 'illustManga'), ('is_first_page', '0')])
                    or any(key != 'ids[]' or not _ID.fullmatch(value)
                           for key, value in parameters[2:])
                    or len({value for _key, value in parameters[2:]}) != len(parameters) - 2):
                raise DestinationRejected()
        elif kind == "artwork":
            if not _DETAIL_PATH.fullmatch(parsed.path) or parsed.query:
                raise DestinationRejected()
        elif kind == "novel":
            if (not _NOVEL_DETAIL_PATH.fullmatch(parsed.path)
                    or url != API_ORIGIN + parsed.path):
                raise DestinationRejected()
        else:
            raise DestinationRejected()
    except (ValueError, UnicodeError):
        raise DestinationRejected() from None


def _read_response(opener, request: Request, timeout: float,
                   limit: int, deadline: float, *, owner=None) -> bytes:
    """Use urllib without automatic redirects, retries, proxies or decoding."""
    try:
        response = opener.open(request, timeout=timeout)
        if owner is not None:
            owner.response(response)
        # The default transport owns close under its SocketFence cleanup gate.
        # An early context-manager close could hide a failed fp alias close.
        with (nullcontext(response) if owner is not None else response) as response:
            _check_time(deadline)
            if response.getcode() != 200:
                status=response.getcode()
                if type(status) is int and 100<=status<=599:raise HTTPStatusRejected(status)
                raise ResponseRejected()
            headers = response.headers
            if sum(len(str(k)) + len(str(v)) for k, v in headers.items()) > MAX_HEADER_BYTES:
                raise ResponseLimitExceeded()
            if headers.get("Content-Encoding", "").strip().lower() not in ("", "identity"):
                raise ResponseRejected()
            length = headers.get("Content-Length")
            if length is not None:
                if not re.fullmatch(r"[0-9]{1,12}", length.strip()):
                    raise ResponseRejected()
                if int(length) > limit:
                    raise ResponseLimitExceeded()
            read = getattr(response, "read1", None)
            if not callable(read):
                read = getattr(getattr(response, "fp", None), "read1", None)
            if not callable(read):
                read = response.read  # Compatibility for explicitly injected test responses.
            data = bytearray()
            while True:
                _check_time(deadline)
                if owner is not None:
                    owner.check()
                chunk = read(min(65536, limit + 1 - len(data)))
                _check_time(deadline)
                if owner is not None:
                    owner.check()
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > limit:
                    raise ResponseLimitExceeded()
            return bytes(data)
    except PixivReadError:
        raise
    except HTTPError as exc:
        status = exc.code
        if owner is not None:
            owner.response(exc)
        else:
            try:
                exc.close()
            except Exception:
                pass
        if type(status) is int and 100 <= status <= 599:
            raise HTTPStatusRejected(status) from None
        raise ResponseRejected() from None
    except (URLError, OSError, ValueError) as error:
        # The owned HTTPS socket timeout is derived from this attempt's
        # absolute deadline. On Windows it can fire a few milliseconds before
        # monotonic() crosses that boundary, while the watchdog still owns
        # cancellation. Preserve that deadline classification instead of
        # reporting a generic transport failure. Borrowed/injected openers
        # do not have the same timeout contract.
        if owner is not None and (
                isinstance(error, TimeoutError)
                or (isinstance(error, URLError)
                    and isinstance(error.reason, TimeoutError))):
            raise ReadDeadlineExceeded() from None
        raise TransportFailed() from None


# Request headers include only API cookies (never image cookies).
# The injected transport receives a prepared urllib.Request and cannot
# redirect or alter the client-side URL allowlist.
Transport = Callable[[Request, float, int, float], bytes]


class _OwnedReadTransport(OwnedHTTPSPool):
    """Owned I/O for an already-admitted GET; public route checks stay upstream."""
    def __call__(self, request, timeout, limit, deadline):
        try:
            parsed = urlsplit(request.full_url)
            port = parsed.port or 443
            if (request.get_method() != "GET" or request.data is not None
                    or parsed.scheme != "https" or not parsed.hostname
                    or parsed.username or parsed.password or parsed.fragment):
                raise DestinationRejected()
        except (AttributeError, TypeError, ValueError):
            raise DestinationRejected() from None
        if (type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0
                or type(deadline) not in (int, float) or not math.isfinite(deadline)
                or type(limit) is not int or limit <= 0):
            raise ResponseRejected()
        effective_deadline = min(deadline, time.monotonic() + MAX_REQUEST_SECONDS)
        try:
            with self.session(
                    timeout=min(timeout, MAX_REQUEST_SECONDS), deadline=effective_deadline,
                    host=parsed.hostname, port=port) as owner:
                opener = build_opener(ProxyHandler({}), _NoRedirects(), OwnedHTTPSHandler(owner))
                return _read_response(
                    opener, request, owner.remaining(), limit, owner.deadline, owner=owner)
        except Exception as error:
            if self.closed:
                raise ClientClosed() from None
            if self.revocation_unverified:
                # Cleanup uncertainty is not a transient GET eligible for retry.
                raise ResponseRejected() from None
            _check_time(effective_deadline)
            if isinstance(error, TimeoutError):
                # Timeout during DNS/connect/TLS/fence checks is likewise
                # bounded by this owner's absolute request deadline.
                raise ReadDeadlineExceeded() from None
            if isinstance(error, PixivReadError):
                raise
            raise TransportFailed() from None




class _UserDataParser(HTMLParser):
    """Only the logged-in global user record is valid identity evidence."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.matches: list[str] = []
        self.scripts: list[str] = []
        self._capture = False
        self._script_chunks: list[str] = []
        self.malformed = False

    def _identity_attributes(self, attrs, expected_id):
        ids = [value for key, value in attrs if key == "id"]
        if expected_id not in ids:
            return None
        keys = [key for key, _value in attrs]
        if ids != [expected_id] or len(keys) != len(set(keys)):
            self.malformed = True
            return None
        return dict(attrs)

    def handle_starttag(self, tag, attrs):
        if self._capture:
            self.malformed = True
        if tag == "meta":
            attributes = self._identity_attributes(attrs, "meta-global-data")
            if attributes is not None:
                self.matches.append(attributes.get("content", ""))
        elif tag == "script" and self._identity_attributes(attrs, "__NEXT_DATA__") is not None:
            if self._capture:
                self.malformed = True
            self._capture = True
            self._script_chunks = []

    def handle_startendtag(self, tag, attrs):
        if tag == "meta":
            # META is a void element: legacy XHTML-style '/>' is valid.
            self.handle_starttag(tag, attrs)
        elif tag == "script" and any(key == "id" and value == "__NEXT_DATA__"
                                      for key, value in attrs):
            self.malformed = True

    def handle_data(self, data):
        if self._capture:
            self._script_chunks.append(data)

    def handle_endtag(self, tag):
        if self._capture and tag == "script":
            self.scripts.append("".join(self._script_chunks))
            self._capture = False


def _identity_json(content: str):
    """Reject conflicting/duplicate authentication keys rather than taking the last."""
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise AuthenticationMissing()
            result[key] = value
        return result

    def invalid_constant(_value):
        raise AuthenticationMissing()

    if type(content) is not str:
        raise AuthenticationMissing()
    return json.loads(content, object_pairs_hook=unique_keys, parse_constant=invalid_constant)


def _next_identity(content: str) -> str:
    document = _identity_json(content)
    if type(document) is not dict or type(document.get("props")) is not dict:
        raise AuthenticationMissing()
    page = document["props"].get("pageProps")
    if type(page) is not dict or page.get("isLoggedIn") is not True:
        raise AuthenticationMissing()
    user = page.get("gaUserData")
    if type(user) is not dict:
        raise AuthenticationMissing()
    candidates = [user[k] for k in ("id", "userId", "user_id") if k in user]
    if len(candidates) != 1:
        raise AuthenticationMissing()
    return _identifier(candidates[0])


def _identity_from_home(data: bytes) -> str:
    try:
        if type(data) is not bytes or len(data) > HTML_LIMIT:
            raise AuthenticationMissing()
        parser = _UserDataParser()
        parser.feed(data.decode("utf-8"))
        parser.close()
        if (parser.malformed or parser._capture or
                len(parser.matches) > MAX_IDENTITY_META or
                len(parser.scripts) > MAX_IDENTITY_SCRIPTS or
                not (parser.matches or parser.scripts)):
            raise AuthenticationMissing()
        identities = []
        if parser.matches:
            global_data = _identity_json(parser.matches[0])
            if type(global_data) is not dict or type(global_data.get("userData")) is not dict:
                raise AuthenticationMissing()
            identities.append(_identifier(global_data["userData"].get("id")))
        if parser.scripts:
            identities.append(_next_identity(parser.scripts[0]))
        if len(set(identities)) != 1:
            raise AuthenticationMissing()
        return identities[0]
    except (ValueError, TypeError, UnicodeError, InvalidPayload, RecursionError, AssertionError):
        raise AuthenticationMissing() from None


class LivePixivReadClient:
    """Explicit-cookie Pixiv reader; all requests are strictly bounded GETs.

    verify_identity establishes the logged-in account from the homepage
    meta-global-data.userData.id or strictly logged-in Next.js page props,
    then checks /ajax/user/extra for success.
    Cookie/PHPSESSID and API IDs, when present, must match that server ID.
    An anonymous home or empty API response alone never proves identity.
    """

    def __init__(self, cookie_path: str | Path, *, transport: Transport | None = None):
        self._cookies, self._cookie_user_id = _read_cookies(cookie_path)
        self._api_opener = build_opener(
            ProxyHandler({}), _NoRedirects(), HTTPSHandler(),
        )
        self._cdn_opener = build_opener(
            ProxyHandler({}), _NoRedirects(), HTTPSHandler(),
        )
        self._owned_transport = _OwnedReadTransport() if transport is None else None
        self._transport: Transport = transport if transport is not None else self._owned_transport
        self._verified_identity: str | None = None
        # A service-owned live client can receive bookmark refresh, original
        # download and explicit detail requests on different worker threads.
        # Protect the entire identity transition, not just individual fields.
        self._identity_lock = RLock()
        self._closed = False

    def __enter__(self):
        self._ensure_open()
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self) -> None:
        # Revoke only this client's default I/O; injected transports are borrowed.
        # The request worker remains responsible for draining its owned aliases.
        self._closed = True
        owned = getattr(self, "_owned_transport", None)
        if owned is not None:
            owned.close()
        self._verified_identity = None
        self._cookie_user_id = None
        self._cookies.clear()

    @property
    def shutdown_unverified(self) -> bool:
        owned = getattr(self, "_owned_transport", None)
        return bool(owned is not None and owned.revocation_unverified)

    def _ensure_open(self) -> None:
        if self._closed or self.shutdown_unverified:
            raise ClientClosed()

    def set_request_admission(self, admission):
        if not callable(admission):raise ValueError('request admission must be callable')
        self._request_admission=admission

    def set_retry_policy(self,policy):
        from .request_policy import RequestRetryPolicy
        if policy is not None and not isinstance(policy,RequestRetryPolicy):raise TypeError('request retry policy required')
        self._retry_policy=policy

    def _get(self, kind: str, url: str, *, deadline: float) -> bytes:
        from .request_policy import request_scope
        scoped_deadline = _READ_DEADLINE.get()
        if scoped_deadline is not None:
            deadline = min(deadline, scoped_deadline)
        with request_scope(checkpoint=self._ensure_open):
            policy=getattr(self,'_retry_policy',None)
            if policy is None:return self._get_once(kind,url,deadline=deadline)
            return policy.execute(lambda:self._get_once(kind,url,deadline=deadline),deadline=deadline)

    def _get_once(self, kind: str, url: str, *, deadline: float) -> bytes:
        from .request_policy import request_checkpoint
        scoped_deadline = _READ_DEADLINE.get()
        if scoped_deadline is not None:
            deadline = min(deadline, scoped_deadline)
        request_checkpoint()
        self._ensure_open()
        _validate_destination(url, kind)
        _check_time(deadline)
        admission=getattr(self, '_request_admission', None)
        if admission is not None:
            admission(deadline)
            self._ensure_open()
            _check_time(deadline)
        request = Request(url, method="GET", headers=_JSON_HEADERS)
        if kind in ("image", "ugoira_zip"):
            request.remove_header("Accept")
            request.add_header("Accept", "image/avif,image/webp,image/*")
        else:
            # Construct a frozen snapshot of the Cookie header, rather than
            # allowing any Set-Cookie response to mutate authentication.
            self._cookies.add_cookie_header(request)
            cookie_header = request.get_header("Cookie") or ""
            if (len(cookie_header) > 16384
                    or any(ord(char) < 32 or ord(char) == 127 for char in cookie_header)):
                raise CookieSourceRejected()
        if request.get_method() != "GET" or request.full_url != url:
            raise DestinationRejected()
        if kind in ("image", "ugoira_zip") and request.has_header("Cookie"):
            raise DestinationRejected()
        per_request_deadline = min(deadline, time.monotonic() + MAX_REQUEST_SECONDS)
        timeout = max(0.001, min(MAX_REQUEST_SECONDS, per_request_deadline - time.monotonic()))
        from .ugoira_archive import MAX_ARCHIVE_BYTES
        limit = (MAX_ARCHIVE_BYTES if kind == "ugoira_zip" else IMAGE_LIMIT if kind == "image" else
                 HTML_LIMIT if kind == "home" else JSON_LIMIT)
        try:
            response = self._transport(request, timeout, limit, per_request_deadline)
        except PixivReadError:
            raise
        except HTTPError as error:
            status=error.code
            try:error.close()
            except Exception:pass
            if type(status) is int and 100<=status<=599:raise HTTPStatusRejected(status) from None
            raise ResponseRejected() from None
        except Exception:
            raise TransportFailed() from None
        _check_time(per_request_deadline)
        if type(response) is not bytes:
            raise ResponseRejected()
        if len(response) > limit:
            raise ResponseLimitExceeded()
        request_checkpoint()
        self._ensure_open()
        return response

    def _json(self, kind: str, url: str, *, deadline: float) -> object:
        body = self._get(kind, url, deadline=deadline)
        try:
            packet = json.loads(
                body, object_pairs_hook=_payload_unique_keys,
                parse_constant=_payload_invalid_constant,
            )
        except (ValueError, UnicodeError):
            raise InvalidPayload() from None
        if type(packet) is not dict:
            raise InvalidPayload()
        if packet.get("error") is not False:
            raise ApiRejected()
        if "body" not in packet:
            raise InvalidPayload()
        return packet["body"]

    def verify_identity(self) -> str:
        with self._identity_lock:
            return self._verify_identity_locked()

    def require_identity(self) -> str:
        """Verify once per immutable cookie source, preserving thread safety."""
        with self._identity_lock:
            self._ensure_open()
            return (self._verified_identity if self._verified_identity is not None
                    else self._verify_identity_locked())

    def _verify_identity_locked(self, *, deadline=None) -> str:
        self._ensure_open()
        self._verified_identity = None
        deadline = (time.monotonic() + MAX_OPERATION_SECONDS if deadline is None else deadline)
        homepage = self._get("home", API_ORIGIN + "/", deadline=deadline)
        actual = _identity_from_home(homepage)
        if self._cookie_user_id is not None and self._cookie_user_id != actual:
            raise AuthenticationMissing()
        body = self._json("identity", API_ORIGIN + "/ajax/user/extra", deadline=deadline)
        if not isinstance(body, dict):
            raise AuthenticationMissing()
        # Some API variants expose a user ID; the legacy endpoint is also
        # known to return account statistics without an ID.
        claimed = [body[key] for key in ("userId", "id") if key in body]
        if isinstance(body.get("user"), dict):
            claimed.extend(body["user"][key] for key in ("userId", "id")
                           if key in body["user"])
        try:
            response_ids = {_identifier(value) for value in claimed}
        except InvalidPayload:
            raise AuthenticationMissing() from None
        if len(response_ids) > 1:
            raise AuthenticationMissing()
        response_id = next(iter(response_ids), None)
        if response_id is not None and response_id != actual:
            raise AuthenticationMissing()
        self._ensure_open()
        self._verified_identity = actual
        return actual

    def _assert_identity(self, my_user_id: str) -> str:
        try:
            requested = _identifier(my_user_id)
        except InvalidPayload:
            raise AuthenticationMissing() from None
        with self._identity_lock:
            self._ensure_open()
            if self._verified_identity is None:
                self.verify_identity()
            if requested != self._verified_identity:
                raise AuthenticationMissing()
        return requested

    def _bookmarks(self, my_user_id: str, rest_type: str, *, novel: bool):
        self._ensure_open()
        if rest_type not in ("show", "hide"):
            raise DestinationRejected()
        uid = self._assert_identity(my_user_id)
        deadline = time.monotonic() + MAX_OPERATION_SECONDS
        category = "novels" if novel else "illusts"
        seen: set[str] = set()
        total = 0
        for page_no in range(MAX_ROUTE_PAGES):
            _check_time(deadline)
            offset = page_no * BOOKMARK_PAGE_SIZE
            query = urlencode({
                "tag": "", "offset": offset, "limit": BOOKMARK_PAGE_SIZE,
                "rest": rest_type, "lang": "ja",
            })
            url = f"{API_ORIGIN}/ajax/user/{uid}/{category}/bookmarks?{query}"
            body = self._json("bookmarks", url, deadline=deadline)
            if not isinstance(body, dict) or type(body.get("works")) is not list:
                raise InvalidPayload()
            works = body["works"]
            if len(works) > BOOKMARK_PAGE_SIZE:
                raise InvalidPayload()
            if not works:
                return
            novel_count = 0
            for work in works:
                if not isinstance(work, dict):
                    raise InvalidPayload()
                if "isMasked" in work and type(work["isMasked"]) is not bool:
                    raise InvalidPayload()
                wid = _identifier(work.get("id"))
                if wid in seen:
                    continue
                seen.add(wid)
                total += 1
                novel_count += 1
                if total > MAX_BOOKMARKS_PER_ROUTE:
                    raise ResponseLimitExceeded()
                # Unavailable/private/deleted entries may now carry a static
                # placeholder URL and author ID zero. They are not viewable
                # works: never fetch that placeholder or invent an identity.
                # Count their stable IDs above so an all-masked page still
                # advances pagination, and a repeated page still fails closed.
                if work.get("isMasked") is True:
                    continue
                thumb = work.get("url")
                if thumb is None or thumb == "":
                    continue  # Legacy library omits works without a preview.
                if type(thumb) is not str:
                    raise InvalidPayload()
                tags = work.get("tags") or []
                if isinstance(tags, dict):
                    tags = tags.get("tags") or []
                if not isinstance(tags, list):
                    raise InvalidPayload()
                tag_names = []
                for tag in tags:
                    if isinstance(tag, str):
                        tag_names.append(tag)
                    elif isinstance(tag, dict) and isinstance(tag.get("tag", tag.get("name")), str):
                        tag_names.append(tag.get("tag", tag.get("name")))
                    else:
                        raise InvalidPayload()
                try:
                    aid = _identifier(work.get("userId"))
                except InvalidPayload:
                    raise InvalidPayload() from None
                title = work.get("title") or ""
                user_name = work.get("userName") or ""
                created = work.get("createDate") or ""
                if any(type(s) is not str for s in (title, user_name, thumb, created)):
                    raise InvalidPayload()
                if thumb:
                    _validate_destination(thumb, "image")
                restrict = work.get("xRestrict", 0)
                if type(restrict) is not int or restrict not in (0, 1, 2):
                    raise InvalidPayload()
                row = {
                    "id": wid, "title": title, "user_id": aid, "user_name": user_name,
                    "tags": tag_names, "thumb_url": thumb, "create_date": created,
                    "x_restrict": restrict,
                }
                if not novel:
                    illust_type = work.get("illustType", 0)
                    pages = work.get("pageCount", 1)
                    if type(illust_type) is not int or illust_type not in (0, 1, 2):
                        raise InvalidPayload()
                    if type(pages) not in (str, int) or not str(pages).isascii() or not str(pages).isdecimal():
                        raise InvalidPayload()
                    if not 1 <= int(pages) <= MAX_ARTWORK_PAGES:
                        raise InvalidPayload()
                    row["type"] = illust_type
                    row["page_count"] = int(pages)
                else:
                    row["type"] = "novel"
                yield row
            if novel_count == 0:
                # Repeated pages must never masquerade as a completed crawl.
                raise InvalidPayload()
        raise ResponseLimitExceeded()

    def get_bookmarked_works(self, my_user_id: str, rest_type: str = "show"):
        yield from self._bookmarks(my_user_id, rest_type, novel=False)

    def get_bookmarked_novels(self, my_user_id: str, rest_type: str = "show"):
        yield from self._bookmarks(my_user_id, rest_type, novel=True)

    def get_following_users(self, my_user_id: str, rest_type: str = 'show') -> tuple[dict, ...]:
        """Bounded GET-only list of the verified account's followed authors."""
        return self._following_users(my_user_id, rest_type, detailed=False)

    def get_following_users_detailed(self, my_user_id: str, rest_type: str = 'show') -> tuple[dict, ...]:
        """Same bounded GET route with validated fields for the owned S03 store."""
        return self._following_users(my_user_id, rest_type, detailed=True)

    def _following_users(self, my_user_id: str, rest_type: str, *, detailed: bool) -> tuple[dict, ...]:
        if rest_type not in ('show', 'hide'):
            raise DestinationRejected()
        uid = self._assert_identity(my_user_id)
        deadline = time.monotonic() + MAX_OPERATION_SECONDS
        found: list[dict] = []
        seen: set[str] = set()
        for page in range((MAX_FOLLOWED_AUTHORS // FOLLOWING_PAGE_SIZE) + 2):
            _check_time(deadline)
            query = urlencode({'offset': page * FOLLOWING_PAGE_SIZE,
                               'limit': FOLLOWING_PAGE_SIZE, 'rest': rest_type,
                               'lang': 'ja'})
            body = self._json('following', f'{API_ORIGIN}/ajax/user/{uid}/following?{query}',
                              deadline=deadline)
            if type(body) is not dict or type(body.get('users')) is not list:
                raise InvalidPayload()
            users = body['users']
            if len(users) > FOLLOWING_PAGE_SIZE:
                raise InvalidPayload()
            if not users:
                return tuple(found)
            progress = 0
            for entry in users:
                if type(entry) is not dict:
                    raise InvalidPayload()
                author_id = _identifier(entry.get('userId'))
                name = entry.get('userName')
                if (type(name) is not str or len(name) > 256 or
                        any(ord(char) < 32 or ord(char) == 127 for char in name)):
                    raise InvalidPayload()
                if author_id in seen:
                    continue
                progress += 1
                seen.add(author_id)
                record = {'user_id': author_id, 'name': name}
                if detailed:
                    from .bookmark_remote_adapter import thumbnail_url
                    account = entry.get('userAccount')
                    profile = entry.get('profileImageUrl')
                    account = '' if account is None else account
                    profile = '' if profile is None else profile
                    if (type(account) is not str or len(account) > 256
                            or any(ord(char) < 32 or ord(char) == 127
                                   or 0xD800 <= ord(char) <= 0xDFFF for char in account)
                            or type(profile) is not str or len(profile) > MAX_URL_LENGTH):
                        raise InvalidPayload()
                    if profile:
                        try:
                            thumbnail_url(profile)
                        except (ValueError, TypeError):
                            raise InvalidPayload() from None
                    record.update(account=account, profile_img=profile)
                found.append(record)
                if len(found) > MAX_FOLLOWED_AUTHORS:
                    raise ResponseLimitExceeded()
            if progress == 0:
                raise InvalidPayload()  # Repeated nonempty page cannot end a crawl.
        raise ResponseLimitExceeded()

    def get_author_artworks(self, user_id: str) -> tuple[dict, ...]:
        """Read an author's illustration/manga metadata with one bounded GET batch.

        Returns no partial result if a detail batch is missing or malformed.
        This reader does not decide whether an artwork needs downloading.
        """
        return self._get_author_artworks(user_id, require_complete=False)

    def get_author_artworks_complete(self, user_id: str) -> tuple[dict, ...]:
        """Require explicit complete illustration/manga buckets and detail pages.

        Uses exactly the existing author profile/all and profile/illusts GETs.
        No partial inventory is returned when a bucket or detail is invisible.
        """
        return self._get_author_artworks(user_id, require_complete=True)

    def get_user_works(self, user_id, early_exit_checker=None, lane='background'):
        """Original discovery author lane; an early exit is never a complete inventory."""
        if early_exit_checker is not None and not callable(early_exit_checker):
            raise ValueError('invalid early exit checker')
        return self._get_author_artworks(user_id, require_complete=False,
            discovery=True, early_exit_checker=early_exit_checker)

    def _get_author_artworks(self, user_id: str, *, require_complete: bool,
                            discovery=False, early_exit_checker=None) -> tuple[dict, ...]:
        self._ensure_open()
        try:
            uid = _identifier(user_id)
        except InvalidPayload:
            raise DestinationRejected() from None
        if self._verified_identity is None:
            self.verify_identity()
        deadline = time.monotonic() + MAX_OPERATION_SECONDS
        body = self._json('author_all',
                          f'{API_ORIGIN}/ajax/user/{uid}/profile/all', deadline=deadline)
        if type(body) is not dict:
            raise InvalidPayload()
        ids: set[str] = set()
        for field in ('illusts', 'manga'):
            bucket = body.get(field)
            # Empty arrays are an explicit zero-count bucket for both reader
            # modes. A non-empty array remains malformed and never supplies IDs.
            if type(bucket) is list and not bucket:
                bucket = {}
            if require_complete:
                # Some response variants may encode an explicitly empty
                # category as []; only that exact empty list proves zero.
                # Missing/null buckets and non-empty arrays remain incomplete.
                if type(bucket) is list:
                    if bucket:
                        raise InvalidPayload()
                    bucket = {}
                elif type(bucket) is not dict:
                    raise InvalidPayload()
            if bucket is None:
                bucket = {}
            if type(bucket) is not dict:
                raise InvalidPayload()
            for raw_id in bucket:
                ids.add(_identifier(raw_id))
                if len(ids) > MAX_AUTHOR_WORKS:
                    raise ResponseLimitExceeded()
        ordered = sorted(ids, key=int, reverse=True)
        records: list[dict] = []
        for offset in range(0, len(ordered), BOOKMARK_PAGE_SIZE):
            _check_time(deadline)
            chunk = ordered[offset:offset + BOOKMARK_PAGE_SIZE]
            query = urlencode([
                ('work_category', 'illustManga'),
                ('is_first_page', '1' if offset == 0 else '0'),
                *(("ids[]", wid) for wid in chunk),
            ])
            details = self._json('author_details',
                                 f'{API_ORIGIN}/ajax/user/{uid}/profile/illusts?{query}',
                                 deadline=deadline)
            if type(details) is not dict or type(details.get('works')) is not dict:
                raise InvalidPayload()
            works = details['works']
            if set(works) != set(chunk):
                raise InvalidPayload()
            for wid in chunk:
                entry = works[wid]
                if type(entry) is not dict or _identifier(entry.get('id')) != wid:
                    raise InvalidPayload()
                if require_complete and entry.get('isMasked', False) is not False:
                    raise InvalidPayload()
                if _identifier(entry.get('userId')) != uid:
                    raise InvalidPayload()
                kind = entry.get('illustType')
                count = entry.get('pageCount')
                if (type(kind) is not int or kind not in (0, 1, 2)
                        or type(count) not in (int, str) or not str(count).isascii()
                        or not str(count).isdecimal()
                        or not 1 <= int(count) <= MAX_ARTWORK_PAGES):
                    raise InvalidPayload()
                title, name = entry.get('title'), entry.get('userName')
                # Missing updateDate cannot prove an existing work is current;
                # pass the empty marker to the conservative diff classifier.
                updated, created = entry.get('updateDate', ''), entry.get('createDate', '')
                if (any(type(s) is not str or len(s) > 512
                        for s in (title, name, updated, created))):
                    raise InvalidPayload()
                if discovery:
                    normalized = _discovery_record(entry)
                    if early_exit_checker is not None and early_exit_checker(normalized):
                        return tuple(records)
                    records.append(normalized)
                    continue
                records.append({'id': wid, 'user_id': uid, 'title': title,
                                'user_name': name, 'type': kind, 'page_count': int(count),
                                'update_date': updated, 'create_date': created})
        return tuple(records)
    def get_artwork_download_record(self, work_id: str):
        """Resolve one explicitly requested illustration ID without bookmarking it.

        The full detail endpoint is read-only. Caller must first establish a
        verified identity; the returned DTO is usable only for an explicit
        managed download and is not evidence that this work is bookmarked.
        """
        self._ensure_open()
        try:
            wid = _identifier(work_id)
        except InvalidPayload:
            raise DestinationRejected() from None
        with self._identity_lock:
            self._ensure_open()
            if self._verified_identity is None:
                raise AuthenticationMissing()
            expected_identity = self._verified_identity
            deadline = time.monotonic() + MAX_OPERATION_SECONDS
            try:
                body = self._json("artwork", f"{API_ORIGIN}/ajax/illust/{wid}", deadline=deadline)
            except (HTTPStatusRejected, ApiRejected) as error:
                if isinstance(error, HTTPStatusRejected) and error.status_code != 403:
                    raise
                # A private/deleted work and an expired login may share 403
                # or an API rejection. Revalidate the SAME account once under
                # the original deadline before classifying this one row.
                # Auth/transport/429 failures in this verification propagate;
                # they must stop a bulk operation, not multiply across rows.
                actual = self._verify_identity_locked(deadline=deadline)
                if actual != expected_identity:
                    self._verified_identity = None
                    raise AuthenticationMissing()
                raise ArtworkUnavailable() from None
            self._ensure_open()
        if type(body) is dict and body.get('isMasked') is True:
            raise ArtworkUnavailable()
        if type(body) is not dict or ("isMasked" in body and body["isMasked"] is not False):
            raise InvalidPayload()
        try:
            if _identifier(body.get("id")) != wid:
                raise InvalidPayload()
            author_id = _identifier(body.get("userId"))
        except InvalidPayload:
            raise InvalidPayload() from None
        art_type = body.get("illustType")
        count = body.get("pageCount")
        restrict = body.get("xRestrict", 0)
        if type(art_type) is int and art_type == 2:
            # Ugoira is an understood but unsupported direct-static target;
            # unknown types and malformed identities remain shared failures.
            raise ArtworkUnavailable()
        if (type(art_type) is not int or art_type not in (0, 1) or
                type(count) not in (int, str) or not str(count).isascii() or
                not str(count).isdecimal() or not 1 <= int(count) <= MAX_ARTWORK_PAGES or
                type(restrict) is not int or restrict not in (0, 1, 2)):
            raise InvalidPayload()
        title, author, created = (body.get("title"), body.get("userName"),
                                  body.get("createDate", ""))
        for value, maximum in ((title, 256), (author, 256), (created, 128)):
            if (type(value) is not str or len(value) > maximum or
                    any(ord(char) < 32 or ord(char) == 127 for char in value)):
                raise InvalidPayload()
        tag_data = body.get("tags", {})
        tags_raw = tag_data.get("tags") if type(tag_data) is dict else None
        if type(tags_raw) is not list or len(tags_raw) > 100:
            raise InvalidPayload()
        tags = []
        for item in tags_raw:
            tag = item.get("tag") if type(item) is dict else None
            if (type(tag) is not str or len(tag) > 128 or
                    any(ord(char) < 32 or ord(char) == 127 for char in tag)):
                raise InvalidPayload()
            tags.append(tag)
        urls = body.get("urls", {})
        if type(urls) is not dict:
            raise InvalidPayload()
        thumb = urls.get("small") or ""
        if type(thumb) is not str:
            raise InvalidPayload()
        if thumb:
            try:
                _validate_destination(thumb, "image")
            except DestinationRejected:
                raise InvalidPayload() from None
        from .bookmark_remote_adapter import RemoteBookmark
        return RemoteBookmark(wid, "illustration", "show", title, author_id,
                              author, tuple(tags), thumb, created, int(count),
                              art_type, restrict)

    def get_artwork_metadata_raw(self, work_id: str, expected_author_id: str) -> dict:
        """Read a saved illustration's detail metadata; never writes any catalog.

        The explicit caller supplies both IDs from an immutable managed receipt.
        Identity must already be verified. HTTP status remains typed so a 404
        can be distinguished from auth/rate-limit/server failures without
        inspecting messages or response bodies.
        """
        self._ensure_open()
        try:
            wid = _identifier(work_id)
            author_id = _identifier(expected_author_id)
        except InvalidPayload:
            raise DestinationRejected() from None
        with self._identity_lock:
            self._ensure_open()
            if self._verified_identity is None:
                raise AuthenticationMissing()
            from .request_policy import request_scope
            with request_scope(allow_retry=False):
                body = self._json("artwork", f"{API_ORIGIN}/ajax/illust/{wid}",
                                  deadline=time.monotonic() + MAX_OPERATION_SECONDS)
            self._ensure_open()
        if type(body) is not dict or body.get("isMasked") is True:
            raise InvalidPayload()
        try:
            if _identifier(body.get("id")) != wid:
                raise InvalidPayload()
            if _identifier(body.get("userId")) != author_id:
                raise InvalidPayload()
        except InvalidPayload:
            raise InvalidPayload() from None
        return body

    def get_ranking(self, *, mode='daily', content='illust', page=1, date=None):
        """One explicit public-ranking page; no retry or implicit next-page GET."""
        if type(page) is not int or page<1:raise DestinationRejected()
        params={'mode':mode,'content':content,'format':'json','p':page}
        if date is not None:params['date']=date
        url=API_ORIGIN+'/ranking.php?'+urlencode(params)
        _validate_destination(url,'ranking')
        self.require_identity()
        raw=self._get('ranking',url,deadline=time.monotonic()+MAX_OPERATION_SECONDS)
        try:packet=json.loads(raw,object_pairs_hook=_payload_unique_keys,parse_constant=_payload_invalid_constant)
        except (ValueError,UnicodeError):raise InvalidPayload() from None
        if type(packet) is not dict or type(packet.get('contents')) is not list:raise InvalidPayload()
        return packet['contents']

    def get_novel_ranking(self, *, mode='daily', page=1, date=None):
        """One explicit public GET from /ajax/ranking/novel, never ranking.php.

        The published schema has body.display_a.rank_a. No Cookie file,
        original media, response body, or raw exception is logged here.
        """
        if (type(mode) is not str or mode not in {'daily','weekly','monthly','rookie','weekly_ai'}
                or type(page) is not int or not 1 <= page <= MAX_ROUTE_PAGES):
            raise DestinationRejected()
        if date is not None:
            if (type(date) is not str or not re.fullmatch(r'[0-9]{8}',date)):
                raise DestinationRejected()
            from datetime import datetime
            try:
                datetime.strptime(date,'%Y%m%d')
            except ValueError:
                raise DestinationRejected() from None
        params=dict(mode=mode,p=page)
        if date is not None:params['date']=date
        params['lang']='ja'
        url=API_ORIGIN+'/ajax/ranking/novel?'+urlencode(params)
        _validate_destination(url,'novel_ranking')
        self.require_identity()
        body=self._json('novel_ranking',url,deadline=time.monotonic()+MAX_OPERATION_SECONDS)
        if type(body) is not dict or type(body.get('display_a')) is not dict:
            raise InvalidPayload()
        display=body['display_a']
        rows=display.get('rank_a')
        if type(rows) is dict:
            # Weekly AI was observed as a sparse, numeric-index object,
            # including missing positions between 0 and 49. Never use
            # insertion order or arbitrary object keys as a ranking order.
            if (len(rows)>50 or any(type(key) is not str or
                    not re.fullmatch(r'(0|[1-9][0-9]*)',key) or
                    int(key)>49 for key in rows)):
                raise InvalidPayload()
            rows=[value for key,value in sorted(rows.items(),key=lambda entry:int(entry[0]))]
        if (type(rows) is not list or len(rows)>50
                or type(display.get('page')) is not int
                or display['page']!=page
                or any(type(row) is not dict for row in rows)):
            raise InvalidPayload()
        return rows

    def search_illusts(self, word, *, page=1, order='date_d', mode='safe'):
        """Original non-premium single-page tag search, explicitly labelled by UI."""
        if type(word) is not str or type(page) is not int:raise DestinationRejected()
        params=dict(word=word,order=order,mode=mode,p=page,s_mode='s_tag',type='all',lang='ja')
        url=API_ORIGIN+'/ajax/search/artworks/'+quote(word,safe='')+'?'+urlencode(params)
        _validate_destination(url,'tag_search')
        self.require_identity()
        body=self._json('tag_search',url,deadline=time.monotonic()+MAX_OPERATION_SECONDS)
        if type(body) is not dict or type(body.get('illustManga')) is not dict:raise InvalidPayload()
        raw=body['illustManga'].get('data')
        if type(raw) is not list:raise InvalidPayload()
        works=[]
        for row in raw:
            if type(row) is not dict:raise InvalidPayload()
            if row.get('isMasked') is True:continue
            tags=row.get('tags',[])
            if type(tags) is not list or any(type(t) is not str for t in tags):raise InvalidPayload()
            work=dict(id=_identifier(row.get('id')),user_id=_identifier(row.get('userId')),
                title=row.get('title',''),user_name=row.get('userName',''),tags=tags,
                thumbnail_url=row.get('url'),is_masked=False)
            for source,target in [('pageCount','page_count'),('illustType','illust_type'),('xRestrict','x_restrict'),('aiType','ai_type')]:
                if source in row:work[target]=row[source]
            works.append(work)
        return {'works':works}

    def search_recommendation_illusts(self, word, *, page=1, mode='all', order='date_d'):
        """One explicit original recommendation page; no retry or auto-pagination."""
        if type(word) is not str or type(page) is not int or page < 1:
            raise DestinationRejected()
        params=dict(word=word,order=order,mode=mode,p=page,s_mode='s_tag',type='all',lang='ja')
        url=API_ORIGIN+'/ajax/search/artworks/'+quote(word,safe='')+'?'+urlencode(params)
        _validate_destination(url,'recommendation_search')
        self.require_identity()
        body=self._json('recommendation_search',url,deadline=time.monotonic()+MAX_OPERATION_SECONDS)
        if type(body) is not dict or type(body.get('illustManga')) is not dict:
            raise InvalidPayload()
        result=body['illustManga'];raw=result.get('data');total=result.get('total')
        if type(raw) is not list or len(raw)>60 or type(total) is not int or total<0:
            raise InvalidPayload()
        works=[_discovery_record(row) for row in raw if not (type(row) is dict and row.get('isMasked') is True)]
        related=body.get('relatedTags',[])
        if type(related) is not list:raise InvalidPayload()
        return dict(works=works,total=total,related_tags=related)

    def get_recommended_users(self, user_id, user_num=10, work_num=4, include_r18=True, lane='background'):
        uid=_identifier(user_id)
        if type(user_num) is not int or user_num!=10 or type(work_num) is not int or work_num!=4 or type(include_r18) is not bool:
            raise DestinationRejected()
        params=dict(userNum=user_num,workNum=work_num,isR18=str(include_r18).lower(),lang='ja')
        url=f'{API_ORIGIN}/ajax/user/{uid}/recommends?'+urlencode(params)
        _validate_destination(url,'related_authors')
        self.require_identity()
        body=self._json('related_authors',url,deadline=time.monotonic()+MAX_OPERATION_SECONDS)
        if type(body) is not dict or type(body.get('users')) is not list or type(body.get('thumbnails')) is not dict:
            raise InvalidPayload()
        raw=body['thumbnails'].get('illust');users=[]
        if type(raw) is not list or len(body['users'])>10 or len(raw)>40:raise InvalidPayload()
        for row in body['users']:
            if type(row) is not dict or type(row.get('name')) is not str or type(row.get('isFollowed')) is not bool:
                raise InvalidPayload()
            users.append(dict(user_id=_identifier(row.get('userId')),name=row['name'],is_followed=row['isFollowed']))
        return dict(users=users,works=[_discovery_record(row) for row in raw
                    if not (type(row) is dict and row.get('isMasked') is True)])

    def get_discovery_work(self, work_id):
        wid=_identifier(work_id)
        self.require_identity()
        body=self._json('artwork',f'{API_ORIGIN}/ajax/illust/{wid}',deadline=time.monotonic()+MAX_OPERATION_SECONDS)
        record=_discovery_record(body)
        if record['id']!=wid:raise InvalidPayload()
        return record

    def get_work_info(self, work_id):
        """Selected ranking work AI classification; missing evidence fails closed."""
        wid=_identifier(work_id)
        self.require_identity()
        body=self._json('artwork',f'{API_ORIGIN}/ajax/illust/{wid}',deadline=time.monotonic()+MAX_OPERATION_SECONDS)
        if (type(body) is not dict or _identifier(body.get('id'))!=wid or body.get('isMasked') is True
                or type(body.get('aiType')) is not int or body['aiType'] not in (0,1,2)):
            raise InvalidPayload()
        return {'ai_type':body['aiType']}

    def get_author_novel_ids(self, author_id: str) -> tuple[str, ...]:
        """Reuse the adopted profile/all route, with no partial-list success."""
        self.require_identity()
        uid = _identifier(author_id)
        body = self._json('author_all', f'{API_ORIGIN}/ajax/user/{uid}/profile/all',
                          deadline=time.monotonic() + MAX_OPERATION_SECONDS)
        if type(body) is not dict or 'novels' not in body:
            raise InvalidPayload()
        novels = body['novels']
        if novels == []:
            return ()
        if type(novels) is not dict or len(novels) > 256:
            raise ResponseLimitExceeded()
        return tuple(_identifier(wid) for wid in novels)

    def get_user_novels(self, user_id, early_exit_checker=None):
        """Original newest-first 48-item novel metadata batches; partial on early exit."""
        if early_exit_checker is not None and not callable(early_exit_checker):
            raise ValueError('invalid early exit checker')
        uid = _identifier(user_id)
        ids = sorted(self.get_author_novel_ids(uid), key=int, reverse=True)
        deadline = time.monotonic() + MAX_OPERATION_SECONDS
        output = []
        for offset in range(0, len(ids), 48):
            _check_time(deadline)
            chunk = ids[offset:offset+48]
            query = urlencode([('is_first_page', '1' if offset == 0 else '0'),
                               *[('ids[]', wid) for wid in chunk]])
            body = self._json('author_novel_details',
                f'{API_ORIGIN}/ajax/user/{uid}/profile/novels?{query}', deadline=deadline)
            if type(body) is not dict or type(body.get('works')) is not dict or set(body['works']) != set(chunk):
                raise InvalidPayload()
            for wid in chunk:
                row = body['works'][wid]
                if (type(row) is not dict or _identifier(row.get('id')) != wid
                        or row.get('isMasked', False) is not False
                        or ('userId' in row and _identifier(row['userId']) != uid)):
                    raise InvalidPayload()
                title,date = row.get('title'),row.get('updateDate')
                if any(type(v) is not str or len(v)>512 or any(ord(c)<32 for c in v) for v in (title,date)):
                    raise InvalidPayload()
                record = dict(id=wid,user_id=uid,type='novel',title=title,update_date=date)
                if early_exit_checker is not None and early_exit_checker(record):
                    return tuple(output)
                output.append(record)
        return tuple(output)

    def resolve_novel_author(self, work_id: str) -> str:
        """Resolve an explicit novel ID without weakening the saved author pin.

        Caller verifies its identity first. Validate the complete same-route
        novel response, return only its author ID, and retain no payload cache.
        Subsequent get_novel_payload still requires that explicit author pin.
        """
        self._ensure_open()
        try:
            wid = _identifier(work_id)
        except InvalidPayload:
            raise DestinationRejected() from None
        with self._identity_lock:
            self._ensure_open()
            if self._verified_identity is None:
                raise AuthenticationMissing()
            body = self._json("novel", f"{API_ORIGIN}/ajax/novel/{wid}",
                              deadline=time.monotonic() + MAX_OPERATION_SECONDS)
            self._ensure_open()
        try:
            if type(body) is not dict:
                raise InvalidPayload()
            author_id = _identifier(body.get('userId'))
            parse_novel_detail(body, work_id=wid, expected_author_id=author_id)
            return author_id
        except (InvalidPayload, MediaPayloadRejected):
            raise InvalidPayload() from None

    def get_novel_payload(self, work_id: str, expected_author_id: str) -> dict:
        """Read one explicit novel after identity verification and pin its author.

        Reuse the existing JSON byte/time budgets and pure media parser. This
        does not enumerate a series, fetch embedded images, or save content.
        """
        self._ensure_open()
        try:
            wid = _identifier(work_id)
            author_id = _identifier(expected_author_id)
        except InvalidPayload:
            raise DestinationRejected() from None
        with self._identity_lock:
            self._ensure_open()
            if self._verified_identity is None:
                raise AuthenticationMissing()
            body = self._json("novel", f"{API_ORIGIN}/ajax/novel/{wid}",
                              deadline=time.monotonic() + MAX_OPERATION_SECONDS)
            self._ensure_open()
        try:
            parse_novel_detail(body, work_id=wid, expected_author_id=author_id)
            return body
        except MediaPayloadRejected:
            raise InvalidPayload() from None

    def get_novel_detail(self, work_id: str, expected_author_id: str) -> NovelDocument:
        """PR33-compatible DTO API over the same bounded, author-pinned GET."""
        return parse_novel_detail(self.get_novel_payload(work_id, expected_author_id),
                                  work_id=work_id, expected_author_id=expected_author_id)

    def get_artwork_update_revision(self, work_id: str, author_id: str) -> dict:
        """Reconfirm author, page/type and remote generation before v2 saving.

        Use the legacy author's `profile/illusts` detail endpoint, which
        actually supplies updateDate. The unrelated `/ajax/illust/{id}` detail
        can omit that field. Missing generations fail closed.
        """
        try:
            wid = _identifier(work_id)
            uid = _identifier(author_id)
        except InvalidPayload:
            raise DestinationRejected() from None
        with self._identity_lock:
            self._ensure_open()
            if self._verified_identity is None:
                raise AuthenticationMissing()
            query = urlencode([
                ('work_category', 'illustManga'), ('is_first_page', '1'),
                ('ids[]', wid),
            ])
            body = self._json('author_details',
                              f'{API_ORIGIN}/ajax/user/{uid}/profile/illusts?{query}',
                              deadline=time.monotonic() + MAX_OPERATION_SECONDS)
            self._ensure_open()
        if (type(body) is not dict or type(body.get('works')) is not dict
                or set(body['works']) != {wid}):
            raise InvalidPayload()
        entry = body['works'][wid]
        if type(entry) is not dict or entry.get('isMasked', False) is not False:
            raise InvalidPayload()
        try:
            if _identifier(entry.get('id')) != wid:
                raise InvalidPayload()
            if _identifier(entry.get('userId')) != uid:
                raise InvalidPayload()
        except InvalidPayload:
            raise InvalidPayload() from None
        kind = entry.get('illustType')
        count = entry.get('pageCount')
        update = entry.get('updateDate', '')
        if (type(kind) is not int or kind not in (0, 1)
                or type(count) not in (str, int) or not str(count).isascii()
                or not str(count).isdecimal() or not 1 <= int(count) <= MAX_ARTWORK_PAGES
                or type(update) is not str or len(update) > 128
                or any(ord(char) < 32 or ord(char) == 127 for char in update)):
            raise InvalidPayload()
        return {'work_id': wid, 'author_id': uid, 'illustration_type': kind,
                'page_count': int(count), 'update_date': update}

    def get_artwork_pages(self, work_id: str) -> tuple[str, ...]:
        self._ensure_open()
        try:
            wid = _identifier(work_id)
        except InvalidPayload:
            raise DestinationRejected() from None
        deadline = time.monotonic() + MAX_OPERATION_SECONDS
        body = self._json("pages", f"{API_ORIGIN}/ajax/illust/{wid}/pages",
                          deadline=deadline)
        if type(body) is not list or not 1 <= len(body) <= MAX_ARTWORK_PAGES:
            raise InvalidPayload()
        urls = []
        seen = set()
        for entry in body:
            if not isinstance(entry, dict) or not isinstance(entry.get("urls"), dict):
                raise InvalidPayload()
            url = entry["urls"].get("regular")
            _validate_destination(url, "image")
            if url in seen:
                raise InvalidPayload()
            seen.add(url)
            urls.append(url)
        return tuple(urls)

    def get_artwork_original_pages(self, work_id: str) -> tuple[str, ...]:
        """Return verified, ordered original-size pages suitable for saving.

        The regular-size viewer API intentionally remains independent.
        """
        self._ensure_open()
        try:
            wid = _identifier(work_id)
        except InvalidPayload:
            raise DestinationRejected() from None
        deadline = time.monotonic() + MAX_OPERATION_SECONDS
        body = self._json("pages", f"{API_ORIGIN}/ajax/illust/{wid}/pages",
                          deadline=deadline)
        if type(body) is not list or not 1 <= len(body) <= MAX_ARTWORK_PAGES:
            raise InvalidPayload()
        urls: list[str] = []
        seen: set[str] = set()
        for page_no, entry in enumerate(body):
            if type(entry) is not dict or type(entry.get("urls")) is not dict:
                raise InvalidPayload()
            url = entry["urls"].get("original")
            try:
                _validate_destination(url, "image")
            except DestinationRejected:
                raise InvalidPayload() from None
            parsed = urlsplit(url)
            match = _ORIGINAL_IMAGE_PATH.fullmatch(parsed.path)
            if (match is None or match.group(1) != wid
                    or match.group(2) != str(page_no) or parsed.query
                    or url in seen):
                raise InvalidPayload()
            seen.add(url)
            urls.append(url)
        return tuple(urls)

    def get_ugoira_manifest(self, work_id):
        from .media_parity_policy import parse_ugoira_manifest
        self.require_identity()
        wid=_identifier(work_id)
        body=self._json('ugoira_meta',f'{API_ORIGIN}/ajax/illust/{wid}/ugoira_meta',deadline=time.monotonic()+MAX_OPERATION_SECONDS)
        return parse_ugoira_manifest(body,work_id=wid)

    def get_ugoira_archive(self, manifest):
        from .media_parity_policy import UgoiraManifest, parse_ugoira_manifest
        if type(manifest) is not UgoiraManifest: raise DestinationRejected()
        checked=parse_ugoira_manifest(dict(originalSrc=manifest.archive_url,frames=[dict(file=f.filename,delay=f.delay_ms) for f in manifest.frames]),work_id=manifest.work_id)
        if checked!=manifest: raise DestinationRejected()
        self.require_identity()
        return self._get('ugoira_zip',manifest.archive_url,deadline=time.monotonic()+MAX_OPERATION_SECONDS)

    def get_image_bytes(self, url: str) -> bytes:
        self._ensure_open()
        deadline = time.monotonic() + MAX_OPERATION_SECONDS
        data = self._get("image", url, deadline=deadline)
        if not data:
            raise InvalidPayload()
        return data


class PublicImageReadClient:
    """CDN-only borrowed-ranking images; no Cookie source or API capability."""
    def __init__(self, *, transport=None):
        self._closed=False
        self._opener=build_opener(ProxyHandler({}),_NoRedirects(),HTTPSHandler())
        self._owned_transport = _OwnedReadTransport() if transport is None else None
        self._transport = transport if transport is not None else self._owned_transport
    _ensure_open=LivePixivReadClient._ensure_open
    shutdown_unverified=LivePixivReadClient.shutdown_unverified
    set_request_admission=LivePixivReadClient.set_request_admission
    set_retry_policy=LivePixivReadClient.set_retry_policy
    _get_once=LivePixivReadClient._get_once
    get_image_bytes=LivePixivReadClient.get_image_bytes
    def _get(self,kind,url,*,deadline):
        if kind!='image':raise DestinationRejected()
        return LivePixivReadClient._get(self,kind,url,deadline=deadline)
    def close(self):
        self._closed=True
        owned = getattr(self, "_owned_transport", None)
        if owned is not None:
            owned.close()


class VaultImageReader:
    def __init__(self,vault,images):self.vault=vault;self.images=images
    def get_image_bytes(self,url):return self.images.get_image_bytes(url)
    def get_artwork_pages(self,work_id):return self.vault.get_artwork_pages(work_id)


__all__ = [
    "LivePixivReadClient", "PixivReadError", "CookieSourceRejected",
    "DestinationRejected", "ReadDeadlineExceeded", "ResponseRejected", "HTTPStatusRejected",
    "ResponseLimitExceeded", "InvalidPayload", "ApiRejected",
    "AuthenticationMissing", "ClientClosed", "TransportFailed",
]
