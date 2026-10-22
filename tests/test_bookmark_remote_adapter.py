"""Synthetic-only contract tests for a four-route, read-only bookmark bridge."""

from copy import deepcopy

import pytest

from pixivvault_nicegui.bookmark_remote_adapter import (
    BookmarkFetch, fetch_all_bookmarks, parse_bookmark,
)
from pixivvault_nicegui.bookmark_state import BookmarkState
from pixivvault_nicegui.models import Artwork


def entry(i, **overrides):
    data = {
        "id": str(i), "title": f"Work {i}", "user_id": "40",
        "user_name": "Demo Author", "tags": ["one", "two"],
        "thumb_url": f"https://i.pximg.net/c/360x360_70/img-master/img/{i}.jpg",
        "page_count": 3, "create_date": "2026-01-02T00:00:00",
    }
    data.update(overrides)
    return data


class Client:
    def __init__(self, data=None, fail=None):
        self.data = data if data is not None else {
            ("illustration", "show"): [entry(101), entry(102)],
            ("novel", "show"): [entry(11)],
            ("illustration", "hide"): [entry(103)],
            ("novel", "hide"): [entry(12)],
        }
        self.fail = fail
        self.calls = []

    def route(self, kind, user_id, rest_type):
        self.calls.append((kind, user_id, rest_type))
        if self.fail == (kind, rest_type):
            def broken():
                yield entry(400)
                raise ConnectionError("must never reach status/cache")
            return broken()
        return deepcopy(self.data[(kind, rest_type)])

    def get_bookmarked_works(self, user_id, rest_type):
        return self.route("illustration", user_id, rest_type)

    def get_bookmarked_novels(self, user_id, rest_type):
        return self.route("novel", user_id, rest_type)


def test_four_routes_order_metadata_projection_and_novel_separation():
    client = Client()
    result = fetch_all_bookmarks(client, "55")
    assert result.ok and result.failures == ()
    assert [item.work_id for item in result.records] == [
        "101", "102", "novel:11", "103", "novel:12"]
    assert [call[0:3:2] for call in client.calls] == [
        ("illustration", "show"), ("novel", "show"),
        ("illustration", "hide"), ("novel", "hide")]
    assert all(call[1] == "55" for call in client.calls)
    assert result.records[0].tags == ("one", "two")
    assert result.records[0].page_count == 3
    assert result.records[2].page_count == 1
    assert result.records[0].visibility == "show"
    assert result.records[-1].visibility == "hide"
    assert result.records[0].illustration_type == 0
    assert result.records[2].illustration_type is None
    assert result.records[0].legacy_identity == ("101", False)
    assert result.records[2].legacy_identity == ("11", True)
    assert result.records[0].thumbnail.startswith("https://i.pximg.net/")
    assert result.records[0].as_artwork().work_id == "101"
    assert result.records[2].as_artwork().work_id == "novel:11"


def test_duplicate_ids_dedup_with_first_route_winner_and_type_namespaces():
    client = Client(data={
        ("illustration", "show"): [entry(10, title="first"), entry(10, title="later")],
        ("novel", "show"): [entry(10, title="novel")],
        ("illustration", "hide"): [entry(10, title="hidden duplicate"), entry(11)],
        ("novel", "hide"): [entry(10, title="later novel")],
    })
    result = fetch_all_bookmarks(client, "55")
    assert result.ok
    assert [(i.work_id, i.title) for i in result.records] == [
        ("10", "first"), ("novel:10", "novel"), ("11", "Work 11")]


def test_missing_preview_is_omitted_but_valid_zero_clears_cache():
    client = Client(data={
        (kind, vis): [entry(10, thumb_url="")] for kind in ("illustration", "novel")
        for vis in ("show", "hide")
    })
    state = BookmarkState(cached_remote=(Artwork("old", "Old", "Author"),))
    epoch = state.begin_fetch()
    result = fetch_all_bookmarks(client, "55")
    assert result.ok and not result.records
    assert result.apply_to_state(state, epoch)
    assert state.remote_cache == () and state.status == "empty"


