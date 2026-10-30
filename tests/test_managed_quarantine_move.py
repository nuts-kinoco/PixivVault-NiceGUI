"""P3-346 explicit physical relocation only on generated owned managed roots.

All file mutations are confined to pytest tmp_path. The legacy source is never used.
"""
import hashlib
import os
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from pixivvault_nicegui.managed_downloads import ManagedDownloadError, ManagedPixivDownloads
from test_managed_quarantine import owned, plan_for, record


def _source(item, writer):
    return writer.root / item.relative_receipt.rsplit("/", 1)[0] / f"page-{item.page}.png"


def _copy(item, writer, operation_id):
    return writer.root / "quarantine" / "operations" / operation_id / f"{item.work_id}-p{item.page}.png"


def test_copy_still_default_and_does_not_evict_owned_source(owned):
    writer, source, _ = owned
    plan = plan_for(source, "123:2")
    item = plan.items[0]
    physical = _source(item, writer)
    before = physical.read_bytes()
    result = source.execute(plan, operation_id="a" * 32)
    assert result.committed == (item,)
    assert physical.read_bytes() == before
    assert _copy(item, writer, result.operation_id).read_bytes() == before
    assert writer.visible_source_pages("123") == (0, 2)


def test_explicit_move_releases_only_selected_original_then_restores(owned):
    writer, source, remote = owned
    plan = plan_for(source, "123:2")
    item = plan.items[0]
    target = _source(item, writer)
    original = target.read_bytes()
    untouched = {
        path: path.read_bytes()
        for path in target.parent.iterdir()
        if path != target and path.is_file()
    }
    with pytest.raises(ManagedDownloadError):
        source.execute(plan, operation_id="b" * 32, mode="move", confirmed_move=False)
    assert target.read_bytes() == original
    result = source.execute(plan, operation_id="b" * 32, mode="move", confirmed_move=True)
    assert result.committed == (item,)
    assert not target.exists()
    assert _copy(item, writer, result.operation_id).read_bytes() == original
    assert {path: path.read_bytes() for path in untouched} == untouched
    assert writer.visible_source_pages("123") == (0, 2)
    assert len(writer.saved_receipts()) == 4
    restarted = ManagedPixivDownloads(writer.root, remote, resolve_record=record)
    try:
        assert restarted.visible_source_pages("123") == (0, 2)
        assert len(restarted.saved_receipts()) == 4
        with pytest.raises(ManagedDownloadError):
            restarted.read_saved_page(item.relative_receipt, 1, item.receipt_sha256)
        before = restarted._generations.state_snapshot()
        restarted.quarantine.restore(result.operation_id, expected_revision=before["revision"])
        assert restarted.visible_source_pages("123") == (0, 1, 2)
        assert target.read_bytes() == original
        assert _copy(item, writer, result.operation_id).read_bytes() == original
        assert {path: path.read_bytes() for path in untouched} == untouched
        assert len(restarted.saved_receipts()) == 4
    finally:
        restarted.close()


def test_partial_relocation_failure_never_claims_moved_and_can_restore(owned, monkeypatch):
    writer, source, _ = owned
    plan = plan_for(source, "123:2", "223:1")
    originals = {_source(i, writer): _source(i, writer).read_bytes() for i in plan.items}
    original_relocate = writer.quarantine._relocate_verified_page
    calls = []

    def fails_second(item, operation_id):
        calls.append(item.key)
        if item.work_id == "223":
            raise OSError("synthetic second relocation failure")
        return original_relocate(item, operation_id)

    monkeypatch.setattr(writer.quarantine, "_relocate_verified_page", fails_second)
    with pytest.raises((ManagedDownloadError, OSError)):
        source.execute(plan, operation_id="c" * 32, mode="move", confirmed_move=True)
    assert calls == ["123:2", "223:1"]
    state = writer._generations.state_snapshot()
    assert state["operations"]["c" * 32]["state"] == "relocating"
    assert writer.visible_source_pages("123") == (0, 2)
    assert writer.visible_source_pages("223") == ()
    writer.quarantine.restore("c" * 32, expected_revision=state["revision"])
    assert writer.visible_source_pages("123") == (0, 1, 2)
    assert writer.visible_source_pages("223") == (0,)
    for path, before in originals.items():
        assert path.read_bytes() == before


