"""Synthetic, offline contracts for explicitly ordered multi-chapter EPUBs."""

from io import BytesIO
import xml.etree.ElementTree as ET
import zipfile

import pytest

from pixivvault_nicegui.media_parity_policy import (
    MediaPayloadRejected, NovelDocument,
)
from pixivvault_nicegui import novel_series_export as series


def _novel(work_id: str, *, author_id: str = '91', series_id: str = '42',
           title: str = 'Chapter', content: str = 'Synthetic body') -> NovelDocument:
    return NovelDocument(work_id, author_id, title, content, series_id)


_DEFAULT = object()


def _render(documents=_DEFAULT, *, author_name='Author <&>', series_title='Series <&>'):
    if documents is _DEFAULT:
        documents = [_novel('123'), _novel('124', title='Second')]
    return series.render_novel_series_epub(
        documents, author_name=author_name, series_title=series_title,
    )


def test_chapter_order_matches_manifest_toc_spine_and_body_without_external_resources():
    documents = [
        _novel('127', title='Last <&>', content='three'),
        _novel('123', title='First', content='<script>x</script> & two'),
        _novel('126', title='Middle', content='one'),
    ]
    payload = _render(documents)
    assert payload == _render(documents)
    assert payload == _render(tuple(documents))
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        names = archive.namelist()
        paths = [f'OEBPS/Text/chapter-{n:03d}.xhtml' for n in range(1, 4)]
        assert names == ['mimetype', 'META-INF/container.xml',
                         'OEBPS/content.opf', 'OEBPS/toc.ncx', *paths]
        assert archive.read('mimetype') == b'application/epub+zip'
        assert archive.infolist()[0].compress_type == zipfile.ZIP_STORED
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) and
                   info.external_attr == 0o100644 << 16
                   for info in archive.infolist())
        for name in names[1:]:
            ET.fromstring(archive.read(name))

        opf = ET.fromstring(archive.read('OEBPS/content.opf'))
        ncx = ET.fromstring(archive.read('OEBPS/toc.ncx'))
        opf_ns = {'o': 'http://www.idpf.org/2007/opf',
                  'dc': 'http://purl.org/dc/elements/1.1/'}
        ncx_ns = {'n': 'http://www.daisy.org/z3986/2005/ncx'}
        manifest = opf.findall('o:manifest/o:item', opf_ns)
        spine = opf.findall('o:spine/o:itemref', opf_ns)
        nav = ncx.findall('n:navMap/n:navPoint', ncx_ns)
        chapter_ids = [f'chapter-{n:03d}' for n in range(1, 4)]
        hrefs = [f'Text/{name}' for name in
                 ('chapter-001.xhtml', 'chapter-002.xhtml', 'chapter-003.xhtml')]
        assert [(item.get('id'), item.get('href')) for item in manifest[1:]] == list(
            zip(chapter_ids, hrefs))
        assert [item.get('idref') for item in spine] == chapter_ids
        assert [point.get('id') for point in nav] == chapter_ids
        assert [point.get('playOrder') for point in nav] == ['1', '2', '3']
        assert [point.find('n:content', ncx_ns).get('src') for point in nav] == hrefs
        assert [point.find('n:navLabel/n:text', ncx_ns).text for point in nav] == [
            doc.title for doc in documents]
        assert opf.find('o:metadata/dc:title', opf_ns).text == 'Series <&>'
        assert opf.find('o:metadata/dc:creator', opf_ns).text == 'Author <&>'
        assert [ET.fromstring(archive.read(path)).find(
            '{http://www.w3.org/1999/xhtml}body/{http://www.w3.org/1999/xhtml}p'
        ).text for path in paths] == [doc.raw_content for doc in documents]
        assert b'&lt;script&gt;' in archive.read(paths[1])
        assert b'<script>' not in archive.read(paths[1])
        assert b'Last &lt;&amp;&gt;' in archive.read(paths[0])
        assert b'Series &lt;&amp;&gt;' in archive.read('OEBPS/content.opf')
        assert b'Author &lt;&amp;&gt;' in archive.read('OEBPS/content.opf')
        assert not any(b'<script' in archive.read(name) or b'<img' in archive.read(name)
                       for name in names[1:])
        assert all(item.get('href') in {'toc.ncx', *hrefs} for item in manifest)


