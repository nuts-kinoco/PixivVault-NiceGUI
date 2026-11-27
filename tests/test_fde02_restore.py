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
        assert legacy.recovery_status()['blocked_sources'] == (target.name,)
        assert not source.recoverable_moves()
        assert legacy.state()['revision'] == revision
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
            assert not view.near_panel.visible
            assert view.legacy_recover_button not in set(view.near_panel.descendants())
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


@pytest.mark.parametrize('work,interrupted', [('1', False), ('2', True)])
def test_neon_1_restore_survives_inode_change_on_cross_directory_rename(tmp_path, monkeypatch, work, interrupted):
    from types import SimpleNamespace
    from pixivvault_nicegui.fs_paths import native_io_path

    source, legacy, owner, target, original = moved(tmp_path, work)
    try:
        op, _, _, revision = source.recoverable_moves()[0]
        stat = type(target).stat
        installed = False

        def different_inode(path, *args, **kwargs):
            info = stat(path, *args, **kwargs)
            if installed and path == native_io_path(target):
                fields = {name: getattr(info, name) for name in dir(info) if name.startswith('st_')}
                fields['st_ino'] += 1
                return SimpleNamespace(**fields)
            return info

        def checkpoint(stage):
            nonlocal installed
            if stage == f'restore-installed:{work}:1':
                installed = True
                if interrupted:
                    raise OSError('synthetic interruption after rename')

        if work == '1':
            from pixivvault_nicegui import legacy_physical_restore as restore
            def unsupported(*args, **kwargs):
                raise OSError('synthetic filesystem has no hardlinks')
            monkeypatch.setattr(restore.os, 'link', unsupported)
        monkeypatch.setattr(type(target), 'stat', different_inode)
        monkeypatch.setattr(legacy, '_checkpoint', checkpoint)
        if interrupted:
            with pytest.raises(OSError, match='synthetic interruption'):
                source.restore_move(op, expected_revision=revision)
            source.recover_legacy()
        else:
            source.restore_move(op, expected_revision=revision)
        assert installed and target.read_bytes() == original
        assert not legacy.state()['exclusions']
        assert not legacy.recovery_status()['blocked_sources']
    finally:
        source.close(); owner.close()


def test_neon_2_rebuilt_zip_regenerates_zip64_headers_and_keeps_metadata(tmp_path, monkeypatch):
    import struct
    import zipfile
    from pixivvault_nicegui.legacy_physical_restore import _restore_member

    # Exercise the stdlib's real Zip64 writer/reader with small fixtures.
    monkeypatch.setattr(zipfile, 'ZIP64_LIMIT', 128)
    current = tmp_path / 'current.zip'
    backup = tmp_path / 'before.zip'
    output = tmp_path / 'restored.zip'
    metadata = struct.pack('<HH2s', 0xCAFE, 2, b'ok')
    keep = zipfile.ZipInfo('keep.bin', (2020, 5, 6, 0, 0, 0))
    keep.extra, keep.comment = metadata, b'entry comment'
    keep.external_attr = 0o100640 << 16
    content, selected = b'k' * 260, b'p' * 190
    with zipfile.ZipFile(current, 'w') as archive:
        archive.comment = b'archive comment'
        archive.writestr(keep, content)
    with zipfile.ZipFile(backup, 'w') as archive:
        archive.writestr('padding.bin', b'a' * 300)
        archive.writestr('selected.png', selected)
    with zipfile.ZipFile(backup) as archive:
        original_info = archive.getinfo('selected.png')
        old_offset = original_info.header_offset
        _restore_member(current, output, original_info, archive.read(original_info))

    with zipfile.ZipFile(output) as archive, output.open('rb') as raw:
        assert archive.testzip() is None
        assert archive.comment == b'archive comment'
        entry = archive.getinfo('keep.bin')
        assert (entry.date_time, entry.comment, entry.external_attr) == (keep.date_time, keep.comment, keep.external_attr)
        for name, data, retained_extra in [('keep.bin', content, metadata), ('selected.png', selected, b'')]:
            entry = archive.getinfo(name)
            assert archive.read(entry) == data
            raw.seek(entry.header_offset + 26)
            name_len, extra_len = struct.unpack('<HH', raw.read(4))
            raw.read(name_len)
            extra = raw.read(extra_len)
            assert extra == retained_extra + struct.pack('<HHQQ', 1, 16, len(data), len(data))
    assert original_info.header_offset == old_offset


