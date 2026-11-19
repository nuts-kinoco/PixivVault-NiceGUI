"""Z07: the legacy database is never the repository's live source."""
import sqlite3

import pytest

from pixivvault_nicegui.legacy_library_copies import LegacyLibraryCopies, LegacyCopyError


def test_copies_are_frozen_and_source_never_changes(tmp_path):
    old = tmp_path / 'old'
    old.mkdir()
    source = old / 'library.sqlite'
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE works (work_id TEXT, title TEXT, page_count INTEGER, user_id TEXT, tags TEXT, is_deleted INTEGER)')
        conn.execute('CREATE TABLE following_users (user_id TEXT, name TEXT)')
        conn.execute("INSERT INTO works (work_id) VALUES ('1')")
    before = {p.name: p.read_bytes() for p in old.iterdir()}
    root = tmp_path / 'managed'
    root.mkdir()
    copies = LegacyLibraryCopies(root, source=source, max_generations=2, max_bytes=1048576)
    try:
        frozen = copies.snapshot()
        assert frozen != source and root in frozen.parents
        with sqlite3.connect(frozen) as conn:
            assert conn.execute('PRAGMA application_id').fetchone() == (0x5056434C,)
            assert conn.execute('PRAGMA user_version').fetchone() == (1,)
            assert conn.execute('SELECT work_id FROM works').fetchall() == [('1',)]
        assert {p.name: p.read_bytes() for p in old.iterdir()} == before
        with sqlite3.connect(source) as conn:
            conn.execute("INSERT INTO works (work_id) VALUES ('2')")
        with sqlite3.connect(frozen) as conn:
            assert conn.execute('SELECT work_id FROM works').fetchall() == [('1',)]
    finally:
        copies.close()


def test_missing_managed_destination_refuses_before_read(tmp_path):
    source = tmp_path / 'old.sqlite'
    source.write_bytes(b'not a database')
    with pytest.raises(LegacyCopyError):
        LegacyLibraryCopies(tmp_path / 'absent', source=source)


def test_active_generations_are_not_pruned_and_owner_is_exclusive(tmp_path):
    old = tmp_path / 'old'
    old.mkdir()
    source = old / 'library.sqlite'
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE works (work_id TEXT, title TEXT, page_count INTEGER, user_id TEXT, tags TEXT, is_deleted INTEGER)')
        conn.execute('CREATE TABLE following_users (user_id TEXT, name TEXT)')
    root = tmp_path / 'managed'
    root.mkdir()
    copies = LegacyLibraryCopies(root, source=source, max_generations=2, max_bytes=1048576)
    try:
        first, second = copies.snapshot(), copies.snapshot()
        with pytest.raises(LegacyCopyError):
            copies.snapshot()
        assert first.is_file() and second.is_file()
        with pytest.raises(LegacyCopyError):
            LegacyLibraryCopies(root, source=source)
    finally:
        copies.close()
    copies = LegacyLibraryCopies(root, source=source, max_generations=2, max_bytes=1048576)
    try:
        third = copies.snapshot()
        assert third.is_file()
        assert len(tuple(root.glob('*.sqlite'))) == 2
    finally:
        copies.close()


def test_sidecar_and_unknown_files_fail_closed(tmp_path):
    old = tmp_path / 'old'
    old.mkdir()
    source = old / 'library.sqlite'
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE works (work_id TEXT, title TEXT, page_count INTEGER, user_id TEXT, tags TEXT, is_deleted INTEGER)')
        conn.execute('CREATE TABLE following_users (user_id TEXT, name TEXT)')
    sidecar = old / 'library.sqlite-wal'
    sidecar.write_bytes(b'fake')
    root = tmp_path / 'managed'
    root.mkdir()
    copies = LegacyLibraryCopies(root, source=source, max_bytes=1048576)
    try:
        with pytest.raises(LegacyCopyError):
            copies.snapshot()
        assert sidecar.read_bytes() == b'fake'
        sidecar.unlink()
        unknown = root / 'unknown'
        unknown.write_bytes(b'keep')
        with pytest.raises(LegacyCopyError):
            copies.snapshot()
        assert unknown.read_bytes() == b'keep'
    finally:
        copies.close()


def test_oversized_source_is_rejected_before_database_read(tmp_path, monkeypatch):
    old = tmp_path / 'old'
    old.mkdir()
    source = old / 'library.sqlite'
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE works (work_id TEXT, title TEXT, page_count INTEGER, user_id TEXT, tags TEXT, is_deleted INTEGER)')
        conn.execute('CREATE TABLE following_users (user_id TEXT, name TEXT)')
    root = tmp_path / 'managed'
    root.mkdir()
    copies = LegacyLibraryCopies(root, source=source, max_bytes=128)
    import os
    original_open = os.open
    def deny_source_read(path, *args, **kwargs):
        if path == source:
            raise AssertionError('oversized source must not be read')
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(os, 'open', deny_source_read)
    try:
        with pytest.raises(LegacyCopyError, match='capacity'):
            copies.snapshot()
        assert not tuple(root.glob('*.sqlite'))
    finally:
        copies.close()


