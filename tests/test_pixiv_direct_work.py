"""Manual artwork-ID lookup uses only a bounded, injected Pixiv GET transport."""

import json
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from threading import Event

import pytest

from pixivvault_nicegui.pixiv_read_client import (
    AuthenticationMissing, ClientClosed, DestinationRejected, InvalidPayload, LivePixivReadClient,
)


def detail(**changes):
    result = {
        "id": "123", "title": "架空の作品", "userId": "23", "userName": "架空作者",
        "pageCount": 2, "illustType": 1, "xRestrict": 0,
        "createDate": "2026-10-23T00:00:00+09:00",
        "tags": {"tags": [{"tag": "風景"}, {"tag": "青空"}]},
        "urls": {"small": "https://i.pximg.net/img-master/synthetic/123.jpg"},
    }
    result.update(changes)
    return result


def reader(tmp_path, item, calls):
    cookie = tmp_path / "synthetic-cookie.txt"
    cookie.write_text("# Netscape HTTP Cookie File\n"
        f".pixiv.net\tTRUE\t/\tTRUE\t{int(time.time())+3600}\tPHPSESSID\t55_fake\n", encoding="utf-8")
    original = cookie.read_bytes()

    def transport(request, timeout, limit, deadline):
        assert request.get_method() == "GET" and 0 < timeout <= 10
        calls.append((request.full_url, request.has_header("Cookie")))
        if request.full_url == "https://www.pixiv.net/":
            return (b'<meta id="meta-global-data" content=\'{"userData":{"id":"55"}}\'>')
        if request.full_url.endswith("/ajax/user/extra"):
            return b'{"error":false,"body":{}}'
        assert request.full_url == "https://www.pixiv.net/ajax/illust/123"
        return json.dumps({"error": False, "body": item}).encode("utf-8")

    return LivePixivReadClient(cookie, transport=transport), cookie, original


def test_explicit_verified_id_resolves_immutable_download_metadata(tmp_path):
    calls = []
    client, cookie, original = reader(tmp_path, detail(), calls)
    with client:
        with pytest.raises(AuthenticationMissing):
            client.get_artwork_download_record("123")
        assert calls == []
        assert client.verify_identity() == "55"
        record = client.get_artwork_download_record("123")
        assert (record.remote_id, record.kind, record.page_count, record.illustration_type) == (
            "123", "illustration", 2, 1)
        assert (record.author_id, record.title, record.tags) == ("23", "架空の作品", ("風景", "青空"))
        assert record.thumbnail.startswith("https://i.pximg.net/")
    assert len(calls) == 3 and all(sent for _, sent in calls)
    assert cookie.read_bytes() == original


@pytest.mark.parametrize("changes", [
    {"id": "124"}, {"userId": "0"}, {"illustType": 2}, {"pageCount": 0},
    {"pageCount": 1001}, {"isMasked": True}, {"xRestrict": 99},
    {"tags": {"tags": [{"tag": "ok"}, {"name": None}]}},
    {"urls": {"small": "https://i.pximg.net.evil/synthetic.jpg"}},
    {"title": "bad\x00title"},
])
def test_invalid_or_unsupported_direct_artwork_fails_closed(tmp_path, changes):
    client, cookie, before = reader(tmp_path, detail(**changes), [])
    with client:
        client.verify_identity()
        with pytest.raises(InvalidPayload):
            client.get_artwork_download_record("123")
    assert cookie.read_bytes() == before


@pytest.mark.parametrize("bad", ["../123", "0", "000123", "123?type=novel", "1" * 21])
def test_direct_id_rejected_without_detail_request(tmp_path, bad):
    calls = []
    client, _, _ = reader(tmp_path, detail(), calls)
    with client:
        client.verify_identity()
        with pytest.raises(DestinationRejected):
            client.get_artwork_download_record(bad)
    assert len(calls) == 2


def test_concurrent_authentication_cannot_transiently_reject_a_verified_detail(tmp_path):
    """One refresh must not expose its temporary unverified state to another GET."""
    client, _, _ = reader(tmp_path, detail(), [])
    client.verify_identity()
    second_auth_started = Event()
    release_second_auth = Event()
    detail_attempt = Event()
    original_transport = client._transport
    home_count = 0

    def held_transport(request, timeout, limit, deadline):
        nonlocal home_count
        if request.full_url == "https://www.pixiv.net/":
            home_count += 1
            if home_count == 1:
                second_auth_started.set()
                assert release_second_auth.wait(5)
        return original_transport(request, timeout, limit, deadline)

    client._transport = held_transport
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            second = executor.submit(client.verify_identity)
            assert second_auth_started.wait(5)

            def detail_call():
                detail_attempt.set()
                return client.get_artwork_download_record("123")

            direct = executor.submit(detail_call)
            assert detail_attempt.wait(5)
            with pytest.raises(FutureTimeout):
                direct.result(timeout=0.15)
            release_second_auth.set()
            assert second.result(timeout=5) == "55"
            assert direct.result(timeout=5).remote_id == "123"
    finally:
        release_second_auth.set()
        client.close()


def test_close_is_nonblocking_during_held_read_and_rejects_late_payload(tmp_path):
    client, _, _ = reader(tmp_path, detail(), [])
    client.verify_identity()
    entered, release = Event(), Event()
    original_transport = client._transport

    def held_transport(request, timeout, limit, deadline):
        if request.full_url.endswith("/ajax/illust/123"):
            entered.set()
            assert release.wait(5)
        return original_transport(request, timeout, limit, deadline)

    client._transport = held_transport
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(client.get_artwork_download_record, "123")
        assert entered.wait(5)
        start = time.monotonic()
        client.close()
        assert time.monotonic() - start < 0.25, "close waited on the HTTP identity lock"
        release.set()
        with pytest.raises(ClientClosed):
            pending.result(timeout=5)


def test_verified_identity_can_be_reused_without_two_extra_gets(tmp_path):
    calls = []
    client, _, _ = reader(tmp_path, detail(), calls)
    try:
        assert client.verify_identity() == "55"
        assert len(calls) == 2
        assert client.require_identity() == "55"
        assert len(calls) == 2
        assert client.get_artwork_download_record("123").remote_id == "123"
        assert len(calls) == 3
    finally:
        client.close()
