"""One explicit GET-only, complete bookmark/following comparison."""
from dataclasses import dataclass
from .bookmark_remote_adapter import positive_id, thumbnail_url, KINDS, VISIBILITIES, MAX_ITEMS_PER_ROUTE


@dataclass(frozen=True)
class UnfollowedAuthor:
    author_id: str
    name: str
    work_id: str
    kind: str
    thumbnail: str


@dataclass(frozen=True)
class UnfollowedAuthors:
    account_id: str
    authors: tuple[UnfollowedAuthor, ...]


def fetch_unfollowed_authors(reader, *, kinds=KINDS, visibilities=VISIBILITIES,
                            is_current=lambda:True, max_items_per_route=MAX_ITEMS_PER_ROUTE):
    """No cache writes; a partial route is never presented as a complete list."""
    def identifier(value):
        parsed=positive_id(value)
        if parsed is None:raise ValueError()
        return str(int(parsed))
    try:
        if (not kinds or not visibilities or not set(kinds)<=set(KINDS)
                or not set(visibilities)<=set(VISIBILITIES)
                or len(kinds)!=len(set(kinds)) or len(visibilities)!=len(set(visibilities))
                or type(max_items_per_route) is not int or not 1<=max_items_per_route<=MAX_ITEMS_PER_ROUTE):
            raise ValueError()
        if not is_current():raise ValueError()
        account=identifier(reader.verify_identity())
        def check():
            if not is_current() or identifier(reader.require_identity())!=account:raise ValueError()
        def bounded(rows):
            for index,row in enumerate(rows):
                check()
                if index>=max_items_per_route or not isinstance(row,dict):raise ValueError()
                yield row
            check()
        following=set()
        for rest in VISIBILITIES:
            check()
            for row in bounded(reader.get_following_users(account,rest_type=rest)):
                following.add(identifier(row.get('user_id')))
        authors={}
        for kind in kinds:
            method=reader.get_bookmarked_works if kind=='illustration' else reader.get_bookmarked_novels
            for rest in visibilities:
                check()
                for row in bounded(method(account,rest_type=rest)):
                    raw=positive_id(row.get('user_id'))
                    name=row.get('user_name') or ''
                    # The old UI omits inaccessible/deleted authors.
                    if raw is None or name=='-----':continue
                    if type(name) is not str or len(name)>256 or any(ord(ch)<32 for ch in name):raise ValueError()
                    aid=identifier(raw)
                    if aid in following:continue
                    wid=identifier(row.get('id'))
                    preview=thumbnail_url(row['thumb_url']) if row.get('thumb_url') else ''
                    item=UnfollowedAuthor(aid,name,wid,kind,preview)
                    if aid not in authors or (not authors[aid].thumbnail and preview):
                        authors[aid]=item
        check()
        return UnfollowedAuthors(account,tuple(authors.values()))
    except Exception:
        raise ValueError('unfollowed-author-fetch-failed') from None
