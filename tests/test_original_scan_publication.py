from required_network_guard import no_network
import pytest
from test_original_ledger_rebuild import fixture,store
from pixivvault_nicegui.models import Artwork
from pixivvault_nicegui.library_models import LibrarySnapshot,LibraryWork
from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads
from pixivvault_nicegui.legacy_quarantine import LegacyQuarantine
from pixivvault_nicegui.duplicate_inspection import ManagedDuplicateInspection


def opened(tmp_path):
    db,images,managed=fixture(tmp_path);store(images)
    snapshot=LibrarySnapshot(tuple(LibraryWork(Artwork(w,'題名','合成作者',page_count=n),'9') for w,n in [('1',1),('2',3)]))
    owner=ManagedPixivDownloads(managed,object(),resolve_record=lambda _:None)
    legacy=LegacyQuarantine(images,snapshot,owner=owner)
    return ManagedDuplicateInspection(owner,legacy=legacy,undecidable_db=db),owner,legacy,db,snapshot


def test_explicit_scan_rebuilds_undecidable_then_persists_across_reopen(tmp_path):
    source,owner,legacy,db,snapshot=opened(tmp_path)
    try:
        source.scan()
        assert [g['ambiguous_group'] for g in source.undecidable_groups()]==['9:題名']
        root=legacy.catalog.root;source.close()
        second=ManagedDuplicateInspection(owner,legacy=LegacyQuarantine(root,snapshot,owner=owner),undecidable_db=db)
        assert [g['ambiguous_group'] for g in second.undecidable_groups()]==['9:題名']
        second.close()
    finally:source.close();owner.close()


def test_failed_scan_publication_keeps_previous_candidates(tmp_path,monkeypatch):
    from pixivvault_nicegui import legacy_ledger_rebuild as module
    source,owner,legacy,db,_=opened(tmp_path)
    try:
        previous=source.scan();groups=source.undecidable_groups()
        monkeypatch.setattr(module,'publish_rebuilt_ledger',lambda *a,**k:(_ for _ in ()).throw(OSError('generated publish fault')))
        with pytest.raises(OSError):source.scan()
        assert source.latest_scan is previous
        assert source.undecidable_groups()==groups
    finally:source.close();owner.close()