def test_service_reads_only_the_managed_frozen_copy(tmp_path):
    from pixivvault_nicegui.services import ApplicationServices
    old = tmp_path / 'old'
    images = tmp_path / 'images'
    root = tmp_path / 'managed'
    old.mkdir()
    images.mkdir()
    root.mkdir()
    source = old / 'library.sqlite'
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE works (work_id TEXT, title TEXT, page_count INTEGER, user_id TEXT, tags TEXT, is_deleted INTEGER)')
        conn.execute('CREATE TABLE following_users (user_id TEXT, name TEXT)')
        conn.execute("INSERT INTO works VALUES ('1', 'one', 1, '9', '[]', 0)")
    service = ApplicationServices(library_readonly_db=source, library_image_root=images, legacy_copy_root=root)
    try:
        assert service.library_readonly_db != source
        assert root in service.library_readonly_db.parents
        with sqlite3.connect(source) as conn:
            conn.execute("INSERT INTO works VALUES ('2', 'two', 1, '9', '[]', 0)")
        assert service.library_repository.count_artworks() == 1
    finally:
        service.close()


def test_unowned_source_directory_is_never_modified(tmp_path):
    old = tmp_path / 'old'
    old.mkdir()
    source = old / 'library.sqlite'
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE works (work_id TEXT, title TEXT, page_count INTEGER, user_id TEXT, tags TEXT, is_deleted INTEGER)')
        conn.execute('CREATE TABLE following_users (user_id TEXT, name TEXT)')
    before = {p.name: p.read_bytes() for p in old.iterdir()}
    with pytest.raises(LegacyCopyError):
        LegacyLibraryCopies(old, source=source)
    assert {p.name: p.read_bytes() for p in old.iterdir()} == before


def test_failed_new_snapshot_keeps_the_previous_generation(tmp_path):
    old = tmp_path / 'old'
    old.mkdir()
    source = old / 'library.sqlite'
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE works (work_id TEXT, title TEXT, page_count INTEGER, user_id TEXT, tags TEXT, is_deleted INTEGER)')
        conn.execute('CREATE TABLE following_users (user_id TEXT, name TEXT)')
    root = tmp_path / 'managed'
    root.mkdir()
    copies = LegacyLibraryCopies(root, source=source, max_generations=1, max_bytes=1048576)
    saved = copies.snapshot()
    copies.close()
    original = saved.read_bytes()
    source.write_bytes(b'bad' * 1000)
    copies = LegacyLibraryCopies(root, source=source, max_generations=1, max_bytes=1048576)
    try:
        with pytest.raises(LegacyCopyError):
            copies.snapshot()
        assert saved.read_bytes() == original
    finally:
        copies.close()


def _fixture_paths(tmp_path):
    old = tmp_path / 'old'
    root = tmp_path / 'managed'
    old.mkdir()
    root.mkdir()
    source = old / 'library.sqlite'
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE works (work_id TEXT, title TEXT, page_count INTEGER, user_id TEXT, tags TEXT, is_deleted INTEGER)')
        conn.execute('CREATE TABLE following_users (user_id TEXT, name TEXT)')
    return source, root


def test_protected_overlap_is_rejected_before_any_marker_or_lock(tmp_path):
    source, root = _fixture_paths(tmp_path)
    with pytest.raises(LegacyCopyError):
        LegacyLibraryCopies(root, source=source, protected_roots=(root,))
    assert list(root.iterdir()) == []


def test_only_known_owned_stages_are_recovered(tmp_path):
    source, root = _fixture_paths(tmp_path)
    copies = LegacyLibraryCopies(root, source=source)
    copies.close()
    raw = root / ('.raw-' + 'a' * 32)
    ready = root / ('.ready-' + 'b' * 32 + '.sqlite')
    sidecar = root / (ready.name + '-journal')
    unknown = root / '.raw-unknown'
    for path in (raw, ready, sidecar, unknown):
        path.write_bytes(b'keep')
    with pytest.raises(LegacyCopyError):
        LegacyLibraryCopies(root, source=source)
    assert all(path.is_file() for path in (raw, ready, sidecar, unknown))
    unknown.unlink()
    copies = LegacyLibraryCopies(root, source=source)
    try:
        assert all(not path.exists() for path in (raw, ready, sidecar))
    finally:
        copies.close()


