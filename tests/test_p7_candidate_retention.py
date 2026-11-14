"""P8: failed/missing selections remain reviewable; generated files only."""
from hashlib import sha256
from uuid import uuid4

import pytest

from test_required_legacy_duplicates import setup, select, png


def keys(scan):
    return {page.key for group in scan.groups for page in group.items}


def test_identical_bytes_at_replaced_container_do_not_bypass_plan_binding(tmp_path):
    source,legacy,owner,_,_,archive=setup(tmp_path)
    try:
        plan=select(source,{'2'})
        before=archive.read_bytes()
        archive.rename(tmp_path/'preserved-generated-archive.zip')
        archive.write_bytes(before)
        with pytest.raises(ValueError):
            source.execute(plan,operation_id=uuid4().hex,mode='move',confirmed_move=True)
        assert archive.read_bytes()==before and not legacy.state()['exclusions']
    finally:source.close();owner.close()


def plan_keys(source, selected):
    scan = source.latest_scan
    return source.plan(scan, {
        group.group_id: {page.key for page in group.items if page.key in selected}
        for group in scan.groups
    })


@pytest.mark.parametrize("condition", ["missing_file", "missing_folder", "non_file"])
def test_missing_selected_source_is_failed_and_other_items_remain_unprocessed(
        tmp_path, condition):
    source, legacy, owner, _, original, archive = setup(tmp_path, extra_zip=True)
    folder = original / "Author(91)"
    image = folder / "Folder(1).png"
    preserved = tmp_path / "preserved-generated-source"
    try:
        plan = select(source, {"1", "2", "5"})
        before_state = legacy.state()
        before_managed = owner._generations.state_snapshot()
        archives = {path: sha256(path.read_bytes()).hexdigest()
                    for path in (archive, original / "Third(93).zip")}
        if condition == "missing_folder":
            folder.rename(preserved)
        else:
            image.rename(preserved)
            if condition == "non_file":
                image.mkdir()
        result = source.execute(
            plan, operation_id=uuid4().hex, mode="move", confirmed_move=True)
        assert not result.committed
        assert [page.key for page in result.failed] == ["1:1"]
        assert {page.key for page in result.unprocessed} == {"2:1", "5:1"}
        assert {row.key: row.status for row in result.item_results} == {
            "1:1": "failed", "2:1": "unprocessed", "5:1": "unprocessed"}
        assert {"1:1", "2:1", "5:1"} <= keys(source.latest_scan)
        assert legacy.state() == before_state
        assert owner._generations.state_snapshot() == before_managed
        assert {path: sha256(path.read_bytes()).hexdigest() for path in archives} == archives

        # Restore only the generated fixture. A new explicit confirmation must
        # work after the cause is removed; no stale failed rollback is replayed.
        if condition == "missing_folder":
            preserved.rename(folder)
        else:
            if condition == "non_file":
                image.rename(tmp_path / "preserved-generated-directory")
            preserved.rename(image)
        source.recover_legacy()
        retry = plan_keys(source, {"1:1", "2:1", "5:1"})
        retried = source.execute(
            retry, operation_id=uuid4().hex, mode="move", confirmed_move=True)
        assert {page.key for page in retried.committed} == {"1:1", "2:1", "5:1"}
        assert not retried.failed and not retried.unprocessed
        assert all(row.status == "success" for row in retried.item_results)
    finally:
        source.close()
        owner.close()


def test_missing_survivor_keeps_selected_group_failed_without_moving_last_image(tmp_path):
    source, legacy, owner, _, original, archive = setup(tmp_path, extra_zip=True)
    survivor = original / "Author(91)" / "Peer(4).png"
    preserved = tmp_path / "preserved-generated-survivor.png"
    try:
        plan = select(source, {"1", "2", "5"})
        before_state = legacy.state()
        before_archive = archive.read_bytes()
        survivor.rename(preserved)
        result = source.execute(
            plan, operation_id=uuid4().hex, mode="move", confirmed_move=True)
        assert not result.committed
        assert [page.key for page in result.failed] == ["1:1"]
        assert {page.key for page in result.unprocessed} == {"2:1", "5:1"}
        assert {"1:1", "4:1", "2:1", "5:1"} <= keys(source.latest_scan)
        assert (original / "Author(91)" / "Folder(1).png").read_bytes() == png()
        assert archive.read_bytes() == before_archive
        assert legacy.state() == before_state
        preserved.rename(survivor)
        source.recover_legacy()
        retry = plan_keys(source, {"1:1"})
        retried = source.execute(
            retry, operation_id=uuid4().hex, mode="move", confirmed_move=True)
        assert [page.key for page in retried.committed] == ["1:1"]
        assert not retried.failed
    finally:
        source.close()
        owner.close()


def test_previous_failed_bridge_candidate_survives_another_group_success(tmp_path, monkeypatch):
    from pixivvault_nicegui import duplicate_inspection as module
    source, legacy, owner, _, _, archive = setup(tmp_path)
    try:
        values = iter((0, 0, 0, 15, 255))
        with monkeypatch.context() as patch:
            patch.setattr(module, "dhash", lambda _: next(values))
            plan = select(source, {"2", "3"})
        def fail(stage):
            if stage == "physical-installed:2:1":
                raise PermissionError("generated selected-item failure")
        with monkeypatch.context() as patch:
            patch.setattr(legacy, "_checkpoint", fail)
            first = source.execute(
                plan, operation_id=uuid4().hex, mode="move", confirmed_move=True)
        assert [page.key for page in first.failed] == ["2:1"]
        assert [page.key for page in first.committed] == ["3:1"]
        held = next(group for group in source.latest_scan.groups if group.author_id == "92")
        assert {page.key for page in held.items} == {"2:1", "3:2"} and not held.edges

        independent = plan_keys(source, {"1:1"})
        second = source.execute(
            independent, operation_id=uuid4().hex, mode="move", confirmed_move=True)
        assert [page.key for page in second.committed] == ["1:1"]
        assert {"2:1", "3:2"} <= keys(source.latest_scan)
        retry = plan_keys(source, {"2:1"})
        third = source.execute(
            retry, operation_id=uuid4().hex, mode="move", confirmed_move=True)
        assert [page.key for page in third.committed] == ["2:1"]
        assert not third.failed
    finally:
        source.close()
        owner.close()

