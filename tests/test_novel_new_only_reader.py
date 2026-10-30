"""Original novel batch boundary and partial-list guard; synthetic JSON only."""
import pytest
from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient, InvalidPayload, _validate_destination


def test_newest_first_stops_before_second_batch_without_partial_success_claim():
    reader=LivePixivReadClient.__new__(LivePixivReadClient)
    reader.get_author_novel_ids=lambda aid:tuple(str(n) for n in range(1,51))
    calls=[]
    def response(kind,url,**kwargs):
        calls.append((kind,url))
        from urllib.parse import urlsplit,parse_qs
        ids=parse_qs(urlsplit(url).query)['ids[]']
        return {'works':{wid:{'id':wid,'userId':'91','title':wid,'updateDate':'2026-01-01'} for wid in ids}}
    reader._json=response
    rows=reader.get_user_novels('91',lambda row:row['id']=='49')
    assert [row['id'] for row in rows]==['50']
    assert len(calls)==1
    _validate_destination(calls[0][1],calls[0][0])


def test_missing_batch_member_is_failure_not_complete_inventory():
    reader=LivePixivReadClient.__new__(LivePixivReadClient)
    reader.get_author_novel_ids=lambda aid:('1',)
    reader._json=lambda *args,**kw:{'works':{}}
    with pytest.raises(InvalidPayload):reader.get_user_novels('91')
