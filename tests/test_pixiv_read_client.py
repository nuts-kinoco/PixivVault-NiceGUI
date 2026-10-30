"""Offline tests for the explicit-cookie, GET-only Pixiv read client."""

import json
import time
from email.message import Message
from html import escape
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import ProxyHandler

import pytest

from pixivvault_nicegui import pixiv_read_client as mod
from pixivvault_nicegui.bookmark_remote_adapter import fetch_all_bookmarks
from pixivvault_nicegui.media_parity_policy import NovelDocument


def packet(body, *, error=False):
    return json.dumps({"error": error, "message": "", "body": body}).encode()


def home_page(uid="55"):
    data = {"userData": {"id": uid}} if uid else {"recommendations": {"userId": "55"}}
    return (
        '<html><head><meta id="meta-global-data" content="'
        + escape(json.dumps(data)) + '"></head></html>'
    ).encode()


def next_home(*, login=True, user=None, meta="", duplicate=False):
    if user is None:
        user = {"id": "55"}
    payload = json.dumps({"props": {"pageProps": {"isLoggedIn": login, "gaUserData": user}}})
    script = '<script id="__NEXT_DATA__" type="application/json">' + payload + '</script>'
    return ('<html><head>' + meta + '</head><body>' + script
            + (script if duplicate else '') + '</body></html>').encode()


def test_next_identity_passes_injected_home_extra_cookie(tmp_path):
    mock = MockTransport()
    mock.when("https://www.pixiv.net/", next_home())
    mock.when("/ajax/user/extra", packet({"userId": "55"}))
    client = make_client(tmp_path, mock)
    assert client.verify_identity() == "55"
    assert len(mock.calls) == 2
    assert all(req.get_method() == "GET" for req in mock.calls)


@pytest.mark.parametrize("page", [
    next_home(login=False), next_home(login=1), next_home(user={"id": "0"}),
    next_home(user={"id": True}), next_home(user={"id": "55", "userId": "56"}),
    next_home(user={"id": "55", "userId": "55"}),
    next_home(duplicate=True),
    next_home(meta='<meta id="meta-global-data" content="{}"><meta id="meta-global-data" content="{}">'),
    next_home(meta='<meta id="meta-global-data" content="{}">'),
    next_home(meta='<meta id="meta-global-data" content="&quot;broken&quot;">'),
    next_home(meta='<meta id="meta-global-data" content="{&quot;userData&quot;:{&quot;id&quot;:&quot;56&quot;}}">'),
    b'<script id="__NEXT_DATA__">{invalid}</script>',
    b'<script id="__NEXT_DATA__"/>',
    b'<script id="__NEXT_DATA__">{"props":{"pageProps":{"isLoggedIn":true,"gaUserData":{"id":"55"}}}}',
    b'<script id="__NEXT_DATA__">{"props":{"pageProps":{"isLoggedIn":true,"gaUserData":{"id":"55"}}}}</script><script id="__NEXT_DATA__"/>',
    b'<script id="__NEXT_DATA__">{"pageProps":{"isLoggedIn":true,"gaUserData":{"id":"55"}}}</script>',
])
def test_next_identity_rejects_invalid_evidence(page):
    with pytest.raises(mod.AuthenticationMissing):
        mod._identity_from_home(page)


def test_next_and_meta_agreement_and_id_variants():
    meta = home_page().decode().split('<head>')[1].split('</head>')[0]
    assert mod._identity_from_home(next_home(meta=meta)) == "55"
    for key in ('id', 'userId', 'user_id'):
        assert mod._identity_from_home(next_home(user={key: 55})) == "55"


def test_legacy_self_closing_meta_remains_compatible():
    meta = home_page().replace(b'"></head>', b'" /></head>')
    assert mod._identity_from_home(meta) == "55"
    fragment = meta.decode().split("<head>")[1].split("</head>")[0]
    assert mod._identity_from_home(next_home(meta=fragment)) == "55"


@pytest.mark.parametrize("page", [
    home_page().replace(b'id="meta-global-data"', b'id="other" id="meta-global-data"'),
    home_page().replace(b'content="', b'content="{}" content="', 1),
    next_home().replace(b'id="__NEXT_DATA__"', b'id="other" id="__NEXT_DATA__"'),
    b'<meta id="meta-global-data" content>',
    next_home().replace(b'"isLoggedIn": true', b'"isLoggedIn": false, "isLoggedIn": true'),
    next_home().replace(b'"id": "55"', b'"id": "56", "id": "55"'),
    b'<script id="__NEXT_DATA__">' + b'[' * 2000 + b']' * 2000 + b'</script>',
])
def test_identity_evidence_ambiguity_or_invalid_shape_is_sanitized(page):
    with pytest.raises(mod.AuthenticationMissing) as error:
        mod._identity_from_home(page)
    assert str(error.value) == "Pixiv identity not verified"


def cookie_file(tmp_path, *, uid="55", expired=False, name="PHPSESSID",
                domain=".pixiv.net"):
    expires = int(time.time()) + (-180 if expired else 86400)
    path = tmp_path / "fixture_cookies.txt"
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        + f"{domain}\tTRUE\t/\tTRUE\t{expires}\t{name}\t{uid}_fixtureonly\n",
        encoding="utf-8",
    )
    return path


class MockTransport:
    def __init__(self):
        self.calls = []
        self.responses = {"https://www.pixiv.net/": home_page()}

    def when(self, marker, response):
        self.responses[marker] = response

    def __call__(self, request, timeout, limit, deadline):
        assert request.get_method() == "GET"
        assert 0 < timeout <= mod.MAX_REQUEST_SECONDS
        assert deadline >= time.monotonic()
        assert not request.has_header("Authorization")
        assert request.get_header("Referer") == "https://www.pixiv.net/"
        assert request.get_header("Accept-encoding") == "identity"
        self.calls.append(request)
        if request.full_url in self.responses:
            response = self.responses[request.full_url]
            return response(request) if callable(response) else response
        for marker, answer in self.responses.items():
            if marker == "https://www.pixiv.net/":
                continue
            if marker in request.full_url:
                return answer(request) if callable(answer) else answer
        raise AssertionError("Unexpected endpoint")


