from types import SimpleNamespace

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.desktop_integration import DesktopActionRejected, DesktopIntegration
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.duplicate_view import DuplicateView
from test_required_legacy_duplicates import select, setup


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
