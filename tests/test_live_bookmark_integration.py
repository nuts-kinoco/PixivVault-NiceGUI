"""Synthetic client exercises production UI/service paths without credentials or HTTP."""
import asyncio
from dataclasses import replace
from io import BytesIO

from nicegui import Client, ui
from PIL import Image
import pytest

from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.library_view import LibraryView
from pixivvault_nicegui.bookmark_remote_adapter import parse_bookmark


class SyntheticReadClient:
    def __init__(self):
        self.fail = False
        self.routes = []
        self.closed = False

    def verify_identity(self):
        return "12"

    def get_bookmarked_works(self, my_user_id, rest_type):
        self.routes.append(("illustration", rest_type))
        if rest_type == "show":
            yield dict(id="202", title="合成未DLの青", user_id="23", user_name="架空作家",
                       tags=["blue"], thumb_url="https://i.pximg.net/fake/202.png", page_count=2)

    def get_bookmarked_novels(self, my_user_id, rest_type):
        self.routes.append(("novel", rest_type))
        if self.fail and rest_type == "hide":
            raise RuntimeError("injected")
        if rest_type == "show":
            yield dict(id="202", title="合成小説", user_id="23", user_name="架空作家",
                       tags=[], thumb_url="https://i.pximg.net/fake/novel202.png")

    def get_artwork_pages(self, work_id):
        return ("https://i.pximg.net/fake/202p0.png", "https://i.pximg.net/fake/202p1.png")

    def get_image_bytes(self, url):
        out = BytesIO()
        Image.new("RGB", (32, 24), "blue" if "p1" in url else "red").save(out, "PNG")
        return out.getvalue()

    def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_remote_only_bookmark_selection_viewer_and_four_route_failure():
    fake = SyntheticReadClient()
    services = ApplicationServices(live_client=fake)
    client = Client(ui.page("/_live_bookmark_unit"))
    with client:
        view = LibraryView(services)
        view.subview = "bookmarks"
        view._sync_panels()
    try:
        epoch = view.bookmark_state.begin_fetch()
        await view._refresh_bookmarks_async(epoch)
        assert len(fake.routes) == 4
        assert [item.work_id for item in view.bookmark_state.items] == ["202", "novel:202"]
        view.bookmarks.select("202", True)
        assert view.bookmark_state.selected_ids == {"202"}
        view.open_bookmark("202")
        assert view._viewer_state.viewer_open
        assert view._viewer_state.selected_artwork.page_count == 2
        assert "/media/202/0/view.png?catalog=" in view._image_url
        pin = view._image_provider.token
        first = services.images.get_versioned(pin, "202", 0, "view")
        second = services.images.get_versioned(pin, "202", 1, "view")
        assert first.etag != second.etag
        view.close_viewer()
        view.open_bookmark("novel:202")
        assert view._viewer_state.selected_artwork.work_id == "novel:202"
        assert services.images.get_versioned(pin, "novel:202", 0, "view").data
        before = view.bookmark_state.remote_cache
        fake.fail = True
        epoch = view.bookmark_state.begin_fetch()
        await view._refresh_bookmarks_async(epoch)
        assert view.bookmark_state.fetch_failed
        assert view.bookmark_state.remote_cache == before
        assert view._image_provider.token == pin
        assert view.bookmark_state.selected_ids == {"202"}
    finally:
        client.delete()
        services.close()
    assert fake.closed


@pytest.mark.asyncio
async def test_search_and_tab_return_keep_selection_without_automatic_network_refresh():
    fake = SyntheticReadClient()
    services = ApplicationServices(live_client=fake)
    await services.refresh_bookmark_cache()
    client = Client(ui.page("/_live_bookmark_search"))
    with client:
        view = LibraryView(services)
        view.show_subview("bookmarks")
    try:
        view.bookmarks.select("202", True)
        view.bookmarks.search("blue")
        assert [card.work_id for card in view.bookmark_state.visible_items] == ["202"]
        routes = len(fake.routes)
        view.set_active(False)
        view.set_active(True)
        assert view.bookmark_state.query == "blue"
        assert view.bookmark_state.selected_ids == {"202"}
        assert len(fake.routes) == routes
        view.bookmarks.search("not present")
        assert view.bookmark_state.status == "no_match"
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_unknown_cached_page_count_resolves_on_open_for_both_clients():
    fake = SyntheticReadClient()
    services = ApplicationServices(live_client=fake)
    row = parse_bookmark(dict(id="202", title="cached unknown pages", user_id="23",
                              user_name="author", tags=[], thumb_url="https://i.pximg.net/fake.png"),
                         kind="illustration", visibility="show")
    row = replace(row, page_count_known=False)
    services._cached_remote_bookmarks = (row.as_artwork(),)
    services._remote_records = (row,)
    services._publish_images(services.library_snapshot(), (row,))
    clients = [Client(ui.page("/_cached_unknown_a")), Client(ui.page("/_cached_unknown_b"))]
    views = []
    for client in clients:
        with client:
            view = LibraryView(services)
            view.show_subview("bookmarks")
            views.append(view)
    try:
        first, second = views
        assert first.bookmark_state.items[0].artwork.page_count == 1
        first._bookmark_open_epoch += 1
        await first._open_unknown_bookmark("202", first._bookmark_open_epoch, first._image_provider)
        assert first._viewer_state.selected_artwork.page_count == 2
        second.open_bookmark("202")
        assert second._viewer_state.selected_artwork.page_count == 2
        assert len(fake.routes) == 0  # No full bookmark refetch or TTL behavior.
    finally:
        for client in clients:
            client.delete()
        services.close()


@pytest.mark.asyncio
async def test_disk_refresh_keeps_search_entered_while_worker_is_running():
    services = ApplicationServices()
    client = Client(ui.page("/_refresh_search_inflight"))
    with client:
        view = LibraryView(services)
    started, finish = asyncio.Event(), asyncio.Event()
    async def slow_refresh():
        started.set()
        await finish.wait()
        return services.library
    services.library_repository = object()  # Only gates the injected synthetic refresh.
    services.refresh_library_snapshot = slow_refresh
    try:
        old = view.state
        task = asyncio.create_task(view.refresh_from_disk())
        await started.wait()
        view.search("0003")
        finish.set()
        await task
        assert view.state is not old
        assert view.state.query == "0003"
        assert view.state.matched_author_count == 1
    finally:
        services.library_repository = None
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_animation_thumbnail_does_not_break_other_bookmark_viewers():
    class Mixed(SyntheticReadClient):
        def get_bookmarked_works(self, my_user_id, rest_type):
            yield from super().get_bookmarked_works(my_user_id, rest_type)
            if rest_type == "show":
                yield dict(id="299", title="animation", user_id="23", user_name="author",
                           tags=[], thumb_url="https://i.pximg.net/fake/299.png", type=2)
    services = ApplicationServices(live_client=Mixed())
    await services.refresh_bookmark_cache()
    client = Client(ui.page("/_mixed_animation_metadata"))
    with client:
        view = LibraryView(services)
        view.show_subview("bookmarks")
    try:
        assert not view._image_provider.can_open("299")
        assert services.images.get_versioned(view._image_provider.token, "299", 0, "thumb").data
        view.open_bookmark("202")
        assert view._viewer_state.viewer_open
        assert view._viewer_state.selected_artwork.work_id == "202"
        view.open_bookmark("299")
        assert view._viewer_state.selected_artwork.work_id == "202"
    finally:
        client.delete()
        services.close()