def bookmark_row(work_id=10, *, novel=False):
    return {
        "id": str(work_id), "title": f"Work {work_id}", "illustType": 1,
        "userId": "91", "userName": "Synthetic Author", "pageCount": 3,
        "createDate": "2026-10-01T12:00:00",
        "tags": ["alpha", {"tag": "beta"}],
        "url": f"https://i.pximg.net/c/360x360_70/img-master/{work_id}.jpg",
        "xRestrict": 0, "textCount": 100 if novel else 0,
    }


def make_client(tmp_path, transport=None, **cookie_kwargs):
    return mod.LivePixivReadClient(
        cookie_file(tmp_path, **cookie_kwargs), transport=transport,
    )


def test_construction_is_offline_and_cookie_path_explicit(tmp_path):
    mock = MockTransport()
    client = make_client(tmp_path, mock)
    assert mock.calls == []
    assert client._verified_identity is None
    assert isinstance(client._cookies, CookieJar)
    client.close()
    with pytest.raises(mod.ClientClosed, match="Pixiv client closed"):
        client.verify_identity()
    with pytest.raises(mod.CookieSourceRejected):
        mod.LivePixivReadClient("relative-cookie-file.txt")


def test_cookie_expiration_invalid_sources_and_unknown_cookie(tmp_path):
    with pytest.raises(mod.CookieSourceRejected):
        make_client(tmp_path, MockTransport(), expired=True)
    path = cookie_file(tmp_path)
    path.write_text("not a netscape cookie jar", encoding="utf-8")
    with pytest.raises(mod.CookieSourceRejected):
        mod.LivePixivReadClient(path, transport=MockTransport())
    path.write_bytes(b"a" * (mod.COOKIE_LIMIT + 1))
    with pytest.raises(mod.CookieSourceRejected):
        mod.LivePixivReadClient(path, transport=MockTransport())
    with pytest.raises(mod.CookieSourceRejected):
        make_client(tmp_path, MockTransport(), domain=".other.example")


def test_malformed_cookie_never_emits_parser_values(tmp_path, capsys):
    path = cookie_file(tmp_path)
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        ".pixiv.net\tTRUE\t/\tTRUE\tinvalid-expiry\tPHPSESSID\tsecret-never-log\n",
        encoding="utf-8",
    )
    with pytest.raises(mod.CookieSourceRejected) as error:
        mod.LivePixivReadClient(path, transport=MockTransport())
    stdio = capsys.readouterr()
    assert stdio.out == stdio.err == ""
    assert "secret-never-log" not in str(error.value)


def test_identity_checks_authenticated_extra_and_cookie_claim(tmp_path):
    mock = MockTransport()
    mock.when("/ajax/user/extra", packet({"following": 23}))
    client = make_client(tmp_path, mock)
    assert client.verify_identity() == "55"
    assert client._verified_identity == "55"
    assert len(mock.calls) == 2
    assert "fixtureonly" in (mock.calls[0].get_header("Cookie") or "")
    client.close()


def test_identity_explicit_id_and_conflict_fail_closed(tmp_path):
    mock = MockTransport()
    mock.when("/ajax/user/extra", packet({"userId": "56"}))
    client = make_client(tmp_path, mock)
    with pytest.raises(mod.AuthenticationMissing):
        client.verify_identity()
    assert client._verified_identity is None
    mock.when("/ajax/user/extra", packet({"id": "55"}))
    assert client.verify_identity() == "55"
    client.close()


def test_identity_missing_or_api_error_fails_and_no_bookmark_call(tmp_path):
    mock = MockTransport()
    mock.when("https://www.pixiv.net/", home_page(None))
    mock.when("/ajax/user/extra", packet({"following": 23}))
    client = make_client(tmp_path, mock, uid="not_an_id")
    with pytest.raises(mod.AuthenticationMissing):
        list(client.get_bookmarked_works("55"))
    assert len(mock.calls) == 1
    mock.when("https://www.pixiv.net/", home_page())
    mock.when("/ajax/user/extra", packet({}, error=True))
    with pytest.raises(mod.ApiRejected):
        client.verify_identity()


def test_cookie_claim_must_match_homepage_server_id(tmp_path):
    mock = MockTransport()
    mock.when("/ajax/user/extra", packet({}))
    client = make_client(tmp_path, mock, uid="56")
    with pytest.raises(mod.AuthenticationMissing):
        client.verify_identity()
    assert len(mock.calls) == 1
    assert client._verified_identity is None


def test_api_extra_empty_is_insufficient_when_home_is_anonymous(tmp_path):
    mock = MockTransport()
    mock.when("https://www.pixiv.net/", home_page(None))
    mock.when("/ajax/user/extra", packet({}))
    client = make_client(tmp_path, mock)
    with pytest.raises(mod.AuthenticationMissing):
        client.verify_identity()
    assert len(mock.calls) == 1


