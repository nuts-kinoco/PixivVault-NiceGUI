"""Bounded offline Pillow GIF encoding, matching the old GIF centisecond floor.

All validated frames are emitted separately (including identical frames), with
loop=0 and disposal=2. GIF cannot express sub-10ms timing: encoded delays are
floor(source_ms / 10) * 10, as in the original Pillow path, and are recorded
separately from source duration. No silent MP4 fallback or external codec runs.
Each palette uses up to 255 visible RGB colors plus transparent index 255.
Zero alpha is transparent; nonzero alpha is opaque (no partial-alpha promise).
The deadline is checked between Pillow calls/chunks; it is not OS preemption.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
import hashlib
import time
import zipfile

from PIL import GifImagePlugin, Image

from .job_service import JobCancelled
from .ugoira_archive import (
    MAX_FRAME_PIXELS, MAX_TOTAL_DECODED_PIXELS, MAX_SINGLE_FRAME_BYTES,
    SUPPORTED_TYPES, inspect_ugoira_archive,
)

MAX_GIF_BYTES = 64 * 1024 * 1024
MAX_GIF_SECONDS = 30.0


class UgoiraGifError(ValueError):
    def __init__(self):
        super().__init__('ugoira GIF conversion failed')


@dataclass(frozen=True, slots=True)
class RenderedUgoiraGif:
    data: bytes = field(repr=False)
    sha256: str = field(repr=False)
    width: int
    height: int
    frame_count: int
    source_duration_ms: int
    encoded_delays_ms: tuple[int, ...]


def _guard(checkpoint, deadline):
    checkpoint()
    if time.monotonic() > deadline:
        raise UgoiraGifError()


def _gif_plan(inspection):
    width = max(frame.width for frame in inspection.frames)
    height = max(frame.height for frame in inspection.frames)
    pixels = width * height
    if (width > 65535 or height > 65535 or pixels > MAX_FRAME_PIXELS
            or pixels * len(inspection.frames) > MAX_TOTAL_DECODED_PIXELS):
        raise UgoiraGifError()
    return width, height, tuple(frame.delay_ms // 10 * 10 for frame in inspection.frames)


def _verify_gif(data, inspection, checkpoint, deadline):
    width, height, delays = _gif_plan(inspection)
    if type(data) is not bytes or not 0 < len(data) <= MAX_GIF_BYTES or not data.endswith(b';'):
        raise UgoiraGifError()
    with Image.open(BytesIO(data), formats=('GIF',)) as image:
        if image.size != (width, height) or image.info.get('loop') != 0:
            raise UgoiraGifError()
        for index, delay in enumerate(delays):
            _guard(checkpoint, deadline)
            image.seek(index)
            if (image.size != (width, height) or image.info.get('duration', 0) != delay
                    or image.disposal_method != 2):
                raise UgoiraGifError()
            image.load()
        try:
            image.seek(len(delays))
        except EOFError:
            pass
        else:
            raise UgoiraGifError()
    _guard(checkpoint, deadline)
    return RenderedUgoiraGif(data, hashlib.sha256(data).hexdigest(), width, height,
                             len(delays), inspection.duration_ms, delays)


def render_ugoira_gif(raw_zip, manifest, *, checkpoint=None):
    check = (lambda: None) if checkpoint is None else checkpoint
    if not callable(check):
        raise UgoiraGifError()
    deadline = time.monotonic() + MAX_GIF_SECONDS
    try:
        _guard(check, deadline)
        inspection = inspect_ugoira_archive(raw_zip, manifest)
        _guard(check, deadline)
        width, height, _ = _gif_plan(inspection)
        output = BytesIO()
        def emit(chunk):
            _guard(check, deadline)
            if output.tell() + len(chunk) > MAX_GIF_BYTES:
                raise UgoiraGifError()
            output.write(chunk)
        with zipfile.ZipFile(BytesIO(raw_zip), 'r', allowZip64=True) as archive:
            for index, frame in enumerate(inspection.frames):
                _guard(check, deadline)
                with archive.open(frame.filename) as stream:
                    content = stream.read(MAX_SINGLE_FRAME_BYTES + 1)
                if len(content) != frame.bytes_count or hashlib.sha256(content).hexdigest() != frame.sha256:
                    raise UgoiraGifError()
                kind = SUPPORTED_TYPES['.' + frame.filename.rsplit('.', 1)[-1]]
                with Image.open(BytesIO(content), formats=(kind,)) as source:
                    source.load()
                    with source.convert('RGBA') as rgba, Image.new('RGBA', (width, height)) as canvas:
                        canvas.paste(rgba, (0, 0))
                        # GIF alpha is binary: zero is transparent, nonzero
                        # opaque RGB (the previous zero-alpha policy). Every
                        # palette reserves index 255, even for opaque frames,
                        # so disposal and padding share one transparent index.
                        with canvas.convert('RGB') as rgb, canvas.getchannel('A') as alpha:
                            with rgb.quantize(colors=255) as indexed, alpha.point(
                                    lambda value: 255 if value == 0 else 0) as mask:
                                palette = indexed.getpalette()[:765]
                                indexed.putpalette(palette + [0] * (768 - len(palette)))
                                indexed.paste(255, mask=mask)
                                params = {'duration': frame.delay_ms, 'disposal': 2,
                                          'include_color_table': True, 'transparency': 255}
                                if index == 0:
                                    header, _ = GifImagePlugin.getheader(
                                        indexed, info={'loop': 0, 'transparency': 255, 'background': 255})
                                    for chunk in header:
                                        emit(chunk)
                                for chunk in GifImagePlugin.getdata(indexed, **params):
                                    emit(chunk)
                _guard(check, deadline)
        emit(b';')
        return _verify_gif(output.getvalue(), inspection, check, deadline)
    except JobCancelled:
        raise
    except Exception:
        raise UgoiraGifError() from None


__all__ = ['render_ugoira_gif', 'RenderedUgoiraGif', 'UgoiraGifError']
