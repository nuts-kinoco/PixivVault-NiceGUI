from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from pixivvault_nicegui.library_media_registry import LibraryImageRegistry
from pixivvault_nicegui.media import ImageBlob


class Catalog:
    def __init__(self, ids=(), value=b"demo"):
        self.works = dict.fromkeys(ids)
        self.value, self.closed = value, False

    def url(self, work_id, page_no=0, variant="view"):
        if self.closed:
            raise RuntimeError("closed")
        if work_id not in self.works:
            raise ValueError("missing")
        return f"/media/{work_id}/{page_no}/{variant}.png"

    def get(self, work_id, page_no=0, variant="view"):
        self.url(work_id, page_no, variant)
        return ImageBlob(self.value, "image/png", '"test"')

    def close(self):
        self.closed = True


def test_two_clients_keep_distinct_catalogs_across_refresh():
    demo = Catalog(["demo-0001"])
    images = LibraryImageRegistry(demo)
    old = Catalog(["101"], b"old-page")
    images.publish(old, None)
    client_a = images.acquire()
    assert "catalog=" + client_a.token in client_a.url("101")
    new = Catalog(["101", "102"], b"new-page")
    images.publish(new, None)
    client_b = images.acquire()
    assert images.get_versioned(client_a.token, "101", 0, "view").data == b"old-page"
    assert images.get_versioned(client_b.token, "101", 0, "view").data == b"new-page"
    assert not old.closed
    client_a.close()
    assert old.closed and not new.closed
    with pytest.raises(ValueError):
        images.get_versioned(client_a.token, "101", 0, "view")
    client_b.close()
    images.close()
    assert new.closed and demo.closed


def test_inflight_read_owns_snapshot_even_when_client_leaves():
    started, proceed = Event(), Event()
    class Slow(Catalog):
        def get(self, *args):
            started.set()
            assert proceed.wait(3)
            return super().get(*args)
    images = LibraryImageRegistry(Catalog())
    old = Slow(["101"], b"old")
    images.publish(old, None)
    client = images.acquire()
    with ThreadPoolExecutor() as pool:
        future = pool.submit(images.get_versioned, client.token, "101", 0, "view")
        assert started.wait(3)
        images.publish(Catalog(["102"]), None)
        client.close()
        assert not old.closed
        proceed.set()
        assert future.result(3).data == b"old"
    assert old.closed
    images.close()


def test_bounded_retention_never_evicts_active_client():
    images = LibraryImageRegistry(Catalog(), max_versions=2)
    first = images.acquire()
    images.publish(Catalog(["101"]), None)
    second = images.acquire()
    with pytest.raises(RuntimeError, match="too many"):
        images.publish(Catalog(["102"]), None)
    first.close()
    images.publish(Catalog(["102"]), None)
    second.close()
    images.close()


def test_unavailable_remote_placeholder_does_not_break_card_construction():
    images = LibraryImageRegistry(Catalog(["demo-0001"]))
    pin = images.acquire()
    assert "/media/novel:22/0/thumb.png?catalog=" in pin.url("novel:22", 0, "thumb")
    assert not pin.can_open("novel:22")
    assert pin.can_open("demo-0001")
    pin.close()
    images.close()