def test_illustration_and_novel_routes_map_original_fields(tmp_path):
    mock = MockTransport()
    mock.when("/ajax/user/extra", packet({}))
    mock.when("/illusts/bookmarks?", packet({"works": [bookmark_row()]}))
    mock.when("/novels/bookmarks?", packet({"works": [bookmark_row(11, novel=True)]}))
    client = make_client(tmp_path, mock)
    # A nonempty page must be followed until the endpoint proves completion.
    def illust(request):
        return packet({"works": [bookmark_row()] if "offset=0" in request.full_url else []})
    def novels(request):
        return packet({"works": [bookmark_row(11, novel=True)]
                       if "offset=0" in request.full_url else []})
    mock.when("/illusts/bookmarks?", illust)
    mock.when("/novels/bookmarks?", novels)
    arts = list(client.get_bookmarked_works("55", "show"))
    books = list(client.get_bookmarked_novels("55", "hide"))
    assert arts == [{
        "id": "10", "title": "Work 10", "user_id": "91",
        "user_name": "Synthetic Author", "tags": ["alpha", "beta"],
        "thumb_url": "https://i.pximg.net/c/360x360_70/img-master/10.jpg",
        "create_date": "2026-10-01T12:00:00", "x_restrict": 0,
        "type": 1, "page_count": 3,
    }]
    assert books[0]["type"] == "novel"
    assert "page_count" not in books[0]
    assert "rest=show" in mock.calls[2].full_url
    assert "rest=hide" in mock.calls[4].full_url
    assert all(r.get_method() == "GET" for r in mock.calls)


def test_actual_bookmarkreadclient_protocol_four_routes_complete(tmp_path):
    mock = MockTransport()
    mock.when("/ajax/user/extra", packet({}))
    def response(req):
        if "offset=0" not in req.full_url:
            return packet({"works": []})
        if "/illusts/" in req.full_url:
            return packet({"works": [bookmark_row(10)]})
        return packet({"works": [bookmark_row(10, novel=True)]})
    mock.when("/illusts/bookmarks?", response)
    mock.when("/novels/bookmarks?", response)
    result = fetch_all_bookmarks(make_client(tmp_path, mock), "55")
    assert result.ok and not result.failures
    # Same numeric work ID is legal in both namespaces.
    assert [record.work_id for record in result.records] == ["10", "novel:10"]
    assert [record.visibility for record in result.records] == ["show", "show"]
    assert len(mock.calls) == 10  # identity x2 + four routes x2


def test_forty_eight_items_require_next_offset_and_final_empty(tmp_path):
    mock = MockTransport()
    mock.when("/ajax/user/extra", packet({}))
    def pages(req):
        if "offset=0" in req.full_url:
            return packet({"works": [bookmark_row(n) for n in range(1, 49)]})
        if "offset=48" in req.full_url:
            return packet({"works": [bookmark_row(49)]})
        return packet({"works": []})
    mock.when("/illusts/bookmarks?", pages)
    result = list(make_client(tmp_path, mock).get_bookmarked_works("55"))
    assert len(result) == 49
    assert result[0]["id"] == "1" and result[-1]["id"] == "49"
    assert len(mock.calls) == 5  # home + identity extra + 3 bookmark requests
    assert all("limit=48" in r.full_url for r in mock.calls[2:])


def test_midstream_failure_surfaces_and_does_not_return_partial_snapshot(tmp_path):
    mock = MockTransport()
    mock.when("/ajax/user/extra", packet({}))
    def pages(req):
        if "offset=0" in req.full_url:
            return packet({"works": [bookmark_row(1)]})
        raise OSError("sensitive url and account token")
    mock.when("/illusts/bookmarks?", pages)
    client = make_client(tmp_path, mock)
    generator = client.get_bookmarked_works("55")
    first = next(generator)
    assert first["id"] == "1"
    with pytest.raises(mod.TransportFailed) as exc:
        next(generator)
    assert str(exc.value) == "Pixiv transport failed"
    assert "sensitive" not in str(exc.value)


@pytest.mark.parametrize("invalid", [
    {"works": {}}, {"works": "none"}, {"works": [None]},
    {"works": [dict(bookmark_row(), id="../other")]},
    {"works": [dict(bookmark_row(), url="https://i.pximg.net.evil/x.jpg")]},
    {"works": [dict(bookmark_row(), pageCount=0)]},
    {"works": [dict(bookmark_row(), tags=["ok", 3])]},
])
def test_bad_bookmark_payload_is_rejected(tmp_path, invalid):
    mock = MockTransport()
    mock.when("/ajax/user/extra", packet({}))
    mock.when("/illusts/bookmarks?", packet(invalid))
    with pytest.raises(mod.PixivReadError):
        list(make_client(tmp_path, mock).get_bookmarked_works("55"))


def test_duplicate_only_next_page_not_considered_success(tmp_path):
    mock = MockTransport()
    mock.when("/ajax/user/extra", packet({}))
    mock.when("/illusts/bookmarks?", packet({"works": [bookmark_row(1)]}))
    with pytest.raises(mod.InvalidPayload):
        list(make_client(tmp_path, mock).get_bookmarked_works("55"))


@pytest.mark.parametrize("novel", [False, True])
def test_unavailable_entries_do_not_abort_or_truncate_paging(tmp_path, novel):
    masked = dict(bookmark_row(101), isMasked=True, userId="0",
                  url="https://s.pximg.net/common/images/unavailable.png")
    no_preview = dict(bookmark_row(102), userId=None, url="")
    mock = MockTransport()
    mock.when("/ajax/user/extra", packet({}))

    def pages(request):
        offset = int(parse_qs(urlsplit(request.full_url).query)["offset"][0])
        return packet({"works": {0: [masked, no_preview], 48: [bookmark_row(103)], 96: []}[offset]})

    marker = "/novels/bookmarks?" if novel else "/illusts/bookmarks?"
    mock.when(marker, pages)
    with make_client(tmp_path, mock) as client:
        method = client.get_bookmarked_novels if novel else client.get_bookmarked_works
        assert [row["id"] for row in method("55")] == ["103"]
        requests = [request for request in mock.calls if marker in request.full_url]
        assert [parse_qs(urlsplit(r.full_url).query)["offset"] for r in requests] == [["0"], ["48"], ["96"]]
        assert all(urlsplit(r.full_url).netloc == "www.pixiv.net" for r in mock.calls)
        with pytest.raises(mod.DestinationRejected):
            client.get_image_bytes("https://s.pximg.net/common/images/unavailable.png")


