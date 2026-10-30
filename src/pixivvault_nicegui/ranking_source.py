"""Original public ranking/tag-search contracts, with an injected GET-only reader.

No HTTP client, cookies, DB, model, or renderer is constructed here. The optional premium vault is a separate original operation; no main Cookie
identity is borrowed by its tag-search branch.
"""
from dataclasses import dataclass
from datetime import datetime
import time
from .pixiv_read_client import MAX_OPERATION_SECONDS
from .bookmark_remote_adapter import RemoteBookmark, positive_id, thumbnail_url

MODES={'daily':'日間','weekly':'週間','monthly':'月間','rookie':'新人'}
CONTENTS={'illust':'イラスト','manga':'マンガ','ugoira':'うごイラ','novel':'小説'}
SORTS={'rank':'公式順','rating_count':'評価数','view_count':'閲覧数','momentum':'伸びしろ'}

class RankingUnavailable(ValueError):
    def __init__(self): super().__init__('ranking mode unavailable')
class RankingReadFailed(ValueError):
    def __init__(self): super().__init__('ranking read failed')

@dataclass(frozen=True)
class RankingQuery:
    mode: str='daily'
    content: str='illust'
    date: str|None=None
    r18: bool=False
    exclude_ai: bool=False
    tag: str=''
    def __post_init__(self):
        if self.mode not in MODES or self.content not in CONTENTS or type(self.r18) is not bool or type(self.exclude_ai) is not bool:
            raise ValueError('invalid ranking selection')
        if self.date is not None:
            if type(self.date) is not str or len(self.date)!=8 or not self.date.isascii() or not self.date.isdecimal():raise ValueError('invalid date')
            datetime.strptime(self.date,'%Y%m%d')
        if self.r18 and self.mode not in {'daily','weekly'} and not self.tag:raise ValueError('mode does not support R18')
        if type(self.tag) is not str or len(self.tag)>256 or any(ord(c)<32 or ord(c)==127 for c in self.tag):raise ValueError('invalid tag')
    @property
    def effective_mode(self):return self.mode+'_r18' if self.r18 else self.mode

@dataclass(frozen=True)
class RankingItem:
    record: RemoteBookmark
    rank: int|None=None
    previous_rank: int|None=None
    rating_count: int|None=None
    view_count: int|None=None
    ai_type: int|None=None

@dataclass(frozen=True)
class RankingPage:
    query: RankingQuery
    page: int
    items: tuple[RankingItem,...]
    exhausted: bool
    order_label: str

def _number(value,*,default=None):
    if value is None:return default
    if type(value) is not int or value<0:raise ValueError('invalid numeric field')
    return value

def _public_wire_number(value,*,minimum,maximum):
    # ranking.php uses canonical ASCII decimal strings for illust_type and
    # illust_page_count. Other counts/ranks retain their strict int contract.
    # Do not use permissive int() on arbitrary JSON, whitespace, booleans,
    # Unicode digits, signs, floats, leading zeroes or unbounded strings.
    if type(value) is str:
        if (not 1<=len(value)<=4 or not value.isascii() or not value.isdecimal()
                or len(value)>1 and value[0]=='0'):
            raise ValueError('invalid public numeric field')
        value=int(value)
    if type(value) is not int or not minimum<=value<=maximum:
        raise ValueError('invalid public numeric field')
    return value

def _text(value):
    if type(value) is not str or any(ord(c)<32 or ord(c)==127 for c in value):raise ValueError('invalid text')
    return value

