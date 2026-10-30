"""Synthetic files only; original library images and ZIPs are never opened."""

from io import BytesIO
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from zipfile import ZipFile, ZipInfo

from PIL import Image
import pytest

from pixivvault_nicegui.library_models import LibrarySnapshot, LibraryWork
from pixivvault_nicegui.local_media_adapter import (
    ReadonlyLocalMediaCatalog, MediaMissing, MediaAmbiguous, MediaUnavailable,
)
from pixivvault_nicegui.models import Artwork


def png(color):
    image = Image.new("RGB", (20, 16), color)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def snapshot(pages=2, *, title="題名", known=True):
    return LibrarySnapshot((LibraryWork(
        Artwork("101", title, "作家", page_count=pages), "12",
        page_count_known=known,
    ),))


def test_exact_id_page_folder_and_converted_png(tmp_path):
    folder = tmp_path / "作家(12)"
    folder.mkdir()
    (folder / "題名(101)_1.png").write_bytes(png("red"))
    (folder / "題名(101)_2.png").write_bytes(png("blue"))
    with_image = ReadonlyLocalMediaCatalog(tmp_path, snapshot())
    try:
        assert with_image.url("101", 1) == "/media/101/1/view.png"
        first = with_image.get("101", 0)
        second = with_image.get("101", 1)
        assert first.media_type == "image/png" and first.etag != second.etag
        with Image.open(BytesIO(second.data)) as decoded:
            assert decoded.getpixel((0, 0)) == (0, 0, 255)
        with pytest.raises(MediaMissing):
            with_image.get("101", 2)
    finally:
        with_image.close()
    with pytest.raises(MediaUnavailable):
        with_image.get("101", 0)


def test_zip_and_work_folder_page_zero_mapping(tmp_path):
    with ZipFile(tmp_path / "作者(12).zip", "w") as z:
        z.writestr("作品(101)/101_p0.png", png("red"))
        z.writestr("作品(101)/101_p1.png", png("green"))
    catalog = ReadonlyLocalMediaCatalog(tmp_path, snapshot())
    assert catalog.get("101", 0).etag != catalog.get("101", 1).etag
    catalog.close()


def test_duplicate_stores_do_not_silently_select_one(tmp_path):
    (tmp_path / "作家(12)").mkdir()
    (tmp_path / "作家(12)" / "絵(101)_1.png").write_bytes(png("red"))
    with ZipFile(tmp_path / "別名(12).zip", "w") as z:
        z.writestr("絵(101)_1.png", png("blue"))
    catalog = ReadonlyLocalMediaCatalog(tmp_path, snapshot())
    with pytest.raises(MediaAmbiguous):
        catalog.get("101", 0)
    catalog.close()


def test_missing_page_does_not_fall_back_to_second_page(tmp_path):
    (tmp_path / "作家(12)").mkdir()
    (tmp_path / "作家(12)" / "絵(101)_2.png").write_bytes(png("blue"))
    catalog = ReadonlyLocalMediaCatalog(tmp_path, snapshot())
    with pytest.raises(MediaMissing):
        catalog.get("101", 0)
    assert catalog.get("101", 1)
    catalog.close()


def test_malicious_zip_member_is_rejected(tmp_path):
    with ZipFile(tmp_path / "作家(12).zip", "w") as z:
        z.writestr("../絵(101)_1.png", png("red"))
    catalog = ReadonlyLocalMediaCatalog(tmp_path, snapshot())
    with pytest.raises(MediaUnavailable):
        catalog.get("101", 0)
    catalog.close()


def test_unrelated_legacy_title_is_not_guessed(tmp_path):
    (tmp_path / "作家(12)").mkdir()
    (tmp_path / "作家(12)" / "別の題名_1.png").write_bytes(png("red"))
    catalog = ReadonlyLocalMediaCatalog(tmp_path, snapshot())
    with pytest.raises(MediaMissing):
        catalog.get("101", 0)
    catalog.close()


def test_missing_media_url_can_render_placeholder_without_blocking_ui(tmp_path):
    catalog = ReadonlyLocalMediaCatalog(tmp_path, snapshot())
    assert catalog.url("101", 0, "thumb").endswith("/101/0/thumb.png")
    with pytest.raises(MediaMissing):
        catalog.get("101", 0, "thumb")
    catalog.close()


