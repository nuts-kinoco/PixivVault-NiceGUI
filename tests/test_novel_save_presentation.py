"""M4 off-loop presentation contract over frozen M3, using synthetic roots only."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from pixivvault_nicegui.bootstrap import parse_args, main
from pixivvault_nicegui.managed_novel_jobs import FixtureNovelSource
from pixivvault_nicegui.novel_save_presentation import (
    NovelPresentationError, NovelSavePresentation, build_novel_ui_fixture,
)
from pixivvault_nicegui.services import ApplicationServices


def wait(services, job_id, state="done"):
    with services.jobs._condition:
        assert services.jobs._condition.wait_for(
            lambda: any(j.job_id == job_id and j.state == state for j in services.jobs.snapshot()), timeout=10)
    return next(j for j in services.jobs.snapshot() if j.job_id == job_id)


@pytest.mark.parametrize("fmt", ["EPUB", "TXT", "BOTH"])
def test_preview_enqueue_saved_file_read_and_reopen_use_actual_outputs(tmp_path, fmt, monkeypatch):
    root = tmp_path / "novels"
    services = ApplicationServices(2, novel_fixture_root=root, novel_fixture_source=build_novel_ui_fixture())
    try:
        bridge = NovelSavePresentation(services)
        preview = bridge.preview("123", fmt)
        assert preview.title == "架空小説：青い栞" and "<script>" in preview.excerpt
        assert services.jobs.snapshot() == ()
        assert list((root / "exports" / "outputs").iterdir()) == []
        job = bridge.enqueue(preview, "ui-operation")
        done = wait(services, job.job_id)
        entries = bridge.library()
        assert len(entries) == 1 and entries[0].saved.relative_receipt == done.result
        text = bridge.read_saved(entries[0])
        assert "<script>" in text and "文字として表示" in text
        assert bridge.preview("123", fmt).saved == entries[0].saved
        with pytest.raises(NovelPresentationError):
            bridge.enqueue(bridge.preview("123", fmt), "duplicate-saved")
    finally:
        services.close()
    reopened = ApplicationServices(2, novel_fixture_root=root, novel_fixture_source=build_novel_ui_fixture())
    try:
        def no_fetch(*_):
            raise AssertionError("saved reading must not fetch fixture content again")
        monkeypatch.setattr(FixtureNovelSource, "fetch", no_fetch)
        bridge = NovelSavePresentation(reopened)
        entries = bridge.library()
        assert entries[0].title == "架空小説：青い栞"
        assert bridge.read_saved(entries[0]) == text
        assert len(list((root / "exports" / "outputs").iterdir())) == 1
    finally:
        reopened.close()


@pytest.mark.parametrize("raw", ["", "../123", "00123", "123\n", "https://www.pixiv.net/novel/show.php?id=123", "999"])
def test_unrecognized_input_is_refused_without_enqueue(tmp_path, raw):
    services = ApplicationServices(1, novel_fixture_root=tmp_path / "novels", novel_fixture_source=build_novel_ui_fixture())
    try:
        with pytest.raises(NovelPresentationError):
            NovelSavePresentation(services).preview(raw, "EPUB")
        assert services.jobs.snapshot() == ()
    finally:
        services.close()


def test_preview_authorship_and_content_cannot_be_substituted(tmp_path, monkeypatch):
    services = ApplicationServices(1, novel_fixture_root=tmp_path / "novels", novel_fixture_source=build_novel_ui_fixture())
    try:
        bridge = NovelSavePresentation(services)
        selection = bridge.preview("123", "TXT")
        original = FixtureNovelSource.fetch
        def changed(self, wid):
            body = original(self, wid)
            body["content"] = "different synthetic text"
            return body
        monkeypatch.setattr(FixtureNovelSource, "fetch", changed)
        with pytest.raises(NovelPresentationError):
            bridge.enqueue(selection, "substitution")
        assert services.jobs.snapshot() == ()
    finally:
        services.close()


def test_corrupt_output_never_becomes_readable_or_a_verified_library_row(tmp_path):
    services = ApplicationServices(1, novel_fixture_root=tmp_path / "novels", novel_fixture_source=build_novel_ui_fixture())
    try:
        bridge = NovelSavePresentation(services)
        job = bridge.enqueue(bridge.preview("123", "TXT"), "corruption")
        wait(services, job.job_id)
        entry = bridge.library()[0]
        folder = (services.novel_jobs.store.root / entry.saved.relative_receipt).parent
        (folder / "novel.txt").write_bytes(b"synthetic corrupt bytes")
        with pytest.raises(ValueError):
            bridge.read_saved(entry)
        with pytest.raises(ValueError):
            bridge.library()
    finally:
        services.close()


def test_cli_requires_isolated_absolute_fixture_root_without_creating_it(tmp_path, capsys):
    root = tmp_path / "novels"
    config = parse_args(["--novel-fixture-root", str(root)])
    assert config.novel_fixture_root == root and not root.exists()
    assert main(["--check", "--novel-fixture-root", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["download_storage"] == "novel-fixture"
    assert not root.exists()
    with pytest.raises(SystemExit):
        parse_args(["--novel-fixture-root", "relative"])
    with pytest.raises(SystemExit):
        parse_args(["--novel-fixture-root", str(root), "--pixiv-cookie-file", str(tmp_path / "not-read.txt")])
    with pytest.raises(SystemExit):
        parse_args(["--novel-fixture-root", str(root), "--download-sandbox", str(tmp_path / "images")])