@pytest.mark.parametrize('after_intent', [False, True])
def test_neon_3_missing_file_parent_does_not_leave_restore_pending(tmp_path, monkeypatch, after_intent):
    import json

    source, legacy, owner, target, _ = moved(tmp_path)
    old_parent = tmp_path / 'author-offline'
    try:
        op, _, _, revision = source.recoverable_moves()[0]
        if after_intent:
            def checkpoint(stage):
                if stage == 'restore-intent:1:1':
                    target.parent.rename(old_parent)
            monkeypatch.setattr(legacy, '_checkpoint', checkpoint)
        else:
            target.parent.rename(old_parent)
        with pytest.raises((OSError, ValueError)):
            source.restore_move(op, expected_revision=revision)
        assert old_parent.is_dir() and not target.parent.exists()
        journals = legacy._physical.journals
        if after_intent:
            markers = list(journals.glob('*/restored.json'))
            assert len(markers) == 1
            assert json.loads(markers[0].read_bytes())['status'] == 'cancelled'
        else:
            assert not list(journals.glob('*/restore.json'))
        assert legacy.state()['revision'] == revision
        assert not legacy.recovery_status()['blocked_sources']
        assert legacy.purge(expected_count=1, confirmed=True).removed == 1
    finally:
        source.close(); owner.close()


@pytest.mark.parametrize('link_available', [True, False])
def test_neon_4_bookmark_alias_retains_inode_or_restore_refuses(tmp_path, monkeypatch, link_available):
    import os
    from pixivvault_nicegui import legacy_physical_restore as restore

    source, legacy, owner, snapshot, original, _ = setup(tmp_path)
    target = original / 'Author(91)' / 'Folder(1).png'
    bookmark = original / '\u2606\u30d6\u30c3\u30af\u30de\u30fc\u30af(7)'
    bookmark.mkdir()
    alias = bookmark / 'Author(91)_Folder(1).png'
    os.link(target, alias)
    data = alias.read_bytes()
    try:
        result = source.execute(select(source, {'1'}), operation_id=uuid4().hex,
                                mode='move', confirmed_move=True)
        assert len(result.committed) == 1 and not target.exists()
        op, _, _, revision = source.recoverable_moves()[0]
        unit = op.split(':')[1]
        donor = legacy._physical._unit(unit) / target.name
        assert os.path.samefile(alias, donor)
        if not link_available:
            def unavailable(*args, **kwargs):
                raise OSError('synthetic link unavailable')
            monkeypatch.setattr(restore.os, 'link', unavailable)
            with pytest.raises((OSError, ValueError)):
                source.restore_move(op, expected_revision=revision)
            assert not target.exists() and os.path.samefile(alias, donor)
            assert not list(legacy._physical.journals.glob('*/restore.json'))
            assert legacy.state()['revision'] == revision
        else:
            source.restore_move(op, expected_revision=revision)
            assert os.path.samefile(target, alias)
            visible, catalog = legacy.project(snapshot)
            try:
                assert '1' in {work.work_id for work in visible.works}
                assert catalog.get('1', 0, 'original').data == data
                assert select(source, {'1'}).items[0].key == '1:1'
            finally:
                catalog.close()
        assert alias.read_bytes() == data
    finally:
        source.close(); owner.close()


def test_neon_2_archive_readback_rejects_changed_output(tmp_path, monkeypatch):
    import zipfile
    from pixivvault_nicegui.legacy_physical_restore import _restore_member

    current = tmp_path / 'current.zip'
    backup = tmp_path / 'backup.zip'
    output = tmp_path / 'output.zip'
    with zipfile.ZipFile(current, 'w') as archive:
        archive.writestr('keep.bin', b'known data')
    with zipfile.ZipFile(backup, 'w') as archive:
        archive.writestr('selected.png', b'page data')

    write = zipfile._ZipWriteFile.write

    def corrupt(stream, data):
        if bytes(data) == b'known data':
            data = b'wrong data'
        return write(stream, data)

    monkeypatch.setattr(zipfile._ZipWriteFile, 'write', corrupt)
    with zipfile.ZipFile(backup) as archive, pytest.raises(ValueError, match='archive readback failed'):
        info = archive.getinfo('selected.png')
        _restore_member(current, output, info, archive.read(info))
    with zipfile.ZipFile(current) as archive:
        assert archive.read('keep.bin') == b'known data'


def test_neon_4_single_link_can_fall_back_to_verified_copy(tmp_path, monkeypatch):
    import os
    from pixivvault_nicegui import legacy_physical_restore as restore

    source, legacy, owner, target, data = moved(tmp_path)
    try:
        op, _, _, revision = source.recoverable_moves()[0]
        donor = legacy._physical._unit(op.split(':')[1]) / target.name
        assert donor.stat().st_nlink == 1

        def unavailable(*args, **kwargs):
            raise OSError('synthetic link unavailable')

        monkeypatch.setattr(restore.os, 'link', unavailable)
        source.restore_move(op, expected_revision=revision)
        assert target.read_bytes() == donor.read_bytes() == data
        assert not os.path.samefile(target, donor)
        assert not legacy.state()['exclusions']
    finally:
        source.close(); owner.close()