def test_direct_bookmark_folder_uses_both_author_and_work_id(tmp_path):
    wrapper = tmp_path / "☆ブックマーク(555)"
    wrapper.mkdir()
    (wrapper / "作家(12)_題名(101)_1.png").write_bytes(png("red"))
    (wrapper / "別人(112)_題名(101)_2.png").write_bytes(png("blue"))
    catalog = ReadonlyLocalMediaCatalog(tmp_path, snapshot())
    try:
        assert catalog.get("101", 0).data
        with pytest.raises(MediaMissing):
            catalog.get("101", 1)
    finally:
        catalog.close()


def test_legacy_hardlink_mirror_of_same_image_is_not_ambiguous(tmp_path):
    folder = tmp_path / "作者(12)"
    folder.mkdir()
    source = folder / "作品(101)_1.png"
    source.write_bytes(png("red"))
    wrapper = tmp_path / "☆ブックマーク(555)"
    wrapper.mkdir()
    os.link(source, wrapper / "作者(12)_作品(101)_1.png")
    catalog = ReadonlyLocalMediaCatalog(tmp_path, snapshot())
    try:
        assert catalog.get("101", 0).media_type == "image/png"
    finally:
        catalog.close()


def test_very_long_zip_page_digits_cannot_disable_other_author(tmp_path):
    with ZipFile(tmp_path / "危険作者(12).zip", "w") as z:
        z.writestr("題名(101)_" + "9" * 4800 + ".png", png("red"))
    folder = tmp_path / "正常作者(22)"
    folder.mkdir()
    (folder / "作品(202).png").write_bytes(png("blue"))
    both = LibrarySnapshot((
        LibraryWork(Artwork("101", "題名", "危険作者"), "12"),
        LibraryWork(Artwork("202", "作品", "正常作者"), "22"),
    ))
    catalog = ReadonlyLocalMediaCatalog(tmp_path, both)
    try:
        assert catalog.get("202", 0).data
        with pytest.raises(MediaUnavailable):
            catalog.get("101", 0)
    finally:
        catalog.close()


def test_single_page_title_ending_in_page_number_uses_page_zero(tmp_path):
    folder = tmp_path / "作者(12)"
    folder.mkdir()
    (folder / "作品(101)_2.png").write_bytes(png("red"))
    catalog = ReadonlyLocalMediaCatalog(tmp_path, snapshot(pages=1, title="作品_2"))
    try:
        assert catalog.get("101", 0).data
    finally:
        catalog.close()


@pytest.mark.parametrize("known", [True, False])
def test_second_page_must_not_be_used_as_first_when_page_count_is_one(tmp_path, known):
    folder = tmp_path / "作者(12)"
    folder.mkdir()
    (folder / "作品(101)_2.png").write_bytes(png("blue"))
    catalog = ReadonlyLocalMediaCatalog(tmp_path, snapshot(pages=1, known=known))
    with pytest.raises(MediaMissing):
        catalog.get("101", 0)
    catalog.close()


def test_unknown_page_count_accepts_only_explicit_first_page(tmp_path):
    folder = tmp_path / "作者(12)"
    folder.mkdir()
    (folder / "作品(101)_1.png").write_bytes(png("red"))
    catalog = ReadonlyLocalMediaCatalog(tmp_path, snapshot(pages=1, known=False))
    assert catalog.get("101", 0).data
    catalog.close()


def test_legacy_idless_filename_from_unique_author_title_in_folder_and_zip(tmp_path):
    folder = tmp_path / "作者(12)"
    folder.mkdir()
    (folder / "画_作品_1.png").write_bytes(png("red"))
    with ZipFile(tmp_path / "別名(12).zip", "w") as z:
        z.writestr("画_作品_2.png", png("blue"))
    catalog = ReadonlyLocalMediaCatalog(tmp_path, snapshot(title="画/作品"))
    try:
        assert catalog.get("101", 0).media_type == "image/png"
        assert catalog.get("101", 1).media_type == "image/png"
    finally:
        catalog.close()


def test_legacy_idless_title_collision_must_not_guess(tmp_path):
    folder = tmp_path / "作者(12)"
    folder.mkdir()
    (folder / "同名_1.png").write_bytes(png("red"))
    works = LibrarySnapshot((
        LibraryWork(Artwork("101", "同名", "作者", page_count=2), "12"),
        LibraryWork(Artwork("202", "同名", "作者", page_count=2), "12"),
    ))
    catalog = ReadonlyLocalMediaCatalog(tmp_path, works)
    try:
        with pytest.raises(MediaMissing):
            catalog.get("101", 0)
        with pytest.raises(MediaMissing):
            catalog.get("202", 0)
    finally:
        catalog.close()


