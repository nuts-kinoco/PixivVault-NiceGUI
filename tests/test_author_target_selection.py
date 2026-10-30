from pixivvault_nicegui.author_download import AuthorTarget
from pixivvault_nicegui.author_target_selection import (MAX_SELECTED_AUTHORS,
    visible_authors, select_many_authors)


def test_favorite_filter_does_not_treat_unknown_as_unstarred_or_selected():
    authors = (AuthorTarget('91', 'Zed', 'follow', True, 2),
               AuthorTarget('92', 'Alpha', 'follow', None, 0),
               AuthorTarget('93', 'Beta', 'follow', False, 1))
    assert tuple(a.user_id for a in visible_authors(authors, favorites_only=True)) == ('91',)
    assert tuple(a.user_id for a in visible_authors(authors, sort_key='name')) == ('92','93','91')
    assert tuple(a.user_id for a in visible_authors(authors, sort_key='follow_order')) == ('92','93','91')
    assert tuple(a.user_id for a in visible_authors(authors, sort_key='name', descending=True)) == ('91','93','92')
    assert tuple(a.user_id for a in visible_authors(authors, query='beta')) == ('93',)


def test_batch_selection_retains_id_origin_and_is_idempotent():
    original = (AuthorTarget('91','Direct','id'),)
    candidates = (AuthorTarget('91','Follow','follow',True), AuthorTarget('92','Second','follow',False))
    selected = select_many_authors(original,candidates)
    assert selected == (original[0],candidates[1])
    assert select_many_authors(selected,candidates) is selected


def test_batch_rejects_over_capacity_without_partial_result():
    import pytest
    authors = tuple(AuthorTarget(str(i+1), str(i), 'follow') for i in range(MAX_SELECTED_AUTHORS+1))
    with pytest.raises(ValueError, match='too many'):
        select_many_authors((),authors)


def test_original_421_follows_are_all_selected_while_only_48_are_rendered():
    authors=tuple(AuthorTarget(str(i+1), f'Author {i+1}', 'follow', bool(i%2), i)
                  for i in range(421))
    selected=select_many_authors((),visible_authors(authors))
    assert len(selected)==421 and selected[420].user_id=='421'
    assert len(select_many_authors(selected,authors))==421


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
                assert view.target_mode == 'bookmark' and view.bookmark_panel.visible
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


def test_unsynced_unknown_favorites_never_silently_become_false_or_bulk_selected():
    import asyncio
    from nicegui import Client, ui
    from pixivvault_nicegui.ui.managed_download_view import ManagedDownloadView

    class Service:
        managed_downloads = object()
        jobs = object()
        def download_candidates(self): return ()
        async def followed_download_authors(self, *, owner):
            return (AuthorTarget('91','Unknown','follow'),)

    async def exercise():
        client=Client(ui.page('/_u_targets_unknown'))
        try:
            with client:
                view=ManagedDownloadView(Service())
                assert await view.refresh_follow_authors()
                assert not view._favorites_known()
                assert not view._select_favorite_follow() and not view.author_targets
                assert not view._toggle_follow_stars()
                assert '未取得' in view.author_status_label.text or '確認できない' in view.author_status_label.text
        finally: client.delete()
    asyncio.run(exercise())


def test_author_media_settings_choose_author_per_preview_and_queue():
    import asyncio
    from types import SimpleNamespace
    from nicegui import Client, ui
    from pixivvault_nicegui.author_media import AuthorMediaCandidate, AuthorTargetSetting
    from pixivvault_nicegui.author_download import Difference
    from pixivvault_nicegui.ui.author_media_view import AuthorMediaView

    class Media:
        def __init__(self):
            self.settings={'91':('illust',0),'92':(None,0)}
            self.zips={}
        def author_target(self, aid):
            target,revision=self.settings[aid]
            return AuthorTargetSetting(aid,target,revision)
        def configure_author_target(self,aid,target_type,*,expected_revision):
            assert expected_revision == self.settings[aid][1]
            self.settings[aid]=(target_type,expected_revision+1)
            return self.author_target(aid)
        def author_zip_setting(self, aid): return self.zips.get(aid)
        def configure_author_zip(self,aid,value,*,expected_revision):
            assert expected_revision == self.settings[aid][1]
            self.settings[aid]=(self.settings[aid][0],expected_revision+1)
            self.zips[aid]=value
            return expected_revision+1

    media=Media(); preview_calls=[]; accepted=[]
    def preview(aid, *, target_type, novel_format, ugoira_format):
        preview_calls.append((aid,target_type))
        effective=media.author_target(aid).target_type or 'both' if target_type is None else target_type
        kind='novel' if effective=='novel' else 'illustration'
        return (AuthorMediaCandidate(kind,aid,aid,'date',1,'Synthetic',Difference.NEW),)
    def enqueue(candidate,operation,*,origin):
        accepted.append((candidate.author_id,candidate.kind,origin))

    services=SimpleNamespace(author_media=media,author_media_preview=preview,
                             enqueue_author_media=enqueue,save_exports=object())
    p=SimpleNamespace(services=services,
                      author_targets=(AuthorTarget('91','First','follow'),AuthorTarget('92','Second','follow')),
                      author_revision=1,_valid=lambda:True)
    async def exercise():
        client=Client(ui.page('/_u_per_author_media'))
        try:
            with client:
                view=AuthorMediaView(p)
                assert view.target.value=='author'
                view.setting_author.set_value('92')
                assert await view.load_setting()
                view.target_setting_choice.set_value('novel')
                assert await view.save_setting()
                assert media.settings['92'][0]=='novel'
                assert await view.zip_setting(False)
                view.zip_choice.set_value('on')
                assert await view.zip_setting(True)
                assert media.zips['92'] is True
                # Saving one author's ZIP choice must re-enable the author
                # selector before editing another author in the same panel.
                assert view.setting_author.enabled
                view.setting_author.set_value('91')
                assert view.setting_author.enabled
                assert await view.load_setting()
                view.setting_author.set_value('92')
                assert await view.preview()
                assert preview_calls==[('91',None),('92',None)]
                assert {c.kind for c in view.rows}=={'illustration','novel'}
                assert await view.submit()
                assert {(a,k) for a,k,_ in accepted}=={('91','illustration'),('92','novel')}
        finally: client.delete()
    asyncio.run(exercise())