def test_body_normalizes_line_endings_and_keeps_paragraph_order():
    payload = _render([_novel('123', content='A\r\nB\rC\n<&>'), _novel('124')])
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        chapter = ET.fromstring(archive.read('OEBPS/Text/chapter-001.xhtml'))
    paragraphs = chapter.findall(
        '{http://www.w3.org/1999/xhtml}body/{http://www.w3.org/1999/xhtml}p'
    )
    assert [paragraph.text for paragraph in paragraphs] == ['A', 'B', 'C', '<&>']


@pytest.mark.parametrize('documents', [
    None, [], [_novel('123')], {}, (_novel('123'), 'not a document'),
    [_novel('123'), _novel('123')],
    [_novel('123'), _novel('124', author_id='92')],
    [_novel('123'), _novel('124', series_id='43')],
    [_novel('123'), _novel('124', series_id=None)],
    [_novel('123', series_id=None), _novel('124')],
    [_novel('0'), _novel('124')],
    [_novel('123'), _novel(124)],
    [_novel('123', author_id='bad'), _novel('124')],
    [_novel('123'), _novel('124', series_id=True)],
    [_novel('123', content=''), _novel('124')],
    [_novel('123', content='\n' * 20_000), _novel('124')],
])
def test_rejects_empty_wrong_types_identity_mismatch_and_duplicates(documents):
    with pytest.raises(MediaPayloadRejected, match='unsupported or invalid'):
        _render(documents)


@pytest.mark.parametrize('field,value', [
    ('title', 'bad\x01'), ('title', 'bad\ud800'),
    ('content', 'bad\x00'), ('content', 'bad\udfff'),
])
def test_rejects_invalid_xml_characters_in_chapters(field, value):
    changes = {field: value}
    with pytest.raises(MediaPayloadRejected):
        _render([_novel('123', **changes), _novel('124')])


@pytest.mark.parametrize('metadata', [
    {'author_name': 'bad\x01'}, {'author_name': 'bad\ud800'},
    {'author_name': 1}, {'author_name': 'A' * 257},
    {'series_title': 'bad\x01'}, {'series_title': 'bad\ud800'},
    {'series_title': 1}, {'series_title': ''},
    {'series_title': 'A' * 257}, {'series_title': 'two\nlines'},
])
def test_rejects_invalid_metadata_without_reflecting_values(metadata):
    with pytest.raises(MediaPayloadRejected, match='unsupported or invalid') as exc:
        _render(**metadata)
    assert 'bad' not in str(exc.value)


def test_chapter_and_total_character_limits_accept_boundary_and_reject_next():
    chapters = [_novel(str(n)) for n in range(1, series.MAX_SERIES_CHAPTERS + 1)]
    assert _render(chapters).startswith(b'PK')
    with pytest.raises(MediaPayloadRejected):
        _render(chapters + [_novel('101')])

    exact = [_novel('123', content='x' * (series.MAX_SERIES_CHARS - 1)),
             _novel('124', content='y')]
    assert _render(exact).startswith(b'PK')
    with pytest.raises(MediaPayloadRejected):
        _render([exact[0], _novel('124', content='yy')])


def test_expanded_and_output_byte_limits_accept_boundary_and_reject_next(monkeypatch):
    payload = _render()
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        expanded = sum(info.file_size for info in archive.infolist())
    monkeypatch.setattr(series, 'MAX_SERIES_EXPANDED_BYTES', expanded)
    monkeypatch.setattr(series, 'MAX_SERIES_EPUB_BYTES', len(payload))
    assert _render() == payload
    monkeypatch.setattr(series, 'MAX_SERIES_EXPANDED_BYTES', expanded - 1)
    with pytest.raises(MediaPayloadRejected):
        _render()
    monkeypatch.setattr(series, 'MAX_SERIES_EXPANDED_BYTES', expanded)
    monkeypatch.setattr(series, 'MAX_SERIES_EPUB_BYTES', len(payload) - 1)
    with pytest.raises(MediaPayloadRejected):
        _render()
