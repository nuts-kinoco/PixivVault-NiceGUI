"""Bounded, GET-only Pixiv reader for explicitly supplied Netscape cookies.

No network or application configuration is consulted at construction. This
module does not import the legacy application, persist cookies, or write data.
An optional injected transport lets all behavior be tested without HTTP.
"""

from __future__ import annotations

import copy
import io
import json
from html.parser import HTMLParser
from http.cookiejar import CookieJar, DefaultCookiePolicy, MozillaCookieJar
from pathlib import Path
import re
import time
import warnings
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import (
    HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener,
)


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


class ResponseRejected(PixivReadError):
    message = "HTTP response rejected"


class ResponseLimitExceeded(PixivReadError):
    message = "Response limit exceeded"


class InvalidPayload(PixivReadError):
    message = "Pixiv payload invalid"


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
        allowed_host = CDN_HOST if kind == "image" else "www.pixiv.net"
        if (parsed.scheme != "https" or parsed.netloc != allowed_host
                or parsed.fragment or not parsed.path.startswith("/")
                or parsed.path.startswith("//") or "\\" in parsed.path):
            raise DestinationRejected()
        if kind == "image":
            if not parsed.path or parsed.path == "/":
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
        elif kind == "author_details":
            if not _AUTHOR_DETAILS_PATH.fullmatch(parsed.path):
                raise DestinationRejected()
            # Internal construction: one bounded batch of unique IDs, in order.
            from urllib.parse import parse_qsl
            parameters = parse_qsl(parsed.query, keep_blank_values=True)
            if (len(parameters) < 3 or len(parameters) > BOOKMARK_PAGE_SIZE + 2
                    or parameters[:2] not in
                    ([('work_category', 'illustManga'), ('is_first_page', '1')],
                     [('work_category', 'illustManga'), ('is_first_page', '0')])
                    or any(key != 'ids[]' or not _ID.fullmatch(value)
                           for key, value in parameters[2:])
                    or len({value for _key, value in parameters[2:]}) != len(parameters) - 2):
                raise DestinationRejected()
        else:
            raise DestinationRejected()
    except (ValueError, UnicodeError):
        raise DestinationRejected() from None


def _read_response(opener, request: Request, timeout: float,
                   limit: int, deadline: float) -> bytes:
    """Use urllib without automatic redirects, retries, proxies or decoding."""
    try:
        with opener.open(request, timeout=timeout) as response:
            _check_time(deadline)
            if response.getcode() != 200:
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
            data = bytearray()
            while True:
                _check_time(deadline)
                chunk = response.read(min(65536, limit + 1 - len(data)))
                _check_time(deadline)
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > limit:
                    raise ResponseLimitExceeded()
            return bytes(data)
    except PixivReadError:
        raise
    except HTTPError as exc:
        try:
            exc.close()
        except Exception:
            pass
        raise ResponseRejected() from None
    except (URLError, OSError, ValueError):
        raise TransportFailed() from None


# Request headers include only API cookies (never image cookies).
# The injected transport receives a prepared urllib.Request and cannot
# redirect or alter the client-side URL allowlist.
Transport = Callable[[Request, float, int, float], bytes]


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
        self._transport: Transport = transport or (
            lambda req, timeout, limit, deadline: _read_response(
                (self._cdn_opener if urlsplit(req.full_url).netloc == CDN_HOST
                 else self._api_opener),
                req, timeout, limit, deadline,
            )
        )
        self._verified_identity: str | None = None
        self._closed = False

    def __enter__(self):
        self._ensure_open()
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self) -> None:
        self._closed = True
        self._verified_identity = None
        self._cookie_user_id = None
        self._cookies.clear()

    def _ensure_open(self) -> None:
        if self._closed:
            raise ClientClosed()

    def _get(self, kind: str, url: str, *, deadline: float) -> bytes:
        self._ensure_open()
        _validate_destination(url, kind)
        _check_time(deadline)
        request = Request(url, method="GET", headers=_JSON_HEADERS)
        if kind == "image":
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
        if kind == "image" and request.has_header("Cookie"):
            raise DestinationRejected()
        per_request_deadline = min(deadline, time.monotonic() + MAX_REQUEST_SECONDS)
        timeout = max(0.001, min(MAX_REQUEST_SECONDS, per_request_deadline - time.monotonic()))
        limit = (IMAGE_LIMIT if kind == "image" else
                 HTML_LIMIT if kind == "home" else JSON_LIMIT)
        try:
            response = self._transport(request, timeout, limit, per_request_deadline)
        except PixivReadError:
            raise
        except Exception:
            raise TransportFailed() from None
        _check_time(per_request_deadline)
        if type(response) is not bytes:
            raise ResponseRejected()
        if len(response) > limit:
            raise ResponseLimitExceeded()
        return response

    def _json(self, kind: str, url: str, *, deadline: float) -> object:
        body = self._get(kind, url, deadline=deadline)
        try:
            packet = json.loads(body)
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
        self._ensure_open()
        self._verified_identity = None
        deadline = time.monotonic() + MAX_OPERATION_SECONDS
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
        self._verified_identity = actual
        return actual

    def _assert_identity(self, my_user_id: str) -> str:
        try:
            requested = _identifier(my_user_id)
        except InvalidPayload:
            raise AuthenticationMissing() from None
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

    def get_author_artworks(self, user_id: str) -> tuple[dict, ...]:
        """Read an author's illustration/manga metadata with one bounded GET batch.

        Returns no partial result if a detail batch is missing or malformed.
        This reader does not decide whether an artwork needs downloading.
        """
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
                records.append({'id': wid, 'user_id': uid, 'title': title,
                                'user_name': name, 'type': kind, 'page_count': int(count),
                                'update_date': updated, 'create_date': created})
        return tuple(records)

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

    def get_image_bytes(self, url: str) -> bytes:
        self._ensure_open()
        deadline = time.monotonic() + MAX_OPERATION_SECONDS
        data = self._get("image", url, deadline=deadline)
        if not data:
            raise InvalidPayload()
        return data


__all__ = [
    "LivePixivReadClient", "PixivReadError", "CookieSourceRejected",
    "DestinationRejected", "ReadDeadlineExceeded", "ResponseRejected",
    "ResponseLimitExceeded", "InvalidPayload", "ApiRejected",
    "AuthenticationMissing", "ClientClosed", "TransportFailed",
]