def parse_item(raw,query):
    if type(raw) is not dict:raise ValueError('invalid ranking row')
    if raw.get('is_masked') or raw.get('isMasked'):return None
    search=bool(query.tag)
    wid=positive_id(raw.get('id') if search else raw.get('illust_id'))
    aid=positive_id(raw.get('user_id'))
    if wid is None or aid is None:raise ValueError('invalid identity')
    tags=raw.get('tags',[])
    if type(tags) is not list or any(type(t) is not str for t in tags):raise ValueError('invalid tags')
    count=_number(raw.get('page_count'),default=1)
    known='page_count' in raw
    if not search and 'illust_page_count' in raw:
        wire_count=_public_wire_number(raw['illust_page_count'],minimum=1,maximum=1000)
        if known and (type(raw['page_count']) is not int or count!=wire_count):
            raise ValueError('conflicting public page counts')
        count,known=wire_count,True
    if not 1<=count<=1000:raise ValueError('invalid page count')
    raw_type=raw.get('illust_type')
    if not search and raw_type is not None:
        typ=_public_wire_number(raw_type,minimum=0,maximum=2)
    else:
        typ=_number(raw_type,default={'illust':0,'manga':1,'ugoira':2}[query.content])
    if typ not in (0,1,2):raise ValueError('invalid artwork type')
    restriction=_number(raw.get('x_restrict'),default=1 if query.r18 else 0)
    record=RemoteBookmark(wid,'illustration','show',_text(raw.get('title','')),aid,
        _text(raw.get('user_name','')),tuple(_text(t) for t in tags),
        thumbnail_url(raw.get('thumbnail_url') if search else raw.get('url')),
        '',count,typ,restriction,page_count_known=known)
    return RankingItem(record,
        None if search else _number(raw.get('rank')),
        None if search else _number(raw.get('yes_rank')),
        None if search else _number(raw.get('rating_count')),
        None if search else _number(raw.get('view_count')),
        _number(raw.get('ai_type')))


def parse_novel_item(raw):
    """Keep novel IDs and cover thumbnails in their own verified namespace.

    Only one public daily schema is observed. ai_type is an *uninterpreted*
    string until the independent type/flag comparison establishes semantics.
    """
    if type(raw) is not dict:
        raise ValueError('invalid novel ranking row')
    wid, aid = raw.get('id'), raw.get('user_id')
    if (type(wid) is not str or type(aid) is not str
            or positive_id(wid) != wid or positive_id(aid) != aid
            or wid.startswith('0') or aid.startswith('0')):
        raise ValueError('invalid novel ranking identity')
    rank = _number(raw.get('rank'))
    if rank is None or rank < 1:
        raise ValueError('invalid novel ranking rank')
    bookmarks = _number(raw.get('bookmark_count'))
    if bookmarks is None:
        raise ValueError('invalid novel bookmark count')
    title, author = _text(raw.get('title')), _text(raw.get('user_name'))
    if len(title)>512 or len(author)>512:
        raise ValueError('invalid novel ranking text')
    tags = raw.get('tag_a')
    if (type(tags) is not list or len(tags)>100 or any(
            type(value) is not str or len(value)>128 for value in tags)):
        raise ValueError('invalid novel ranking tags')
    tags = tuple(_text(value) for value in tags)
    thumbnail = raw.get('url')
    if type(thumbnail) is not str:
        raise ValueError('invalid novel cover')
    if thumbnail=='https://s.pximg.net/common/images/limit_unviewable_s.png':
        # Known public visibility mask; never expose its ID/title or issue a
        # download for the masked work. No broad s.pximg.net allowlist.
        return None
    if not thumbnail:
        raise ValueError('novel cover unavailable')
    thumbnail = thumbnail_url(thumbnail)
    created = _text(raw.get('create_date', ''))
    if len(created)>128:
        raise ValueError('invalid novel date')
    restrict = raw.get('restrict')
    x_restrict = raw.get('x_restrict')
    if (type(restrict) is not str or not restrict.isascii() or
            not restrict.isdecimal() or
            type(x_restrict) is not str or not x_restrict.isascii() or
            not x_restrict.isdecimal() or int(x_restrict) not in (0,1,2)):
        raise ValueError('invalid novel restriction flags')
    ai_type=raw.get('ai_type')
    # Observed dated-daily: 1 visible / 0 masked; weekly_ai: 2.
    # No artwork get_work_info fallback (different ID namespace). Unknowns
    # must not be advertised as non-AI or silently indexed as viewable.
    if type(ai_type) is not str or ai_type not in ('1','2'):
        raise ValueError('invalid novel AI flag')
    record = RemoteBookmark(wid,'novel','show',title,aid,author,tags,thumbnail,
                            created,1,None,int(x_restrict))
    return RankingItem(record,rank,None,bookmarks,None,int(ai_type))

