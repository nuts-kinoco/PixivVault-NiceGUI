"""Saved CPU vectors only; no model load, API, GPU or original DB."""
from pathlib import Path
import hashlib
import sqlite3
import struct
import pytest
from pixivvault_nicegui.similar_saved import (
    SavedSimilaritySource, SimilaritySourceError, decode_embeddings,
    decode_embeddings_pages, search_similar, MODEL,
)

def blob(*values):return struct.pack('<'+'f'*len(values),*values)

def database(tmp_path,rows):
    path=tmp_path/'vectors.db'
    connection=sqlite3.connect(path)
    connection.execute('CREATE TABLE work_embeddings(work_id TEXT,kind TEXT,model TEXT,page INTEGER,dim INTEGER,vec BLOB,PRIMARY KEY(work_id,kind,model,page))')
    connection.executemany('INSERT INTO work_embeddings VALUES(?,?,?,?,?,?)',[(wid,kind,model,page,dim,data) for wid,page,dim,data,kind,model in rows])
    connection.commit();connection.close()
    return path

def row(wid,page,values,kind='composition',model=MODEL):return wid,page,len(values),blob(*values),kind,model

def test_majority_dimension_before_corrupt_blob_filter_and_tie_order():
    keys,mat=decode_embeddings([('a',2,blob(1,0)),('b',3,blob(1,0,0)),('c',2,b'broken'),('d',3,blob(0,1,0))])
    assert keys==['a'] and mat==[(1,0)]
    assert decode_embeddings([])==([],None)
    assert decode_embeddings([('a',2,b'bad')])==([],None)

def test_page_identity_same_work_exclusion_threshold_and_no_normalization():
    keys,mat=decode_embeddings_pages([('1',1,2,blob(1,0)),('1',2,2,blob(1,0)),('2',3,2,blob(.7,.7)),('3',1,2,blob(.49,.8)),('4',2,2,blob(2,0))])
    result=search_similar(keys,mat,('1',1))
    assert result['status']=='ok' and result['pool_size']==5 and result['excluded_same_work']==1
    assert result['results'][0]==(('4',2),2.0)  # no silent normalization
    assert result['results'][1][0]==('2',3)
    assert result['results'][1][1]==pytest.approx(.7)
    assert search_similar(keys,mat,'1')['status']=='no_query_embedding'
    assert search_similar([],None,('1',1))['status']=='empty_pool'

def test_top30_and_exact_threshold_boundary():
    keys,mat=decode_embeddings([('origin',2,blob(1,0))]+[(str(i),2,blob(.5+i*.001,0)) for i in range(40)])
    result=search_similar(keys,mat,'origin')
    assert len(result['results'])==30
    assert result['results'][0][0]=='39' and result['results'][-1][0]=='10'
    keys,mat=decode_embeddings([('a',2,blob(1,0)),('b',2,blob(.5,0))])
    assert search_similar(keys,mat,'a')['results']==[('b',.5)]

def test_nonfinite_and_duplicate_embeddings_rejected():
    assert decode_embeddings([('a',2,blob(float('nan'),0)),('b',2,blob(float('inf'),0))])==([],None)
    with pytest.raises(ValueError):decode_embeddings([('a',2,blob(1,0)),('a',2,blob(1,0))])

@pytest.mark.parametrize('composition,expected_kind,expected_status',[
    ([], 'composition','empty_pool'),
    ([row('other',1,[1,0])], 'style','ok'),
    ([row('1',1,[1,0])], 'composition','ok'),
    ([row('1',1,[1,0]),row('other',1,[.1,1])], 'composition','ok'),
])
def test_style_fallback_only_when_composition_query_missing(tmp_path,composition,expected_kind,expected_status):
    path=database(tmp_path,composition+[row('1',1,[1,0],kind='style'),row('2',1,[.9,.1],kind='style')])
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    source=SavedSimilaritySource(path)
    try:
        result=source.search('1')
        assert result['kind']==expected_kind and result['status']==expected_status
        if expected_kind=='style':assert result['results'][0][0]==('2',1)
        else:assert not result['results']
    finally:source.close()
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    assert not any(Path(str(path)+suffix).exists() for suffix in ('-wal','-shm','-journal'))