@pytest.mark.parametrize("flag", ["true", 1, None, [], {}])
def test_masked_flag_requires_exact_boolean_when_present(tmp_path, flag):
    row = dict(bookmark_row(), isMasked=flag)
    mock = MockTransport()
    mock.when("/ajax/user/extra", packet({}))
    mock.when("/illusts/bookmarks?", lambda r: packet({"works": [row] if "offset=0" in r.full_url else []}))
    with make_client(tmp_path, mock) as client:
        with pytest.raises(mod.InvalidPayload):
            list(client.get_bookmarked_works("55"))


def test_repeated_masked_page_is_not_a_successful_empty_route(tmp_path):
    row = dict(bookmark_row(), isMasked=True, userId="0", url="")
    mock = MockTransport()
    mock.when("/ajax/user/extra", packet({}))
    mock.when("/illusts/bookmarks?", packet({"works": [row]}))
    with make_client(tmp_path, mock) as client:
        with pytest.raises(mod.InvalidPayload):
            list(client.get_bookmarked_works("55"))
    assert len(mock.calls) == 4  # home, extra, first masked page, duplicate page


def test_all_masked_routes_can_complete_without_visible_records(tmp_path):
    row = dict(bookmark_row(), isMasked=True, userId="0", url="")
    mock = MockTransport()
    mock.when("/ajax/user/extra", packet({}))
    mock.when("/bookmarks?", lambda r: packet({"works": [row] if "offset=0" in r.full_url else []}))
    with make_client(tmp_path, mock) as client:
        result = fetch_all_bookmarks(client, "55")
    assert result.ok and result.records == () and result.failures == ()
    assert len(mock.calls) == 10


@pytest.mark.parametrize("preview", [False, 0, [], {}])
def test_non_string_preview_is_not_silently_treated_as_unavailable(tmp_path, preview):
    mock = MockTransport()
    mock.when("/ajax/user/extra", packet({}))
    row = dict(bookmark_row(), url=preview)
    mock.when("/illusts/bookmarks?", lambda r: packet({"works": [row] if "offset=0" in r.full_url else []}))
    with make_client(tmp_path, mock) as client:
        with pytest.raises(mod.InvalidPayload):
            list(client.get_bookmarked_works("55"))


def test_invalid_masked_work_identity_is_still_rejected(tmp_path):
    mock = MockTransport()
    mock.when("/ajax/user/extra", packet({}))
    mock.when("/illusts/bookmarks?", packet({"works": [dict(bookmark_row(), id="../bad", isMasked=True)]}))
    with make_client(tmp_path, mock) as client:
        with pytest.raises(mod.InvalidPayload):
            list(client.get_bookmarked_works("55"))


def test_page_bound_prevents_unending_pagination(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "MAX_ROUTE_PAGES", 2)
    mock = MockTransport()
    mock.when("/ajax/user/extra", packet({}))
    def pages(req):
        start = int(req.full_url.split("offset=")[1].split("&")[0])
        return packet({"works": [bookmark_row(n) for n in range(start + 1, start + 49)]})
    mock.when("/illusts/bookmarks?", pages)
    with pytest.raises(mod.ResponseLimitExceeded):
        list(make_client(tmp_path, mock).get_bookmarked_works("55"))


def test_identity_mismatch_and_invalid_visibility_make_no_bookmark_request(tmp_path):
    mock = MockTransport()
    mock.when("/ajax/user/extra", packet({}))
    client = make_client(tmp_path, mock)
    with pytest.raises(mod.AuthenticationMissing):
        list(client.get_bookmarked_works("56"))
    assert len(mock.calls) == 2
    with pytest.raises(mod.DestinationRejected):
        list(client.get_bookmarked_novels("55", "private"))
    assert len(mock.calls) == 2


def test_artwork_pages_order_and_image_cdn_is_cookie_free(tmp_path):
    mock = MockTransport()
    urls = [
        "https://i.pximg.net/img-master/img/2026/a_p0_master1200.jpg",
        "https://i.pximg.net/img-master/img/2026/a_p1_master1200.jpg",
    ]
    mock.when("/ajax/illust/99/pages", packet([
        {"urls": {"regular": u, "original": "https://other.example/no"}}
        for u in urls
    ]))
    mock.when("/img-master/img/2026/a_p0", b"\xff\xd8\xff\x00")
    client = make_client(tmp_path, mock)
    assert client.get_artwork_pages("99") == tuple(urls)
    assert client.get_image_bytes(urls[0]) == b"\xff\xd8\xff\x00"
    assert mock.calls[0].has_header("Cookie")
    assert not mock.calls[1].has_header("Cookie")
    assert all(c.full_url.startswith("https://") for c in mock.calls)


@pytest.mark.parametrize("url", [
    "http://i.pximg.net/file.jpg",
    "https://i.pximg.net.evil.invalid/file.jpg",
    "https://i.pximg.net:443/file.jpg",
    "https://name@i.pximg.net/file.jpg",
    "https://i.pximg.net/file.jpg#fragment",
    "https://i.pximg.net/\nother.jpg",
    "https://www.pixiv.net/ajax/user/extra",
    "https://i.pximg.net//evil",
])
def test_cdn_destination_exact_allowlist(tmp_path, url):
    mock = MockTransport()
    client = make_client(tmp_path, mock)
    with pytest.raises(mod.DestinationRejected):
        client.get_image_bytes(url)
    assert not mock.calls


@pytest.mark.parametrize(("kind", "url"), [
    ("home", "https://www.pixiv.net/ajax/user/extra"),
    ("identity", "https://www.pixiv.net/"),
    ("bookmarks", "https://www.pixiv.net/ajax/illusts/bookmarks/add"),
    ("bookmarks", "https://www.pixiv.net/ajax/user/55/illusts/bookmarks?rest=show"),
    ("pages", "https://www.pixiv.net/ajax/illust/99/ugoira_meta"),
    ("pages", "https://www.pixiv.net/ajax/illust/99/pages?token=abc"),
    ("pages", "https://www.pixiv.net.evil.invalid/ajax/illust/99/pages"),
])
def test_api_path_and_query_allowlist_is_exact(kind, url):
    with pytest.raises(mod.DestinationRejected):
        mod._validate_destination(url, kind)


