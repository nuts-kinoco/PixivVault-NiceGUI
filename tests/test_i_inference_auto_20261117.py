"""FL-C21/A12: 絵柄・構図の推論の自動起動（既定ON）、設定スイッチ、リセット/再スキャン。

推論そのものは偽の解析関数に差し替える。モデル・GPU・Pixivには触れない。
"""
import asyncio
from threading import Event
import time
from types import SimpleNamespace

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.egara_inference import EgaraInferenceError
from pixivvault_nicegui.preferences import SPEC_BY_KEY
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.similarity_worker import SimilarityWorker
from test_i_metadata_auto_20261117 import Source, wait_terminal


def wait_for(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_spec_defaults_on_with_original_name_and_group():
    spec = SPEC_BY_KEY["embed_auto_on_download"]
    assert spec.default == "1" and spec.choices == ("0", "1")
    assert spec.label == "ダウンロードした画像を、バックグラウンドで自動的に推論する"
    assert spec.group == "絵柄・構図の推論"


# --- ワーカー: 自動が OFF のとき、時間経過での再走査はしない ---

def test_worker_without_auto_does_not_rescan_on_its_own_but_obeys_wake():
    scans = []

    def pages():
        scans.append(1)
        return iter(())
    worker = SimilarityWorker(pages, lambda *a, **k: True, idle_seconds=0.05, auto=lambda: False)
    worker.start()
    try:
        assert wait_for(lambda: len(scans) == 1)
        time.sleep(0.4)
        assert len(scans) == 1, "自動 OFF では手動で起こしたあとも、勝手に再走査しない"
        worker.wake()
        assert wait_for(lambda: len(scans) == 2)
    finally:
        worker.stop(timeout=5)


def test_worker_with_auto_rescans_periodically():
    scans = []

    def pages():
        scans.append(1)
        return iter(())
    worker = SimilarityWorker(pages, lambda *a, **k: True, idle_seconds=0.05, auto=lambda: True)
    worker.start()
    try:
        assert wait_for(lambda: len(scans) >= 3)
    finally:
        worker.stop(timeout=5)


# --- Services ---

async def make_similarity_services(tmp_path, *, pref="1", model=True, analyze=None):
    from test_i_metadata_auto_20261117 import make_services
    services = await make_services(tmp_path)
    if pref != "1":
        snap = services.preferences.snapshot()
        services.preferences.update({"embed_auto_on_download": pref}, expected_revision=snap.revision)
    model_file = tmp_path / "model" / "egara.onnx"
    model_file.parent.mkdir()
    model_file.write_bytes(b"not a real model")
    services.configure_similarity_index(tmp_path / "similarity", model_file=model_file if model else None,
                                        provider="cpu")
    seen = []

    def fake_analyze(work_id, page, *, is_current=None):
        seen.append((work_id, page))
        if analyze is not None:
            return analyze(work_id, page)
        return True
    services.analyze_saved_for_similarity = fake_analyze
    services._similarity_idle_seconds = 0.05
    return services, seen


@pytest.mark.asyncio
async def test_auto_on_by_default_starts_at_startup_and_analyzes_saved_pages(tmp_path):
    services, seen = await make_similarity_services(tmp_path)
    try:
        assert services.similarity_auto_enabled() is True
        assert services.start_similarity_auto() is True
        assert await asyncio.to_thread(wait_for, lambda: len({w for w, _ in seen}) == 3)
        assert services.similarity_analysis_status()["auto"] is True
    finally:
        await asyncio.to_thread(services.close)


@pytest.mark.asyncio
async def test_auto_off_means_nothing_runs_at_startup(tmp_path):
    services, seen = await make_similarity_services(tmp_path, pref="0")
    try:
        assert services.similarity_auto_enabled() is False
        assert services.start_similarity_auto() is False
        await asyncio.sleep(0.4)
        assert seen == [] and services._similarity_worker is None
        assert services.similarity_analysis_status()["auto"] is False
    finally:
        await asyncio.to_thread(services.close)


@pytest.mark.asyncio
async def test_startup_without_model_is_a_quiet_noop(tmp_path):
    services, seen = await make_similarity_services(tmp_path, model=False)
    try:
        assert services.start_similarity_auto() is False
        assert services._similarity_worker is None and seen == []
        assert "embed_auto_on_download" not in services.preference_consumers()
    finally:
        await asyncio.to_thread(services.close)


@pytest.mark.asyncio
async def test_off_with_manual_start_does_not_pick_up_new_pages_by_itself(tmp_path):
    services, seen = await make_similarity_services(tmp_path, pref="0")
    try:
        await asyncio.to_thread(services.start_similarity_analysis)
        assert await asyncio.to_thread(wait_for, lambda: len({w for w, _ in seen}) == 3)
        base = services._similarity_pages
        extra = [("999", 1)]
        services._similarity_pages = lambda: tuple(base()) + tuple(extra)
        # 構築済みのワーカーは古い参照を持つので、新しい一覧を見るワーカーとして作り直す
        services._similarity_worker.stop(timeout=5)
        services._similarity_worker = None
        await asyncio.to_thread(services.start_similarity_analysis)
        assert await asyncio.to_thread(wait_for, lambda: ("999", 1) in seen)
        count = len(seen)
        extra.append(("1000", 1))
        await asyncio.sleep(0.5)
        assert ("1000", 1) not in seen and len(seen) == count, "OFF では勝手に拾わない"
        await asyncio.to_thread(services.rescan_similarity_analysis)
        assert await asyncio.to_thread(wait_for, lambda: ("1000", 1) in seen)
    finally:
        await asyncio.to_thread(services.close)


@pytest.mark.asyncio
async def test_toggle_applies_at_once_off_pauses_on_resumes(tmp_path):
    services, seen = await make_similarity_services(tmp_path)
    try:
        services.start_similarity_auto()
        assert await asyncio.to_thread(wait_for, lambda: len(seen) >= 3)
        snap = services.preferences.snapshot()
        services.preferences.update({"embed_auto_on_download": "0"}, expected_revision=snap.revision)
        services.apply_preferences()
        assert services.similarity_analysis_status()["phase"] == "paused"
        snap = services.preferences.snapshot()
        services.preferences.update({"embed_auto_on_download": "1"}, expected_revision=snap.revision)
        services.apply_preferences()
        assert services.similarity_analysis_status()["phase"] in ("idle", "running")
    finally:
        await asyncio.to_thread(services.close)


@pytest.mark.asyncio
async def test_toggle_on_when_never_started_starts_without_restart(tmp_path):
    services, seen = await make_similarity_services(tmp_path, pref="0")
    try:
        assert services.start_similarity_auto() is False
        snap = services.preferences.snapshot()
        services.preferences.update({"embed_auto_on_download": "1"}, expected_revision=snap.revision)
        services.apply_preferences()
        assert await asyncio.to_thread(wait_for, lambda: len({w for w, _ in seen}) == 3)
    finally:
        await asyncio.to_thread(services.close)


@pytest.mark.asyncio
async def test_reset_resumes_a_failed_worker_and_rescan_runs_a_fresh_pass(tmp_path):
    state = {"fail": True}

    def analyze(work_id, page):
        if state["fail"]:
            raise EgaraInferenceError("synthetic")
        return True
    services, seen = await make_similarity_services(tmp_path, analyze=analyze)
    try:
        services.start_similarity_auto()
        assert await asyncio.to_thread(wait_for, lambda: services.similarity_analysis_status()["phase"] == "failed")
        state["fail"] = False
        assert await asyncio.to_thread(services.reset_similarity_analysis) is True
        assert await asyncio.to_thread(wait_for, lambda: services.similarity_analysis_status()["indexed"] >= 3)
        before = services.similarity_analysis_status()["examined"]
        await asyncio.to_thread(services.rescan_similarity_analysis)
        assert await asyncio.to_thread(
            wait_for, lambda: services.similarity_analysis_status()["examined"] > before)
    finally:
        await asyncio.to_thread(services.close)


def test_startup_is_wired_through_the_guarded_entry():
    import inspect
    from pixivvault_nicegui import bootstrap
    source = inspect.getsource(bootstrap)
    assert "app.on_startup(services.start_similarity_auto)" in source
    assert "app.on_startup(services.start_similarity_analysis)" not in source


# --- 解析ダイアログ: リセット・再スキャン ---

@pytest.mark.asyncio
async def test_similar_view_has_reset_and_rescan_behind_one_shot_tokens():
    from pixivvault_nicegui.ui.similar_view import SimilarView
    calls = []
    status = {"phase": "failed", "indexed": 1, "resumed": 0, "errors": 5, "examined": 6,
              "started": True, "auto": True}
    client = Client(ui.page("/_i_similar"))
    try:
        with client:
            view = SimilarView(SimpleNamespace(), lambda *a: None, analysis_status=lambda: dict(status),
                               start_analysis=lambda: calls.append("start"),
                               pause_analysis=lambda: calls.append("pause"),
                               resume_analysis=lambda: calls.append("resume"),
                               reset_analysis=lambda: calls.append("reset"),
                               rescan_analysis=lambda: calls.append("rescan"))
            view.dialog.open()
            await view.refresh_analysis_status()
            assert view.reset_button.text == "諦めたぶんを対象へ戻す"
            assert view.rescan_button.text == "数え直す"
            assert view.reset_button.enabled and not view.rescan_button.enabled
            event = view.control_event("reset")
            assert await view.run_analysis_control("reset", event)
            assert calls == ["reset"]
            assert not await view.run_analysis_control("reset", event), "トークンは 1 回きり"
            status.update(phase="idle", errors=0)
            await view.refresh_analysis_status()
            assert view.rescan_button.enabled
            assert await view.run_analysis_control("rescan", view.control_event("rescan"))
            assert calls == ["reset", "rescan"]
            assert "自動実行は ON" in view.analysis_text.text
            status.update(auto=False)
            await view.refresh_analysis_status()
            assert "自動実行は OFF です" in view.analysis_text.text
    finally:
        view.close()
        client.delete()


# --- profile 画面: 自動実行スイッチは設定へ移した（二重にしない） ---

@pytest.fixture
def isolated(tmp_path):
    from pixivvault_nicegui.test_owner_runtime import isolated_test_workspace
    (tmp_path / '.pixivvault-isolated-test-workspace').write_bytes(
        b'PixivVault isolated source test workspace v1\n')
    with isolated_test_workspace(tmp_path):
        yield tmp_path


@pytest.mark.asyncio
async def test_profile_view_has_no_second_auto_switch(isolated):
    from nicegui.page import page as Page
    from pixivvault_nicegui.native_profile import NativeProfileStore
    from pixivvault_nicegui.ui.native_profile_view import NativeProfileView
    store = NativeProfileStore(isolated / 'profile')
    client = Client(Page('/i-native-profile'))
    view = None
    try:
        with client:
            view = NativeProfileView(store)
            view.build()
            texts = [e.text for e in client.layout.descendants() if isinstance(getattr(e, 'text', None), str)]
            assert view.auto_switch is None
            assert not any('保存後に未解析ページを自動解析' in t for t in texts)
            assert any('絵柄・構図の推論' in t and 'すぐ反映' in t for t in texts)
            assert not any('変更は次回起動から適用します' in t and '自動' in t for t in texts)
            assert view.save() is True
            assert store.snapshot().similarity_auto is False
    finally:
        client.delete()
