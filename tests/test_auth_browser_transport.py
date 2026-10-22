"""Offline end-to-end checks for the real Pixiv reader's browser fixture transport."""

from io import BytesIO
from pathlib import Path
import tempfile

from PIL import Image
import pytest

from pixivvault_nicegui.bookmark_remote_adapter import fetch_all_bookmarks
from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient
from tools.check_live_bookmark_browser import (
    assert_transport_evidence,
    SyntheticPixivTransport,
    write_synthetic_cookie,
)


@pytest.mark.parametrize("identity_format", ["next", "meta"])
def test_live_client_identity_four_routes_images_and_failure_are_offline(identity_format):
    with tempfile.TemporaryDirectory(prefix="pixiv-transport-unit-") as cookie_directory:
        cookie_path = write_synthetic_cookie(cookie_directory)
        transport = SyntheticPixivTransport(identity_format)
        client = LivePixivReadClient(cookie_path, transport=transport)
        try:
            assert client.verify_identity() == "12"
            assert transport.request_count == 2

            fetched = fetch_all_bookmarks(client, "12")
            assert fetched.ok and not fetched.failures
            assert len(fetched.records) == 71
            assert not {record.work_id for record in fetched.records} & {"900001", "novel:900002"}
            assert {record.work_id for record in fetched.records} >= {"202", "novel:202"}
            assert transport.route_calls == 4

            pages = client.get_artwork_pages("202")
            assert len(pages) == 2
            image_bytes = client.get_image_bytes(pages[1])
            with Image.open(BytesIO(image_bytes)) as image:
                assert image.format == "PNG"
                assert image.getpixel((0, 0)) == (0, 0, 255)

            transport.fail = True
            failed = fetch_all_bookmarks(client, "12")
            assert not failed.ok and not failed.records
            assert any(
                failure.kind == "novel"
                and failure.visibility == "hide"
                and failure.error_type == "TransportFailed"
                for failure in failed.failures
            )
            assert transport.request_count == 18
            assert transport.all_requests_get
            assert transport.allowed_https_hosts_only
            assert transport.image_requests_cookie_free
        finally:
            client.close()
        assert client._closed
    assert not Path(cookie_directory).exists()


@pytest.mark.parametrize("identity_format", ["next", "meta"])
def test_reader_acceptance_rejects_silent_fallback_and_missing_execution(identity_format):
    status = {"synthetic": True, "actual_parser_used": identity_format,
              "all_requests_get": True, "allowed_https_hosts_only": True,
              "image_requests_cookie_free": True,
              "request_count": 18, "calls": 8, "completed": 7}
    assert_transport_evidence(status, identity_format)
    corruptions = (
        {"actual_parser_used": None}, {"actual_parser_used": "other"},
        {"request_count": 0}, {"request_count": True},
        {"calls": 3}, {"completed": 3}, {"synthetic": False},
        {"all_requests_get": False}, {"image_requests_cookie_free": 1},
        {"allowed_https_hosts_only": False},
    )
    for mutation in corruptions:
        with pytest.raises(AssertionError):
            assert_transport_evidence({**status, **mutation}, identity_format)


def test_legacy_fixture_is_only_accepted_when_explicitly_requested():
    status = {"synthetic": True, "actual_parser_used": None,
              "all_requests_get": True, "allowed_https_hosts_only": True,
              "image_requests_cookie_free": True, "request_count": 0}
    assert_transport_evidence(status, None)
    with pytest.raises(AssertionError):
        assert_transport_evidence(status, "next")


def test_shutdown_fixture_checks_loopback_and_acknowledgement(monkeypatch):
    from tools import check_live_bookmark_browser as checker

    class Response:
        status = 200
        payload = b'{"stopping": true}'
        closed = False

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.closed = True

        def read(self, limit):
            assert limit == 1024
            return self.payload

    reply = Response()
    requests = []

    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            assert request.get_method() == "POST"
            assert request.full_url == "http://127.0.0.1:12345/_acceptance/shutdown"
            assert timeout == 5
            return reply

    def opener(*handlers):
        assert isinstance(handlers[0], checker.ProxyHandler) and handlers[0].proxies == {}
        assert isinstance(handlers[1], checker._NoShutdownRedirects)
        assert handlers[1].redirect_request(None, None, 302, "", {}, "https://other.invalid") is None
        return Opener()

    monkeypatch.setattr(checker, "build_opener", opener)
    checker.shutdown_fixture("http://127.0.0.1:12345")
    assert reply.closed and len(requests) == 1
    reply.payload = b'{"stopping": false}'
    reply.closed = False
    with pytest.raises(AssertionError):
        checker.shutdown_fixture("http://127.0.0.1:12345")
    assert reply.closed
    for origin in ("https://elsewhere.invalid", "http://u@127.0.0.1:12345",
                   "http://127.0.0.1:12345?x=1", "http://127.0.0.1:12345#x"):
        with pytest.raises(AssertionError):
            checker.shutdown_fixture(origin)
