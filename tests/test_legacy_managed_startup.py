"""Reopen indexless artwork storage with its real sibling owners."""
import ast
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from pixivvault_nicegui.author_media import AuthorMediaService
from pixivvault_nicegui.duplicate_inspection import ManagedDuplicateInspection
from pixivvault_nicegui.legacy_author_sync import LegacyAuthorBridge
from pixivvault_nicegui.legacy_ledger_rebuild import publish_rebuilt_ledger, rebuild_undecidable_ledger
from pixivvault_nicegui import legacy_quarantine
from pixivvault_nicegui.library_models import LibrarySnapshot
from pixivvault_nicegui.managed_downloads import ManagedDownloadError, ManagedPixivDownloads
from pixivvault_nicegui import managed_generations as generations
from pixivvault_nicegui.managed_generations import ManagedGenerations
from pixivvault_nicegui.managed_network_stop_integration import ManagedNetworkStopIntegration
from pixivvault_nicegui.managed_scheduler import ManagedScheduler
from pixivvault_nicegui.managed_viewer_page import _prepare as prepare_viewer_pages
from pixivvault_nicegui.pixiv_write_client import _append_follow_response
from pixivvault_nicegui.services import ApplicationServices
from test_fixloop2_library_nullable_title import make_bridge
from test_managed_download_services import FakeSource
from test_managed_downloads import Client, record
from test_original_ledger_rebuild import fixture as legacy_ledger_fixture
from test_v_dl_cap_20261117 import make_generation, make_legacy, make_root, png, publish
from test_w_round6_contract import snapshot


_ARTWORK_ROOTS = {'owner.root', 'self.owner.root', 'self.managed_downloads.root',
                  'services.managed_downloads.root', 'self.managed_root', 'managed_root', 'pixiv_download_root'}
_ROOT_ALIASES = {
    'legacy_ledger_rebuild.py': {'managed'},
    'managed_download_requests.py': {'self.root'},
    'managed_downloads.py': {'self.root'},
    'managed_network_stop_integration.py': {'root'},
    'managed_scheduler.py': {'root'},
    'managed_viewer_page.py': {'root'},
    'metadata_ledger.py': {'root'},
    'pixiv_write_client.py': {'root'},
    'services.py': {'parent'},  # _failure_actions chooses the artwork root if preferences are absent.
}
_ROOT_CONSUMERS = {
    # Constructors, source reads and plumbing reached via the artwork root.
    'ApplicationServices', 'LegacyAuthorBridge', 'ManagedDownloadRequests', 'ManagedMetadataLedger',
    'ManagedNetworkStopIntegration', 'ManagedScheduler', 'Path', 'StopMigrationService._verify_archive',
    '_build_owned_media_readers', '_nonoverlap', '_publication_inputs', '_safe_path', '_validate_managed_paths',
    'desktop_saved_roots', 'get_services', 'load_rebuilt_ledger', 'logical_path', 'maintenance.allows',
    'native_io_path', 'publish_rebuilt_ledger', 'rebuild_undecidable_ledger', 'replace',
    'require_no_unmigrated_root_stop', 'resolve_rebuilt_group', 'validate_media_roots', 'writer_fence',
    'source.is_relative_to',  # viewer_folders: a read-only check against the managed root.
}
_SQLITE_TRANSIENTS = {
    '.pixivvault-legacy-author-sync.sqlite3-journal',
    '.pixivvault-managed-metadata.sqlite3-journal', '.pixivvault-managed-scheduler.sqlite3-journal',
    '.pixivvault-network-stop.sqlite3-journal', '.pixivvault-network-stop.sqlite3-shm',
    '.pixivvault-network-stop.sqlite3-wal',
    'author-media.sqlite3-journal', 'author-media.sqlite3-shm', 'author-media.sqlite3-wal', 'jobs.sqlite3-journal',
}