def test_publication_failure_preserves_previous_generation(tmp_path, monkeypatch):
    source, root = _fixture_paths(tmp_path)
    copies = LegacyLibraryCopies(root, source=source, max_generations=1, max_bytes=1048576)
    first = copies.snapshot()
    copies.close()
    original = first.read_bytes()
    copies = LegacyLibraryCopies(root, source=source, max_generations=1, max_bytes=1048576)
    def fail(*args):
        raise OSError('synthetic publication failure')
    monkeypatch.setattr('pixivvault_nicegui.legacy_library_copies._publish_copy', fail)
    try:
        with pytest.raises(LegacyCopyError):
            copies.snapshot()
        assert first.read_bytes() == original
        assert {p.name for p in root.glob('*.sqlite')} == {first.name}
    finally:
        copies.close()


def test_capacity_failure_preserves_previous_generation(tmp_path):
    source, root = _fixture_paths(tmp_path)
    copies = LegacyLibraryCopies(root, source=source, max_generations=1, max_bytes=1048576)
    first = copies.snapshot()
    copies.close()
    original = first.read_bytes()
    copies = LegacyLibraryCopies(root, source=source, max_generations=1, max_bytes=len(original) + 1)
    try:
        with pytest.raises(LegacyCopyError, match='capacity'):
            copies.snapshot()
        assert first.read_bytes() == original
    finally:
        copies.close()


def test_source_change_before_publication_is_rejected(tmp_path, monkeypatch):
    source, root = _fixture_paths(tmp_path)
    copies = LegacyLibraryCopies(root, source=source, max_bytes=1048576)
    original = copies._read_source
    def changed(raw, expected):
        original(raw, expected)
        with sqlite3.connect(source) as conn:
            conn.execute("INSERT INTO works (work_id) VALUES ('7')")
    monkeypatch.setattr(copies, '_read_source', changed)
    try:
        with pytest.raises(LegacyCopyError, match='changed'):
            copies.snapshot()
        assert not tuple(root.glob('*.sqlite'))
    finally:
        copies.close()


def test_close_waits_for_snapshot_and_does_not_release_owner_early(tmp_path, monkeypatch):
    from threading import Event, Thread
    source, root = _fixture_paths(tmp_path)
    copies = LegacyLibraryCopies(root, source=source, max_bytes=1048576)
    entered, release, closed = Event(), Event(), Event()
    original = copies._read_source
    outcomes = []
    def wait(raw, expected):
        entered.set()
        assert release.wait(10)
        return original(raw, expected)
    monkeypatch.setattr(copies, '_read_source', wait)
    def make():
        try:
            outcomes.append(copies.snapshot())
        except Exception as error:
            outcomes.append(error)
    def close():
        copies.close()
        closed.set()
    maker = Thread(target=make)
    closer = Thread(target=close)
    maker.start()
    try:
        assert entered.wait(10)
        closer.start()
        assert not closed.wait(.1)
        with pytest.raises(LegacyCopyError):
            LegacyLibraryCopies(root, source=source)
    finally:
        release.set()
        maker.join(10)
        closer.join(10)
    assert closed.is_set() and not maker.is_alive() and not closer.is_alive()
    assert len(outcomes) == 1 and isinstance(outcomes[0], type(root)) and outcomes[0].is_file()


def test_sqlite_never_connects_to_original_database(tmp_path, monkeypatch):
    source, root = _fixture_paths(tmp_path)
    copies = LegacyLibraryCopies(root, source=source, max_bytes=1048576)
    original_connect = sqlite3.connect
    destinations = []
    def connect(database, *args, **kwargs):
        destinations.append(str(database))
        assert str(source).lower().replace('\\', '/') not in str(database).lower().replace('\\', '/')
        return original_connect(database, *args, **kwargs)
    monkeypatch.setattr(sqlite3, 'connect', connect)
    try:
        assert copies.snapshot().is_file()
        assert len(destinations) == 8
    finally:
        copies.close()


def test_oversized_owner_marker_is_rejected_without_changing_it(tmp_path):
    source, root = _fixture_paths(tmp_path)
    copies = LegacyLibraryCopies(root, source=source)
    copies.close()
    marker = root / '.legacy-library-copies'
    marker.write_bytes(b'x' * 1000000)
    with pytest.raises(LegacyCopyError):
        LegacyLibraryCopies(root, source=source)
    assert marker.stat().st_size == 1000000


