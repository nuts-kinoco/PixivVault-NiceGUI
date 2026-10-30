"""Read-only saved-vector search; no inference, GPU, network or DB migration.

Original contract: similar_works.py and library_tab.py at 3ac3aa0. Existing
float32 vectors are already normalized. Scores use a standard-library dot
product rounded to float32; BLAS reduction order and equal-score tie order
are not promised bit-identical. No optional numpy dependency is loaded.
"""
from __future__ import annotations
from collections import Counter
import hashlib
import math
from pathlib import Path
import sqlite3
import struct
from threading import RLock
from .managed_downloads import _safe_path
from .fs_paths import sqlite_readonly_uri

MODEL = 'egara_net_fp32'
TOP_K = 30
MIN_SIMILARITY = 0.50
STATUS_OK = 'ok'
STATUS_NO_QUERY_EMBEDDING = 'no_query_embedding'
STATUS_EMPTY_POOL = 'empty_pool'

class SimilaritySourceError(ValueError):
    def __init__(self):super().__init__('saved similarity source unavailable or changed')

def normalize_key(key):
    if isinstance(key,(tuple,list)):
        if len(key)!=2 or type(key[1]) is not int or key[1]<1:raise ValueError('invalid page key')
        return str(key[0]),key[1]
    return str(key)

def decode_embeddings(rows):
    """Choose original most frequent dimension, then discard corrupt vectors.

    Ties choose the first dimension observed, as the original insertion-ordered
    dictionary did. Finite-value rejection additionally prevents NaN sorting.
    """
    rows=tuple(rows)
    counts=Counter(dim for _,dim,_ in rows if type(dim) is int and dim>0)
    if not counts:return [],None
    target=max(counts,key=counts.get)
    keys,vectors=[],[];seen=set()
    for key,dim,blob in rows:
        if dim!=target or not isinstance(blob,bytes) or len(blob)!=4*target:continue
        values=struct.unpack('<'+'f'*target,blob)
        if not all(math.isfinite(v) for v in values):continue
        try:key=normalize_key(key)
        except (TypeError,ValueError):continue
        if key in seen:raise ValueError('duplicate embedding identity')
        seen.add(key)
        keys.append(key);vectors.append(values)
    return (keys,vectors) if keys else ([],None)

def decode_embeddings_pages(rows):
    return decode_embeddings([((wid,page),dim,blob) for wid,page,dim,blob in rows])

def search_similar(keys,matrix,query_work_id,top_k=TOP_K,min_similarity=MIN_SIMILARITY):
    if type(top_k) is not int or top_k<1 or not math.isfinite(min_similarity):raise ValueError('invalid similarity request')
    empty=dict(status=STATUS_EMPTY_POOL,results=[],pool_size=0,excluded_same_work=0)
    if matrix is None or not keys:return empty
    query=normalize_key(query_work_id)
    if query not in keys:return dict(empty,status=STATUS_NO_QUERY_EMBEDDING,pool_size=len(keys))
    if len(matrix)!=len(keys):raise ValueError('inconsistent embedding matrix')
    origin=keys.index(query);vector=matrix[origin];scores=[]
    for index,row in enumerate(matrix):
        if len(row)!=len(vector):raise ValueError('inconsistent embedding dimensions')
        score=math.fsum(a*b for a,b in zip(row,vector))
        try:score=struct.unpack('<f',struct.pack('<f',score))[0]
        except OverflowError:raise ValueError('invalid embedding score') from None
        if not math.isfinite(score):raise ValueError('invalid embedding score')
        scores.append((index,score))
    results=[];excluded=0
    for index,score in sorted(scores,key=lambda x:-x[1]):
        if index==origin:continue
        key=keys[index]
        if isinstance(query,tuple) and isinstance(key,tuple) and key[0]==query[0]:
            excluded+=1;continue
        if score<min_similarity:break
        results.append((key,score))
        if len(results)>=top_k:break
    return dict(status=STATUS_OK,results=results,pool_size=len(keys),excluded_same_work=excluded)

class SavedSimilaritySource:
    """Explicit standalone copied SQLite, fingerprinted; no original DB writer.

    A changed copy, WAL/journal, links or unsupported schema fails closed.
    Open only per request so reads can run in a UI worker thread. Callers must
    supply an authorized static copy, never create a copy from a live DB here.
    """
    def __init__(self,db_path):
        self.path=Path(db_path)
        if not self.path.is_absolute():raise SimilaritySourceError()
        self._lock=RLock();self._closed=False
        self._signature=self._fingerprint()
    def _fingerprint(self, *, cancelled=None):
        _safe_path(self.path,file=True)
        if not self.path.is_file() or any(Path(str(self.path)+s).exists() for s in ('-wal','-shm','-journal')):
            raise SimilaritySourceError()
        before=self.path.stat();digest=hashlib.sha256()
        with self.path.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):
                if cancelled is not None and cancelled():
                    raise SimilaritySourceError()
                digest.update(block)
        after=self.path.stat()
        if (before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns):raise SimilaritySourceError()
        return after.st_ino,after.st_size,after.st_mtime_ns,digest.digest()
    def search(self,work_id,page=1):
        return self._search(normalize_key((work_id,page)))

    def search_author(self,work_ids,representative_id):
        """Original author-card query: prefer an embedded first page, not p2."""
        work_ids=tuple(str(value) for value in work_ids)
        representative_id=str(representative_id)
        if not work_ids or representative_id not in work_ids:
            raise ValueError('invalid author similarity origin')
        return self._search((representative_id,1),author_ids=work_ids)

    def _search(self,origin,*,author_ids=None):
        with self._lock:
            if self._closed:raise SimilaritySourceError()
            if self._fingerprint()!=self._signature:raise SimilaritySourceError()
            connection=None
            try:
                connection=sqlite3.connect(sqlite_readonly_uri(self.path, immutable=True),uri=True)
                connection.execute('PRAGMA query_only=ON')
                columns={r[1] for r in connection.execute('PRAGMA table_info(work_embeddings)')}
                if not {'work_id','page','kind','model','dim','vec'}<=columns:raise SimilaritySourceError()
                if author_ids is not None:
                    # Match Flet's get_embedded_work_ids: presence at p1 in
                    # either kind chooses the origin; decoding/search below
                    # still controls valid vectors, fallback and candidates.
                    embedded={str(row[0]) for row in connection.execute(
                        "SELECT DISTINCT work_id FROM work_embeddings WHERE page=1 AND model=? AND kind IN ('composition','style')",
                        (MODEL,))}
                    chosen=(origin[0] if origin[0] in embedded else
                            next((wid for wid in author_ids if wid in embedded),origin[0]))
                    origin=(chosen,1)
                def find(kind):
                    rows=connection.execute('SELECT work_id,page,dim,vec FROM work_embeddings WHERE kind=? AND model=? ORDER BY work_id,page',(kind,MODEL)).fetchall()
                    keys,matrix=decode_embeddings_pages(rows)
                    return search_similar(keys,matrix,origin)
                result=find('composition');kind='composition'
                if result['status']==STATUS_NO_QUERY_EMBEDDING:
                    result=find('style');kind='style'
                if self._fingerprint()!=self._signature:raise SimilaritySourceError()
                result=dict(result,kind=kind)
                return (origin[0],result) if author_ids is not None else result
            except (sqlite3.Error,OSError,ValueError):raise SimilaritySourceError() from None
            finally:
                if connection is not None:connection.close()
    def close(self):
        with self._lock:self._closed=True