def test_broken_pages_do_not_skip_pages_or_allow_other_cdn(tmp_path):
    mock = MockTransport()
    mock.when("/ajax/illust/99/pages", packet([
        {"urls": {"regular": "https://i.pximg.net/one.jpg"}},
        {"urls": {"regular": "https://evil.invalid/two.jpg"}},
    ]))
    with pytest.raises(mod.DestinationRejected):
        make_client(tmp_path, mock).get_artwork_pages("99")
    mock.when("/ajax/illust/99/pages", packet([
        {"urls": {"regular": "https://i.pximg.net/one.jpg"}},
        {"urls": {"regular": "https://i.pximg.net/one.jpg"}},
    ]))
    with pytest.raises(mod.InvalidPayload):
        make_client(tmp_path, mock).get_artwork_pages("99")


def test_fixed_error_strings_response_size_and_deadline(tmp_path, monkeypatch):
    mock = MockTransport()
    mock.when("/img-master/", b"X" * 30)
    monkeypatch.setattr(mod, "IMAGE_LIMIT", 20)
    client = make_client(tmp_path, mock)
    with pytest.raises(mod.ResponseLimitExceeded, match="Response limit exceeded"):
        client.get_image_bytes("https://i.pximg.net/img-master/a.jpg")
    mock.when("/img-master/", b"ok")
    def expired(req, timeout, limit, deadline):
        raise RuntimeError("secret Cookie and URL")
    client._transport = expired
    with pytest.raises(mod.TransportFailed) as exc:
        client.get_image_bytes("https://i.pximg.net/img-master/a.jpg")
    assert str(exc.value) == "Pixiv transport failed"
    monkeypatch.setattr(mod, "MAX_OPERATION_SECONDS", -1.0)
    with pytest.raises(mod.ReadDeadlineExceeded):
        client.get_image_bytes("https://i.pximg.net/img-master/a.jpg")


class FakeResponse:
    def __init__(self, *, status=200, length=None, encoding=None, data=b"ok"):
        self.status = status
        self.data = data
        self.start = False
        self.headers = Message()
        if length is not None:
            self.headers["Content-Length"] = length
        if encoding is not None:
            self.headers["Content-Encoding"] = encoding

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def getcode(self):
        return self.status

    def read(self, length):
        if self.start:
            return b""
        self.start = True
        return self.data


class FakeOpener:
    def __init__(self, response):
        self.response = response

    def open(self, req, timeout):
        return self.response


@pytest.mark.parametrize(("response", "expected"), [
    (FakeResponse(status=302), mod.ResponseRejected),
    (FakeResponse(length="9999999"), mod.ResponseLimitExceeded),
    (FakeResponse(length="NaN"), mod.ResponseRejected),
    (FakeResponse(encoding="gzip"), mod.ResponseRejected),
    (FakeResponse(data=b"t" * 25), mod.ResponseLimitExceeded),
])
def test_urllib_transport_rejects_redirects_encodings_and_oversize(response, expected):
    request = mod.Request("https://i.pximg.net/img-master/test.jpg", method="GET")
    with pytest.raises(expected):
        mod._read_response(FakeOpener(response), request, 2.0, 20, time.monotonic() + 2.0)


@pytest.mark.parametrize('status', [401, 403, 404, 429, 500, 503])
def test_urllib_transport_preserves_only_safe_http_status(status):
    class RaisingOpener:
        def open(self, _request, timeout):
            assert timeout > 0
            raise HTTPError('https://www.pixiv.net/ajax/illust/123', status,
                            'sensitive server text', None, None)

    request = mod.Request('https://www.pixiv.net/ajax/illust/123', method='GET')
    with pytest.raises(mod.HTTPStatusRejected) as caught:
        mod._read_response(RaisingOpener(), request, 2.0, 1024,
                           time.monotonic() + 2.0)
    assert caught.value.status_code == status
    assert isinstance(caught.value, mod.ResponseRejected)
    assert str(caught.value) == 'HTTP response rejected'
    assert 'sensitive' not in str(caught.value)


def test_metadata_detail_reader_requires_verified_identity_and_pinned_author(tmp_path):
    mock = MockTransport()
    mock.when('https://www.pixiv.net/', home_page())
    mock.when('/ajax/user/extra', packet({'userId': '55'}))
    body = {'id': '123', 'userId': '91', 'bookmarkCount': 0,
            'likeCount': 0, 'xRestrict': 0}
    mock.when('/ajax/illust/123', packet(body))
    client = make_client(tmp_path, mock)
    try:
        with pytest.raises(mod.AuthenticationMissing):
            client.get_artwork_metadata_raw('123', '91')
        assert client.verify_identity() == '55'
        assert client.get_artwork_metadata_raw('123', '91') == body
        with pytest.raises(mod.InvalidPayload):
            client.get_artwork_metadata_raw('123', '92')
        assert all(call.get_method() == 'GET' for call in mock.calls)
        assert all('/ajax/illust/123' in call.full_url for call in mock.calls[2:])
    finally:
        client.close()


def test_metadata_detail_reader_rejects_duplicate_json_keys(tmp_path):
    mock = MockTransport()
    mock.when('https://www.pixiv.net/', home_page())
    mock.when('/ajax/user/extra', packet({'userId': '55'}))
    mock.when('/ajax/illust/123',
              b'{"error":false,"message":"","body":{"id":"123","id":"124","userId":"91"}}')
    client = make_client(tmp_path, mock)
    try:
        client.verify_identity()
        with pytest.raises(mod.InvalidPayload):
            client.get_artwork_metadata_raw('123', '91')
    finally:
        client.close()