def test_service_refuses_unavailable_destination_before_sqlite_open(tmp_path, monkeypatch):
    from pixivvault_nicegui.services import ApplicationServices
    old = tmp_path / 'old'
    images = tmp_path / 'images'
    old.mkdir()
    images.mkdir()
    source = old / 'library.sqlite'
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE works (work_id TEXT)')
    original_connect = sqlite3.connect
    calls = []
    def record_connect(*args, **kwargs):
        calls.append(True)
        return original_connect(*args, **kwargs)
    monkeypatch.setattr(sqlite3, 'connect', record_connect)
    with pytest.raises(LegacyCopyError):
        ApplicationServices(library_readonly_db=source, library_image_root=images,
                            legacy_copy_root=tmp_path / 'absent')
    assert calls == []


def test_service_reuse_compares_original_path_and_releases_owner(tmp_path):
    from types import SimpleNamespace
    from pixivvault_nicegui.services import get_services
    old = tmp_path / 'old'
    images = tmp_path / 'images'
    root = tmp_path / 'managed'
    old.mkdir()
    images.mkdir()
    root.mkdir()
    source = old / 'library.sqlite'
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE works (work_id TEXT, title TEXT, page_count INTEGER, user_id TEXT, tags TEXT, is_deleted INTEGER)')
        conn.execute('CREATE TABLE following_users (user_id TEXT, name TEXT)')
    app = SimpleNamespace(state=SimpleNamespace())
    service = get_services(app, library_readonly_db=source, library_image_root=images, legacy_copy_root=root)
    try:
        assert get_services(app, library_readonly_db=source, library_image_root=images, legacy_copy_root=root) is service
        with pytest.raises(LegacyCopyError):
            LegacyLibraryCopies(root, source=source, protected_roots=(images,))
    finally:
        service.close()
    reopened = LegacyLibraryCopies(root, source=source, protected_roots=(images,))
    reopened.close()


def test_native_registration_provisions_and_passes_managed_copy_root(tmp_path, monkeypatch):
    from pixivvault_nicegui import bootstrap, services
    from pixivvault_nicegui.native_profile import NativeProfileSnapshot, NativeProfileStore, provision_storage
    from pixivvault_nicegui.native_startup import NativeProfileBinding
    old, images, parent = tmp_path / 'old', tmp_path / 'images', tmp_path / 'save'
    for folder in (old, images, parent):
        folder.mkdir()
    source = old / 'library.sqlite'
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE works (work_id TEXT)')
    profile = NativeProfileStore(tmp_path / 'profile')
    snapshot = NativeProfileSnapshot(0, save_parent=parent, library_readonly_db=source, library_image_root=images)
    binding = NativeProfileBinding(profile, launch_snapshot=snapshot)
    provision_storage(snapshot, profile.root)
    class Captured(Exception):
        pass
    seen = {}
    def capture(*args, **kwargs):
        seen.update(kwargs)
        raise Captured()
    monkeypatch.setattr(bootstrap, '_registered', False)
    monkeypatch.setattr(services, 'get_services', capture)
    try:
        with pytest.raises(Captured):
            bootstrap.register_pages(library_readonly_db=source, library_image_root=images, native_profile=binding)
        copy_root = seen['legacy_copy_root']
        assert copy_root == parent / 'PixivVault-NiceGUI' / 'legacy-library-copies'
        assert copy_root.is_dir()
        assert seen['library_readonly_db'] == source
    finally:
        binding.close()


def test_initialization_failure_releases_snapshot_owner(tmp_path, monkeypatch):
    from pixivvault_nicegui.services import ApplicationServices
    old, images, root = tmp_path / 'old', tmp_path / 'images', tmp_path / 'managed'
    for folder in (old, images, root):
        folder.mkdir()
    source = old / 'library.sqlite'
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE works (work_id TEXT, title TEXT, page_count INTEGER, user_id TEXT, tags TEXT, is_deleted INTEGER)')
        conn.execute('CREATE TABLE following_users (user_id TEXT, name TEXT)')
    class BrokenRepository:
        def __init__(self, *args, **kwargs):
            raise RuntimeError('synthetic failure')
    monkeypatch.setattr('pixivvault_nicegui.library_repository.ReadonlyLibraryRepository', BrokenRepository)
    with pytest.raises(RuntimeError, match='synthetic failure'):
        ApplicationServices(library_readonly_db=source, library_image_root=images, legacy_copy_root=root)
    reopened = LegacyLibraryCopies(root, source=source, protected_roots=(images,))
    reopened.close()