def _seventy_author_media_parent(enqueue):
    from types import SimpleNamespace
    from pixivvault_nicegui.author_media import AuthorMediaCandidate
    from pixivvault_nicegui.author_download import Difference
    rows=tuple(AuthorMediaCandidate('illustration',str(1000+i),'91','date',1,
                f'Synthetic {i}',Difference.NEW) for i in range(70))
    service=SimpleNamespace(author_media_preview=lambda *a,**kw:rows,
                            enqueue_author_media=enqueue)
    return SimpleNamespace(services=service,
                           author_targets=(AuthorTarget('91','Synthetic','follow'),),
                           author_revision=0,_valid=lambda:True)


def test_seventy_media_selection_paginates_only_dom_and_handles_queue_capacity():
    import asyncio
    from nicegui import Client, ui
    from pixivvault_nicegui.ui.author_media_view import AuthorMediaView
    operations={}
    ceiling=[50]
    def enqueue(c,op,origin):
        if op in operations:
            assert operations[op]==c.key
            return
        if len(operations)>=ceiling[0]:
            raise RuntimeError('synthetic aggregate queue capacity')
        operations[op]=c.key
    async def exercise():
        client=Client(ui.page('/_u_seventy_media'))
        try:
            with client:
                view=AuthorMediaView(_seventy_author_media_parent(enqueue))
                assert await view.preview()
                assert len(view.rows)==len(view.selected)==70 and view.shown==50
                assert not await view.submit()
                assert len(operations)==50 and len(view.selected)==20
                assert '残り 20 件は未受付' in view.status.text
                ceiling[0]=70
                assert await view.submit()
                assert len(operations)==70 and not view.selected
                assert len(set(operations.values()))==70
                assert not await view.submit()  # no second admission
                assert view._select_all(True)
                assert len(view.selected)==70
                assert view._select_all(False)
                assert not view.selected
        finally: client.delete()
    asyncio.run(exercise())


def test_uncertain_middle_operation_reuses_identity_and_does_not_duplicate():
    import asyncio
    from nicegui import Client, ui
    from pixivvault_nicegui.ui.author_media_view import AuthorMediaView
    operations={}; attempts={}; uncertain=['1030']
    def enqueue(c,op,origin):
        if op in operations:
            assert operations[op]==c.key
        else:
            operations[op]=c.key
        attempts[op]=attempts.get(op,0)+1
        if c.key==uncertain[0] and attempts[op]==1:
            raise OSError('synthetic response lost after owned admission')
    async def exercise():
        client=Client(ui.page('/_u_uncertain_media'))
        try:
            with client:
                view=AuthorMediaView(_seventy_author_media_parent(enqueue))
                assert await view.preview()
                assert not await view.submit()
                assert len(operations)==31 and len(view.selected)==40
                assert '残り 40 件は未受付' in view.status.text
                assert await view.submit()
                assert len(operations)==70
                recovered=next(op for op,key in operations.items() if key=='1030')
                assert attempts[recovered]==2
                assert sum(attempts.values())==71
        finally:client.delete()
    asyncio.run(exercise())


def test_close_while_accepted_call_pending_stops_any_new_admissions():
    import asyncio
    from threading import Event
    from nicegui import Client, ui
    from pixivvault_nicegui.ui.author_media_view import AuthorMediaView
    entered,release=Event(),Event(); accepted=[]
    def enqueue(c,op,origin):
        accepted.append((c.key,op))
        entered.set()
        assert release.wait(5)
    async def exercise():
        client=Client(ui.page('/_u_close_media'))
        try:
            with client:
                view=AuthorMediaView(_seventy_author_media_parent(enqueue))
                assert await view.preview()
                task=asyncio.create_task(view.submit())
                assert await asyncio.to_thread(entered.wait,5)
                view.close()
                release.set()
                assert not await task
                assert len(accepted)==1
                assert not await view.submit()
        finally:
            release.set();client.delete()
    asyncio.run(exercise())