def _source_artwork_layout():
    """Check concrete children and calls directly receiving the artwork root."""
    sources = Path(__file__).resolve().parents[1] / 'src' / 'pixivvault_nicegui'
    children = {}
    consumers = {}
    for path in sources.rglob('*.py'):
        source = path.read_text(encoding='utf-8')
        if path.name not in _ROOT_ALIASES and not any(root in source for root in _ARTWORK_ROOTS):
            continue
        tree = ast.parse(source, filename=str(path))
        constants = {statement.targets[0].id: statement.value.value
                     for statement in tree.body if isinstance(statement, ast.Assign)
                     and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name)
                     and isinstance(statement.value, ast.Constant)}
        roots = _ARTWORK_ROOTS | _ROOT_ALIASES.get(path.name, set())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                args = [*node.args, *(kw.value for kw in node.keywords)]
                if any(ast.unparse(arg) in _ARTWORK_ROOTS for arg in args):
                    consumers[ast.unparse(node.func)] = f'{path.name}:{node.lineno}'
            if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Div):
                continue
            base = node.left
            if isinstance(base, ast.Call) and base.args and ast.unparse(base.func) in {'Path', 'native_io_path'}:
                base = base.args[0]
            if ast.unparse(base) not in roots:
                continue
            name = (node.right.value if isinstance(node.right, ast.Constant)
                    else constants.get(node.right.id) if isinstance(node.right, ast.Name) else None)
            if isinstance(name, str):
                children[name] = f'{path.name}:{node.lineno}'
    return children, consumers


@pytest.fixture
def old_artwork_root(tmp_path):
    root = make_root(tmp_path).rename(tmp_path / 'artwork-originals')
    for name in ('generations', 'generation-incomplete', 'incomplete', 'request-incomplete',
                 'requests', 'extension-commands', 'viewer-pages'):
        (root / name).mkdir()
    for relative in ('.pixivvault-managed-metadata.lock', '.pixivvault-managed-metadata.sqlite3',
                     'author-media.sqlite3', 'jobs.sqlite3', 'jobs.sqlite3.lock',
                     'requests/example.json', 'extension-commands/extension-owner-v1',
                     'extension-commands/bindings.sqlite3', 'extension-commands/jobs.sqlite3',
                     'extension-commands/jobs.sqlite3.lock'):
        (root / relative).write_bytes(relative.encode('ascii'))
    make_legacy(root, '123')
    return root


@pytest.mark.parametrize('missing_folders', [False, True])
def test_indexless_artwork_root_preserves_existing_outputs_on_both_opens(old_artwork_root, missing_folders):
    root = old_artwork_root
    if missing_folders:
        (root / 'generation-incomplete').rmdir()
        (root / 'incomplete').rmdir()
    before = snapshot(root)
    client = Client()
    receipt, = (root / 'outputs').glob('*/receipt.json')
    relative = receipt.relative_to(root).as_posix()
    digest = hashlib.sha256(receipt.read_bytes()).hexdigest()
    assert not (root / 'active-generations.json').exists()

    for attempt in range(2):
        owner = ManagedPixivDownloads(root, client, resolve_record=lambda _: record())
        try:
            assert (root / 'active-generations.json').is_file()
            (saved_relative, saved, saved_digest), = owner.saved_receipts()
            assert (saved_relative, saved['work_id'], saved_digest) == (relative, '123', digest)
            assert owner.read_saved_page(relative, 0, digest) == png()
        finally:
            owner.close()
        after = snapshot(root)
        if attempt == 0:
            assert {name: value for name, value in after.items() if name in before} == before
            first_open = after
        else:
            assert after == first_open

    assert (client.identity, client.urls, client.count) == (0, 0, 0)


