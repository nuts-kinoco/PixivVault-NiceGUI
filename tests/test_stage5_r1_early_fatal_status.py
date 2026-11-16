"""Never lose a received raw 401/403/429 to socket-close/deadline errors.

All transports, responses, owner cleanup and credentials are synthetic. No
network, original Pixiv data or profile is opened by this test module.
"""

from contextlib import contextmanager
import time
from urllib.request import Request
from urllib.error import HTTPError

import pytest

from pixivvault_nicegui import pixiv_read_client as reads


@pytest.mark.parametrize("status", (401, 403, 429))
def test_owned_transport_preserves_observed_fatal_http_before_cleanup_timeout(
    monkeypatch, status,
):
    seen = []
    deadline = time.monotonic() + 10

    class FakeOwner:
        def __init__(self):
            self.deadline = deadline

        def remaining(self):
            return 1.0

    @contextmanager
    def failing_cleanup(**_):
        try:
            yield FakeOwner()
        finally:
            raise TimeoutError("generated owner cleanup race")

    def parsed_response(_opener, _request, _timeout, _limit, _deadline,
                        *, owner=None, observe_status=None):
        assert owner is not None
        assert observe_status is not None
        observe_status(status)
        seen.append("observed HTTP status")
        raise reads.HTTPStatusRejected(status)

    owner = reads._OwnedReadTransport()
    monkeypatch.setattr(owner, "session", failing_cleanup)
    monkeypatch.setattr(reads, "build_opener", lambda *_: object())
    monkeypatch.setattr(reads, "OwnedHTTPSHandler", lambda *_: object())
    monkeypatch.setattr(reads, "_read_response", parsed_response)
    try:
        with pytest.raises(reads.HTTPStatusRejected) as raised:
            owner(Request("https://www.pixiv.net/ajax/user/extra", method="GET"),
                  2.0, 4096, deadline)
        assert raised.value.status_code == status
        assert seen == ["observed HTTP status"]
    finally:
        owner.close()


@pytest.mark.parametrize("status", (401, 403, 429))
def test_read_response_reports_fatal_headers_before_owner_registration_error(status):
    response = type("GeneratedResponse", (), {"getcode": lambda _: status})()

    class Opener:
        def open(self, _request, timeout):
            assert timeout > 0
            return response

    class Owner:
        def response(self, _response):
            raise TimeoutError("generated late registration failure")

    observed = []
    with pytest.raises(reads.ReadDeadlineExceeded):
        reads._read_response(
            Opener(), Request("https://www.pixiv.net/ajax/user/extra", method="GET"),
            1.0, 4096, time.monotonic()+10,
            owner=Owner(), observe_status=observed.append,
        )
    assert observed == [status]


@pytest.mark.parametrize("status", (401, 403, 429))
def test_raw_urllib_http_error_observed_before_owner_registration_failure(status):
    class Opener:
        def open(self, request, timeout):
            raise HTTPError(request.full_url, status, "generated", {}, None)

    class Owner:
        def response(self, _response):
            raise TimeoutError("generated registration failure after HTTPError")

    observed = []
    with pytest.raises(TimeoutError):
        reads._read_response(
            Opener(), Request("https://www.pixiv.net/ajax/user/extra", method="GET"),
            1.0, 4096, time.monotonic()+10,
            owner=Owner(), observe_status=observed.append,
        )
    assert observed == [status]


def test_owned_transport_prioritizes_received_403_over_concurrent_close(monkeypatch):
    deadline = time.monotonic() + 10
    owner = reads._OwnedReadTransport()

    class FakeOwner:
        def __init__(self):
            self.deadline = deadline

        def remaining(self):
            return 1.0

    @contextmanager
    def close_on_exit(**_):
        try:
            yield FakeOwner()
        finally:
            owner.close()

    def fake_response(_opener, _request, _timeout, _limit, _deadline, *, owner=None,
                      observe_status=None):
        observe_status(403)
        raise reads.HTTPStatusRejected(403)

    monkeypatch.setattr(owner, "session", close_on_exit)
    monkeypatch.setattr(reads, "build_opener", lambda *_: object())
    monkeypatch.setattr(reads, "OwnedHTTPSHandler", lambda *_: object())
    monkeypatch.setattr(reads, "_read_response", fake_response)
    with pytest.raises(reads.HTTPStatusRejected) as raised:
        owner(Request("https://www.pixiv.net/ajax/user/extra", method="GET"),
              2.0, 4096, deadline)
    assert raised.value.status_code == 403
