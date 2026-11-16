"""Pure, bounded EPUB 2 serialization of an explicitly ordered novel series.

The caller supplies complete, already selected chapters. This module neither
discovers missing works nor fetches, saves, or embeds external resources.
"""

from __future__ import annotations

from io import BytesIO
from xml.sax.saxutils import escape
import zipfile

from .media_parity_policy import MAX_NOVEL_CHARS, MediaPayloadRejected, NovelDocument
from .novel_export import _validated, _xml_text


MAX_SERIES_CHAPTERS = 100
MAX_SERIES_CHARS = MAX_NOVEL_CHARS
MAX_SERIES_EXPANDED_BYTES = 32 * 1024 * 1024
MAX_SERIES_EPUB_BYTES = 32 * 1024 * 1024

_CONTAINER = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<container version="1.0" '
    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
    '<rootfiles><rootfile full-path="OEBPS/content.opf" '
    'media-type="application/oebps-package+xml"/>'
    '</rootfiles></container>'
).encode('utf-8')


def render_novel_series_epub(
    documents: list[NovelDocument] | tuple[NovelDocument, ...],
    *,
    author_name: str,
    series_title: str,
) -> bytes:
    """Return one deterministic EPUB for the supplied chapter order.

    At least two chapters are required. The caller must establish that the
    supplied sequence is the intended complete series; this function cannot
    make that claim from NovelDocument alone.
    """
    if (type(documents) not in (list, tuple) or
            not 2 <= len(documents) <= MAX_SERIES_CHAPTERS or
            type(series_title) is not str or
            not 0 < len(series_title) <= 256 or
            any(char in '\r\n\t' for char in series_title)):
        raise MediaPayloadRejected()
    documents = tuple(documents)
    escaped_series_title = _xml_text(series_title)

    chapters: list[tuple[str, str, str]] = []
    seen_work_ids: set[str] = set()
    first_author_id: str | None = None
    first_series_id: str | None = None
    escaped_author: str | None = None
    total_chars = 0
    for document in documents:
        # Keep the single-novel XML, ID, per-work length and line boundaries.
        title, author, content = _validated(document, author_name)
        if (document.series_id is None or document.work_id in seen_work_ids or
                (first_author_id is not None and
                 document.author_id != first_author_id) or
                (first_series_id is not None and
                 document.series_id != first_series_id)):
            raise MediaPayloadRejected()
        seen_work_ids.add(document.work_id)
        first_author_id = document.author_id
        first_series_id = document.series_id
        escaped_author = author
        total_chars += len(content)
        if total_chars > MAX_SERIES_CHARS:
            raise MediaPayloadRejected()
        chapters.append((document.work_id, title, content))

    # The nonempty input and validation above establish these values.
    assert first_series_id is not None and escaped_author is not None
    book_id = 'urn:pixiv:novel-series:' + first_series_id
    manifest = [
        '<item id="toc" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
    ]
    spine: list[str] = []
    nav_points: list[str] = []
    chapter_entries: list[tuple[str, bytes]] = []
    expanded_bytes = len(b'application/epub+zip') + len(_CONTAINER)
    for number, (_, title, content) in enumerate(chapters, 1):
        chapter_id = f'chapter-{number:03d}'
        href = f'Text/{chapter_id}.xhtml'
        chapter = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ja">'
            '<head><title>' + title + '</title></head><body><h1>' + title +
            '</h1>' + ''.join('<p>' + escape(line) + '</p>'
                              for line in content.split('\n')) + '</body></html>'
        ).encode('utf-8')
        expanded_bytes += len(chapter)
        if expanded_bytes > MAX_SERIES_EXPANDED_BYTES:
            raise MediaPayloadRejected()
        chapter_entries.append(('OEBPS/' + href, chapter))
        manifest.append('<item id="' + chapter_id + '" href="' + href +
                        '" media-type="application/xhtml+xml"/>')
        spine.append('<itemref idref="' + chapter_id + '"/>')
        nav_points.append(
            '<navPoint id="' + chapter_id + '" playOrder="' + str(number) +
            '"><navLabel><text>' + title + '</text></navLabel>'
            '<content src="' + href + '"/></navPoint>'
        )

    opf = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" '
        'unique-identifier="BookId" version="2.0">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:title>' + escaped_series_title + '</dc:title><dc:creator>' +
        escaped_author + '</dc:creator><dc:language>ja</dc:language>'
        '<dc:identifier id="BookId">' + book_id + '</dc:identifier>'
        '</metadata><manifest>' + ''.join(manifest) + '</manifest>'
        '<spine toc="toc">' + ''.join(spine) + '</spine></package>'
    ).encode('utf-8')
    ncx = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx" version="2005-1">'
        '<head><meta name="dtb:uid" content="' + book_id + '"/>'
        '<meta name="dtb:depth" content="1"/>'
        '<meta name="dtb:totalPageCount" content="0"/>'
        '<meta name="dtb:maxPageNumber" content="0"/></head>'
        '<docTitle><text>' + escaped_series_title + '</text></docTitle>'
        '<navMap>' + ''.join(nav_points) + '</navMap></ncx>'
    ).encode('utf-8')
    expanded_bytes += len(opf) + len(ncx)
    if expanded_bytes > MAX_SERIES_EXPANDED_BYTES:
        raise MediaPayloadRejected()

    output = BytesIO()
    entries = [
        ('mimetype', b'application/epub+zip', zipfile.ZIP_STORED),
        ('META-INF/container.xml', _CONTAINER, zipfile.ZIP_DEFLATED),
        ('OEBPS/content.opf', opf, zipfile.ZIP_DEFLATED),
        ('OEBPS/toc.ncx', ncx, zipfile.ZIP_DEFLATED),
        *((name, data, zipfile.ZIP_DEFLATED) for name, data in chapter_entries),
    ]
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6, allowZip64=False) as archive:
        for name, data, compression in entries:
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            entry.compress_type = compression
            archive.writestr(entry, data)
            if output.tell() > MAX_SERIES_EPUB_BYTES:
                raise MediaPayloadRejected()
    result = output.getvalue()
    if len(result) > MAX_SERIES_EPUB_BYTES:
        raise MediaPayloadRejected()
    return result


__all__ = [
    'MAX_SERIES_CHAPTERS', 'MAX_SERIES_CHARS',
    'MAX_SERIES_EXPANDED_BYTES', 'MAX_SERIES_EPUB_BYTES',
    'render_novel_series_epub',
]
