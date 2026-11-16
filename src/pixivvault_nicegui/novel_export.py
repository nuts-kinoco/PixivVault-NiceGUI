"""Bounded, offline TXT/EPUB serialization for validated novel documents.

No HTTP, disk access, remote resource references or executable markup.
These bytes are *not* a durable managed download receipt: future integration
must atomically persist, verify and reconcile them in a separate managed root.
Illustrations and multi-chapter series are intentionally not claimed here.
"""

from __future__ import annotations

from io import BytesIO
import re
from xml.sax.saxutils import escape
import zipfile

from .media_parity_policy import (
    MAX_NOVEL_CHARS, MediaPayloadRejected, NovelDocument,
)

_ID = re.compile(r"[1-9][0-9]{0,19}\Z")
_MAX_AUTHOR_CHARS = 256
_MAX_LINES = 20_000
_MAX_EPUB_BYTES = 32 * 1024 * 1024


def _xml_text(value: str) -> str:
    """Reject invalid XML 1.0 scalars instead of silently dropping content."""
    if any(not (
        code == 9 or code == 10 or code == 13 or
        0x20 <= code <= 0xD7FF or 0xE000 <= code <= 0xFFFD or
        0x10000 <= code <= 0x10FFFF
    ) for code in map(ord, value)):
        raise MediaPayloadRejected()
    return escape(value)


def _validated(document: NovelDocument, author_name: str) -> tuple[str, str, str]:
    if type(document) is not NovelDocument or type(author_name) is not str:
        raise MediaPayloadRejected()
    if (type(document.work_id) is not str or
            type(document.author_id) is not str or
            _ID.fullmatch(document.work_id) is None or
            _ID.fullmatch(document.author_id) is None or
            (document.series_id is not None and
             (type(document.series_id) is not str or
              _ID.fullmatch(document.series_id) is None))):
        raise MediaPayloadRejected()
    if (type(document.title) is not str or not 0 < len(document.title) <= 256 or
            type(document.raw_content) is not str or
            not 0 < len(document.raw_content) <= MAX_NOVEL_CHARS or
            not 0 < len(author_name) <= _MAX_AUTHOR_CHARS):
        raise MediaPayloadRejected()
    # Headings cannot masquerade as multiple headers, and all text stays inert.
    if any(ch in "\r\n\t" for ch in document.title + author_name):
        raise MediaPayloadRejected()
    title, author = _xml_text(document.title), _xml_text(author_name)
    text = document.raw_content.replace("\r\n", "\n").replace("\r", "\n")
    if text.count("\n") >= _MAX_LINES:
        raise MediaPayloadRejected()
    _xml_text(text)
    return title, author, text


def render_novel_txt(document: NovelDocument, *, author_name: str) -> bytes:
    """Create a UTF-8 TXT payload from an already validated synthetic DTO."""
    _validated(document, author_name)
    return (document.title + "\n" + author_name + "\n\n" +
            document.raw_content.replace("\r\n", "\n").replace("\r", "\n")
            ).encode("utf-8")


def render_novel_epub(document: NovelDocument, *, author_name: str) -> bytes:
    """Create a minimal EPUB 2 archive; remote HTML/images are never embedded."""
    title, author, content = _validated(document, author_name)
    work_id = document.work_id  # strict ASCII digits
    book_id = "urn:pixiv:novel:" + work_id
    chapter = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ja">'
        "<head><title>" + title + "</title></head><body><h1>" + title +
        "</h1>" + "".join("<p>" + escape(line) + "</p>"
                         for line in content.split("\n")) + "</body></html>"
    ).encode("utf-8")
    opf = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" '
        'unique-identifier="BookId" version="2.0">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:title>" + title + "</dc:title><dc:creator>" + author +
        "</dc:creator><dc:language>ja</dc:language>"
        '<dc:identifier id="BookId">' + book_id + '</dc:identifier>'
        '</metadata><manifest>'
        '<item id="toc" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        '<item id="chapter" href="Text/chapter.xhtml" '
        'media-type="application/xhtml+xml"/>'
        '</manifest><spine toc="toc"><itemref idref="chapter"/></spine>'
        '</package>'
    ).encode("utf-8")
    ncx = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx" version="2005-1">'
        '<head><meta name="dtb:uid" content="' + book_id + '"/>'
        '<meta name="dtb:depth" content="1"/>'
        '<meta name="dtb:totalPageCount" content="0"/>'
        '<meta name="dtb:maxPageNumber" content="0"/></head>'
        '<docTitle><text>' + title + '</text></docTitle>'
        '<navMap><navPoint id="chapter" playOrder="1">'
        '<navLabel><text>' + title + '</text></navLabel>'
        '<content src="Text/chapter.xhtml"/></navPoint></navMap></ncx>'
    ).encode("utf-8")
    container = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/>'
        '</rootfiles></container>'
    ).encode("utf-8")
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6, allowZip64=False) as archive:
        for name, data, compression in (
            ("mimetype", b"application/epub+zip", zipfile.ZIP_STORED),
            ("META-INF/container.xml", container, zipfile.ZIP_DEFLATED),
            ("OEBPS/content.opf", opf, zipfile.ZIP_DEFLATED),
            ("OEBPS/toc.ncx", ncx, zipfile.ZIP_DEFLATED),
            ("OEBPS/Text/chapter.xhtml", chapter, zipfile.ZIP_DEFLATED),
        ):
            # ZipFile.writestr(name, ...) otherwise inserts local wall-clock
            # timestamps. Stable metadata is required for M3 hash receipts.
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            entry.compress_type = compression
            archive.writestr(entry, data)
    result = output.getvalue()
    if len(result) > _MAX_EPUB_BYTES:
        raise MediaPayloadRejected()
    return result


__all__ = ["render_novel_txt", "render_novel_epub"]
