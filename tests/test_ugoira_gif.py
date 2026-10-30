"""Small offline GIF budget/timing regressions; no external codecs."""
from io import BytesIO
from dataclasses import replace
import traceback
import zipfile

from PIL import Image
import pytest

from pixivvault_nicegui import ugoira_gif as subject
from pixivvault_nicegui.job_service import JobCancelled
from pixivvault_nicegui.media_parity_policy import parse_ugoira_manifest
from pixivvault_nicegui.ugoira_archive import inspect_ugoira_archive


def _fixture(delays=(50, 180), colors=('red', 'blue'), sizes=None):
    sizes = sizes or [(4, 3)] * len(delays)
    output = BytesIO()
    rows, encoded = [], []
    for index, delay in enumerate(delays):
        image_bytes = BytesIO()
        with Image.new('RGB', sizes[index], colors[index % len(colors)]) as image:
            image.save(image_bytes, format='PNG')
        name = f'{index:06}.png'
        encoded.append((name, image_bytes.getvalue()))
        rows.append({'file': name, 'delay': delay})
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
        # Playback must follow the manifest, never physical ZIP entry order.
        for name, data in reversed(encoded):
            archive.writestr(name, data)
    manifest = parse_ugoira_manifest({'originalSrc': 'https://i.pximg.net/img-zip-ugoira/offline/123_ugoira.zip',
                                      'frames': rows}, work_id='123')
    return output.getvalue(), manifest


def test_gif_preserves_order_variable_timing_loop_and_disposal():
    raw, manifest = _fixture()
    result = subject.render_ugoira_gif(raw, manifest)
    assert result.frame_count == 2
    assert result.encoded_delays_ms == (50, 180)
    assert result.source_duration_ms == 230
    assert result.data not in repr(result).encode()
    with Image.open(BytesIO(result.data), formats=('GIF',)) as image:
        assert image.n_frames == 2 and image.info['loop'] == 0
        for index, (color, delay) in enumerate([((255, 0, 0), 50), ((0, 0, 255), 180)]):
            image.seek(index)
            assert image.convert('RGB').getpixel((0, 0)) == color
            assert image.info['duration'] == delay
            assert image.disposal_method == 2


def test_gif_does_not_merge_identical_frames_or_overflow_their_combined_delay():
    raw, manifest = _fixture(delays=(60000, 60000), colors=('red',))
    result = subject.render_ugoira_gif(raw, manifest)
    with Image.open(BytesIO(result.data), formats=('GIF',)) as image:
        assert image.n_frames == 2
        assert result.encoded_delays_ms == (60000, 60000)


def test_gif_records_original_pillow_centisecond_floor_without_claiming_exact_ms():
    raw, manifest = _fixture(delays=(1, 9, 17, 28))
    result = subject.render_ugoira_gif(raw, manifest)
    assert result.source_duration_ms == 55
    assert result.encoded_delays_ms == (0, 0, 10, 20)
    with Image.open(BytesIO(result.data), formats=('GIF',)) as image:
        assert image.n_frames == 4
        for index, delay in enumerate(result.encoded_delays_ms):
            image.seek(index)
            assert image.info.get('duration', 0) == delay


def test_mixed_frame_dimensions_use_bounded_maximum_canvas_without_clipping():
    raw, manifest = _fixture(sizes=[(3, 2), (5, 4)])
    result = subject.render_ugoira_gif(raw, manifest)
    assert (result.width, result.height) == (5, 4)
    with Image.open(BytesIO(result.data), formats=('GIF',)) as image:
        image.seek(1)
        assert image.convert('RGB').getpixel((4, 3)) == (0, 0, 255)


def test_output_byte_cap_rejects_small_fixture_before_growing_buffer(monkeypatch):
    raw, manifest = _fixture()
    monkeypatch.setattr(subject, 'MAX_GIF_BYTES', 10)
    with pytest.raises(subject.UgoiraGifError):
        subject.render_ugoira_gif(raw, manifest)


def test_deadline_is_checked_before_expensive_inspection(monkeypatch):
    raw, manifest = _fixture()
    moments = iter((0.0, subject.MAX_GIF_SECONDS + 1))
    monkeypatch.setattr(subject.time, 'monotonic', lambda: next(moments))
    def forbidden(*args):
        pytest.fail('deadline already exceeded before inspection')
    monkeypatch.setattr(subject, 'inspect_ugoira_archive', forbidden)
    with pytest.raises(subject.UgoiraGifError):
        subject.render_ugoira_gif(raw, manifest)


@pytest.mark.parametrize('dimensions', [(65536, 1), (subject.MAX_FRAME_PIXELS, 2)])
def test_canvas_and_format_bounds_are_checked_without_allocating_large_images(monkeypatch, dimensions):
    raw, manifest = _fixture()
    inspection = inspect_ugoira_archive(raw, manifest)
    large = replace(inspection.frames[0], width=dimensions[0], height=dimensions[1])
    inspection = replace(inspection, frames=(large,))
    monkeypatch.setattr(subject, 'inspect_ugoira_archive', lambda *args: inspection)
    def forbidden(*args, **kwargs):
        pytest.fail('oversized GIF canvas reached image allocation')
    monkeypatch.setattr(subject.Image, 'new', forbidden)
    with pytest.raises(subject.UgoiraGifError):
        subject.render_ugoira_gif(raw, manifest)


