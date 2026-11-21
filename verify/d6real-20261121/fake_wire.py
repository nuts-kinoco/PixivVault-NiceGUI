"""Deterministic wire responses injected below both applications' read paths."""

from io import BytesIO
import json
from urllib.parse import urlsplit


def response_body(url):
    path = urlsplit(url).path
    if path == "/":
        return "text/html", (
            b'<meta id="meta-global-data" content=\'{"userData":{"id":"55"}}\'>'
            b'<script>const fixture = {"userId":"55"};</script>'
        )
    if path == "/ajax/user/extra":
        return "application/json", b'{"error":false,"body":{"userId":"55"}}'
    if path == "/ranking.php":
        rows = [{
            "illust_id": 995000 + rank, "user_id": 55, "rank": rank, "illust_type": 0,
            "title": f"Fixture illustration {rank}", "user_name": "Fixture",
            "url": f"https://i.pximg.net/c/240x480/img-master/img/2026/11/20/00/00/00/{995000 + rank}_p0_master1200.jpg",
        } for rank in range(1, 51)]
        return "application/json", json.dumps({"contents": rows}).encode("ascii")
    if urlsplit(url).netloc == "i.pximg.net":
        from PIL import Image

        out = BytesIO()
        Image.new("RGB", (192, 128), (43, 116, 182)).save(out, format="JPEG")
        return "image/jpeg", out.getvalue()
    raise ValueError("unexpected fixture route")


def original_response(_adapter, request, *args, **kwargs):
    from requests import Response

    content_type, body = response_body(request.url)
    reply = Response()
    reply.status_code = 200
    reply.headers["Content-Type"] = content_type
    reply.encoding = "utf-8"
    reply._content = body
    reply._content_consumed = True
    reply.request = request
    reply.url = request.url
    return reply


def nice_response(_transport, request, _timeout, _limit, _deadline):
    from pixivvault_nicegui.pixiv_read_client import _ReadResponse

    return _ReadResponse(response_body(request.full_url)[1], ())
