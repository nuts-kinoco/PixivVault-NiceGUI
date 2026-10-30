"""Synthetic GET deadline classification at an owned HTTPS socket boundary."""

from contextlib import contextmanager
import time
from urllib.error import URLError

import pytest

from pixivvault_nicegui import pixiv_read_client as read


class _FailingOpener:
    def __init__(self, error):
        self.error = error

    def open(self, request, timeout):
        assert request.get_method() == "GET"
        assert timeout > 0
        raise self.error


@pytest.mark.parametrize("error", [
    TimeoutError("sensitive socket deadline"),
    URLError(TimeoutError("sensitive socket deadline")),
])
def test_owned_get_socket_timeout_is_bounded_deadline_not_generic_failure(error):
    # In production the owned socket timeout is calculated from the absolute
    # deadline; Windows may surface it milliseconds before monotonic >= deadline.
    request = read.Request("https://www.pixiv.net/", method="GET")
    with pytest.raises(read.ReadDeadlineExceeded) as caught:
        read._read_response(_FailingOpener(error), request, .75, 1024,
                            time.monotonic() + .75, owner=object())
    assert str(caught.value) == "Read deadline exceeded"
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("error", [
    OSError("sensitive connection reset"),
    URLError(OSError("sensitive network unavailable")),
])
def test_owned_get_non_timeout_transport_error_stays_transport_failed(error):
    request = read.Request("https://www.pixiv.net/", method="GET")
    with pytest.raises(read.TransportFailed) as caught:
        read._read_response(_FailingOpener(error), request, .75, 1024,
                            time.monotonic() + .75, owner=object())
    assert str(caught.value) == "Pixiv transport failed"
    assert caught.value.__suppress_context__


def test_borrowed_opener_timeout_not_reclassified_without_owned_socket():
    request = read.Request("https://www.pixiv.net/", method="GET")
    with pytest.raises(read.TransportFailed):
        read._read_response(_FailingOpener(TimeoutError("borrowed")), request,
                            .75, 1024, time.monotonic() + .75)


def test_owned_connect_timeout_uses_absolute_deadline(monkeypatch):
    @contextmanager
    def timed_out(_self, **_kwargs):
        raise TimeoutError("sensitive connect detail")
        yield  # pragma: no cover - contextmanager construction only

    monkeypatch.setattr(read._OwnedReadTransport, "session", timed_out)
    request = read.Request("https://www.pixiv.net/", method="GET")
    with pytest.raises(read.ReadDeadlineExceeded) as caught:
        read._OwnedReadTransport()(request, .75, 1024, time.monotonic() + .75)
    assert str(caught.value) == "Read deadline exceeded"
    assert caught.value.__suppress_context__
