"""Synthetic offline EPUB/TXT boundaries; nothing is written to user storage."""

from io import BytesIO
import xml.etree.ElementTree as ET
import zipfile

import pytest

from pixivvault_nicegui.media_parity_policy import (
    MAX_NOVEL_CHARS, MediaPayloadRejected, NovelDocument, parse_novel_detail,
)
from pixivvault_nicegui.novel_export import render_novel_epub, render_novel_txt


def _novel(content="Line one\n<script>alert('x')</script> & second line", title="Title <&>"):
    return parse_novel_detail(
        {"id": "123", "userId": "91", "title": title, "content": content,
         "textEmbeddedImages": {}},
        work_id="123", expected_author_id="91",
    )


def test_txt_preserves_untrusted_text_without_html_execution():
    body = render_novel_txt(_novel(), author_name="Author <&>")
    assert body.startswith("Title <&>\nAuthor <&>\n\n".encode())
    assert b"<script>" in body


def test_epub_is_parseable_and_escapes_title_author_and_body():
    content = render_novel_epub(_novel(), author_name="Author <&>")
    with zipfile.ZipFile(BytesIO(content)) as archive:
        names = archive.namelist()
        assert names == ["mimetype", "META-INF/container.xml",
                         "OEBPS/content.opf", "OEBPS/toc.ncx",
                         "OEBPS/Text/chapter.xhtml"]
        assert all(entry.date_time == (1980, 1, 1, 0, 0, 0)
                   for entry in archive.infolist())
        assert all(entry.external_attr == 0o100644 << 16
                   for entry in archive.infolist())
        assert archive.infolist()[0].compress_type == zipfile.ZIP_STORED
        assert archive.read("mimetype") == b"application/epub+zip"
        for name in names[1:]:
            ET.fromstring(archive.read(name))
        chapter = archive.read(names[-1])
        assert b"&lt;script&gt;" in chapter
        assert b"<script>" not in chapter
        assert b"Title &lt;&amp;&gt;" in chapter
        assert b"Author &lt;&amp;&gt;" in archive.read("OEBPS/content.opf")
        assert b"http://" in archive.read("OEBPS/content.opf")  # XML namespace only
        assert len(names) == 5  # No remote scripts/images/resources.


@pytest.mark.parametrize("author", ["", "a" * 257, "<bad\x00>", "one\ntwo", "a\ud800"])
def test_export_rejects_bad_author_without_reflecting_input(author):
    for export in (render_novel_txt, render_novel_epub):
        with pytest.raises(MediaPayloadRejected, match="unsupported or invalid"):
            export(_novel(), author_name=author)


@pytest.mark.parametrize("body", ["bad\x01xml", "bad\ud800surrogate"])
def test_export_rejects_unsafe_xml_codepoints(body):
    forged = NovelDocument("123", "91", "Title", body, None)
    for export in (render_novel_txt, render_novel_epub):
        with pytest.raises(MediaPayloadRejected):
            export(forged, author_name="Synthetic Author")


def test_export_rejects_forged_document_and_bounds_without_mutation():
    sample = _novel()
    bad = [
        {}, NovelDocument("0", "91", "Title", "Valid", None),
        NovelDocument(123, "91", "Title", "Valid", None),
        NovelDocument("123", 91, "Title", "Valid", None),
        NovelDocument("123", "bad", "Title", "Valid", None),
        NovelDocument("123", "91", "Title", "", None),
        NovelDocument("123", "91", "Title", "x" * (MAX_NOVEL_CHARS + 1), None),
        NovelDocument("123", "91", "Title", "\n" * 20_000, None),
    ]
    for item in bad:
        with pytest.raises(MediaPayloadRejected):
            render_novel_epub(item, author_name="Author")
    assert sample == _novel()


def test_export_payload_is_deterministic_and_does_not_include_urls():
    sample = _novel()
    first = render_novel_epub(sample, author_name="Author")
    assert first == render_novel_epub(sample, author_name="Author")
    assert b"i.pximg.net" not in first