def sorted_items(items,sort='rank'):
    if sort not in SORTS:raise ValueError('invalid ranking sort')
    if sort=='rank':return tuple(sorted(items,key=lambda i:i.rank or 0))
    if sort=='momentum':return tuple(sorted(items,key=lambda i:10**9 if not i.previous_rank else i.previous_rank-(i.rank or 0),reverse=True))
    return tuple(sorted(items,key=lambda i:getattr(i,sort) or 0,reverse=True))

def merge_pages(previous,page):
    if previous is not None and (previous.query!=page.query or page.page!=previous.page+1):raise ValueError('stale ranking page')
    if previous is None and page.page!=1:raise ValueError('missing ranking first page')
    rows={i.record.work_id:i for i in (() if previous is None else previous.items)}
    for item in page.items:rows.setdefault(item.record.work_id,item)
    return RankingPage(page.query,page.page,tuple(rows.values()),page.exhausted,page.order_label)

class RankingSource:
    def __init__(self,reader,vault=None):self.reader=reader;self.vault=vault
    def fetch(self,query,page=1):
        if not isinstance(query,RankingQuery) or type(page) is not int or page<1 or (query.tag and page!=1):raise ValueError('invalid ranking request')
        # Do not silently route novel tags to search_illusts; R-18 mode syntax
        # for the dedicated novel endpoint has not been observed.
        if query.content=='novel' and (query.tag or query.r18):
            raise RankingUnavailable()
        try:
            deadline=time.monotonic()+MAX_OPERATION_SECONDS
            if query.tag and self.vault is not None:
                status=self.vault.status()
                if status['open'] and status['mode']=='nana7mi':
                    from .personal_ranking import normalize_item
                    packet=self.vault.search(query.tag,0,sort='popular_desc',r18=query.r18,exclude_ai=query.exclude_ai)
                    if type(packet) is not dict or type(packet.get('illusts')) is not list:raise ValueError('invalid premium search response')
                    rows=tuple(normalize_item(raw)['item'] for raw in packet['illusts'])
                    return RankingPage(query,1,rows,True,'人気順（プレミアム）・R-18 絞り込み未適用（原 API）')
            self.reader.require_identity()
            if query.content=='novel':
                raw=self.reader.get_novel_ranking(mode=query.mode,page=page,date=query.date)
            elif query.tag:
                packet=self.reader.search_illusts(query.tag,page=1,order='date_d',mode='r18' if query.r18 else 'safe')
                if type(packet) is not dict or type(packet.get('works')) is not list:raise ValueError('invalid search response')
                raw=packet['works']
            else:
                raw=self.reader.get_ranking(mode=query.effective_mode,content=query.content,page=page,date=query.date)
            if type(raw) not in (list,tuple):raise ValueError('invalid ranking response')
            rows=[]
            novel_ids=set()
            if query.content=='novel' and len(raw)>50:
                raise ValueError('novel ranking page too large')
            for value in raw:
                if time.monotonic()>deadline:raise RankingReadFailed()
                item=parse_novel_item(value) if query.content=='novel' else parse_item(value,query)
                if item is None:continue
                if query.content=='novel':
                    if item.record.work_id in novel_ids:
                        raise ValueError('duplicate novel ranking ID')
                    novel_ids.add(item.record.work_id)
                if query.exclude_ai:
                    ai=item.ai_type
                    if ai is None:
                        detail=self.reader.get_work_info(item.record.remote_id)
                        ai=_number(detail.get('ai_type'))
                        if ai is None:raise ValueError('AI classification unavailable')
                    if ai==2:continue
                rows.append(item)
            if time.monotonic()>deadline:raise RankingReadFailed()
            return RankingPage(query,page,tuple(rows),bool(query.tag) or len(raw)==0,
                ('公式小説ランキング · 評価数=ブックマーク数 · 閲覧数/伸びしろ未取得'
                 if query.content=='novel' else
                 '新着順のみ（非プレミアムのため人気順は使えません）' if query.tag
                 else '公式ランキング'))
        except Exception as error:
            status=getattr(error,'status_code',None)
            if status is None:status=getattr(getattr(error,'response',None),'status_code',None)
            if status==404 and not query.tag:
                if page>1:return RankingPage(query,page,(),True,
                    '公式小説ランキング · 評価数=ブックマーク数' if query.content=='novel'
                    else '公式ランキング')
                raise RankingUnavailable() from None
            raise RankingReadFailed() from None
