"""Unsupported legacy indexes are refused at both entrances without writes."""
import json

import pytest

from pixivvault_nicegui import managed_generations as mg
from pixivvault_nicegui.managed_downloads import ManagedDownloadError, ManagedPixivDownloads
from test_v_dl_cap_20261117 import make_root


@pytest.mark.parametrize('version', [1, 2, 3, 4])
@pytest.mark.parametrize('opener', ['generations', 'downloads'])
def test_old_index_is_rejected_without_creating_anything(tmp_path, version, opener):
    root = make_root(tmp_path)
    packet = {'version': version, 'entries': {}}
    if version in (3, 4):
        packet.update(exclusions={}, quarantine_operations={})
    (root / 'active-generations.json').write_text(json.dumps(packet))

    def snapshot():
        return {str(p.relative_to(root)): p.read_bytes() if p.is_file() else None
                for p in root.rglob('*')}

    before = snapshot()
    with pytest.raises(ManagedDownloadError):
        if opener == 'generations':
            mg.ManagedGenerations(root)
        else:
            ManagedPixivDownloads(root, None, resolve_record=lambda *_: None)
    assert snapshot() == before