@pytest.mark.parametrize("failed_route", [
    ("illustration", "show"), ("novel", "show"),
    ("illustration", "hide"), ("novel", "hide"),
])
def test_mid_stream_failure_on_any_route_preserves_existing_cache(failed_route):
    client = Client(fail=failed_route)
    previous = Artwork("old", "Existing Cache", "Owner")
    state = BookmarkState(cached_remote=[previous])
    epoch = state.begin_fetch()
    result = fetch_all_bookmarks(client, "55")
    assert not result.ok and result.records == ()
    assert len(result.failures) == 1
    assert result.failures[0].kind == failed_route[0]
    assert result.failures[0].visibility == failed_route[1]
    assert result.failures[0].error_type == "ConnectionError"
    assert result.apply_to_state(state, epoch)
    assert state.fetch_failed and state.remote_cache == (previous,)
    assert len(client.calls) == 4  # all routes get a chance; no half snapshot committed


@pytest.mark.parametrize("invalid", [
    entry("bad-id"), entry(11, thumb_url="http://i.pximg.net/img.jpg"),
    entry(11, thumb_url="https://pixiv.net/img.jpg"),
    entry(11, thumb_url="https://i.pximg.net.evil.example/img.jpg"),
    entry(11, page_count=0), entry(11, page_count=999999),
    entry(11, page_count=True), entry(11, tags=["ok", 3]), "not a dict",
    entry(11, thumb_url="https://i.pximg.net/\nfoo.jpg"),
    entry(11, thumb_url=" https://i.pximg.net/foo.jpg"),
    entry(11, thumb_url="https://i.pximg.net/foo.jpg\t"),
    entry(11, thumb_url="https://i.pximg.net/foo.jpg#fragment"),
    entry(11, type=8), entry(11, x_restrict=3),
])
def test_malformed_server_data_aborts_all_four_routes_without_partial_commit(invalid):
    rows = {(k, v): [] for k in ("illustration", "novel") for v in ("show", "hide")}
    rows[("illustration", "show")] = [invalid]
    outcome = fetch_all_bookmarks(Client(data=rows), "55")
    assert not outcome.ok
    assert outcome.records == ()
    assert len(outcome.failures) == 1
    assert outcome.failures[0].kind == "illustration"


def test_no_user_id_means_no_network_calls():
    client = Client()
    outcome = fetch_all_bookmarks(client, "unknown")
    assert not outcome.ok and outcome.failures[0].error_type == "InvalidUserId"
    assert not client.calls


def test_read_only_client_and_input_rows_not_mutated():
    client = Client()
    before = deepcopy(client.data)
    assert fetch_all_bookmarks(client, "55").ok
    assert client.data == before
    assert not hasattr(client, "add_bookmark")
    assert not hasattr(client, "delete_bookmark")


def test_stale_result_does_not_overwrite_new_state():
    state = BookmarkState(cached_remote=[Artwork("old", "Previous", "A")])
    old = state.begin_fetch()
    current = state.begin_fetch()
    outcome = fetch_all_bookmarks(Client(), "55")
    assert not outcome.apply_to_state(state, old)
    assert outcome.apply_to_state(state, current)
    assert state.items[0].work_id == "101"
    assert not outcome.apply_to_state(state, current)


def test_local_downloads_do_not_add_local_only_to_remote_list():
    state = BookmarkState(local=[Artwork("101", "Downloaded", "A"),
                                 Artwork("only-local", "Local", "A")])
    result = fetch_all_bookmarks(Client(), "55")
    assert result.apply_to_state(state, state.begin_fetch())
    assert state.items[0].downloaded
    assert state.items[0].artwork.title == "Downloaded"
    assert not any(i.work_id == "only-local" for i in state.items)


def test_route_maximum_rejects_large_or_infinite_like_results():
    rows = {(k, v): [] for k in ("illustration", "novel") for v in ("show", "hide")}
    rows[("illustration", "show")] = [entry(1), entry(2), entry(3)]
    result = fetch_all_bookmarks(Client(data=rows), "55", max_items_per_route=2)
    assert not result.ok and result.failures[0].error_type == "OverflowError"


def test_thumb_url_missing_field_returns_none_without_mutating_input():
    raw = entry(10)
    raw.pop("thumb_url")
    assert parse_bookmark(raw, kind="illustration", visibility="show") is None
    assert "thumb_url" not in raw