@pytest.mark.parametrize('already_installed', [False, True])
def test_neon_4_old_copy_intent_cannot_publish_split_bookmark_alias(tmp_path, monkeypatch, already_installed):
    import json
    import os
    from pixivvault_nicegui.fs_paths import native_io_path

    source, legacy, owner, _, original, _ = setup(tmp_path)
    target = original / 'Author(91)' / 'Folder(1).png'
    bookmark = original / '\u2606\u30d6\u30c3\u30af\u30de\u30fc\u30af(7)'
    bookmark.mkdir()
    alias = bookmark / 'Author(91)_Folder(1).png'
    os.link(target, alias)
    data = alias.read_bytes()
    try:
        move = source.execute(select(source, {'1'}), operation_id=uuid4().hex,
                              mode='move', confirmed_move=True)
        assert len(move.committed) == 1 and not target.exists()
        op, _, _, revision = source.recoverable_moves()[0]

        def old_stage(_intent, ready, contents):
            ready.write_bytes(contents)
            return False

        def old_intent(stage):
            if stage != 'restore-intent:1:1':
                return
            journal, = legacy._physical.journals.glob('*/restore.json')
            record = json.loads(journal.read_bytes())
            record.pop('ready_linked')
            record.pop('ready_size')
            journal.write_text(json.dumps(record), encoding='utf-8')
            if already_installed:
                ready, _ = legacy._physical.restores._locations(record, native_io_path(target))
                ready.rename(target)
            raise OSError('synthetic old restore interruption')

        with monkeypatch.context() as legacy_code:
            legacy_code.setattr(legacy._physical.restores, '_stage_image', old_stage)
            legacy_code.setattr(legacy, '_checkpoint', old_intent)
            with pytest.raises(OSError, match='synthetic old restore interruption'):
                source.restore_move(op, expected_revision=revision)
        if already_installed:
            with pytest.raises(OSError, match='recovery required'):
                source.recover_legacy()
            assert target.read_bytes() == data and not os.path.samefile(target, alias)
            assert legacy.state(recover=False)['revision'] == revision
            saved = tmp_path / 'old-restored-copy'
            target.rename(saved)
        source.recover_legacy()
        assert legacy.state()['revision'] == revision
        assert not target.exists()
        source.restore_move(op, expected_revision=revision)
        assert os.path.samefile(target, alias)
        if already_installed:
            assert saved.read_bytes() == data
    finally:
        source.close(); owner.close()


@pytest.mark.asyncio
async def test_neon_6_new_view_exposes_recovery_for_known_pending_source(tmp_path, monkeypatch):
    from nicegui import Client, ui
    from pixivvault_nicegui.ui.duplicate_view import DuplicateView

    source, legacy, owner, target, before = moved(tmp_path, '2')
    client = Client(ui.page('/_fde02_neon_resume_new_view'))

    def external_writer(stage):
        if stage == 'restore-displaced:2:1':
            target.write_bytes(b'other archive')

    try:
        op, _, _, revision = source.recoverable_moves()[0]
        monkeypatch.setattr(legacy, '_checkpoint', external_writer)
        with pytest.raises(FileExistsError):
            source.restore_move(op, expected_revision=revision)
        assert legacy.recovery_status()['blocked_sources'] == (target.name,)
        with client:
            view = DuplicateView(source)
            view.set_active(True)
            assert not view.near_panel.visible
            assert view.legacy_recover_button.visible
            preserved = tmp_path / 'external-archive'
            target.rename(preserved)
            await view.recover_legacy()
            assert not view.legacy_recover_button.visible
            assert target.read_bytes() == before
            assert preserved.read_bytes() == b'other archive'
            view.close()
    finally:
        client.delete()
        source.close(); owner.close()


def test_neon_4_linked_restore_rejects_equal_bytes_with_a_different_inode(tmp_path, monkeypatch):
    import os

    source, legacy, owner, _, original, _ = setup(tmp_path)
    target = original / 'Author(91)' / 'Folder(1).png'
    bookmarks = original / '\u2606\u30d6\u30c3\u30af\u30de\u30fc\u30af(7)'
    bookmarks.mkdir()
    alias = bookmarks / 'Author(91)_Folder(1).png'
    os.link(target, alias)
    data = alias.read_bytes()
    linked = tmp_path / 'preserved-linked-image'
    separate = tmp_path / 'preserved-separate-image'

    def external_writer(stage):
        if stage == 'restore-installed:1:1':
            target.rename(linked)
            target.write_bytes(data)

    try:
        move = source.execute(select(source, {'1'}), operation_id=uuid4().hex,
                              mode='move', confirmed_move=True)
        assert len(move.committed) == 1
        op, _, _, revision = source.recoverable_moves()[0]
        monkeypatch.setattr(legacy, '_checkpoint', external_writer)
        with pytest.raises(ValueError, match='lost its hardlink'):
            source.restore_move(op, expected_revision=revision)
        assert target.read_bytes() == data and os.path.samefile(alias, linked)
        assert legacy.state(recover=False)['revision'] == revision

        target.rename(separate)
        linked.rename(target)
        source.recover_legacy()
        assert os.path.samefile(target, alias) and separate.read_bytes() == data
        assert not legacy.state()['exclusions']
    finally:
        source.close(); owner.close()
