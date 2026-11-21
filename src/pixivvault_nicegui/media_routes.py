"""Image endpoints: FastAPI sync handlers run in its threadpool."""

from __future__ import annotations

import re
from uuid import uuid4
from weakref import WeakValueDictionary

from fastapi import HTTPException, Request
from fastapi.responses import Response

from .media import DemoImageCatalog


_GIF_KEY = re.compile(r'[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}')


class ViewerGif:
    """Loopback media retained only while a viewer owns this source."""

    def __init__(self, data: bytes, registry: WeakValueDictionary[str, ViewerGif]):
        if not isinstance(data, bytes):
            raise TypeError('viewer GIF data must be bytes')
        self._registry = registry
        self._key = uuid4().hex
        self.data = data
        self.url = f'/media/viewer-gif/{self._key}.gif'
        registry[self._key] = self

    def close(self) -> None:
        self._registry.pop(self._key, None)


def register_media_routes(app, catalog: DemoImageCatalog) -> None:
    viewer_gifs: WeakValueDictionary[str, ViewerGif] = WeakValueDictionary()
    app.state.viewer_gifs = viewer_gifs

    async def serve_viewer_gif(key: str, request: Request) -> Response:
        if _GIF_KEY.fullmatch(key) is None:
            raise HTTPException(status_code=404)
        # The app owns the lookup and the catalog closes with its services.
        if getattr(catalog, 'closed', False):
            raise HTTPException(status_code=503)
        source = viewer_gifs.get(key)
        if source is None:
            raise HTTPException(status_code=404)
        headers = {'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'}
        if request.method == 'HEAD':
            headers['Content-Length'] = str(len(source.data))
            return Response(content=b'', media_type='image/gif', headers=headers)
        return Response(content=source.data, media_type='image/gif', headers=headers)

    def serve(work_id: str, page_no: str, variant: str, request: Request) -> Response:
        cache_policy = (catalog.cache_control(work_id) if hasattr(catalog, "cache_control")
                        else "private, max-age=3600")
        # Exact originals are only fetched on explicit viewer action. Do not
        # persist them in the browser's long-lived thumbnail cache.
        headers = {"X-Content-Type-Options": "nosniff",
                   "Cache-Control": "private, no-store" if variant == "original" else cache_policy}
        if (not 1 <= len(page_no) <= 4 or not page_no.isascii()
                or not page_no.isdecimal() or (len(page_no) > 1 and page_no.startswith("0"))):
            raise HTTPException(status_code=404)
        try:
            version = request.query_params.get("catalog")
            if request.method == "HEAD" and hasattr(catalog, "metadata_versioned"):
                blob = catalog.metadata_versioned(version, work_id, int(page_no), variant)
            elif hasattr(catalog, "get_versioned"):
                blob = catalog.get_versioned(version, work_id, int(page_no), variant)
            elif version is not None:
                raise ValueError("unknown image snapshot")
            else:
                blob = catalog.get(work_id, int(page_no), variant)
        except ValueError:
            raise HTTPException(status_code=404) from None
        except RuntimeError:
            raise HTTPException(status_code=503) from None
        headers["ETag"] = blob.etag
        if any(value.strip() in (blob.etag, "W/" + blob.etag, "*")
               for value in request.headers.get("if-none-match", "").split(",")):
            return Response(status_code=304, headers=headers)
        if request.method == "HEAD":
            headers["Content-Length"] = str(len(blob.data))
            return Response(content=b"", media_type=blob.media_type, headers=headers)
        return Response(content=blob.data, media_type=blob.media_type, headers=headers)

    app.add_api_route("/media/{work_id}/{page_no}/{variant}.png", serve, methods=["GET", "HEAD"], include_in_schema=False)
    app.add_api_route('/media/viewer-gif/{key}.gif', serve_viewer_gif, methods=['GET', 'HEAD'], include_in_schema=False)