def test_service_reuse_rejects_a_different_snapshot_root(tmp_path):
    from types import SimpleNamespace
    from pixivvault_nicegui.services import get_services
    old, images, first, second = (tmp_path / name for name in ('old', 'images', 'first', 'second'))
    for folder in (old, images, first, second):
        folder.mkdir()
    source = old / 'library.sqlite'
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE works (work_id TEXT, title TEXT, page_count INTEGER, user_id TEXT, tags TEXT, is_deleted INTEGER)')
        conn.execute('CREATE TABLE following_users (user_id TEXT, name TEXT)')
    app = SimpleNamespace(state=SimpleNamespace())
    service = get_services(app, library_readonly_db=source, library_image_root=images, legacy_copy_root=first)
    try:
        with pytest.raises(RuntimeError):
            get_services(app, library_readonly_db=source, library_image_root=images, legacy_copy_root=second)
    finally:
        service.close()


def test_failed_shutdown_keeps_snapshot_owner_until_retry(tmp_path, monkeypatch):
    from pixivvault_nicegui import services
    old, root = tmp_path / 'old', tmp_path / 'managed'
    old.mkdir()
    root.mkdir()
    source = old / 'library.sqlite'
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE works (work_id TEXT, title TEXT, page_count INTEGER, user_id TEXT, tags TEXT, is_deleted INTEGER)')
        conn.execute('CREATE TABLE following_users (user_id TEXT, name TEXT)')
    owner = LegacyLibraryCopies(root, source=source)
    before = len(services._FAILED_LEGACY_COPY_OWNERS)
    def failed(self, *args, **kwargs):
        self._legacy_copy_owner = owner
        self.closing = True
        self.closed = False
        raise RuntimeError('shutdown unverified')
    monkeypatch.setattr(services.ApplicationServices, '_initialize', failed)
    try:
        with pytest.raises(RuntimeError, match='shutdown unverified'):
            services.ApplicationServices()
        with pytest.raises(LegacyCopyError):
            LegacyLibraryCopies(root, source=source)
    finally:
        del services._FAILED_LEGACY_COPY_OWNERS[before:]
        owner.close()


def test_successful_rotation_retains_room_for_the_next_snapshot(tmp_path):
    old, root = tmp_path / 'old', tmp_path / 'managed'
    old.mkdir()
    root.mkdir()
    source = old / 'library.sqlite'
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE works (work_id TEXT, title TEXT, page_count INTEGER, user_id TEXT, tags TEXT, is_deleted INTEGER)')
        conn.execute('CREATE TABLE following_users (user_id TEXT, name TEXT)')
    size = source.stat().st_size
    limit = 65536 + size * 4 + 100
    for _ in range(3):
        owner = LegacyLibraryCopies(root, source=source, max_generations=3, max_bytes=limit)
        try:
            assert owner.snapshot().is_file()
        finally:
            owner.close()


def test_same_failure_source_is_remapped_to_frozen_copy(tmp_path):
    from pixivvault_nicegui.services import ApplicationServices
    from test_managed_download_services import FakeSource
    old, images, root = tmp_path / 'old', tmp_path / 'images', tmp_path / 'snapshots'
    for folder in (old, images, root):
        folder.mkdir()
    source = old / 'library.sqlite'
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE works (work_id TEXT, title TEXT, page_count INTEGER, user_id TEXT, tags TEXT, is_deleted INTEGER)')
        conn.execute('CREATE TABLE following_users (user_id TEXT, name TEXT)')
    service = ApplicationServices(live_client=FakeSource(), pixiv_download_root=tmp_path / 'downloads',
                                  library_readonly_db=source, library_image_root=images,
                                  failed_queue_readonly_db=source, legacy_copy_root=root)
    try:
        assert service.failed_queue_readonly_db == service.library_readonly_db
        assert service.failed_queue_source_db == source
        assert service.failed_queue_readonly_db != source
        from pixivvault_nicegui.legacy_ledger_rebuild import _path_digest
        assert service.duplicate_inspection.source_db_path_sha256 == _path_digest(source.resolve(strict=True))
    finally:
        service.close()


def test_registration_reports_active_legacy_source_without_reading_it(tmp_path, monkeypatch):
    from pixivvault_nicegui import bootstrap
    old, images = tmp_path / 'old', tmp_path / 'images'
    old.mkdir()
    images.mkdir()
    source = old / 'library.sqlite'
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE works (work_id TEXT)')
    (old / 'library.sqlite-wal').write_bytes(b'fake')
    monkeypatch.setattr(bootstrap, '_registered', False)
    with pytest.raises(LegacyCopyError):
        bootstrap.register_pages(library_readonly_db=source, library_image_root=images)