def novel_body(**changes):
    body = {'id': '123', 'userId': '91', 'title': 'Synthetic novel',
            'content': 'Synthetic body <&>\nsecond paragraph',
            'seriesNavData': None, 'textEmbeddedImages': None}
    body.update(changes)
    return body


def verified_novel_client(tmp_path, response):
    mock = MockTransport()
    mock.when('/ajax/user/extra', packet({'userId': '55'}))
    mock.when('/ajax/novel/123', response)
    client = make_client(tmp_path, mock)
    assert client.verify_identity() == '55'
    mock.calls.clear()
    return client, mock


@pytest.mark.parametrize('series', [None, {'seriesId': '42'}])
def test_novel_detail_returns_pinned_document_with_one_bounded_get(tmp_path, series):
    body = novel_body(seriesNavData=series)
    client, mock = verified_novel_client(tmp_path, packet(body))
    try:
        assert mod.JSON_LIMIT == 2 * 1024 * 1024
        assert mod.MAX_REQUEST_SECONDS == 10.0
        assert mod.MAX_OPERATION_SECONDS == 120.0
        document = client.get_novel_detail(123, 91)
        assert type(document) is NovelDocument
        assert document == NovelDocument('123', '91', body['title'], body['content'],
                                         None if series is None else '42')
        assert len(mock.calls) == 1
        request = mock.calls[0]
        assert request.full_url == 'https://www.pixiv.net/ajax/novel/123'
        assert request.get_method() == 'GET' and request.data is None
        assert request.get_header('Cookie') == 'PHPSESSID=55_fixtureonly'
        assert body['title'] not in repr(document)
        assert body['content'] not in repr(document)
        assert 'fixtureonly' not in repr(document)
    finally:
        client.close()


def test_novel_detail_requires_existing_identity_and_rejects_closed_client(tmp_path):
    mock = MockTransport()
    with make_client(tmp_path, mock) as client:
        with pytest.raises(mod.AuthenticationMissing):
            client.get_novel_detail('123', '91')
        assert mock.calls == []
    with pytest.raises(mod.ClientClosed):
        client.get_novel_detail('123', '91')
    assert mock.calls == []


@pytest.mark.parametrize('bad_id', [None, True, 0, -1, 1.5, '', '0', '01',
                                     '1' * 21, '１２３', '123/other', '123?x=secret'])
@pytest.mark.parametrize('field', ['work', 'author'])
def test_novel_detail_rejects_invalid_ids_before_sending(tmp_path, bad_id, field):
    client, mock = verified_novel_client(tmp_path, packet(novel_body()))
    try:
        with pytest.raises(mod.DestinationRejected) as caught:
            client.get_novel_detail(bad_id if field == 'work' else '123',
                                    bad_id if field == 'author' else '91')
        assert str(caught.value) == 'Destination rejected'
        assert mock.calls == []
    finally:
        client.close()


@pytest.mark.parametrize('body', [
    None, [], 'sensitive body', {},
    novel_body(id='124'), novel_body(userId='92'),
    novel_body(id=True), novel_body(userId=None), novel_body(isMasked=True),
    novel_body(content=None), novel_body(content=''), novel_body(title=[]),
    novel_body(seriesNavData=[]), novel_body(textEmbeddedImages=[]),
    novel_body(coverUrl='https://evil.invalid/secret'),
])
def test_novel_detail_rejects_invalid_body_without_payload_output(tmp_path, body, capsys):
    client, mock = verified_novel_client(tmp_path, packet(body))
    try:
        with pytest.raises(mod.InvalidPayload) as caught:
            client.get_novel_detail('123', '91')
        assert str(caught.value) == 'Pixiv payload invalid'
        assert caught.value.__suppress_context__
        assert len(mock.calls) == 1
        captured = capsys.readouterr()
        assert captured.out == captured.err == ''
    finally:
        client.close()


@pytest.mark.parametrize('response,expected', [
    (b'{secret', mod.InvalidPayload), (b'\xff', mod.InvalidPayload),
    (b'[]', mod.InvalidPayload), (b'{"error":false}', mod.InvalidPayload),
    (b'{"error":false,"body":{"id":"123","id":"124"}}', mod.InvalidPayload),
    (b'{"error":false,"body":NaN}', mod.InvalidPayload),
    (b'{"error":false,"body":Infinity}', mod.InvalidPayload),
    (packet(novel_body(), error=True), mod.ApiRejected),
    (b'{"error":true,"message":"secret body Cookie URL"}', mod.ApiRejected),
    ('not bytes: secret Cookie', mod.ResponseRejected),
])
def test_novel_detail_rejects_json_api_and_transport_shapes(tmp_path, response, expected):
    client, _mock = verified_novel_client(tmp_path, response)
    try:
        with pytest.raises(expected) as caught:
            client.get_novel_detail('123', '91')
        assert 'secret' not in str(caught.value)
        assert 'fixtureonly' not in str(caught.value)
    finally:
        client.close()


@pytest.mark.parametrize('status', [403, 404])
def test_novel_detail_preserves_safe_http_failure_status(tmp_path, status, capsys):
    client, mock = verified_novel_client(tmp_path, packet(novel_body()))

    class RejectedOpener:
        def open(self, request, timeout):
            assert request.full_url == 'https://www.pixiv.net/ajax/novel/123'
            assert 0 < timeout <= 10.0
            raise HTTPError(request.full_url, status, 'secret Cookie body', None, None)

    def transport(request, timeout, limit, deadline):
        mock.calls.append(request)
        return mod._read_response(RejectedOpener(), request, timeout, limit, deadline)

    client._transport = transport
    try:
        with pytest.raises(mod.HTTPStatusRejected) as caught:
            client.get_novel_detail('123', '91')
        assert caught.value.status_code == status
        assert str(caught.value) == 'HTTP response rejected'
        assert len(mock.calls) == 1
        captured = capsys.readouterr()
        assert captured.out == captured.err == ''
    finally:
        client.close()