def test_cancellation_is_not_masked_as_conversion_error():
    raw, manifest = _fixture()
    def stop():
        raise JobCancelled('synthetic stop')
    with pytest.raises(JobCancelled):
        subject.render_ugoira_gif(raw, manifest, checkpoint=stop)


def test_codec_errors_do_not_echo_secrets(monkeypatch):
    raw, manifest = _fixture()
    def fail(*args, **kwargs):
        raise OSError('synthetic-private-cookie-and-path')
    monkeypatch.setattr(subject.GifImagePlugin, 'getdata', fail)
    with pytest.raises(subject.UgoiraGifError) as caught:
        subject.render_ugoira_gif(raw, manifest)
    assert str(caught.value) == 'ugoira GIF conversion failed'
    assert 'synthetic-private' not in ''.join(traceback.format_exception(caught.value))


def test_corrupt_or_truncated_gif_is_not_accepted_as_a_completion():
    raw, manifest = _fixture()
    rendered = subject.render_ugoira_gif(raw, manifest)
    inspection = inspect_ugoira_archive(raw, manifest)
    with pytest.raises(subject.UgoiraGifError):
        subject._verify_gif(rendered.data[:-1], inspection, lambda: None, float('inf'))


def _rgba_fixture(rows):
    archive_bytes = BytesIO()
    manifest_rows = []
    with zipfile.ZipFile(archive_bytes, 'w', compression=zipfile.ZIP_STORED) as archive:
        for index, (size, pixels) in enumerate(rows):
            frame_bytes = BytesIO()
            with Image.new('RGBA', size) as image:
                image.putdata(pixels)
                image.save(frame_bytes, format='PNG')
            name = f'{index:06}.png'
            archive.writestr(name, frame_bytes.getvalue())
            manifest_rows.append({'file': name, 'delay': 50})
    manifest = parse_ugoira_manifest({
        'originalSrc': 'https://i.pximg.net/img-zip-ugoira/offline/123_ugoira.zip',
        'frames': manifest_rows,
    }, work_id='123')
    return archive_bytes.getvalue(), manifest


@pytest.mark.parametrize('reverse', [False, True])
def test_opaque_transparent_transition_clears_previous_canvas_with_local_palettes(reverse):
    red = (255, 0, 0, 255)
    blue = (0, 0, 255, 255)
    transparent = (0, 255, 0, 0)  # Hidden RGB must never remain visible.
    rows = [((2, 1), [red, red]), ((2, 1), [transparent, blue])]
    if reverse:
        rows.reverse()
    raw, manifest = _rgba_fixture(rows)
    rendered = subject.render_ugoira_gif(raw, manifest)
    expected = [[red, red], [(0, 0, 0, 0), blue]]
    if reverse:
        expected.reverse()
    with Image.open(BytesIO(rendered.data), formats=('GIF',)) as image:
        assert image.info['background'] == image.info['transparency'] == 255
        assert image.n_frames == 2
        for index, pixels in enumerate(expected):
            image.seek(index)
            with image.convert('RGBA') as rgba:
                values = list(rgba.getdata())
                assert [pixel[3] for pixel in values] == [pixel[3] for pixel in pixels]
                for actual, wanted in zip(values, pixels):
                    if wanted[3]:
                        assert actual == wanted
            assert image.disposal_method == 2


def test_binary_alpha_policy_preserves_nonzero_alpha_as_opaque_rgb():
    raw, manifest = _rgba_fixture([((3, 1), [
        (255, 0, 0, 0), (0, 255, 0, 128), (0, 0, 255, 255),
    ])])
    rendered = subject.render_ugoira_gif(raw, manifest)
    with Image.open(BytesIO(rendered.data), formats=('GIF',)) as image:
        with image.convert('RGBA') as rgba:
            assert rgba.getpixel((0, 0))[3] == 0
            assert rgba.getpixel((1, 0)) == (0, 255, 0, 255)
            assert rgba.getpixel((2, 0)) == (0, 0, 255, 255)


def test_smaller_later_frame_has_transparent_padding_instead_of_previous_pixels():
    raw, manifest = _rgba_fixture([
        ((2, 2), [(255, 0, 0, 255)] * 4),
        ((1, 1), [(0, 0, 255, 255)]),
    ])
    rendered = subject.render_ugoira_gif(raw, manifest)
    with Image.open(BytesIO(rendered.data), formats=('GIF',)) as image:
        image.seek(1)
        with image.convert('RGBA') as rgba:
            assert rgba.size == (2, 2)
            assert rgba.getpixel((0, 0)) == (0, 0, 255, 255)
            assert all(rgba.getpixel(point)[3] == 0 for point in [(1, 0), (0, 1), (1, 1)])