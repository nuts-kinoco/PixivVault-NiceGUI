"""Generated URL shapes only; no live credentials, IDs, bytes or network."""
import json,time,socket
import pytest
from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient, InvalidPayload

PREFIX="https://i.pximg.net/img-original/img/2026/01/02/03/04/05/"
HASH="a0"*16

@pytest.fixture
def read_pages(tmp_path,monkeypatch):
    def forbidden(*args,**kwargs):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket.socket,"connect",forbidden)
    cookie=tmp_path/"synthetic-cookie.txt"
    cookie.write_text("# Netscape HTTP Cookie File\n"
        f".pixiv.net\tTRUE\t/\tTRUE\t{int(time.time())+86400}\tPHPSESSID\t55_generatedonly\n",
        encoding="utf-8")
    def read(urls):
        calls=[]
        def transport(request,*args):
            assert request.get_method()=="GET" and request.data is None
            assert request.full_url=="https://www.pixiv.net/ajax/illust/202/pages"
            calls.append("pages")
            return json.dumps({"error":False,"body":[{"urls":{"original":url}} for url in urls]}).encode()
        client=LivePixivReadClient(cookie,transport=transport)
        try:
            return client.get_artwork_original_pages("202")
        finally:
            client.close()
            assert calls==["pages"]
    return read

@pytest.mark.parametrize("suffix",["","-"+HASH])
@pytest.mark.parametrize("extension",["jpg","jpeg","png","gif","webp"])
def test_original_url_plain_and_hash_formats(read_pages,suffix,extension):
    urls=tuple(PREFIX+f"202{suffix}_p{page}.{extension}" for page in (0,1))
    assert read_pages(urls)==urls

@pytest.mark.parametrize("tail",[
    "203-"+HASH+"_p0.png",             # another work
    "202-"+HASH+"_p1.png",             # another page
    "202-"+HASH[:-1]+"_p0.png",         # truncated hash
    "202-"+HASH+"0_p0.png",            # oversized hash
    "202-"+("g0"*16)+"_p0.png",        # non-hex suffix
    "202-"+HASH+"-extra_p0.png",       # arbitrary suffix
    "202-"+HASH+"_p00.png",            # noncanonical page
    "202-"+HASH+"_p0.svg",             # unapproved media
    "202-"+HASH+"_p0.png?x=1",         # query
    "202-"+HASH+"_p0.png#fragment",
    "../202-"+HASH+"_p0.png",
])
def test_original_url_rejects_wrong_binding_or_format(read_pages,tail):
    with pytest.raises(InvalidPayload):
        read_pages([PREFIX+tail])

@pytest.mark.parametrize("url",[
    "http://i.pximg.net/img-original/img/2026/01/02/03/04/05/202-"+HASH+"_p0.png",
    "https://i.pximg.net.evil.invalid/img-original/img/2026/01/02/03/04/05/202-"+HASH+"_p0.png",
    "https://user@i.pximg.net/img-original/img/2026/01/02/03/04/05/202-"+HASH+"_p0.png",
    "https://i.pximg.net:443/img-original/img/2026/01/02/03/04/05/202-"+HASH+"_p0.png",
])
def test_original_url_does_not_widen_destination(read_pages,url):
    with pytest.raises(InvalidPayload):
        read_pages([url])

def test_original_url_rejects_duplicate_or_unordered_pages(read_pages):
    first=PREFIX+"202-"+HASH+"_p0.png"
    with pytest.raises(InvalidPayload):
        read_pages([first,first])