@pytest.mark.parametrize('status', [301, 302, 303, 307, 308])
def test_novel_detail_redirects_never_follow_location(tmp_path, status):
    client, mock = verified_novel_client(tmp_path, packet(novel_body()))
    response = FakeResponse(status=status)
    response.headers['Location'] = 'https://evil.invalid/secret'

    def transport(request, timeout, limit, deadline):
        mock.calls.append(request)
        return mod._read_response(FakeOpener(response), request, timeout, limit, deadline)

    client._transport = transport
    try:
        with pytest.raises(mod.ResponseRejected):
            client.get_novel_detail('123', '91')
        assert len(mock.calls) == 1
        assert mock.calls[0].full_url == 'https://www.pixiv.net/ajax/novel/123'
        assert not response.start
        assert any(isinstance(h, mod._NoRedirects) for h in client._api_opener.handlers)
    finally:
        client.close()


@pytest.mark.parametrize('url', [
    'http://www.pixiv.net/ajax/novel/123',
    'https://www.pixiv.net.evil.invalid/ajax/novel/123',
    'https://i.pximg.net/ajax/novel/123',
    'https://www.pixiv.net:443/ajax/novel/123',
    'https://name@www.pixiv.net/ajax/novel/123',
    'https://www.pixiv.net/ajax/novel/123?token=secret',
    'https://www.pixiv.net/ajax/novel/123?',
    'https://www.pixiv.net/ajax/novel/123#secret',
    'https://www.pixiv.net/ajax/novel/123#',
    'https://www.pixiv.net/ajax/novel/123/',
    'https://www.pixiv.net/ajax/novel/123/series',
    'https://www.pixiv.net/ajax/novel/%31%32%33',
    'https://www.pixiv.net/ajax/novel/0123',
    'https://www.pixiv.net/ajax/novel/123\n',
    'https://www.pixiv.net/ajax/illust/123',
])
def test_novel_destination_rejects_before_transport(tmp_path, url):
    client, mock = verified_novel_client(tmp_path, packet(novel_body()))
    try:
        with pytest.raises(mod.DestinationRejected):
            client._get('novel', url, deadline=time.monotonic() + 120.0)
        assert mock.calls == []
    finally:
        client.close()


def test_novel_destination_does_not_expand_artwork_allowlist():
    with pytest.raises(mod.DestinationRejected):
        mod._validate_destination('https://www.pixiv.net/ajax/novel/123', 'artwork')


def test_novel_detail_rejects_non_get_request_before_transport(tmp_path, monkeypatch):
    client, mock = verified_novel_client(tmp_path, packet(novel_body()))
    original_request = mod.Request

    def non_get_request(url, **kwargs):
        kwargs['method'] = 'POST'
        return original_request(url, **kwargs)

    monkeypatch.setattr(mod, 'Request', non_get_request)
    try:
        with pytest.raises(mod.DestinationRejected):
            client.get_novel_detail('123', '91')
        assert mock.calls == []
    finally:
        client.close()


def test_novel_detail_timeout_is_sanitized(tmp_path, capsys):
    def timeout(_request):
        raise TimeoutError('secret Cookie URL body')

    client, mock = verified_novel_client(tmp_path, timeout)
    try:
        with pytest.raises(mod.TransportFailed) as caught:
            client.get_novel_detail('123', '91')
        assert str(caught.value) == 'Pixiv transport failed'
        assert caught.value.__suppress_context__
        assert len(mock.calls) == 1
        captured = capsys.readouterr()
        assert captured.out == captured.err == ''
    finally:
        client.close()


def test_novel_detail_keeps_request_deadline_without_sleep(tmp_path, monkeypatch):
    client, _mock = verified_novel_client(tmp_path, packet(novel_body()))
    clock = [1000.0]
    monkeypatch.setattr(mod.time, 'monotonic', lambda: clock[0])

    def transport(request, timeout, limit, deadline):
        assert request.get_method() == 'GET'
        assert timeout == mod.MAX_REQUEST_SECONDS == 10.0
        assert deadline == 1010.0
        assert limit == mod.JSON_LIMIT == 2 * 1024 * 1024
        clock[0] = deadline
        return packet(novel_body())

    client._transport = transport
    try:
        with pytest.raises(mod.ReadDeadlineExceeded):
            client.get_novel_detail('123', '91')
    finally:
        client.close()


def test_novel_detail_uses_byte_limit_not_full_parser_character_budget(tmp_path):
    response = packet(novel_body())
    exact = response + b' ' * (mod.JSON_LIMIT - len(response))
    client, mock = verified_novel_client(tmp_path, exact)
    try:
        assert len(exact) == 2 * 1024 * 1024
        assert client.get_novel_detail('123', '91').work_id == '123'
        mock.when('/ajax/novel/123', exact + b' ')
        with pytest.raises(mod.ResponseLimitExceeded):
            client.get_novel_detail('123', '91')
        # A valid parser-sized character count can exceed the JSON byte budget.
        large = packet(novel_body(content='あ' * 400_000))
        assert len(large) > mod.JSON_LIMIT
        mock.when('/ajax/novel/123', large)
        with pytest.raises(mod.ResponseLimitExceeded):
            client.get_novel_detail('123', '91')
    finally:
        client.close()


def test_urllib_configuration_ignores_proxy_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:1234")
    client = make_client(tmp_path, MockTransport())
    assert client._api_opener is not client._cdn_opener
    for opener in (client._api_opener, client._cdn_opener):
        proxy_handlers = [h for h in opener.handlers if isinstance(h, ProxyHandler)]
        # The empty ProxyHandler has no protocol methods and may be discarded
        # by build_opener; it suppresses the environment-proxy default.
        assert not proxy_handlers or all(not h.proxies for h in proxy_handlers)
        assert any(isinstance(h, mod._NoRedirects) for h in opener.handlers)
    client.close()

