from uuid import uuid4

import pytest

from test_required_legacy_duplicates import setup, select


def moved(tmp_path, work='1'):
    source, legacy, owner, _, original, archive = setup(tmp_path)
    target = original / 'Author(91)' / 'Folder(1).png' if work == '1' else archive
    before = target.read_bytes()
    result = source.execute(select(source, {work}), operation_id=uuid4().hex,
                            mode='move', confirmed_move=True)
    assert len(result.committed) == 1
    return source, legacy, owner, target, before


@pytest.mark.parametrize('work', ['1', '2'])
def test_physical_move_is_listed_and_restored(tmp_path, work):
    source, legacy, owner, target, before = moved(tmp_path, work)
    try:
        records = source.recoverable_moves()
        assert len(records) == 1
        op, _, count, revision = records[0]
        assert count == 1
        source.restore_move(op, expected_revision=revision)
        assert target.read_bytes() == before
        assert not legacy.state()['exclusions']
        assert not source.recoverable_moves()
    finally:
        source.close(); owner.close()


def test_restore_refuses_occupied_original(tmp_path):
    source, legacy, owner, target, before = moved(tmp_path)
    try:
        op, _, _, revision = source.recoverable_moves()[0]
        target.write_bytes(b'other file')
        with pytest.raises((OSError, ValueError, RuntimeError)):
            source.restore_move(op, expected_revision=revision)
        assert target.read_bytes() == b'other file'
        assert legacy.state()['exclusions']
    finally:
        source.close(); owner.close()


def test_restore_rejects_inconsistent_quarantine_copy(tmp_path):
    source, legacy, owner, target, before = moved(tmp_path)
    try:
        op, _, _, revision = source.recoverable_moves()[0]
        state = legacy.state()
        legacy._path(state['exclusions']['1:1']['copy']).write_bytes(b'tampered')
        assert not source.recoverable_moves()
        with pytest.raises((OSError, ValueError, RuntimeError)):
            source.restore_move(op, expected_revision=revision)
        assert not target.exists()
        assert legacy.state(verify_outputs=False)['exclusions']
    finally:
        source.close(); owner.close()


def test_stale_revision_is_rejected(tmp_path):
    source, legacy, owner, target, before = moved(tmp_path)
    try:
        op, _, _, revision = source.recoverable_moves()[0]
        with pytest.raises((OSError, ValueError, RuntimeError)):
            source.restore_move(op, expected_revision='0' * 32)
        assert not target.exists()
        assert legacy.state()['revision'] == revision
    finally:
        source.close(); owner.close()


def test_interruption_after_install_recovers_on_reopen(tmp_path, monkeypatch):
    from pixivvault_nicegui.legacy_quarantine import LegacyQuarantine
    source, legacy, owner, snapshot, original, archive = setup(tmp_path)
    target = original / 'Author(91)' / 'Folder(1).png'
    before = target.read_bytes()
    try:
        result = source.execute(select(source, {'1'}), operation_id=uuid4().hex,
                                mode='move', confirmed_move=True)
        assert len(result.committed) == 1
        op, _, _, revision = source.recoverable_moves()[0]
        class PowerLoss(BaseException): pass
        def fail(stage):
            if stage == 'restore-installed:1:1': raise PowerLoss()
        monkeypatch.setattr(legacy, '_checkpoint', fail)
        with pytest.raises(PowerLoss):
            source.restore_move(op, expected_revision=revision)
        source.close()
        reopened = LegacyQuarantine(original, snapshot, owner=owner)
        try:
            state = reopened.state()
            assert target.read_bytes() == before
            assert '1:1' not in state['exclusions']
        finally:
            reopened.close()
    finally:
        source.close(); owner.close()


def test_zip_restore_preserves_later_move(tmp_path):
    from zipfile import ZipFile
    source, legacy, owner, _, original, archive = setup(tmp_path)
    try:
        first = source.execute(select(source, {'2'}), operation_id=uuid4().hex,
                               mode='move', confirmed_move=True)
        assert len(first.committed) == 1
        first_unit = next(iter(legacy.state()['operations']))
        second = source.execute(select(source, {'3'}), operation_id=uuid4().hex,
                                mode='move', confirmed_move=True)
        assert len(second.committed) == 1
        records = source.recoverable_moves()
        record = next(r for r in records if first_unit in r[0])
        source.restore_move(record[0], expected_revision=record[3])
        with ZipFile(archive) as z:
            assert 'Zip(2).png' in z.namelist()
            assert 'Pages(3)_1.png' not in z.namelist()
            assert z.read('metadata.json') == b'{"kept":true}'
        assert '3:1' in legacy.state()['exclusions']
        assert '2:1' not in legacy.state()['exclusions']
    finally:
        source.close(); owner.close()