def test_legacy_idless_title_conflicting_page_attribution_is_ambiguous(tmp_path):
    folder = tmp_path / "作者(12)"
    folder.mkdir()
    (folder / "作品_2.png").write_bytes(png("red"))
    works = LibrarySnapshot((
        LibraryWork(Artwork("101", "作品", "作者", page_count=2), "12"),
        LibraryWork(Artwork("202", "作品_2", "作者", page_count=1), "12"),
    ))
    catalog = ReadonlyLocalMediaCatalog(tmp_path, works)
    try:
        with pytest.raises(MediaAmbiguous):
            catalog.get("101", 1)
        with pytest.raises(MediaAmbiguous):
            catalog.get("202", 0)
    finally:
        catalog.close()


def test_old_idless_multiple_candidates_are_not_selected_arbitrarily(tmp_path):
    folder = tmp_path / "作者(12)"
    folder.mkdir()
    (folder / "題名_1.png").write_bytes(png("red"))
    (folder / "題名_1.jpg").write_bytes(png("blue"))
    catalog = ReadonlyLocalMediaCatalog(tmp_path, snapshot())
    with pytest.raises(MediaAmbiguous):
        catalog.get("101", 0)
    catalog.close()


def test_direct_bookmark_idless_names_and_parentheses_in_author(tmp_path):
    wrapper = tmp_path / "☆ブックマーク(555)"
    wrapper.mkdir()
    (wrapper / "別名(旧姓)(12)_題名_1.png").write_bytes(png("red"))
    catalog = ReadonlyLocalMediaCatalog(tmp_path, snapshot())
    assert catalog.get("101", 0).data
    catalog.close()


def test_normal_zip_directory_entries_and_unrelated_invalid_zip(tmp_path):
    with ZipFile(tmp_path / "壊れた(12).zip", "w") as z:
        z.writestr("../異常.png", png("red"))
    with ZipFile(tmp_path / "正常(22).zip", "w") as z:
        z.writestr("フォルダ/", "")
        z.writestr("フォルダ/作品(202)/202_p0.png", png("blue"))
    works = LibrarySnapshot((
        LibraryWork(Artwork("101", "異常", "作者"), "12"),
        LibraryWork(Artwork("202", "作品", "作者"), "22"),
    ))
    catalog = ReadonlyLocalMediaCatalog(tmp_path, works)
    try:
        assert catalog.get("202", 0).data
        with pytest.raises(MediaUnavailable):
            catalog.get("101", 0)
    finally:
        catalog.close()


def test_transparent_png_alpha_is_preserved(tmp_path):
    folder = tmp_path / "作者(12)"
    folder.mkdir()
    source = Image.new("RGBA", (20, 16), (255, 20, 30, 0))
    buffer = BytesIO()
    source.save(buffer, format="PNG")
    (folder / "題名(101)_1.png").write_bytes(buffer.getvalue())
    catalog = ReadonlyLocalMediaCatalog(tmp_path, snapshot())
    with Image.open(BytesIO(catalog.get("101", 0).data)) as converted:
        assert converted.mode == "RGBA"
        assert converted.getpixel((0, 0))[3] == 0
    catalog.close()


def test_cache_counts_a_single_blob_after_concurrent_decode(tmp_path, monkeypatch):
    folder = tmp_path / "作者(12)"
    folder.mkdir()
    (folder / "題名(101)_1.png").write_bytes(png("red"))
    catalog = ReadonlyLocalMediaCatalog(tmp_path, snapshot())
    catalog._ensure_index()
    both_ready = Event()
    release = Event()
    real_checked_open = catalog._checked_open
    from contextlib import contextmanager
    from threading import Lock
    counter_lock = Lock()
    count = 0

    @contextmanager
    def synchronized_open(path):
        nonlocal count
        with counter_lock:
            count += 1
            if count == 2:
                both_ready.set()
        assert release.wait(5)
        with real_checked_open(path) as opened:
            yield opened

    monkeypatch.setattr(catalog, "_checked_open", synchronized_open)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(catalog.get, "101", 0) for _ in range(2)]
        assert both_ready.wait(5)
        release.set()
        blobs = [future.result(timeout=5) for future in futures]
    assert blobs[0] is blobs[1]
    assert catalog._cache_bytes == len(blobs[0].data)
    assert len(catalog._cache) == 1
    catalog.close()


