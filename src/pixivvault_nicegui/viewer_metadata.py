"""Explicit viewer reads via the existing identity-bound GET client."""
from html.parser import HTMLParser
from .bookmark_remote_adapter import RemoteBookmark, positive_id, thumbnail_url


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.parts=[]; self.hidden=0
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style'): self.hidden += 1
        elif tag in ('br','p','div'): self.parts.append('\n')
    def handle_endtag(self, tag):
        if tag in ('script','style') and self.hidden: self.hidden -= 1
    def handle_data(self, data):
        if not self.hidden: self.parts.append(data)


def pixiv_url(identity, *, author=False):
    value=positive_id(identity)
    return ('https://www.pixiv.net/users/' if author else 'https://www.pixiv.net/artworks/')+value if value else None


def read_details(reader, work_id, author_id):
    wid,aid=positive_id(work_id),positive_id(author_id)
    if not wid or not aid: raise ValueError('viewer identity unavailable')
    reader.require_identity()
    raw=reader.get_artwork_metadata_raw(wid,aid)
    if type(raw) is not dict or positive_id(raw.get('id'))!=wid or positive_id(raw.get('userId'))!=aid:
        raise ValueError('viewer identity changed')
    description=raw.get('description','')
    if type(description) is not str: raise ValueError('invalid description')
    text=_PlainText();text.feed(description)
    tags=raw.get('tags',[])
    if type(tags) is dict: tags=tags.get('tags',[])
    if type(tags) is not list: raise ValueError('invalid tags')
    tags=tuple(t.get('tag') if type(t) is dict else t for t in tags)
    if any(type(t) is not str for t in tags): raise ValueError('invalid tag')
    result={'description':''.join(text.parts).strip(),'tags':tags,'create_date':raw.get('createDate')}
    if result['create_date'] is not None and type(result['create_date']) is not str: raise ValueError('invalid date')
    for key in ('bookmarkCount','viewCount','width','height'):
        value=raw.get(key)
        if value is not None and (type(value) is not int or value<0):raise ValueError('invalid counter')
        result[key]=value
    return result


def read_author(reader, author_id):
    """Keep source order and missing-preview cards; never invent remote URLs."""
    aid = positive_id(author_id)
    if not aid:
        raise ValueError('invalid author')
    reader.require_identity()
    rows = reader.get_user_works(aid, lane='interactive')
    if type(rows) not in (list, tuple):
        raise ValueError('invalid author response')
    records, seen = [], set()
    for row in rows:
        if type(row) is not dict or row.get('is_masked', False) is not False:
            raise ValueError('author response unavailable')
        wid = positive_id(row.get('id'))
        if not wid or positive_id(row.get('user_id')) != aid or wid in seen:
            raise ValueError('author identity changed')
        title, name = row.get('title', ''), row.get('user_name', '')
        tags, created = row.get('tags', []), row.get('create_date', '')
        if type(tags) is not list or any(type(v) is not str for v in [title, name, created, *tags]):
            raise ValueError('invalid author metadata')
        if any(any(ord(c) < 32 or ord(c) == 127 for c in v) for v in [title, name, created, *tags]):
            raise ValueError('invalid author metadata')
        count, kind, restriction = row.get('page_count', 1), row.get('illust_type', 0), row.get('x_restrict', 0)
        if (type(count) is not int or not 1 <= count <= 1000
                or type(kind) is not int or kind not in (0, 1, 2)
                or type(restriction) is not int or restriction not in (0, 1, 2)):
            raise ValueError('invalid author media')
        thumb = row.get('thumbnail_url', '')
        if type(thumb) is not str:
            raise ValueError('invalid author thumbnail')
        if thumb:
            thumb = thumbnail_url(thumb)
        records.append(RemoteBookmark(wid, 'illustration', 'show', title, aid, name,
            tuple(tags), thumb, created, count, kind, restriction, page_count_known='page_count' in row))
        seen.add(wid)
    return tuple(records)
