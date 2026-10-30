"""P1 regression specification. Execution awaits explicit test authorization."""
from io import BytesIO
import zipfile
import xml.etree.ElementTree as ET
import pytest
from PIL import Image
from pixivvault_nicegui.media_parity_policy import NovelDocument, MediaPayloadRejected
from pixivvault_nicegui.novel_practical_export import NovelAsset, render_practical_bundle
from pixivvault_nicegui.managed_novel_jobs import PinnedNovelSource, ManagedNovelError


def document(work='123', series=None):
    return NovelDocument(work, '91', '題名', '[[rb:漢字 > かんじ]][newpage]<script>&[uploadedimage:1]', series)


def picture():
    data = BytesIO()
    Image.new('RGB', (2, 2), 'blue').save(data, format='PNG')
    return data.getvalue()


@pytest.mark.parametrize('fmt', ['TXT', 'EPUB', 'BOTH'])
def test_flet_output_contract(fmt):
    result = render_practical_bundle([document()], author_name='作者', output_format=fmt,
                                    assets={('123', '1'): NovelAsset('1', picture())},
                                    cover=NovelAsset('cover', picture()))
    if fmt in ('TXT', 'BOTH'):
        txt = result['題名(123).txt']
        assert txt.startswith(b'\xef\xbb\xbf')
        assert txt.decode('utf-8-sig') == '漢字《かんじ》\n\n---\n\n<script>&[挿絵: images/123-1.png]'
        assert 'images/cover_123.png' in result
    if fmt in ('EPUB', 'BOTH'):
        with zipfile.ZipFile(BytesIO(result['題名(123).epub'])) as archive:
            tree = ET.fromstring(archive.read('OEBPS/Text/chapter.xhtml'))
            assert tree.find('.//{http://www.w3.org/1999/xhtml}ruby') is not None
            assert tree.find('.//{http://www.w3.org/1999/xhtml}hr') is not None
            assert tree.find('.//{http://www.w3.org/1999/xhtml}script') is None
            assert archive.read('OEBPS/Images/123-1.png')


def test_missing_image_never_silently_disappears():
    with pytest.raises(MediaPayloadRejected):
        render_practical_bundle([document()], author_name='作者', output_format='BOTH')


def test_series_reuses_explicit_order_and_rejects_missing_chapter():
    docs = [document('124', '9'), document('123', '9')]
    kwargs = dict(author_name='作者', series_title='連作', output_format='EPUB',
                  assets={(wid, '1'): NovelAsset('1', picture()) for wid in ('123', '124')})
    with pytest.raises(MediaPayloadRejected):
        render_practical_bundle(docs, expected_work_ids=('124', '125', '123'), **kwargs)
    first = render_practical_bundle(docs, expected_work_ids=('124', '123'), **kwargs)
    assert first == render_practical_bundle(docs, expected_work_ids=('124', '123'), **kwargs)


def test_reader_selection_rejects_unknown_id_before_request():
    class Reader:
        def get_novel_payload(self, *args):
            raise AssertionError('must not request')
    source = PinnedNovelSource(Reader(), {'123': '91'})
    with pytest.raises(ManagedNovelError):
        source.fetch('124')


def test_reader_author_pin_preserves_assets_for_bundle():
    calls = []
    class Reader:
        def require_identity(self): return "91"
        def get_novel_payload(self, work, author):
            calls.append((work, author))
            return {'id': work, 'userId': author, 'title': '題名', 'content': '本文',
                    'textEmbeddedImages': {'1': {}}, 'seriesNavData': None}
    source = PinnedNovelSource(Reader(), {'123': '91'})
    assert source.fetch('123')['textEmbeddedImages'] == {'1': {}}
    assert calls == [('123', '91')]