def test_move_rejects_stale_revision_without_new_physical_mutation(owned):
    writer, source, _ = owned
    plan = plan_for(source, "123:2")
    path = _source(plan.items[0], writer)
    original = path.read_bytes()
    source.execute(plan, operation_id="d" * 32)
    with pytest.raises((ManagedDownloadError, ValueError)):
        writer.quarantine.execute(plan, operation_id="e" * 32,
                                  mode="move", confirmed_move=True)
    assert path.read_bytes() == original


def test_move_rejects_foreign_and_linked_source(owned):
    writer, source, _ = owned
    plan = plan_for(source, "123:2")
    item = plan.items[0]
    path = _source(item, writer)
    alias = path.parent / "owned-hardlink.png"
    try:
        os.link(path, alias)
    except (OSError, NotImplementedError):
        pytest.skip("hardlink creation unsupported on synthetic test filesystem")
    try:
        try:
            result = source.execute(plan, operation_id="f" * 32,
                                    mode="move", confirmed_move=True)
        except (ManagedDownloadError, ValueError):
            pass
        else:
            assert not result.committed and result.failed == (item,)
        assert path.exists() and alias.exists()
    finally:
        alias.unlink()


def test_crash_after_atomic_rename_recovers_from_durable_relocation(owned, monkeypatch):
    writer, source, remote = owned
    plan = plan_for(source, "123:2")
    item = plan.items[0]
    original = _source(item, writer).read_bytes()
    transition = writer._generations.transition_quarantine

    def crash_at_final_commit(operation_id, *, expected_revision, state):
        if state == "moved":
            raise OSError("synthetic crash after owned rename")
        return transition(operation_id, expected_revision=expected_revision, state=state)

    monkeypatch.setattr(writer._generations, "transition_quarantine", crash_at_final_commit)
    with pytest.raises(OSError, match="synthetic crash"):
        source.execute(plan, operation_id="1" * 32, mode="move", confirmed_move=True)
    assert not _source(item, writer).exists()
    assert _copy(item, writer, "1" * 32).read_bytes() == original
    # A fresh process can validate the pending index and read visible pages.
    restarted = ManagedPixivDownloads(writer.root, remote, resolve_record=record)
    try:
        assert restarted._generations.state_snapshot()["operations"]["1" * 32]["state"] == "relocating"
        assert len(restarted.saved_receipts()) == 4
        assert restarted.visible_source_pages("123") == (0, 2)
        current = restarted._generations.state_snapshot()
        restarted.quarantine.restore("1" * 32, expected_revision=current["revision"])
        assert _source(item, restarted).read_bytes() == original
        assert restarted.visible_source_pages("123") == (0, 1, 2)
    finally:
        restarted.close()


def test_restore_rejects_reappeared_source_without_overwrite(owned):
    writer, source, _ = owned
    plan = plan_for(source, "123:2")
    item = plan.items[0]
    result = source.execute(plan, operation_id="2" * 32,
                            mode="move", confirmed_move=True)
    target = _source(item, writer)
    assert not target.exists()
    target.write_bytes(b"foreign source collision")
    with pytest.raises(ManagedDownloadError):
        writer.quarantine.restore(result.operation_id,
                                  expected_revision=result.state_revision)
    assert target.read_bytes() == b"foreign source collision"
    assert _copy(item, writer, result.operation_id).exists()