def test_zip_displacement_interruption_keeps_original_and_resumes(tmp_path, monkeypatch):
    source, legacy, owner, target, before = moved(tmp_path, '2')
    try:
        op, _, _, revision = source.recoverable_moves()[0]
        displaced_bytes = target.read_bytes()

        class PowerLoss(BaseException):
            pass

        def checkpoint(stage):
            if stage == 'restore-displaced:2:1':
                raise PowerLoss()

        monkeypatch.setattr(legacy, '_checkpoint', checkpoint)
        with pytest.raises(PowerLoss):
            source.restore_move(op, expected_revision=revision)
        assert not target.exists()
        copies = list(legacy._physical.root.glob('*/displaced'))
        assert len(copies) == 1 and copies[0].read_bytes() == displaced_bytes
        assert legacy.state(recover=False)['revision'] == revision

        source.recover_legacy()
        assert target.read_bytes() == before
        assert copies[0].read_bytes() == displaced_bytes
        assert not legacy.state()['exclusions']
        assert not source.recoverable_moves()
    finally:
        source.close(); owner.close()


def test_zip_new_file_after_displacement_is_never_overwritten(tmp_path, monkeypatch):
    source, legacy, owner, target, before = moved(tmp_path, '2')
    try:
        op, _, _, revision = source.recoverable_moves()[0]
        displaced_bytes = target.read_bytes()

        def checkpoint(stage):
            if stage == 'restore-displaced:2:1':
                target.write_bytes(b'external writer')

        monkeypatch.setattr(legacy, '_checkpoint', checkpoint)
        with pytest.raises(FileExistsError):
            source.restore_move(op, expected_revision=revision)
        assert target.read_bytes() == b'external writer'
        copies = list(legacy._physical.root.glob('*/displaced'))
        assert len(copies) == 1 and copies[0].read_bytes() == displaced_bytes
        assert legacy.state(recover=False)['revision'] == revision
        assert legacy.state()['revision'] == revision
        assert legacy.recovery_status()['blocked_sources'] == (target.name,)
        with pytest.raises(ValueError, match='restore recovery required'):
            legacy.purge(expected_count=1, confirmed=True)
        legacy.require_available('1', 0)

        external = tmp_path / 'external-writer-copy'
        target.rename(external)
        source.recover_legacy()
        assert target.read_bytes() == before
        assert external.read_bytes() == b'external writer'
        assert copies[0].read_bytes() == displaced_bytes
        assert not legacy.state()['exclusions']
    finally:
        source.close(); owner.close()


def test_restore_rejects_changed_live_zip(tmp_path):
    from zipfile import ZipFile
    source, legacy, owner, target, _ = moved(tmp_path, '2')
    try:
        op, _, _, revision = source.recoverable_moves()[0]
        with ZipFile(target, 'a') as archive:
            archive.writestr('external.txt', b'external writer')
        changed = target.read_bytes()
        with pytest.raises((OSError, ValueError, RuntimeError)):
            source.restore_move(op, expected_revision=revision)
        assert target.read_bytes() == changed
        assert legacy.state()['revision'] == revision
        assert '2:1' in legacy.state()['exclusions']
    finally:
        source.close(); owner.close()


def test_purged_move_cannot_be_restored_at_current_revision(tmp_path):
    source, legacy, owner, target, _ = moved(tmp_path)
    try:
        op, _, _, _ = source.recoverable_moves()[0]
        assert legacy.purge(expected_count=1, confirmed=True).removed == 1
        state = legacy.state()
        assert state['exclusions']['1:1']['purged']
        assert not source.recoverable_moves()
        with pytest.raises(ValueError):
            source.restore_move(op, expected_revision=state['revision'])
        assert not target.exists()
        assert legacy.state() == state
    finally:
        source.close(); owner.close()


@pytest.mark.asyncio
async def test_legacy_trash_confirmation_restores_visible_image(tmp_path):
    from nicegui import Client, ui
    from pixivvault_nicegui.ui.duplicate_view import DuplicateView

    source, legacy, owner, target, before = moved(tmp_path)
    client = Client(ui.page('/_fde02_restore'))
    try:
        with client:
            view = DuplicateView(source)
            view.set_active(True)
            await view.open_trash()
            assert len(view._trash_restore_buttons) == 1
            record = view._trash_moves[0]
            view.confirm_restore(record, view._trash_restore_buttons[0])
            assert view.restore_dialog.value and not target.exists()
            view.cancel_restore()
            assert not target.exists()
            view.confirm_restore(record)
            await view.execute_restore()
            assert target.read_bytes() == before
            assert not view._trash_moves
            assert view._trash_snapshot.copy_count == 0
            visible, catalog = legacy.project(legacy.original_snapshot)
            try:
                assert '1' in {work.work_id for work in visible.works}
                assert catalog.get('1', 0, 'original').data == before
            finally:
                catalog.close()
            view.close()
    finally:
        client.delete()
        source.close(); owner.close()