def test_model_filter_and_page_origin(tmp_path):
    path=database(tmp_path,[row('1',2,[1,0]),row('1',1,[0,1]),row('2',4,[1,0]),row('3',1,[1,0],model='other')])
    source=SavedSimilaritySource(path)
    try:
        result=source.search('1',2)
        assert result['pool_size']==3 and result['results']==[(('2',4),1.0)]
    finally:source.close()

def test_frozen_copy_change_missing_schema_and_closed_source(tmp_path):
    path=database(tmp_path,[row('1',1,[1,0]),row('2',1,[.9,.1])])
    source=SavedSimilaritySource(path)
    with sqlite3.connect(path) as conn:conn.execute("UPDATE work_embeddings SET vec=? WHERE work_id='2'",(blob(.8,.2),))
    with pytest.raises(SimilaritySourceError):source.search('1')
    source.close()
    with pytest.raises(SimilaritySourceError):source.search('1')
    missing=tmp_path/'not-created.db'
    with pytest.raises(SimilaritySourceError):SavedSimilaritySource(missing)
    assert not missing.exists()
    empty=tmp_path/'empty.db'
    with sqlite3.connect(empty) as conn:conn.execute('CREATE TABLE unrelated(x)')
    reader=SavedSimilaritySource(empty)
    with pytest.raises(SimilaritySourceError):reader.search('1')
    reader.close()

def test_wal_or_journal_source_refused_without_opening(tmp_path):
    path=database(tmp_path,[row('1',1,[1,0])])
    Path(str(path)+'-wal').write_bytes(b'synthetic sidecar')
    with pytest.raises(SimilaritySourceError):SavedSimilaritySource(path)
    with pytest.raises(SimilaritySourceError):SavedSimilaritySource(Path('relative.db'))


@pytest.mark.asyncio
async def test_dialog_reports_failure_retries_and_opens_exact_page():
    from nicegui import Client,ui
    from pixivvault_nicegui.ui.similar_view import SimilarView
    class Source:
        fail=True
        def search(self,wid,page):
            if self.fail:self.fail=False;raise OSError('fixture failure')
            return dict(status='ok',results=[(('2',3),.8)],pool_size=2,excluded_same_work=0)
    opened=[];client=Client(ui.page('/_similar_fixture'))
    try:
        with client:
            view=SimilarView(Source(),lambda wid,page:opened.append((wid,page)))
            await view.show('1','fixture',2)
            assert view.retry.visible and '再試行' in view.notice.text
            await view.retry_search()
            assert not view.retry.visible and '1枚' in view.notice.text
            generation=view.generation
            await view.select('2',3,generation-1)
            assert not opened
            await view.select('2',3,generation)
            assert opened==[('2',3)]
    finally:client.delete()

@pytest.mark.asyncio
async def test_late_similarity_result_after_tab_exit_is_not_published():
    import asyncio
    from threading import Event
    from nicegui import Client,ui
    from pixivvault_nicegui.ui.similar_view import SimilarView
    entered,release=Event(),Event()
    class Source:
        def search(self,wid,page):
            entered.set();assert release.wait(10)
            return dict(status='ok',results=[(('2',3),.8)],pool_size=2,excluded_same_work=0)
    client=Client(ui.page('/_similar_late'));task=None
    try:
        with client:
            view=SimilarView(Source(),lambda *_:None)
            task=asyncio.create_task(view.show('1'))
            assert await asyncio.to_thread(entered.wait,5)
            view.set_active(False);release.set();await task
            assert not view.dialog.value
            assert not view.results.default_slot.children
            assert '探しています' in view.notice.text
    finally:
        release.set()
        if task is not None:await task
        client.delete()
