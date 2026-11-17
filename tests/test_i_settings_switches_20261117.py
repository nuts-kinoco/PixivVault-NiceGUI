"""FL-C20/C21/A12: 設定画面の自動実行スイッチ（元の名前・位置・説明文）と起動配線。"""
import asyncio
import inspect

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.preferences import PreferencesStore
from pixivvault_nicegui.ui.preferences_view import PreferencesView, DESCRIPTIONS


def descendants_text(view):
    return [e.text for e in view.dialog.descendants() if isinstance(getattr(e, "text", None), str)]


@pytest.mark.asyncio
async def test_metadata_switch_in_settings_has_original_label_description_and_saves(tmp_path):
    store = PreferencesStore(tmp_path / "p.sqlite3")
    changed = []
    client = Client(ui.page("/_i_prefs"))
    try:
        with client:
            view = PreferencesView(store, enabled_keys={"meta_backfill_enabled"},
                                   on_change=lambda snap: changed.append(snap))
            view.open()
            control = view.controls["meta_backfill_enabled"]
            assert control.value == "1" and control.enabled
            assert control.props["label"] == "メタデータ（ブックマーク数など）をバックグラウンドで自動的に埋める"
            texts = descendants_text(view)
            assert "作品メタデータ" in texts
            assert DESCRIPTIONS["meta_backfill_enabled"] in texts
            assert "ブックマーク数・いいね数・閲覧数" in DESCRIPTIONS["meta_backfill_enabled"]
            control.set_value("0")
            assert view.save() is True
            assert store.snapshot().values["meta_backfill_enabled"] == "0"
            assert changed, "保存すると適用先（services.apply_preferences）へ知らせる"
    finally:
        view.close()
        client.delete()


@pytest.mark.asyncio
async def test_unavailable_when_no_pixiv_source(tmp_path):
    store = PreferencesStore(tmp_path / "p.sqlite3")
    client = Client(ui.page("/_i_prefs2"))
    try:
        with client:
            view = PreferencesView(store, enabled_keys=set())
            view.open()
            assert not view.controls["meta_backfill_enabled"].enabled
    finally:
        view.close()
        client.delete()


def test_startup_schedules_the_metadata_auto_loop_only_through_the_guarded_entry():
    from pixivvault_nicegui import bootstrap
    source = inspect.getsource(bootstrap)
    assert "app.on_startup(services.start_metadata_auto)" in source


@pytest.mark.asyncio
async def test_start_metadata_auto_is_refused_without_a_pixiv_source_or_store(tmp_path):
    from pixivvault_nicegui.services import ApplicationServices
    services = ApplicationServices()
    try:
        assert services.start_metadata_auto() is False
        assert "meta_backfill_enabled" not in services.preference_consumers()
    finally:
        await asyncio.to_thread(services.close)