def test_indexless_artwork_root_with_real_sibling_owners(tmp_path):
    db, images, root = legacy_ledger_fixture(tmp_path)
    author_fixture = tmp_path / 'author-fixture'
    author_fixture.mkdir()
    source_bridge, author_db = make_bridge(author_fixture, 'example')
    source_bridge.close()
    # Separate the generation bootstrap from the real service graph so every
    # ledger it creates remains present for the indexless open below.
    ManagedGenerations(root)
    generation_files = [path for path in root.glob('active-generations*') if path.is_file()]
    source = FakeSource()
    services = ApplicationServices(count=0, pixiv_download_root=root, live_client=source,
                                   failed_queue_readonly_db=author_db)
    try:
        author = AuthorMediaService(services)
        try:
            assert author.configure_author_target('456', 'illust', expected_revision=0).target_type == 'illust'
        finally:
            author.close()
        services.enable_managed_metadata()
        extension = services.enable_extension_commands()
        assert extension.jobs.snapshot() == ()
        services._managed_requests.store('synthetic-intent', record())
        assert services._managed_requests.load('synthetic-intent') == record()
        prepare_viewer_pages(services.managed_downloads)
        scheduler = ManagedScheduler(root)
        scheduler.close()
        ManagedNetworkStopIntegration(root)
        services._failure_actions().snapshot()
        sync = LegacyAuthorBridge(author_db, source_bridge.readonly_media_root, root)
        try:
            sync.observe_author('11', (), scan_id='a' * 32)
        finally:
            sync.close()
        _append_follow_response(root, 'follow', 200, {})
        quarantine = legacy_quarantine.LegacyQuarantine(images, LibrarySnapshot(()), owner=services.managed_downloads)
        inspection = ManagedDuplicateInspection(services.managed_downloads, legacy=quarantine, undecidable_db=db)
        try:
            with quarantine._lock:
                quarantine._ensure()
            inspection.scan()
        finally:
            inspection.close()
    finally:
        services.close()

    for path in generation_files:
        path.unlink()
    assert not (root / 'active-generations.json').exists()
    before = snapshot(root)
    generated = {path.name for path in root.iterdir()}
    # These names refer to other owners or to the separately tested managed quarantine.
    source_only = {'.pixivvault-novel-jobs.json', 'migration-record.json', 'quarantine'}
    children, consumers = _source_artwork_layout()
    missing = {name: site for name, site in children.items()
               if name not in generated and name not in source_only}
    assert missing == {}
    assert {name: site for name, site in consumers.items() if name not in _ROOT_CONSUMERS} == {}
    assert set(consumers) == _ROOT_CONSUMERS
    allowed = generations._SHARED_ROOT_FILES | generations._SHARED_ROOT_DIRS
    assert allowed - _SQLITE_TRANSIENTS <= generated
    assert ManagedGenerations(root).entries() == {}
    assert {name: value for name, value in snapshot(root).items() if name in before} == before
    assert source.calls == []


def test_indexless_artwork_root_preserves_rebuilt_legacy_ledger(tmp_path):
    db, images, root = legacy_ledger_fixture(tmp_path)
    args = dict(undecidable_db=db, images_root=images, managed_root=root)
    rebuilt = rebuild_undecidable_ledger(**args)
    publish_rebuilt_ledger(rebuilt, **args)
    make_legacy(root, '123')
    before = snapshot(root)

    owner = ManagedPixivDownloads(root, None, resolve_record=lambda _: record())
    try:
        assert owner.saved_receipts()[0][1]['work_id'] == '123'
    finally:
        owner.close()
    assert {name: value for name, value in snapshot(root).items() if name in before} == before


@pytest.mark.parametrize('torn', [False, True])
def test_indexless_artwork_root_preserves_interrupted_legacy_quarantine_marker(
        old_artwork_root, tmp_path, monkeypatch, torn):
    images = tmp_path / 'legacy-images'
    images.mkdir()
    root = old_artwork_root
    legacy = legacy_quarantine.LegacyQuarantine(
        images, LibrarySnapshot(()), owner=SimpleNamespace(root=root, closed=False))
    original_write = legacy_quarantine._write

    def interrupt(path, data):
        original_write(path, data[:60] if torn else data)
        raise OSError('interrupted before publishing quarantine identity')

    with monkeypatch.context() as patch:
        patch.setattr(legacy_quarantine, '_write', interrupt)
        with pytest.raises(OSError, match='interrupted before publishing'):
            legacy._ensure()
    stage, = root.glob('legacy-identity-*.next')
    assert stage.is_file()
    assert not (root / 'legacy-quarantine' / 'IDENTITY.json').exists()
    before = snapshot(root)

    owner = ManagedPixivDownloads(root, None, resolve_record=lambda _: record())
    try:
        assert owner.saved_receipts()[0][1]['work_id'] == '123'
    finally:
        owner.close()
        legacy.close()
    assert {name: value for name, value in snapshot(root).items() if name in before} == before


