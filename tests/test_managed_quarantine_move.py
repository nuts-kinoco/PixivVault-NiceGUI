"""P3-346 explicit physical relocation only on generated owned managed roots.

All file mutations are confined to pytest tmp_path. The legacy source is never used.
"""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
from threading import Event, Thread
from uuid import uuid4

import pytest

from pixivvault_nicegui.managed_downloads import ManagedDownloadError, ManagedPixivDownloads
from pixivvault_nicegui.duplicate_inspection import ManagedDuplicateInspection
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


def test_last_moment_source_swap_cannot_destroy_verified_backup(owned, monkeypatch):
    """An external path swap just before relocation must remain recoverable."""
    writer, source, remote = owned
    plan = plan_for(source, "123:2")
    item = plan.items[0]
    physical = _source(item, writer)
    original = physical.read_bytes()
    operation_id = "7" * 32
    discard = writer.quarantine._discard_verified_source
    injected = []

    def before_discard(*args):
        physical.write_bytes(b"synthetic path swap before relocation")
        injected.append(True)
        return discard(*args)

    monkeypatch.setattr(writer.quarantine, "_discard_verified_source", before_discard)
    with pytest.raises(ManagedDownloadError):
        source.execute(plan, operation_id=operation_id,
                       mode="move", confirmed_move=True)
    assert injected == [True]
    assert _copy(item, writer, operation_id).read_bytes() == original
    state = writer._generations.state_snapshot()
    assert state["operations"][operation_id]["state"] == "relocating"
    restarted = ManagedPixivDownloads(writer.root, remote, resolve_record=record)
    try:
        if physical.exists():
            assert physical.read_bytes() == b"synthetic path swap before relocation"
            with pytest.raises(ManagedDownloadError):
                restarted.saved_receipts()
            with pytest.raises(ManagedDownloadError):
                restarted.quarantine.restore(
                    operation_id, expected_revision=state["revision"])
            # The unexpected source is an owned synthetic fixture; restore
            # may proceed only after explicit removal of this collision.
            physical.unlink()
            state = restarted._generations.state_snapshot()
        assert len(restarted.saved_receipts()) == 4
        restarted.quarantine.restore(
            operation_id, expected_revision=state["revision"])
        assert physical.read_bytes() == original
        assert restarted.visible_source_pages("123") == (0, 1, 2)
    finally:
        restarted.close()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows share-mode enforcement")
def test_backup_is_pinned_against_external_write_during_source_deletion(owned, monkeypatch):
    writer, source, _ = owned
    plan = plan_for(source, "123:2")
    item = plan.items[0]
    original = _source(item, writer).read_bytes()
    operation_id = "8" * 32
    backup = _copy(item, writer, operation_id)
    delete = writer.quarantine._discard_pinned_windows
    writes = []

    def attack_during_delete(*args):
        try:
            backup.write_bytes(b"synthetic backup corruption")
        except OSError:
            writes.append("blocked")
        else:
            writes.append("unexpectedly writable")
        return delete(*args)

    monkeypatch.setattr(writer.quarantine, "_discard_pinned_windows", attack_during_delete)
    result = source.execute(plan, operation_id=operation_id,
                            mode="move", confirmed_move=True)
    assert result.committed == (item,)
    assert writes == ["blocked"]
    assert backup.read_bytes() == original
    assert not _source(item, writer).exists()
    writer.quarantine.restore(operation_id, expected_revision=result.state_revision)
    assert _source(item, writer).read_bytes() == original


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