def test_copy_v3_record_retained_when_new_v4_move_is_added(owned):
    writer, source, remote = owned
    first = source.execute(plan_for(source, "123:2"), operation_id="3" * 32)
    assert writer._generations.state_snapshot()["operations"][first.operation_id]["state"] == "committed"
    moved_plan = plan_for(source, "223:1")
    moved = source.execute(moved_plan, operation_id="4" * 32,
                           mode="move", confirmed_move=True)
    state = writer._generations.state_snapshot()
    assert state["operations"][first.operation_id]["mode"] == "copy"
    assert state["operations"][moved.operation_id]["mode"] == "move"
    assert state["operations"][moved.operation_id]["state"] == "moved"
    assert writer.visible_source_pages("123") == (0, 2)
    assert writer.visible_source_pages("223") == ()
    restarted = ManagedPixivDownloads(writer.root, remote, resolve_record=record)
    try:
        assert len(restarted.saved_receipts()) == 4
        restarted.quarantine.restore(moved.operation_id,
                                     expected_revision=state["revision"])
        current = restarted._generations.state_snapshot()
        restarted.quarantine.restore(first.operation_id,
                                     expected_revision=current["revision"])
        assert restarted.visible_source_pages("123") == (0, 1, 2)
        assert restarted.visible_source_pages("223") == (0,)
    finally:
        restarted.close()


@pytest.mark.asyncio
async def test_gui_confirmation_defaults_to_copy_and_requires_separate_move_choice(owned):
    from nicegui import Client, ui
    from pixivvault_nicegui.ui.duplicate_view import DuplicateView

    writer, source, _ = owned
    client = Client(ui.page("/_p346_move_checkbox"))
    try:
        with client:
            view = DuplicateView(source)
            view.set_active(True)
            await view.scan()
            view.set_mode("near")
            item = next(item for group in view.scan_result.groups for item in group.items
                        if item.key == "123:2")
            group_id = next(g.group_id for g in view.scan_result.groups
                            if item in g.items)
            view.toggle(group_id, item.key, True, view.selection_revision)
            await view.confirm(group_id, view.selection_revision)
            assert not view.relocation_choice.value
            view.relocation_choice.set_value(True)
            await view.execute_confirmation()
            assert not _source(item, writer).exists()
            assert source.owner._generations.state_snapshot()["operations"][
                view._last_result.operation_id]["state"] == "moved"
            assert "物理移動" in view.notice.text
            await view.open_trash()
            assert len(view._trash_moves) == 1
            move = view._trash_moves[0]
            assert move[0] == view._last_result.operation_id
            view.confirm_restore(move)
            assert view.restore_dialog.value
            await view.execute_restore()
            assert _source(item, writer).exists()
            assert writer.visible_source_pages("123") == (0, 1, 2)
            assert view._trash_moves == ()
    finally:
        client.delete()


def test_relocation_never_excuses_an_unselected_missing_page(owned):
    writer, source, _ = owned
    plan = plan_for(source, "123:2")
    source.execute(plan, operation_id="5" * 32, mode="move", confirmed_move=True)
    other = (_source(plan.items[0], writer)).with_name("page-1.png")
    assert other.exists()
    other.unlink()  # Only the newly generated test fixture, never real data.
    with pytest.raises(ManagedDownloadError):
        writer.saved_receipts()
    with pytest.raises(ManagedDownloadError):
        writer.quarantine.restore(
            "5" * 32,
            expected_revision=writer._generations.state_snapshot()["revision"])


def test_unrelated_owned_work_generation_can_publish_while_move_is_active(owned):
    writer, source, _ = owned
    source.execute(plan_for(source, "123:2"), operation_id="6" * 32,
                   mode="move", confirmed_move=True)
    relative, receipt, digest, _, _ = writer.verified_saved_receipt("224")
    source_folder = writer.root / relative.rsplit("/", 1)[0]
    dest = writer.generation_outputs / ("work-224-" + uuid4().hex)
    shutil.copytree(source_folder, dest)
    target = f"generations/{dest.name}/receipt.json"
    writer._generations.prepare_for_publication()
    token = writer._generations.work_state_token("224")
    published = writer._generations.publish(
        "224", target, hashlib.sha256((dest / "receipt.json").read_bytes()).hexdigest(),
        "2026-10-30T00:00:00+00:00", digest, relative,
        expected_work_state_token=token)
    assert published["relative_receipt"] == target
    assert writer.visible_source_pages("224") == (0,)
    assert writer.visible_source_pages("123") == (0, 2)
    assert writer._generations.state_snapshot()["operations"]["6" * 32]["state"] == "moved"