@pytest.mark.parametrize('novels, expected', [({}, ()), ([], ()), ({'123':None,'124':None}, ('123','124'))])
def test_author_novel_selection_reuses_profile_route(tmp_path, novels, expected):
    mock = MockTransport()
    mock.when('/ajax/user/extra', packet({}))
    mock.when('/ajax/user/91/profile/all', packet({'novels':novels}))
    client = make_client(tmp_path, mock)
    try:
        assert client.get_author_novel_ids('91') == expected
        assert len(mock.calls) == 3
    finally:
        client.close()


@pytest.mark.parametrize('body', [{}, {'novels':['123']}, {'novels':{'bad':None}}, {'novels':{str(i):None for i in range(1,258)}}])
def test_author_novel_catalog_never_returns_partial_invalid_selection(tmp_path, body):
    mock = MockTransport()
    mock.when('/ajax/user/extra', packet({}))
    mock.when('/ajax/user/91/profile/all', packet(body))
    client = make_client(tmp_path, mock)
    try:
        with pytest.raises(mod.PixivReadError):
            client.get_author_novel_ids('91')
    finally:
        client.close()


def test_real_reader_interface_to_bundle_uses_only_mock_gets(tmp_path):
    from io import BytesIO
    from PIL import Image
    from pixivvault_nicegui.managed_novel_jobs import PinnedNovelSource
    from pixivvault_nicegui.novel_save_presentation import NovelSavePresentation
    from pixivvault_nicegui.services import ApplicationServices
    image = BytesIO()
    Image.new('RGB',(2,2),'blue').save(image,format='PNG')
    mock = MockTransport()
    mock.when('/ajax/user/extra',packet({}))
    mock.when('/ajax/user/91/profile/all',packet({'novels':{'123':None}}))
    mock.when('/ajax/novel/123',packet(dict(id='123',userId='91',userName='合成作者',title='合成作品',
        content='[[rb:漢字 > かんじ]][newpage][uploadedimage:1]',seriesNavData=None,
        textEmbeddedImages={'1':{'urls':{'original':'https://i.pximg.net/fixture.png'}}},
        coverUrl='https://i.pximg.net/cover.png')))
    mock.when('https://i.pximg.net/',image.getvalue())
    client=make_client(tmp_path,mock)
    source=PinnedNovelSource(client,{})
    services=ApplicationServices(1,novel_fixture_root=tmp_path/'novels',novel_fixture_source=source)
    try:
        assert source.catalog('91') == (('123','91','123'),)
        presentation=NovelSavePresentation(services)
        presentation.select('123','91')
        selection=presentation.preview('123','BOTH')
        assert services.jobs.snapshot()==()
        job=presentation.enqueue(selection,'bounded-reader-operation')
        with services.jobs._condition:
            assert services.jobs._condition.wait_for(lambda: services.jobs.snapshot()[0].state in ('done','error'),15)
        assert services.jobs.snapshot()[0].state=='done'
        entry=presentation.library()[0]
        assert '漢字《かんじ》' in presentation.read_saved(entry)
        assert len(presentation.read_images(entry))==2
        cdn=[req for req in mock.calls if urlsplit(req.full_url).netloc=='i.pximg.net']
        assert len(cdn)==2 and all(not req.has_header('Cookie') for req in cdn)
        assert all(req.get_method()=='GET' for req in mock.calls)
    finally:
        services.close()
    assert client._closed

def test_ugoira_metadata_zip_transport_is_bounded_and_cookie_free(tmp_path):
    from pixivvault_nicegui.ugoira_fixtures import FixtureUgoiraReader
    from pixivvault_nicegui.ugoira_practical import PinnedUgoiraSource,PracticalUgoiraJobs
    fixture=FixtureUgoiraReader()
    manifest=fixture.get_ugoira_manifest('123')
    raw=fixture.get_ugoira_archive(manifest)
    mock=MockTransport()
    mock.when('/ajax/user/extra',packet({}))
    mock.when('/ajax/illust/123/ugoira_meta',packet(dict(originalSrc=manifest.archive_url,
        frames=[dict(file=f.filename,delay=f.delay_ms) for f in manifest.frames])))
    mock.when('https://www.pixiv.net/ajax/illust/123',packet(fixture.get_artwork_metadata_raw('123','91')))
    mock.when(manifest.archive_url,raw)
    client=make_client(tmp_path,mock)
    owner=PracticalUgoiraJobs(tmp_path/'jobs',PinnedUgoiraSource(client))
    try:
        selection=owner.preview('123','91','frames')
        assert all(urlsplit(r.full_url).netloc!='i.pximg.net' for r in mock.calls)
        job=owner.enqueue_selection(selection,'transport')
        with owner.jobs._condition:
            assert owner.jobs._condition.wait_for(lambda: owner.jobs.snapshot()[0].state in ('done','error'),15)
        assert owner.jobs.snapshot()[0].state=='done'
        assert owner.library()
        cdn=[r for r in mock.calls if urlsplit(r.full_url).netloc=='i.pximg.net']
        assert len(cdn)==1 and not cdn[0].has_header('Cookie')
        assert all(r.get_method()=='GET' for r in mock.calls)
    finally:owner.close()

@pytest.mark.parametrize('url',[
    'http://i.pximg.net/img-zip-ugoira/123_ugoira.zip',
    'https://other.example/img-zip-ugoira/123_ugoira.zip',
    'https://i.pximg.net/other/123.zip',
    'https://i.pximg.net/img-zip-ugoira/../123.zip',
    'https://i.pximg.net/img-zip-ugoira/123.exe',
])
def test_ugoira_zip_destination_rejected_before_transport(url):
    with pytest.raises(mod.DestinationRejected):mod._validate_destination(url,'ugoira_zip')
