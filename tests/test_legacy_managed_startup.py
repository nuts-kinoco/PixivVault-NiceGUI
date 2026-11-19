"""Reopen indexless artwork storage with its real sibling owners."""
import hashlib
import json

import pytest

from pixivvault_nicegui.author_media import AuthorMediaService
from pixivvault_nicegui.managed_downloads import ManagedDownloadError, ManagedPixivDownloads
from pixivvault_nicegui.managed_generations import ManagedGenerations
from pixivvault_nicegui.managed_network_stop_integration import ManagedNetworkStopIntegration
from pixivvault_nicegui.managed_scheduler import ManagedScheduler
from pixivvault_nicegui.managed_viewer_page import _prepare as prepare_viewer_pages
from pixivvault_nicegui.services import ApplicationServices
from test_managed_download_services import FakeSource
from test_managed_downloads import Client, record
from test_v_dl_cap_20261117 import make_generation, make_legacy, make_root, png
from test_w_round6_contract import snapshot


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

    for _ in range(2):
        owner = ManagedPixivDownloads(root, client, resolve_record=lambda _: record())
        try:
            assert (root / 'active-generations.json').is_file()
            (saved_relative, saved, saved_digest), = owner.saved_receipts()
            assert (saved_relative, saved['work_id'], saved_digest) == (relative, '123', digest)
            assert owner.read_saved_page(relative, 0, digest) == png()
            assert {name: snapshot_value for name, snapshot_value in snapshot(root).items()
                    if name in before} == before
        finally:
            owner.close()

    assert (client.identity, client.urls, client.count) == (0, 0, 0)


def test_indexless_artwork_root_with_real_sibling_owners(tmp_path):
    root = make_root(tmp_path)
    # Separate the generation bootstrap from the real service graph so every
    # ledger it creates remains present for the indexless open below.
    ManagedGenerations(root)
    generation_files = [path for path in root.glob('active-generations*') if path.is_file()]
    source = FakeSource()
    services = ApplicationServices(count=0, pixiv_download_root=root, live_client=source)
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
    finally:
        services.close()

    for path in generation_files:
        path.unlink()
    assert not (root / 'active-generations.json').exists()
    before = snapshot(root)
    assert {'.pixivvault-managed-metadata.sqlite3', 'author-media.sqlite3', 'jobs.sqlite3',
            'extension-commands', 'requests', 'viewer-pages'} <= before.keys()
    assert ManagedGenerations(root).entries() == {}
    assert {name: value for name, value in snapshot(root).items() if name in before} == before
    assert source.calls == []


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