def test_cli_accepts_explicit_isolated_snapshot_root(tmp_path):
    from pixivvault_nicegui.bootstrap import parse_args
    old, images, root = tmp_path / 'old', tmp_path / 'images', tmp_path / 'snapshots'
    for folder in (old, images, root):
        folder.mkdir()
    source = old / 'library.sqlite'
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE works (work_id TEXT)')
    config = parse_args(['--check', '--library-readonly-db', str(source),
                         '--library-image-root', str(images), '--legacy-copy-root', str(root)])
    assert config.legacy_copy_root == root


def test_cli_reports_missing_snapshot_destination_without_starting_ui(tmp_path, monkeypatch, capsys):
    from pixivvault_nicegui import bootstrap
    from nicegui import ui
    old, images = tmp_path / 'old', tmp_path / 'images'
    old.mkdir()
    images.mkdir()
    source = old / 'library.sqlite'
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE works (work_id TEXT)')
    monkeypatch.setattr(bootstrap, '_registered', False)
    monkeypatch.setattr(ui, 'run', lambda **kwargs: pytest.fail('UI must not start'))
    assert bootstrap.main(['--library-readonly-db', str(source), '--library-image-root', str(images)]) == 2
    assert 'snapshot destination is required' in capsys.readouterr().err


def test_native_snapshot_failure_uses_recovery_page(tmp_path, monkeypatch):
    from pixivvault_nicegui import bootstrap, managed_network_stop_shared_scope as shared
    from pixivvault_nicegui.native_profile import NativeProfileStore
    from test_stage5_r1_native_first_draft_shared import _stub_native, _generated_shared_owner
    scope = _generated_shared_owner(tmp_path)
    monkeypatch.setattr(shared, 'open_existing_windows_user_stop', lambda: scope)
    calls, recovered, registered = _stub_native(monkeypatch)
    def fail(**kwargs):
        raise LegacyCopyError('synthetic unavailable')
    monkeypatch.setattr(bootstrap, 'register_pages', fail)
    profile_root = tmp_path / 'generated-profile'
    profile = NativeProfileStore(profile_root)
    profile.close()
    assert bootstrap.main(['--native', '--profile-root', str(profile_root)]) == 0
    assert len(calls) == len(recovered) == 1 and registered == []
    assert recovered[0][1] == {'legacy_copy_error': True}


def test_copy_root_cannot_overlap_learning_storage(tmp_path):
    from pixivvault_nicegui.services import ApplicationServices
    old, images, root = tmp_path / 'old', tmp_path / 'images', tmp_path / 'learning'
    for folder in (old, images, root):
        folder.mkdir()
    source = old / 'library.sqlite'
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE works (work_id TEXT, title TEXT, page_count INTEGER, user_id TEXT, tags TEXT, is_deleted INTEGER)')
        conn.execute('CREATE TABLE following_users (user_id TEXT, name TEXT)')
    with pytest.raises(LegacyCopyError):
        ApplicationServices(library_readonly_db=source, library_image_root=images,
                            learning_sandbox=root, legacy_copy_root=root)
    assert list(root.iterdir()) == []


def test_failure_source_alias_is_never_read_as_original(tmp_path):
    from pixivvault_nicegui.services import ApplicationServices
    from test_managed_download_services import FakeSource
    old, images, root = tmp_path / 'old', tmp_path / 'images', tmp_path / 'snapshots'
    for folder in (old, images, root):
        folder.mkdir()
    (old / 'sub').mkdir()
    source = old / 'library.sqlite'
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE works (work_id TEXT, title TEXT, page_count INTEGER, user_id TEXT, tags TEXT, is_deleted INTEGER)')
        conn.execute('CREATE TABLE following_users (user_id TEXT, name TEXT)')
    alias = old / 'sub' / '..' / 'library.sqlite'
    service = ApplicationServices(live_client=FakeSource(), pixiv_download_root=tmp_path / 'downloads',
                                  library_readonly_db=source, library_image_root=images,
                                  failed_queue_readonly_db=alias, legacy_copy_root=root)
    try:
        assert service.failed_queue_readonly_db == service.library_readonly_db
        assert service.failed_queue_readonly_db != source
    finally:
        service.close()


