from pixivvault_nicegui.author_download import AuthorTarget
from pixivvault_nicegui.author_target_selection import (MAX_SELECTED_AUTHORS,
    visible_authors, select_many_authors)
async def _following_service_records(owner):
    return (AuthorTarget('91','Zeta','follow',True,2),
            AuthorTarget('92','Alpha','follow',False,0),
            AuthorTarget('93','Beta','follow',True,1))


def test_follow_mode_sort_favorites_bulk_and_bookmark_bulk_are_really_selected():
    from nicegui import Client, ui
    from pixivvault_nicegui.ui.managed_download_view import ManagedDownloadView
    from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark

    class Service:
        managed_downloads = object()
        jobs = object()
        followed_download_authors = staticmethod(_following_service_records)
        def download_candidates(self):
            return tuple(RemoteBookmark(str(i),'illustration','show',f'Work {i}','91',
                                        'Author',(),'','',1,0,0) for i in range(1,4))

    import asyncio
    async def exercise():
        client = Client(ui.page('/_u_targets_mode'))
        try:
            with client:
                view = ManagedDownloadView(Service())
                assert view.target_mode == 'author' and view.author_panel.visible
                assert not view.bookmark_panel.visible
                assert view.set_target_mode('bookmark')
                assert view.bookmark_panel.visible
                assert view._select_visible_bookmarks() and len(view.selected)==3
                assert view._clear_bookmark_selection() and not view.selected
                assert view.set_target_mode('author')
                assert view.author_panel.visible and not view.bookmark_panel.visible
                assert view.root.props['data-target-mode']=='author'
                assert await view.refresh_follow_authors()
                assert view._set_follow_sort('name')
                assert tuple(a.user_id for a in view._visible_follow_authors())==('92','93','91')
                assert view._set_follow_direction(True)
                assert tuple(a.user_id for a in view._visible_follow_authors())==('91','93','92')
                assert view._toggle_follow_stars()
                assert tuple(a.user_id for a in view._visible_follow_authors())==('91','93')
                assert view._select_visible_follow()
                assert tuple(a.user_id for a in view.author_targets)==('91','93')
                assert view._clear_follow_selection() and not view.author_targets
                assert view._toggle_follow_stars()
                assert view._select_favorite_follow()
                assert tuple(a.user_id for a in view.author_targets)==('91','93')
                assert view.set_target_mode('bookmark') and not view.author_panel.visible
                assert view.set_target_mode('author') and len(view.author_targets)==2
        finally:
            client.delete()
    asyncio.run(exercise())