@pytest.mark.parametrize('bad_stage', ['name', 'payload', 'missing folder'])
def test_indexless_artwork_root_rejects_unrecognized_quarantine_staging(old_artwork_root, bad_stage):
    root = old_artwork_root
    if bad_stage != 'missing folder':
        (root / 'legacy-quarantine').mkdir()
    suffix = 'a' * 31 if bad_stage == 'name' else 'a' * 32
    (root / f'legacy-identity-{suffix}.next').write_bytes(b'not a marker' if bad_stage == 'payload' else b'{')
    before = snapshot(root)
    with pytest.raises(ManagedDownloadError):
        ManagedPixivDownloads(root, None, resolve_record=lambda _: record())
    assert snapshot(root) == before


@pytest.mark.parametrize('name', ['active-generations.floor', 'active-generations.v5-committed',
                                 'active-generations.sequence-required', 'active-generations-*.mark'])
def test_indexless_artwork_root_rejects_a_single_generation_witness(old_artwork_root, tmp_path, name):
    witness_parent = tmp_path / 'witness'
    witness_parent.mkdir()
    witness_root = make_root(witness_parent)
    generation = ManagedGenerations(witness_root)
    publish(generation, witness_root, '456')
    witness, = witness_root.glob(name)
    root = old_artwork_root
    (root / witness.name).write_bytes(witness.read_bytes())
    before = snapshot(root)
    with pytest.raises(ManagedDownloadError):
        ManagedPixivDownloads(root, None, resolve_record=lambda _: record())
    assert snapshot(root) == before


@pytest.mark.parametrize('kind', ['file as directory', 'directory as file', 'symlink', 'hard link'])
def test_indexless_artwork_root_rejects_wrong_kind_or_link_at_shared_name(old_artwork_root, tmp_path, kind):
    root = old_artwork_root
    if kind == 'file as directory':
        (root / 'legacy-ledger').write_bytes(b'wrong kind')
    elif kind == 'directory as file':
        (root / '.pixivvault-managed-scheduler.sqlite3').mkdir()
    else:
        target = tmp_path / 'outside.txt'
        target.write_bytes(b'outside')
        link = root / '.pixivvault-managed-scheduler.sqlite3'
        if kind == 'symlink':
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                pytest.skip('symlink creation unavailable')
        else:
            os.link(target, link)
    before = snapshot(root)
    with pytest.raises(ManagedDownloadError, match='managed (?:storage link|file|directory|storage reparse point) rejected'):
        ManagedPixivDownloads(root, None, resolve_record=lambda _: record())
    assert snapshot(root) == before


@pytest.mark.parametrize('unexpected', ['jobs.sqlite3.unrecognized', 'generations',
                                        'incomplete', 'generation-incomplete', 'journal', 'head',
                                        'quarantine', 'invalid receipt', 'generation in receipt'])
def test_indexless_artwork_root_rejects_unrecognized_or_inconsistent_storage(old_artwork_root, unexpected):
    root = old_artwork_root
    if unexpected == 'generations':
        make_generation(root, '456')
    elif unexpected in ('incomplete', 'generation-incomplete'):
        (root / unexpected / 'unrecognized').write_bytes(b'evidence')
    elif unexpected == 'journal':
        (root / f'active-generations-{"a" * 32}.journal').write_bytes(b'evidence')
    elif unexpected == 'head':
        (root / 'active-generations.head').write_bytes(b'evidence')
    elif unexpected == 'quarantine':
        (root / 'quarantine' / 'operations' / ('a' * 32)).mkdir(parents=True)
    elif unexpected == 'invalid receipt':
        next((root / 'outputs').glob('*/receipt.json')).write_bytes(b'{}')
    elif unexpected == 'generation in receipt':
        receipt = next((root / 'outputs').glob('*/receipt.json'))
        value = json.loads(receipt.read_bytes())
        receipt.write_bytes(json.dumps({**value, 'generation': 1}).encode('ascii'))
    else:
        (root / unexpected).write_bytes(b'evidence')
    before = snapshot(root)
    with pytest.raises(ManagedDownloadError):
        ManagedPixivDownloads(root, None, resolve_record=lambda _: record())
    assert snapshot(root) == before