def test_late_initialization_failure_retains_owner_if_worker_shutdown_fails(tmp_path, monkeypatch):
    from pixivvault_nicegui import services
    from pixivvault_nicegui.job_service import JobService
    from test_managed_download_services import FakeSource
    old, images, root = tmp_path / 'old', tmp_path / 'images', tmp_path / 'snapshots'
    for folder in (old, images, root):
        folder.mkdir()
    source = old / 'library.sqlite'
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE works (work_id TEXT, title TEXT, page_count INTEGER, user_id TEXT, tags TEXT, is_deleted INTEGER)')
        conn.execute('CREATE TABLE following_users (user_id TEXT, name TEXT)')
    before = len(services._FAILED_LEGACY_COPY_OWNERS)
    with monkeypatch.context() as faults:
        def fail(*args, **kwargs):
            raise RuntimeError('synthetic late failure')
        def busy(*args, **kwargs):
            raise RuntimeError('synthetic shutdown unverified')
        faults.setattr('pixivvault_nicegui.duplicate_inspection.ManagedDuplicateInspection', fail)
        faults.setattr(JobService, 'close', busy)
        with pytest.raises(RuntimeError, match='synthetic late failure'):
            services.ApplicationServices(live_client=FakeSource(), pixiv_download_root=tmp_path / 'downloads',
                                         library_readonly_db=source, library_image_root=images, legacy_copy_root=root)
        try:
            with pytest.raises(LegacyCopyError):
                LegacyLibraryCopies(root, source=source, protected_roots=(images,))
        finally:
            retained = services._FAILED_LEGACY_COPY_OWNERS[before:]
    try:
        assert len(retained) == 1
        retained[0].close()
    finally:
        del services._FAILED_LEGACY_COPY_OWNERS[before:]


def test_failure_dismissal_survives_frozen_copy_rotation(tmp_path):
    import shutil
    from types import SimpleNamespace
    from pixivvault_nicegui.failure_record_actions import FailureRecordActions
    from pixivvault_nicegui.failed_records_repository import immutable_copy_signature
    source = tmp_path / 'original.sqlite'
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE failed_queue (work_id TEXT,user_id TEXT,title TEXT,work_type TEXT,error_reason TEXT,failed_at TEXT,retry_count INTEGER)')
        conn.execute("INSERT INTO failed_queue VALUES ('1','9','fake','illust','fake','2020-01-01',0)")
    copy1, copy2 = tmp_path / 'one.sqlite', tmp_path / 'two.sqlite'
    shutil.copyfile(source, copy1)
    shutil.copyfile(source, copy2)
    def service(path, origin):
        return SimpleNamespace(closing=False, failed_queue_readonly_db=path, library_readonly_db=None,
            failed_queue_source_db=origin, library_source_db=None,
            _failed_copy_signature=immutable_copy_signature(path), similarity_source=None)
    root = tmp_path / 'dismissals'
    actions = FailureRecordActions(service(copy1, source), root)
    try:
        snapshot = actions.snapshot()
        assert len(snapshot.records) == 1
        actions.dismiss(snapshot, snapshot.records)
    finally:
        actions.close()
    actions = FailureRecordActions(service(copy2, source), root)
    try:
        assert actions.snapshot().records == ()
    finally:
        actions.close()
    another = tmp_path / 'different-source.sqlite'
    shutil.copyfile(source, another)
    actions = FailureRecordActions(service(copy2, another), root)
    try:
        assert len(actions.snapshot().records) == 1
    finally:
        actions.close()


def test_duplicate_inspection_uses_original_namespace_across_copy_generations(tmp_path):
    import shutil
    from pixivvault_nicegui.duplicate_inspection import ManagedDuplicateInspection
    from pixivvault_nicegui.legacy_quarantine import LegacyQuarantine
    from pixivvault_nicegui.legacy_ledger_rebuild import _path_digest
    from test_original_scan_publication import opened
    initial, owner, legacy, db, snapshot = opened(tmp_path)
    root = legacy.catalog.root
    source_digest = _path_digest(db.resolve())
    copy1, copy2 = tmp_path / 'one.sqlite', tmp_path / 'two.sqlite'
    shutil.copyfile(db, copy1)
    shutil.copyfile(db, copy2)
    try:
        initial.scan()
        initial.close()
        first = ManagedDuplicateInspection(owner, legacy=LegacyQuarantine(root, snapshot, owner=owner),
            undecidable_db=copy1, source_db_path_sha256=source_digest)
        try:
            groups = first.undecidable_groups()
            assert len(groups) == 1
            assert first.resolve_undecidable(groups[0], '1') == ()
        finally:
            first.close()
        second = ManagedDuplicateInspection(owner, legacy=LegacyQuarantine(root, snapshot, owner=owner),
            undecidable_db=copy2, source_db_path_sha256=source_digest)
        try:
            assert second.undecidable_groups() == ()
            second.scan()
            assert second.undecidable_groups() == ()
        finally:
            second.close()
    finally:
        initial.close()
        owner.close()


