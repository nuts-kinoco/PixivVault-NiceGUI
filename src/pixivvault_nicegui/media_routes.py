"""Image endpoints: FastAPI sync handlers run in its threadpool."""

from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import Response

from .media import DemoImageCatalog


def register_media_routes(app, catalog: DemoImageCatalog) -> None:
    def serve(work_id: str, page_no: str, variant: str, request: Request) -> Response:
        cache_policy = (catalog.cache_control(work_id) if hasattr(catalog, "cache_control")
                        else "private, max-age=3600")
        headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": cache_policy}
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
