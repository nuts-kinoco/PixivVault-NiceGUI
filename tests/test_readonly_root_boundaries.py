"""Ownership checks must happen before any managed storage initialization."""
from pathlib import Path
import pytest

from pixivvault_nicegui.media_jobs import validate_readonly_boundaries
from pixivvault_nicegui.services import ApplicationServices


@pytest.mark.parametrize("relation", ["same", "child", "parent"])
def test_each_writable_and_readonly_pair_must_be_disjoint(tmp_path, relation):
    readonly = tmp_path / "legacy" / "images"
    writable = {"same": readonly, "child": readonly / "new", "parent": readonly.parent}[relation]
    with pytest.raises(ValueError, match="disjoint"):
        validate_readonly_boundaries((tmp_path / "safe", writable), (readonly,))
    assert not writable.exists()


def test_separate_readonly_roots_are_not_compared_to_each_other(tmp_path):
    validate_readonly_boundaries((tmp_path / "managed",), (tmp_path / "old", tmp_path / "old" / "images"))


@pytest.mark.parametrize("medium", ["novel_fixture_root", "ugoira_root"])
@pytest.mark.parametrize("relation", ["same", "child", "parent"])
def test_combined_managed_media_rejects_old_image_root_before_any_creation(tmp_path, medium, relation):
    old = tmp_path / "legacy" / "images"
    target = {"same": old, "child": old / "new-media", "parent": old.parent}[relation]
    options = {"novel_fixture_root": None, "novel_fixture_source": None,
               "ugoira_root": None, "ugoira_source": None}
    options[medium] = target
    options["novel_fixture_source" if medium == "novel_fixture_root" else "ugoira_source"] = object()
    with pytest.raises(ValueError, match="disjoint"):
        ApplicationServices(library_readonly_db=tmp_path / "db" / "copy.sqlite3",
            library_image_root=old, pixiv_download_root=tmp_path / "managed",
            live_client=object(), **options)
    assert not old.exists()
    assert not (tmp_path / "managed").exists()


def test_register_pages_rejects_overlap_before_client_or_service_init(tmp_path, monkeypatch):
    from pixivvault_nicegui import bootstrap
    monkeypatch.setattr(bootstrap, "_registered", False)
    old=tmp_path / "old"
    with pytest.raises(ValueError, match="disjoint"):
        bootstrap.register_pages(library_image_root=old, library_readonly_db=tmp_path / "db" / "lib.sqlite3",
                                 ugoira_root=old / "new", pixiv_download_root=tmp_path / "managed")
    assert not old.exists()
    assert not (tmp_path / "managed").exists()


def test_direct_export_configuration_rejects_legacy_diff_before_initialization(tmp_path):
    from types import SimpleNamespace
    old=tmp_path / "legacy-diff"
    service=SimpleNamespace(closing=False, managed_downloads=SimpleNamespace(root=tmp_path / "managed"),
        preferences=object(), library_image_root=None, library_readonly_db=None,
        legacy_diff_image_root=old, failed_queue_readonly_db=tmp_path / "old-db" / "copy.sqlite3")
    with pytest.raises(ValueError, match="disjoint"):
        ApplicationServices.configure_save_exports(service, old / "exports")
    assert not old.exists()
