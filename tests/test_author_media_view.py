import asyncio
from threading import Event
from types import SimpleNamespace
import pytest
from nicegui import Client,ui
from pixivvault_nicegui.author_download import AuthorTarget,Difference
from pixivvault_nicegui.author_media import AuthorMediaCandidate
from pixivvault_nicegui.ui.author_media_view import AuthorMediaView


def parent():
    rows=tuple(AuthorMediaCandidate(k,'123','91','date',1,k,Difference.NEW) for k in ('illustration','novel','ugoira'))
    accepted=[]
    def enqueue(c,op,origin): accepted.append((c.key,op,origin)); return object(),True,'a'*64
    services=SimpleNamespace(author_media_preview=lambda *a,**kw:rows,enqueue_author_media=enqueue)
    p=SimpleNamespace(services=services,author_targets=(AuthorTarget('91','Author','follow'),),author_revision=0,_valid=lambda:True)
    return p,accepted


@pytest.mark.asyncio
async def test_all_media_preview_needs_explicit_queue_and_preserves_namespaces():
    p,accepted=parent(); client=Client(ui.page('/_author_media'))
    try:
        with client:
            view=AuthorMediaView(p)
            assert await view.preview()
            assert not accepted and len(view.selected)==3
            assert await view.submit()
            assert {a[0] for a in accepted}=={'123','novel:123','ugoira:123'}
            assert all(a[2]=='follow' for a in accepted)
    finally:client.delete()


@pytest.mark.asyncio
async def test_author_change_rejects_old_preview_and_duplicate_click():
    p,accepted=parent(); client=Client(ui.page('/_author_media_stale'))
    try:
        with client:
            view=AuthorMediaView(p)
            assert await view.preview()
            p.author_revision+=1
            assert not await view.submit() and not accepted
            assert await view.preview()
            view.set_active(False)
            assert not await view.submit() and not accepted
    finally:client.delete()


@pytest.mark.asyncio
async def test_author_mode_switch_rejects_hidden_and_returned_stale_submit():
    p,accepted=parent()
    p.target_mode='author'
    p.epoch=0
    client=Client(ui.page('/_author_media_mode_epoch'))
    try:
        with client:
            view=AuthorMediaView(p)
            assert await view.preview()
            assert len(view.selected)==3
            p.target_mode='bookmark'; p.epoch+=1
            assert not await view.submit() and not accepted
            p.target_mode='author'; p.epoch+=1
            assert not await view.submit() and not accepted
            assert await view.preview()
            assert await view.submit()
            assert len(accepted)==3
    finally: client.delete()


@pytest.mark.asyncio
async def test_author_setting_is_frozen_around_preview_for_each_author():
    from pixivvault_nicegui.author_media import AuthorTargetSetting
    p,accepted=parent()
    current=AuthorTargetSetting('91',None,0)
    p.services.author_media=SimpleNamespace(author_target=lambda aid:current)
    old_preview=p.services.author_media_preview
    client=Client(ui.page('/_author_media_setting_epoch'))
    try:
        with client:
            view=AuthorMediaView(p)
            assert await view.preview()
            assert {c.author_setting_revision for c in view.rows}=={0}
            assert {c.author_setting_target for c in view.rows}=={None}
            def changed_during_preview(*a,**kw):
                nonlocal current
                current=AuthorTargetSetting('91','novel',current.revision+1)
                return old_preview(*a,**kw)
            p.services.author_media_preview=changed_during_preview
            assert not await view.preview()
            assert not view.selected and not accepted
            assert view.selection_snapshot is None
    finally: client.delete()


@pytest.mark.asyncio
async def test_late_preview_cannot_replace_changed_format():
    p,accepted=parent(); entered,release=Event(),Event()
    original=p.services.author_media_preview
    def blocked(*a,**kw): entered.set(); assert release.wait(5); return original(*a,**kw)
    p.services.author_media_preview=blocked
    client=Client(ui.page('/_author_media_late'))
    try:
        with client:
            view=AuthorMediaView(p)
            task=asyncio.create_task(view.preview())
            assert await asyncio.to_thread(entered.wait,5)
            view.generation+=1
            release.set()
            assert not await task and view.rows==() and not accepted
    finally:release.set();client.delete()


@pytest.mark.asyncio
async def test_uncertain_enqueue_keeps_same_operation_for_explicit_retry():
    p,accepted=parent(); first=True
    def enqueue(c,op,origin):
        nonlocal first
        accepted.append((c.key,op))
        if first: first=False; raise OSError('synthetic uncertain response')
        return object(),False,'a'*64
    p.services.enqueue_author_media=enqueue
    client=Client(ui.page('/_author_media_retry'))
    try:
        with client:
            view=AuthorMediaView(p); await view.preview()
            assert not await view.submit()
            assert await view.submit()
            assert accepted[0]==accepted[1]
    finally:client.delete()

def test_initial_formats_respect_existing_preferences():
    p,_=parent()
    p.services.preferences=SimpleNamespace(get_setting=lambda key,default:{'novel_save_format':'both','ugoira_save_format':'folder'}[key])
    client=Client(ui.page('/_author_media_defaults'))
    try:
        with client:
            view=AuthorMediaView(p)
            assert view.novel_format.value=='BOTH' and view.ugoira_format.value=='frames'
    finally:client.delete()

@pytest.mark.asyncio
async def test_author_override_ui_requires_load_and_cas_before_save_clear():
    from pixivvault_nicegui.author_media import AuthorTargetSetting
    p,_=parent(); current=AuthorTargetSetting('91',None,0)
    def configure(aid,target_type,expected_revision):
        nonlocal current
        if current.revision!=expected_revision: raise ValueError('changed')
        current=AuthorTargetSetting(aid,target_type,current.revision+1)
        return current
    p.services.author_media=SimpleNamespace(author_target=lambda aid:current,configure_author_target=configure)
    client=Client(ui.page('/_author_media_override'))
    try:
        with client:
            view=AuthorMediaView(p)
            assert not await view.save_setting()
            assert await view.load_setting()
            view.target.set_value('novel')
            assert await view.save_setting() and current.target_type=='novel'
            assert await view.save_setting(True) and current.target_type is None
            current=AuthorTargetSetting('91','illust',current.revision+1)
            assert not await view.save_setting() and view.target_setting is None
    finally:client.delete()