def test_two_writers_cannot_commit_a_stale_index_over_a_relocation(owned, monkeypatch):
    """A competing root writer must serialize before reading its CAS state."""
    writer, source, remote = owned
    writer._generations.prepare_for_quarantine()
    second = ManagedPixivDownloads(writer.root, remote, resolve_record=record)
    observer = ManagedDuplicateInspection(second)
    source_plan = plan_for(source, "123:2")
    other_plan = plan_for(observer, "223:1")
    original = _source(source_plan.items[0], writer).read_bytes()
    entered, release, first_finished = Event(), Event(), Event()
    errors = []
    results = []
    commit = second._generations._commit_packet

    def suspended_commit(packet):
        entered.set()
        assert release.wait(12)
        return commit(packet)

    monkeypatch.setattr(second._generations, "_commit_packet", suspended_commit)

    def competing_copy():
        try:
            results.append(observer.execute(other_plan, operation_id="9" * 32))
        except Exception as exc:
            errors.append(exc)

    def competing_move():
        try:
            source.execute(source_plan, operation_id="a" * 32,
                           mode="move", confirmed_move=True)
        except (ManagedDownloadError, ValueError):
            pass  # Another writer's successful commit invalidates this plan.
        finally:
            first_finished.set()

    later = Thread(target=competing_copy, daemon=True)
    earlier = Thread(target=competing_move, daemon=True)
    try:
        later.start()
        assert entered.wait(5), "second writer never reached the commit barrier"
        earlier.start()
        # With a root fence, A cannot delete the physical source while B has
        # admitted an index update. Without it, the old move finishes first.
        assert not first_finished.wait(0.5), "physical move raced an index writer"
    finally:
        release.set()
        later.join(10)
        earlier.join(10)
        observer.close()
        second.close()
    assert not later.is_alive() and not earlier.is_alive()
    assert not errors and len(results) == 1
    assert _source(source_plan.items[0], writer).read_bytes() == original
    assert writer.visible_source_pages("123") == (0, 1, 2)
    assert writer._generations.state_snapshot()["operations"]["9" * 32]["state"] == "committed"


def test_managed_root_writer_fence_is_shared_across_processes(owned):
    """A separate process cannot enter the same root's writer transaction."""
    from pixivvault_nicegui.managed_root_writer import writer_fence

    writer, _, _ = owned
    script = (
        "from pathlib import Path\n"
        "import sys\n"
        "from pixivvault_nicegui.managed_root_writer import writer_fence\n"
        "print('READY', flush=True)\n"
        "with writer_fence(Path(sys.argv[1])):\n"
        "    print('ACQUIRED', flush=True)\n"
    )
    env = os.environ.copy()
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1] / 'src')
    child = None
    try:
        with writer_fence(writer.root):
            child = subprocess.Popen(
                [sys.executable, '-B', '-c', script, str(writer.root)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=env)
            assert child.stdout.readline().strip() == b'READY'
            with pytest.raises(subprocess.TimeoutExpired):
                child.wait(timeout=0.3)
        out, err = child.communicate(timeout=10)
        assert child.returncode == 0, err.decode(errors='replace')
        assert out.strip() == b'ACQUIRED'
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.communicate(timeout=10)


def test_writer_fence_preserves_body_oserror_and_other_root_progress(owned, tmp_path):
    from pixivvault_nicegui.managed_root_writer import writer_fence

    writer, _, remote = owned
    other = ManagedPixivDownloads(tmp_path / 'separate-managed', remote, resolve_record=record)
    entered, release, unrelated = Event(), Event(), Event()

    def hold_first():
        with writer_fence(writer.root):
            entered.set()
            assert release.wait(5)

    def enter_other():
        with writer_fence(other.root):
            unrelated.set()

    first = Thread(target=hold_first, daemon=True)
    second = Thread(target=enter_other, daemon=True)
    try:
        first.start()
        assert entered.wait(3)
        second.start()
        assert unrelated.wait(0.5), 'different managed roots unexpectedly serialized'
    finally:
        release.set()
        first.join(5)
        second.join(5)
        other.close()
    assert not first.is_alive() and not second.is_alive()
    with pytest.raises(OSError, match='synthetic body failure'):
        with writer_fence(writer.root):
            raise OSError('synthetic body failure')

