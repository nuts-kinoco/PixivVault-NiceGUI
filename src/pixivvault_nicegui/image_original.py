"""Validate an explicitly requested original frame without resampling pixels.

All callers must already have validated their own work/page identity, source
permissions and filesystem/URL boundaries. This is a bounded image validator,
not an authorization bypass or an alternate media fetcher.
"""
from __future__ import annotations

from io import BytesIO
import hashlib

from PIL import Image, UnidentifiedImageError

from .media import ImageBlob


MAX_ORIGINAL_BYTES = 24 * 1024 * 1024
MAX_ORIGINAL_PIXELS = 28_000_000
MIME_TYPES = {'JPEG': 'image/jpeg', 'PNG': 'image/png',
              'WEBP': 'image/webp', 'GIF': 'image/gif'}
INPUT_FORMATS = (*MIME_TYPES, 'BMP')


def verified_original_blob(data: bytes, *, expected_size=None) -> ImageBlob:
    """Full source pixels and actual image dimensions, no resampling.

    Browser-safe formats retain exact encoded source bytes. BMP is losslessly
    converted to PNG, never described as the same encoded source. No user bytes
    enter logs.
    """
    if type(data) is not bytes or not 0 < len(data) <= MAX_ORIGINAL_BYTES:
        raise ValueError('original image exceeds bounded source size')
    try:
        with Image.open(BytesIO(data), formats=INPUT_FORMATS) as image:
            width, height = image.size
            if (not 0 < width <= MAX_ORIGINAL_PIXELS
                    or not 0 < height <= MAX_ORIGINAL_PIXELS
                    or width * height > MAX_ORIGINAL_PIXELS):
                raise ValueError('original image dimensions rejected')
            if expected_size is not None and image.size != tuple(expected_size):
                raise ValueError('original image dimensions changed')
            if image.format == 'BMP':
                # BMP is present in legacy folders/ZIPs but is not a dependable
                # browser media type. Preserve pixels/dimensions, never resize.
                converted = BytesIO()
                image.save(converted, format='PNG')
                browser_data = converted.getvalue()
                if not 0 < len(browser_data) <= MAX_ORIGINAL_BYTES:
                    raise ValueError('converted original image exceeds limit')
                media_type = 'image/png'
            else:
                media_type = MIME_TYPES.get(image.format)
                if media_type is None:
                    raise ValueError('unsupported browser image format')
                image.verify()
                browser_data = data
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError('original image could not be verified') from exc
    return ImageBlob(browser_data, media_type, '"' + hashlib.sha256(browser_data).hexdigest() + '"')