def test_replace_snapshot_rejects_obsolete_decoding_and_clears_cache(tmp_path, monkeypatch):
    folder = tmp_path / "作者(12)"
    folder.mkdir()
    (folder / "題名(101)_1.png").write_bytes(png("red"))
    catalog = ReadonlyLocalMediaCatalog(tmp_path, snapshot())
    catalog._ensure_index()
    entered = Event()
    release = Event()
    real_checked_open = catalog._checked_open
    from contextlib import contextmanager

    @contextmanager
    def blocked_open(path):
        entered.set()
        assert release.wait(5)
        with real_checked_open(path) as source:
            yield source

    monkeypatch.setattr(catalog, "_checked_open", blocked_open)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(catalog.get, "101", 0)
        assert entered.wait(5)
        catalog.replace_snapshot(LibrarySnapshot(()))
        release.set()
        with pytest.raises(MediaUnavailable, match="obsolete"):
            pending.result(timeout=5)
    assert not catalog._cache and catalog._cache_bytes == 0
    catalog.close()


def test_index_construction_never_holds_ui_url_lock(tmp_path, monkeypatch):
    folder = tmp_path / "作者(12)"
    folder.mkdir()
    (folder / "題名(101)_1.png").write_bytes(png("red"))
    catalog = ReadonlyLocalMediaCatalog(tmp_path, snapshot())
    entered, release = Event(), Event()
    real_index = catalog._index

    def blocked_index(works):
        entered.set()
        assert release.wait(5)
        return real_index(works)

    monkeypatch.setattr(catalog, "_index", blocked_index)
    with ThreadPoolExecutor(max_workers=2) as executor:
        get_result = executor.submit(catalog.get, "101", 0)
        try:
            assert entered.wait(5)
            # URL validation is called from NiceGUI's event loop. It must not
            # wait for a ZIP index being prepared in another HTTP worker.
            url_result = executor.submit(catalog.url, "101", 0, "thumb")
            assert url_result.result(timeout=2) == "/media/101/0/thumb.png"
        finally:
            release.set()
        assert get_result.result(timeout=5).data
    catalog.close()


def test_index_replaced_during_scan_cannot_publish_obsolete_locations(tmp_path, monkeypatch):
    folder = tmp_path / "作者(12)"
    folder.mkdir()
    (folder / "題名(101)_1.png").write_bytes(png("red"))
    catalog = ReadonlyLocalMediaCatalog(tmp_path, snapshot())
    entered, release = Event(), Event()
    original_index = catalog._index

    def blocked_index(works):
        entered.set()
        assert release.wait(5)
        return original_index(works)

    monkeypatch.setattr(catalog, "_index", blocked_index)
    with ThreadPoolExecutor(max_workers=1) as executor:
        get_result = executor.submit(catalog.get, "101", 0)
        try:
            assert entered.wait(5)
            catalog.replace_snapshot(LibrarySnapshot(()))
        finally:
            release.set()
        with pytest.raises(MediaUnavailable, match="obsolete"):
            get_result.result(timeout=5)
    assert catalog._locations == {} and not catalog._indexed
    catalog.close()


def test_oversize_decoded_dimensions_are_unavailable(tmp_path, monkeypatch):
    folder = tmp_path / "作者(12)"
    folder.mkdir()
    (folder / "題名(101)_1.png").write_bytes(png("red"))
    import pixivvault_nicegui.local_media_adapter as local_media
    monkeypatch.setattr(local_media, "_MAX_PIXELS", 100)
    catalog = ReadonlyLocalMediaCatalog(tmp_path, snapshot())
    with pytest.raises(MediaUnavailable, match="dimension"):
        catalog.get("101", 0)
    catalog.close()


def test_capacity_fixture_flat_name_and_unrelated_zip_member(tmp_path):
    folder=tmp_path/'fixture(12)'
    folder.mkdir()
    (folder/'Fixture work(101).png').write_bytes(png('red'))
    with ZipFile(tmp_path/'fixture(12).zip','w') as archive:
        archive.writestr('capacity-only.bin',b'fixture')
    catalog=ReadonlyLocalMediaCatalog(tmp_path,snapshot())
    try:
        assert catalog.get('101',0).data
    finally:catalog.close()
