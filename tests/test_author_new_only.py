"""New-only author scans must not become complete deletion observations."""
from types import SimpleNamespace
from pixivvault_nicegui.author_media import AuthorMediaService


def test_new_only_stops_at_verified_same_generation_without_full_crawl():
    calls=[]
    def listing(aid,early_exit_checker):
        row=dict(id='123',user_id=aid,type=0,update_date='2026-01-01')
        assert early_exit_checker(row)
        calls.append('partial')
        return ()
    reader=SimpleNamespace(verify_identity=lambda:None,get_user_works=listing)
    services=SimpleNamespace(closing=False,live_client=reader,legacy_author_bridge=None,
        managed_downloads=SimpleNamespace(verified_saved_receipt=lambda wid:('receipt',{},'sha','2026-01-01')))
    owner=AuthorMediaService.__new__(AuthorMediaService)
    owner.services=services;owner.closed=False;owner.reconcile=lambda:None
    assert owner.preview('91','illust',new_only=True)==()
    assert calls==['partial']
