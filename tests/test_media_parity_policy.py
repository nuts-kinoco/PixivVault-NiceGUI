"""Phase11 M1: offline metadata parsing only; no original files or Pixiv GET."""

import pytest

from pixivvault_nicegui.media_parity_policy import (
    MAX_NOVEL_CHARS, MAX_UGOIRA_FRAMES, MediaPayloadRejected,
    parse_novel_detail, parse_ugoira_manifest,
)


def _novel(**changes):
    data = {'id': '123', 'userId': '91', 'title': 'Synthetic title',
            'content': 'Line one\n[chapter:Second]\n<script>not HTML</script>',
            'seriesNavData': {'seriesId': '42'},
            'textEmbeddedImages': {}, 'coverUrl': ''}
    data.update(changes)
    return data


def _ugoira(**changes):
    data = {'originalSrc': 'https://i.pximg.net/img-zip-ugoira/img/2026/01/02/123_ugoira1920x1080.zip',
            'frames': [{'file': '000000.jpg', 'delay': 50},
                       {'file': '000001.jpg', 'delay': 150}]}
    data.update(changes)
    return data


def test_novel_keeps_untrusted_text_inert_and_checks_identity():
    data = parse_novel_detail(_novel(), work_id='123', expected_author_id='91')
    assert data.work_id == '123' and data.author_id == '91'
    assert data.series_id == '42'
    assert '<script>' in data.raw_content  # No untrusted HTML rendering here.
    assert data.raw_content.startswith('Line one')
    assert 'Synthetic title' not in repr(data)
    assert '<script>' not in repr(data)
    assert parse_novel_detail(_novel(textEmbeddedImages=None), work_id='123',
                              expected_author_id='91') == data
    assert parse_novel_detail(_novel(coverUrl=None), work_id='123',
                              expected_author_id='91') == data


@pytest.mark.parametrize('body', [
    _novel(id='124'), _novel(userId='92'), _novel(userId=True),
    _novel(title=''), _novel(content=''), _novel(content='x' * (MAX_NOVEL_CHARS + 1)),
    _novel(content='valid\x00invalid'), _novel(isMasked=True),
    _novel(seriesNavData={'seriesId': '-1'}), _novel(seriesNavData=[]),
    _novel(textEmbeddedImages=[]), _novel(textEmbeddedImages={str(i): i for i in range(101)}),
    _novel(coverUrl='https://example.org/secret.png'),
    _novel(coverUrl='https://i.pximg.net:443/images/a.png'),
    [], None,
])
def test_novel_rejects_untrusted_shapes_without_payload_in_error(body):
    with pytest.raises(MediaPayloadRejected, match='unsupported or invalid Pixiv media metadata') as exc:
        parse_novel_detail(body, work_id='123', expected_author_id='91')
    assert 'secret' not in str(exc.value)


def test_ugoira_manifest_preserves_frame_order_and_variable_delay():
    result = parse_ugoira_manifest(_ugoira(), work_id='123')
    assert result.work_id == '123' and result.duration_ms == 200
    assert tuple((frame.filename, frame.delay_ms) for frame in result.frames) == (
        ('000000.jpg', 50), ('000001.jpg', 150))
    assert 'pximg.net' not in repr(result)


def test_ugoira_duration_exact_limit_is_valid_and_next_frame_is_rejected():
    accepted = _ugoira(frames=[{'file': f'{n:06d}.jpg', 'delay': 60_000}
                               for n in range(180)])
    assert parse_ugoira_manifest(accepted, work_id='123').duration_ms == 10_800_000
    rejected = _ugoira(frames=[{'file': f'{n:06d}.jpg', 'delay': 60_000}
                               for n in range(181)])
    with pytest.raises(MediaPayloadRejected):
        parse_ugoira_manifest(rejected, work_id='123')


def test_ugoira_max_unique_frames_and_maximum_individual_delay():
    largest = _ugoira(frames=[{'file': f'{i:06d}.png', 'delay': 1}
                              for i in range(MAX_UGOIRA_FRAMES)])
    parsed = parse_ugoira_manifest(largest, work_id='123')
    assert len(parsed.frames) == MAX_UGOIRA_FRAMES
    assert parsed.duration_ms == MAX_UGOIRA_FRAMES
    assert parse_ugoira_manifest(_ugoira(frames=[{'file':'a.jpg','delay':60_000}]),
                                 work_id='123').duration_ms == 60_000


@pytest.mark.parametrize('body', [
    _ugoira(originalSrc='https://example.com/123_ugoira.zip'),
    _ugoira(originalSrc='http://i.pximg.net/img-zip-ugoira/123_ugoira.zip'),
    _ugoira(originalSrc='https://i.pximg.net.evil.test/img-zip-ugoira/123_ugoira.zip'),
    _ugoira(originalSrc='https://i.pximg.net/img-zip-ugoira/124_ugoira.zip'),
    _ugoira(originalSrc='https://i.pximg.net/img-zip-ugoira/../123_ugoira.zip'),
    _ugoira(originalSrc='https://i.pximg.net/img-zip-ugoira/%2e%2e/123_ugoira.zip'),
    _ugoira(originalSrc='https://i.pximg.net/img-zip-ugoira/123_ugoira.zip\n'),
    _ugoira(originalSrc='https://i.pximg.net/img-zip-ugoira/123_ugoira.zip\t?x=1'),
    _ugoira(originalSrc='https://i.pximg.net/img-zip-ugoira/123_ugoira.zip\u3000'),
    _ugoira(originalSrc='https://i.pximg.net/img-zip-ugoira/123_ugoira.jpg'),
    _ugoira(frames=[]), _ugoira(frames=[{'file': 'x.jpg', 'delay': 1}] * (MAX_UGOIRA_FRAMES + 1)),
    _ugoira(frames=[{'file': '../x.jpg', 'delay': 100}]),
    _ugoira(frames=[{'file': '000000.jpg', 'delay': 50}] * 2),
    _ugoira(frames=[{'file': 'Abc.jpg', 'delay': 50}, {'file': 'abc.jpg', 'delay': 75}]),
    _ugoira(frames=[{'file': 'CON.jpg', 'delay': 50}]),
    _ugoira(frames=[{'file': 'Nul.png', 'delay': 50}]),
    _ugoira(frames=[{'file': 'COM1.jpg', 'delay': 50}]),
    _ugoira(frames=[{'file': '000000.jpg', 'delay': 0}]),
    _ugoira(frames=[{'file': '000000.jpg', 'delay': True}]),
    _ugoira(frames=[{'file': '000000.jpg', 'delay': 9999999}]),
    _ugoira(frames=[{'file': f'{n:06d}.jpg', 'delay': 60000} for n in range(181)]),
    None,
])
def test_ugoira_rejects_url_traversal_bombs_and_duplicate_frames(body):
    with pytest.raises(MediaPayloadRejected):
        parse_ugoira_manifest(body, work_id='123')