def test_zip_restore_keeps_virtual_copy_exclusions(tmp_path):
    from zipfile import ZipFile

    source, legacy, owner, target, before = moved(tmp_path, '2')
    try:
        result = source.execute(select(source, {'3'}), operation_id=uuid4().hex)
        assert len(result.committed) == 1
        op, _, _, revision = source.recoverable_moves()[0]
        source.restore_move(op, expected_revision=revision)
        assert target.read_bytes() == before
        state = legacy.state()
        assert set(state['exclusions']) == {'3:1'}
        with ZipFile(legacy.active_archive(target)) as active:
            assert 'Zip(2).png' in active.namelist()
            assert 'Pages(3)_1.png' not in active.namelist()
            assert 'Pages(3)_2.png' in active.namelist()
    finally:
        source.close(); owner.close()


def test_reopen_rejects_backup_changed_after_restore_intent(tmp_path, monkeypatch):
    source, legacy, owner, target, _ = moved(tmp_path, '2')
    try:
        op, _, _, revision = source.recoverable_moves()[0]
        live = target.read_bytes()

        class PowerLoss(BaseException):
            pass

        def checkpoint(stage):
            if stage == 'restore-intent:2:1':
                raise PowerLoss()

        monkeypatch.setattr(legacy, '_checkpoint', checkpoint)
        with pytest.raises(PowerLoss):
            source.restore_move(op, expected_revision=revision)
        unit = op.split(':')[1]
        legacy._physical._unit(unit).joinpath('before').write_bytes(b'changed backup')
        assert legacy.state()['revision'] == revision
        assert target.read_bytes() == live
        assert legacy.recovery_status()['blocked_sources'] == (target.name,)
        with pytest.raises(OSError, match='recovery required'):
            source.recover_legacy()
    finally:
        source.close(); owner.close()


def test_damaged_copy_does_not_prevent_restoring_a_healthy_move(tmp_path):
    source, legacy, owner, target, original = moved(tmp_path)
    try:
        moved_zip = source.execute(select(source, {'2'}), operation_id=uuid4().hex,
                                   mode='move', confirmed_move=True)
        assert len(moved_zip.committed) == 1
        state = legacy.state()
        legacy._path(state['exclusions']['2:1']['copy']).write_bytes(b'changed copy')

        records = source.recoverable_moves()
        assert len(records) == 1
        source.restore_move(records[0][0], expected_revision=records[0][3])
        assert target.read_bytes() == original
        assert set(legacy.state(verify_outputs=False)['exclusions']) == {'2:1'}
    finally:
        source.close(); owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('damaged_copy', [False, True])
async def test_legacy_restore_failure_can_resume_from_existing_ui_button(tmp_path, monkeypatch, damaged_copy):
    from nicegui import Client, ui
    from pixivvault_nicegui.ui.duplicate_view import DuplicateView

    source, legacy, owner, target, before = moved(tmp_path)
    remaining_exclusions = set()
    if damaged_copy:
        other = source.execute(select(source, {'2'}), operation_id=uuid4().hex,
                               mode='move', confirmed_move=True)
        assert len(other.committed) == 1
        state = legacy.state()
        legacy._path(state['exclusions']['2:1']['copy']).write_bytes(b'changed copy')
        remaining_exclusions.add('2:1')
    client = Client(ui.page(f'/_fde02_recover_{damaged_copy}'))
    refreshes = []

    def checkpoint(stage):
        if stage == 'restore-installed:1:1':
            raise OSError('synthetic interruption')

    def on_changed():
        refreshes.append(set(legacy.state(verify_outputs=False)['exclusions']))

    source.on_changed = on_changed
    monkeypatch.setattr(legacy, '_checkpoint', checkpoint)
    try:
        with client:
            view = DuplicateView(source)
            view.set_active(True)
            await view.open_trash()
            view.confirm_restore(view._trash_moves[0])
            await view.execute_restore()
            assert target.read_bytes() == before
            assert view.legacy_recover_button.visible
            assert not refreshes
            pending = legacy.state(verify_outputs=False, recover=False)
            assert set(pending['exclusions']) == {'1:1'} | remaining_exclusions
            view.close_trash()
            await view.recover_legacy()
            assert not view.legacy_recover_button.visible
            assert refreshes == [remaining_exclusions]
            await view.open_trash()
            assert not view._trash_moves
            assert view._trash_snapshot.copy_count == 0
            view.close()
    finally:
        client.delete()
        source.close(); owner.close()
