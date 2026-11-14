"""Bounded, pure validation of novel and ugoira metadata.

This module has no network, filesystem, Cookie, archive extraction or UI side
effects. Parsed values are untrusted data and must not be inserted into HTML.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from urllib.parse import urlsplit


_ID = re.compile(r'[1-9][0-9]{0,19}\Z')
_FRAME_NAME = re.compile(r'[A-Za-z0-9_-]{1,80}\.(?:jpg|jpeg|png|webp)\Z')
_WINDOWS_RESERVED = frozenset(
    {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'} |
    {f'COM{n}' for n in range(1, 10)} |
    {f'LPT{n}' for n in range(1, 10)}
)
MAX_NOVEL_CHARS = 2_000_000
MAX_UGOIRA_FRAMES = 3000
MAX_FRAME_DELAY_MS = 60_000
MAX_UGOIRA_DURATION_MS = 3 * 60 * 60 * 1000


class MediaPayloadRejected(ValueError):
    """Safe failure. Never reflect a remote payload or URL in diagnostics."""

    def __init__(self):
        super().__init__('unsupported or invalid Pixiv media metadata')


@dataclass(frozen=True, slots=True)
class NovelDocument:
    work_id: str = field(repr=False)
    author_id: str = field(repr=False)
    title: str = field(repr=False)
    raw_content: str = field(repr=False)
    series_id: str | None


@dataclass(frozen=True, slots=True)
class UgoiraFrame:
    filename: str
    delay_ms: int


@dataclass(frozen=True, slots=True)
class UgoiraManifest:
    work_id: str = field(repr=False)
    archive_url: str = field(repr=False)
    frames: tuple[UgoiraFrame, ...] = field(repr=False)
    duration_ms: int


def _identifier(raw: object) -> str:
    if type(raw) not in (str, int):
        raise MediaPayloadRejected()
    if type(raw) is int and not 0 < raw < 10**20:
        raise MediaPayloadRejected()
    value = str(raw)
    if _ID.fullmatch(value) is None:
        raise MediaPayloadRejected()
    return value


def _text(raw: object, *, limit: int, allow_blank: bool = False) -> str:
    if (type(raw) is not str or len(raw) > limit or
            (not allow_blank and not raw) or
            any(ord(char) == 0 or (ord(char) < 32 and char not in '\n\r\t')
                or ord(char) == 127 for char in raw)):
        raise MediaPayloadRejected()
    return raw


def _cdn_url(raw: object, *, work_id: str, animation: bool) -> str:
    value = _text(raw, limit=4096)
    # urllib.parse may strip CR/LF/TAB before parsing. Reject the *original*
    # URL characters so a malformed input cannot be normalized into approval.
    if (value != value.strip() or '\\' in value or not value.isascii() or
            any(ord(char) <= 32 or ord(char) == 127 for char in value)):
        raise MediaPayloadRejected()
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or parsed.netloc != 'i.pximg.net'
                or parsed.username is not None or parsed.password is not None
                or parsed.port is not None or parsed.fragment):
            raise MediaPayloadRejected()
    except ValueError:
        raise MediaPayloadRejected() from None
    if (not parsed.path.startswith('/') or '%' in parsed.path or
            '..' in parsed.path.split('/') or '//' in parsed.path):
        raise MediaPayloadRejected()
    if animation:
        # Original Pixiv metadata is a ZIP served from the same image CDN.
        # Check its embedded ID so an unrelated remote archive cannot be used.
        if (not parsed.path.startswith('/img-zip-ugoira/') or
                re.fullmatch(r'[1-9][0-9]{0,19}_ugoira[^/]{0,80}\.zip',
                             parsed.path.rsplit('/', 1)[-1]) is None or
                not parsed.path.rsplit('/', 1)[-1].startswith(f'{work_id}_ugoira')):
            raise MediaPayloadRejected()
    return value


def parse_novel_detail(body: object, *, work_id: str,
                       expected_author_id: str) -> NovelDocument:
    """Validate Pixiv novel body; never render or persist the raw content."""
    wid, uid = _identifier(work_id), _identifier(expected_author_id)
    if type(body) is not dict:
        raise MediaPayloadRejected()
    if _identifier(body.get('id')) != wid or _identifier(body.get('userId')) != uid:
        raise MediaPayloadRejected()
    if body.get('isMasked') is True:
        raise MediaPayloadRejected()
    title = _text(body.get('title'), limit=256)
    content = _text(body.get('content'), limit=MAX_NOVEL_CHARS, allow_blank=False)
    series = body.get('seriesNavData')
    if series is not None and type(series) is not dict:
        raise MediaPayloadRejected()
    series_id = None
    if series is not None and series.get('seriesId') is not None:
        series_id = _identifier(series['seriesId'])
    # Embedded images are not fetched/embedded in this read-only milestone.
    # Reject malformed shapes so later renderers cannot silently trust them.
    embedded = body.get('textEmbeddedImages', {})
    if embedded is None:
        embedded = {}  # Pixiv can represent an absent optional image map as null.
    if type(embedded) is not dict or len(embedded) > 100:
        raise MediaPayloadRejected()
    cover = body.get('coverUrl') or ''
    if cover:
        _cdn_url(cover, work_id=wid, animation=False)
    return NovelDocument(wid, uid, title, content, series_id)


def parse_ugoira_manifest(body: object, *, work_id: str) -> UgoiraManifest:
    """Validate a frame-order contract without touching a ZIP or disk."""
    wid = _identifier(work_id)
    if type(body) is not dict:
        raise MediaPayloadRejected()
    archive = _cdn_url(body.get('originalSrc'), work_id=wid, animation=True)
    rows = body.get('frames')
    if type(rows) is not list or not 1 <= len(rows) <= MAX_UGOIRA_FRAMES:
        raise MediaPayloadRejected()
    frames: list[UgoiraFrame] = []
    filenames: set[str] = set()
    total_duration = 0
    for row in rows:
        if type(row) is not dict:
            raise MediaPayloadRejected()
        filename = row.get('file')
        delay = row.get('delay')
        if (type(filename) is not str or _FRAME_NAME.fullmatch(filename) is None or
                filename.casefold() in filenames or
                filename.partition('.')[0].upper() in _WINDOWS_RESERVED or
                type(delay) is not int or
                not 1 <= delay <= MAX_FRAME_DELAY_MS):
            raise MediaPayloadRejected()
        filenames.add(filename.casefold())
        total_duration += delay
        if total_duration > MAX_UGOIRA_DURATION_MS:
            raise MediaPayloadRejected()
        frames.append(UgoiraFrame(filename, delay))
    return UgoiraManifest(wid, archive, tuple(frames), total_duration)


__all__ = [
    'MAX_NOVEL_CHARS', 'MAX_UGOIRA_FRAMES', 'MAX_UGOIRA_DURATION_MS',
    'MAX_FRAME_DELAY_MS', 'MediaPayloadRejected',
    'NovelDocument', 'UgoiraFrame', 'UgoiraManifest',
    'parse_novel_detail', 'parse_ugoira_manifest',
]
