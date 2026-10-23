"""Synthetic, offline checks for original-size saving URLs."""

import json
import time

import pytest

from pixivvault_nicegui.pixiv_read_client import (
    DestinationRejected, InvalidPayload, LivePixivReadClient,
)


def original(page, work="123", *, ext="png"):
    return ("https://i.pximg.net/img-original/img/2026/10/23/01/02/03/"
            f"{work}_p{page}.{ext}")


def regular(page):
    return ("https://i.pximg.net/img-master/img/2026/10/23/01/02/03/"
            f"123_p{page}_master1200.jpg")


def rows(*urls):
    return [{"urls": {"original": url, "regular": regular(i)}}
            for i, url in enumerate(urls)]


def client(tmp_path, payload, calls):
    path = tmp_path / "synthetic-cookie.txt"
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        f".pixiv.net\tTRUE\t/\tTRUE\t{int(time.time()) + 3600}\tPHPSESSID\t55_fixture\n",
        encoding="utf-8",
    )

    def transport(request, timeout, limit, deadline):
        calls.append(request)
        assert request.get_method() == "GET"
        assert request.full_url == "https://www.pixiv.net/ajax/illust/123/pages"
        assert 0 < timeout <= 10 and limit >= 1 and deadline >= time.monotonic()
        return json.dumps({"error": False, "body": payload}).encode()

    return LivePixivReadClient(path, transport=transport)


def test_original_only_order_and_backward_compatible_viewer(tmp_path):
    calls = []
    payload = rows(original(0), original(1, ext="jpg"), original(2))
    with client(tmp_path, payload, calls) as reader:
        assert calls == []  # construction does not communicate
        assert reader.get_artwork_original_pages("123") == tuple(
            entry["urls"]["original"] for entry in payload
        )
        assert reader.get_artwork_pages("123") == tuple(
            entry["urls"]["regular"] for entry in payload
        )
    assert len(calls) == 2
    assert all(call.has_header("Cookie") for call in calls)


@pytest.mark.parametrize("payload", [
    rows(original(0, "124")),
    rows(original(1)),
    rows(original(0), original(2)),
    rows(original(0), original(0)),
    rows(original(0), original(1, "124")),
    rows("https://i.pximg.net.evil/img-original/img/2026/10/23/01/02/03/123_p0.png"),
    rows("http://i.pximg.net/img-original/img/2026/10/23/01/02/03/123_p0.png"),
    rows("https://i.pximg.net/img-master/img/2026/10/23/01/02/03/123_p0.png"),
    rows(original(0) + "?token=secret"),
    rows(original(0).replace("_p0", "_p00")),
    [{"urls": {"regular": regular(0)}}],
    [{"urls": {"original": None, "regular": regular(0)}}],
    [],
    rows(*(original(i) for i in range(1001))),
])
def test_invalid_original_payload_fails_closed(tmp_path, payload):
    calls = []
    with client(tmp_path, payload, calls) as reader:
        with pytest.raises(InvalidPayload):
            reader.get_artwork_original_pages("123")
    assert len(calls) == 1


def test_invalid_work_id_does_not_issue_request(tmp_path):
    calls = []
    with client(tmp_path, rows(original(0)), calls) as reader:
        with pytest.raises(DestinationRejected):
            reader.get_artwork_original_pages("../123")
    assert calls == []
