from types import SimpleNamespace

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.desktop_integration import DesktopActionRejected, DesktopIntegration
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.duplicate_view import DuplicateView
from test_required_legacy_duplicates import select, setup


@pytest.mark.parametrize('relative', [False, True])
def test_original_trash_open_normalizes_configured_image_root(tmp_path, monkeypatch, relative):
    images = tmp_path / 'images'
    (images / 'nested').mkdir(parents=True)
    quarantine = tmp_path / 'quarantine'
    nested = quarantine / 'nested'
    nested.mkdir(parents=True)
    outside = tmp_path / 'outside'
    outside.mkdir()
    monkeypatch.chdir(tmp_path)
    configured = images.relative_to(tmp_path) if relative else images / 'nested' / '..'
    opened = []
    desktop = DesktopIntegration(legacy_image_root=configured,
                                 launcher=SimpleNamespace(open_default=opened.append))

    desktop.open_saved(quarantine, original_quarantine=True)
    for wrong in (nested, outside):
        with pytest.raises(DesktopActionRejected):
            desktop.open_saved(wrong, original_quarantine=True)
    assert opened == [quarantine]


@pytest.mark.asyncio
@pytest.mark.parametrize('original', [False, True])
async def test_healthy_trash_shows_open_location_failure(tmp_path, original):
    source, legacy, owner, *_ = setup(tmp_path)
    client = Client(ui.page('/_p4_x09_open_failure'))
    attempted = []

    async def fail_open(**options):
        attempted.append(options)
        raise DesktopActionRejected('generated launcher failure')

    try:
        with legacy._lock:
            legacy._ensure()
        with client:
            view = DuplicateView(source, open_trash=fail_open)
            view.set_active(True)
            await view.open_trash()
            assert not view.trash_status.visible
            await view.open_trash_location(original=original)
            assert attempted == [{'original': True} if original else {}]
            assert '開けませんでした' in view.trash_status.text
            assert view.trash_status.visible
            await view.refresh_trash()
            assert not view.trash_status.visible
    finally:
        client.delete()
        source.close()
        owner.close()


@pytest.mark.asyncio
async def test_trash_opens_original_and_managed_locations_separately(tmp_path):
    source, legacy, owner, _, images, _ = setup(tmp_path)
    opened = []
    client = Client(ui.page('/_p4_x09_location'))
    try:
        original_trash = images.parent / 'quarantine'
        desktop = DesktopIntegration(allowed_roots=(owner.root,), legacy_image_root=images,
                                     launcher=SimpleNamespace(open_default=opened.append))
        services = SimpleNamespace(duplicate_inspection=source, desktop=desktop, closing=False)

        async def open_trash(**options):
            await ApplicationServices.open_duplicate_quarantine(services, **options)

        with client:
            view = DuplicateView(source, open_trash=open_trash)
            view.set_active(True)
            await view.open_trash()
            assert view.trash_original_open_button.enabled
            await view.open_original_trash_location()
            assert not opened and not original_trash.exists()
            assert '開けませんでした' in view.trash_status.text

            original_trash.mkdir()
            evidence = original_trash / 'original.png'
            evidence.write_bytes(b'generated original quarantine payload')
            await view.open_original_trash_location()
            assert opened == [original_trash]
            with pytest.raises(DesktopActionRejected):
                desktop.open_saved(evidence)

            result = source.execute(select(source, {'1'}), operation_id='a' * 32, mode='copy')
            assert len(result.committed) == 1 and not result.failed
            await view.refresh_trash()
            await view.open_trash_location()
            assert opened == [original_trash, owner.root]
            assert view.trash_copy_count.text.endswith('1件')
            assert evidence.read_bytes() == b'generated original quarantine payload'
    finally:
        client.delete()
        source.close()
        owner.close()