def test_post_publication_error_is_reconciled_without_losing_snapshot(tmp_path, monkeypatch):
    import os
    source, root = _fixture_paths(tmp_path)
    copies = LegacyLibraryCopies(root, source=source, max_generations=1, max_bytes=1048576)
    old = copies.snapshot()
    copies.close()
    with sqlite3.connect(source) as conn:
        conn.execute("INSERT INTO works (work_id) VALUES ('8')")
    expected = source.read_bytes()
    copies = LegacyLibraryCopies(root, source=source, max_generations=1, max_bytes=1048576)
    from pixivvault_nicegui import legacy_library_copies as module
    original_replace = module._publish_copy
    def replace_then_error(src, dst):
        original_replace(src, dst)
        raise OSError('synthetic after commit')
    monkeypatch.setattr(module, '_publish_copy', replace_then_error)
    try:
        result = copies.snapshot()
        assert result == old
        assert source.read_bytes() == expected
        with sqlite3.connect(result) as connection:
            assert connection.execute('PRAGMA application_id').fetchone() == (0x5056434C,)
            assert connection.execute('PRAGMA user_version').fetchone() == (1,)
            assert ('8',) in connection.execute('SELECT work_id FROM works').fetchall()
    finally:
        copies.close()


def test_foreign_marker_is_rejected_before_creating_a_lock(tmp_path):
    old, root = tmp_path / 'old', tmp_path / 'managed'
    old.mkdir()
    root.mkdir()
    source = old / 'library.sqlite'
    source.write_bytes(b'synthetic')
    marker = root / '.legacy-library-copies'
    marker.write_bytes(b'foreign marker')
    with pytest.raises(LegacyCopyError):
        LegacyLibraryCopies(root, source=source)
    assert {p.name for p in root.iterdir()} == {marker.name}
    assert marker.read_bytes() == b'foreign marker'


def test_lowered_generation_limit_is_enforced(tmp_path):
    old, root = tmp_path / 'old', tmp_path / 'managed'
    old.mkdir()
    root.mkdir()
    source = old / 'library.sqlite'
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE works (work_id TEXT, title TEXT, page_count INTEGER, user_id TEXT, tags TEXT, is_deleted INTEGER)')
        conn.execute('CREATE TABLE following_users (user_id TEXT, name TEXT)')
    for _ in range(5):
        owner = LegacyLibraryCopies(root, source=source, max_generations=5, max_bytes=1048576)
        try:
            owner.snapshot()
        finally:
            owner.close()
    owner = LegacyLibraryCopies(root, source=source, max_generations=2, max_bytes=1048576)
    try:
        current = owner.snapshot()
        assert current.is_file()
        assert len(tuple(root.glob('*.sqlite'))) <= 2
    finally:
        owner.close()


def test_marker_initialized_before_failed_lock_can_be_reopened(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from pixivvault_nicegui import legacy_library_copies as module
    old, root = tmp_path / 'old', tmp_path / 'managed'
    old.mkdir()
    root.mkdir()
    source = old / 'library.sqlite'
    source.write_bytes(b'synthetic')
    @contextmanager
    def fail_lock(*args, **kwargs):
        raise OSError('synthetic lock failure')
        yield
    with monkeypatch.context() as faults:
        faults.setattr(module, '_owner_lock', fail_lock)
        with pytest.raises(LegacyCopyError):
            LegacyLibraryCopies(root, source=source)
    assert {p.name for p in root.iterdir()} == {'.legacy-library-copies'}
    owner = LegacyLibraryCopies(root, source=source)
    owner.close()


def test_failed_generation_cleanup_does_not_report_success(tmp_path, monkeypatch):
    from pixivvault_nicegui.legacy_library_copies import LegacyCopyCommitUnconfirmed
    old, root = tmp_path / 'old', tmp_path / 'managed'
    old.mkdir()
    root.mkdir()
    source = old / 'library.sqlite'
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE works (work_id TEXT, title TEXT, page_count INTEGER, user_id TEXT, tags TEXT, is_deleted INTEGER)')
        conn.execute('CREATE TABLE following_users (user_id TEXT, name TEXT)')
    for _ in range(3):
        owner = LegacyLibraryCopies(root, source=source, max_generations=3, max_bytes=1048576)
        try:
            owner.snapshot()
        finally:
            owner.close()
    owner = LegacyLibraryCopies(root, source=source, max_generations=1, max_bytes=1048576)
    from pixivvault_nicegui import legacy_library_copies as module
    original_unlink = module._mutate_owned_copy
    def deny_old(path, expected, destination=None):
        if destination is None and path.parent == root and path.name.endswith('.sqlite') and not path.name.startswith('.'):
            raise OSError('synthetic cleanup failure')
        return original_unlink(path, expected, destination)
    monkeypatch.setattr(module, '_mutate_owned_copy', deny_old)
    try:
        with pytest.raises(LegacyCopyCommitUnconfirmed):
            owner.snapshot()
    finally:
        owner.close()
