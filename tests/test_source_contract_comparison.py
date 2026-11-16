"""Isolated Flet functions and real candidate callbacks, generated data only.

These checks establish observed behavior; they do not assign the unresolved
review-side A02/A10/X02 identifiers to formal acceptance operations.
"""
from pathlib import Path
from types import SimpleNamespace
from required_network_guard import no_network
import pytest
from nicegui import Client, ui
from pixivvault_nicegui.author_media import AuthorMediaCandidate
from pixivvault_nicegui.author_download import Difference
from pixivvault_nicegui.ui.author_browse_view import AuthorBrowseView
from test_required_author_entry import setup


def original(name, namespace):
    path=Path(__file__).parent/'fixtures'/('original_contract_'+name+'.py')
    exec(compile(path.read_text('utf-8'),str(path),'exec'),namespace)
    return namespace


@pytest.mark.parametrize('author,result,raises,expected',[
    (None,{},False,None),
    ('91',{'failed_count':0},False,True),
    ('91',{'failed_count':1},False,True),
    ('91',{},True,False),
])
def test_flet_author_click_reports_call_outcome_not_per_work_result(author,result,raises,expected):
    calls=[];tasks=[];snacks=[]
    def backup(aid,client,db,**kwargs):
        calls.append((aid,kwargs))
        if raises:raise OSError('generated failure')
        return result
    def thread(*,target,daemon):
        assert daemon
        return SimpleNamespace(start=lambda:tasks.append(target))
    state={'author_id':author}
    env=original('recommend_tab',dict(_author_page_state=state,
        threading=SimpleNamespace(Thread=thread),run_backup=backup,
        _get_client=lambda:object(),db=object(),_marshal_call=lambda f:f(),
        _show_result_snack=lambda text,ok:snacks.append((text,ok)),
        logger=SimpleNamespace(exception=lambda *_:None)))
    env['_author_page_diff_dl']()
    if author is None:
        assert not tasks and not calls and not snacks
    else:
        assert len(tasks)==1 and not calls
        state['author_id']='92'
        tasks[0]()
        assert calls==[('91',{'new_only':True})]
        assert snacks==[('差分DLを実行しました。' if expected else '差分DLに失敗しました。',expected)]


def test_flet_move_aggregator_keeps_failure_separate_and_ignores_unselected_item():
    env=original('gui',{})
    a,b,c=({'work_id':str(n),'page':1} for n in (1,2,3))
    group={'user_id':'91','items':[a,b,c]}
    calls=[]
    def move(item,aid):
        calls.append((item['work_id'],aid))
        if item is a:raise PermissionError('generated failure')
    moved,failed=env['dedup_execute_move_selections']([(group,[a,b])],move)
    assert calls==[('1','91'),('2','91')]
    assert moved=={env['dedup_group_key'](group):[b]}
    assert len(failed)==1 and failed[0][:2]==(a,group)
    assert group['items']==[a,b,c]


@pytest.mark.asyncio
async def test_candidate_preview_partial_admission_and_explicit_retry_never_claim_save_completion():
    services,_,_=setup();attempts=[]
    rows=tuple(AuthorMediaCandidate('illustration',wid,'91','generated-date',1,'Generated',Difference.NEW)
               for wid in ('11','12','13'))
    services.author_media_preview=lambda *a,**kw:rows
    reject=True
    def enqueue(candidate,operation,**kw):
        attempts.append((candidate.work_id,operation))
        if reject and candidate.work_id=='12':raise OSError('generated admission failure')
        return object(),True,'a'*64
    services.enqueue_author_media=enqueue
    client=Client(ui.page('/_source_contract_admission'))
    try:
        with client:
            view=AuthorBrowseView(services,services.live_client)
            await view.show('91','Generated')
            panel=view.author_download
            assert not await panel.submit() and not attempts
            assert await panel.preview() and not attempts
            assert not await panel.submit()
            assert [wid for wid,_ in attempts]==['11','12']  # 13 is unprocessed.
            assert panel.selected=={'12','13'}
            assert panel.status.text.startswith('1 件を受付しました。残り 2 件は未受付です。')
            assert '完了' not in panel.status.text
            assert len(panel.rows)==3
            panel.render()
            assert len(attempts)==2  # Rendering cannot retry.
            reject=False
            assert await panel.submit()
            assert [wid for wid,_ in attempts]==['11','12','12','13']
            assert attempts[1]==attempts[2]  # Same admission operation, no duplicate.
            assert not panel.selected and '保存状態はキューで確認できます' in panel.status.text
            assert not await panel.submit() and len(attempts)==4
            view.close()
    finally:client.delete();services.close()


@pytest.mark.asyncio
async def test_candidate_failed_preview_leaves_no_admitted_or_stale_save_selection():
    services,_,accepted=setup()
    client=Client(ui.page('/_source_contract_preview_failure'))
    try:
        with client:
            view=AuthorBrowseView(services,services.live_client)
            await view.show('91','Generated');panel=view.author_download
            assert await panel.preview() and panel.selected
            def fail(*args,**kwargs):raise OSError('generated metadata failure')
            services.author_media_preview=fail
            assert not await panel.preview()
            assert not panel.rows and not panel.selected and not accepted
            assert not await panel.submit() and not accepted
            assert panel.status.text=='全件確認に失敗しました。受付はしていません。再確認できます'
            view.close()
    finally:client.delete();services.close()

