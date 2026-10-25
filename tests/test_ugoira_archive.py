"""Synthetic malicious ZIP matrix; no archive is extracted or persisted."""

from io import BytesIO
import stat
import struct
import traceback
import zipfile
import zlib

from PIL import Image
import pytest

from pixivvault_nicegui.media_parity_policy import parse_ugoira_manifest, MediaPayloadRejected
from pixivvault_nicegui.media_parity_policy import UgoiraManifest, UgoiraFrame
from pixivvault_nicegui import ugoira_archive as subject
from pixivvault_nicegui.ugoira_archive import (
    MAX_ARCHIVE_BYTES, MAX_SINGLE_FRAME_BYTES, MAX_TOTAL_FRAME_BYTES,
    MAX_DECOMPRESSION_RATIO, inspect_ugoira_archive,
)


def _png(width=4, height=3):
    dest = BytesIO()
    Image.new('RGB', (width, height), (33, 66, 99)).save(dest, format='PNG')
    return dest.getvalue()


def _manifest(rows=None):
    return parse_ugoira_manifest({
        'originalSrc': 'https://i.pximg.net/img-zip-ugoira/synthetic/123_ugoira.zip',
        'frames': rows or [{'file': '000001.png', 'delay': 50},
                           {'file': '000000.png', 'delay': 180}],
    }, work_id='123')


def _zip(files, *, compression=zipfile.ZIP_DEFLATED, symlink=None):
    output = BytesIO()
    with zipfile.ZipFile(output, 'w', compression=compression) as writer:
        for filename, data in files:
            if symlink == filename:
                entry = zipfile.ZipInfo(filename)
                entry.create_system = 3
                entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                writer.writestr(entry, data)
            else:
                writer.writestr(filename, data)
    return output.getvalue()


def test_archive_inspection_preserves_manifest_frame_order_and_delays():
    manifest = _manifest()
    raw = _zip([('000000.png', _png()), ('000001.png', _png(5, 4))])
    result = inspect_ugoira_archive(raw, manifest)
    assert result.duration_ms == 230
    assert result.work_id == '123'
    assert [(frame.filename, frame.width, frame.height, frame.delay_ms)
            for frame in result.frames] == [
                ('000001.png', 5, 4, 50), ('000000.png', 4, 3, 180)]
    assert result.total_uncompressed_bytes == 2 * len(_png()) - len(_png()) + len(_png(5, 4))
    assert '000001' not in repr(result)


@pytest.mark.parametrize('zip_bytes', [
    b'', b'not a ZIP',
    _zip([('000001.png', _png())]),   # Missing second frame.
    _zip([('000000.png', _png()), ('000001.png', _png()), ('unexpected.png', _png())]),
    _zip([('000000.png', _png()), ('000000.png', _png()), ('000001.png', _png())]),
    _zip([('../000000.png', _png()), ('000001.png', _png())]),
    _zip([('000000.png', _png()), ('000001.png', _png())], symlink='000000.png'),
    _zip([('000000.png', b'not png'), ('000001.png', _png())]),
    _zip([('000000.png', _png()), ('000001.png', _png())], compression=zipfile.ZIP_BZIP2),
])
def test_archive_rejects_nonmatching_unsafe_or_corrupt_zip(zip_bytes):
    with pytest.raises(MediaPayloadRejected, match='unsupported or invalid Pixiv media metadata'):
        inspect_ugoira_archive(zip_bytes, _manifest())


def test_archive_rejects_foreign_frames_that_dont_match_extension():
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(_zip([('000000.png', b'not an image'),
                                     ('000001.png', _png())]), _manifest())


def test_archive_requires_bytes_and_exact_manifest_type():
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(bytearray(_zip([('000000.png', _png()), ('000001.png', _png())])),
                               _manifest())
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(_zip([('000000.png', _png()), ('000001.png', _png())]), {})
    trusted = _manifest()
    forged = UgoiraManifest(trusted.work_id, trusted.archive_url,
                            (UgoiraFrame('../path.png', 100),), 100)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(_zip([('../path.png', _png())]), forged)
    forged_duration = UgoiraManifest(trusted.work_id, trusted.archive_url,
                                     trusted.frames, 999_999)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(_zip([('000000.png', _png()), ('000001.png', _png())]), forged_duration)


def test_archive_limit_constants_are_bounded():
    assert MAX_ARCHIVE_BYTES <= 64 * 1024 * 1024
    assert MAX_SINGLE_FRAME_BYTES <= 8 * 1024 * 1024
    assert MAX_TOTAL_FRAME_BYTES <= 128 * 1024 * 1024
    assert MAX_DECOMPRESSION_RATIO <= 64


def _one_frame(data=None, filename='000000.png', *, compression=zipfile.ZIP_STORED):
    return (_zip([(filename, _png() if data is None else data)], compression=compression),
            _manifest([{'file': filename, 'delay': 50}]))


def _eocd(raw, **changes):
    """Mutate fixed fields only; never allocate the declared sizes/counts."""
    result = bytearray(raw)
    position = raw.rfind(b'PK\x05\x06')
    offsets = {'disk': (4, 'H'), 'directory_disk': (6, 'H'),
               'disk_count': (8, 'H'), 'count': (10, 'H'),
               'size': (12, 'I'), 'offset': (16, 'I'), 'comment': (20, 'H')}
    for name, value in changes.items():
        offset, kind = offsets[name]
        struct.pack_into('<' + kind, result, position + offset, value)
    return bytes(result)


def _central(raw, *, index=0, **changes):
    result = bytearray(raw)
    position = struct.unpack_from('<I', raw, raw.rfind(b'PK\x05\x06') + 16)[0]
    for _ in range(index):
        lengths = struct.unpack_from('<3H', raw, position + 28)
        position += 46 + sum(lengths)
    offsets = {'flags': (8, 'H'), 'crc': (16, 'I'), 'compressed': (20, 'I'),
               'size': (24, 'I'), 'disk': (34, 'H'), 'attributes': (38, 'I'),
               'local_offset': (42, 'I')}
    for name, value in changes.items():
        offset, kind = offsets[name]
        struct.pack_into('<' + kind, result, position + offset, value)
    return bytes(result)


def _forbid_zipfile(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('ZipFile constructed before directory preflight rejected input')
    monkeypatch.setattr(subject.zipfile, 'ZipFile', forbidden)


@pytest.mark.parametrize('changes', [
    {'count': subject.MAX_UGOIRA_FRAMES + 1, 'disk_count': subject.MAX_UGOIRA_FRAMES + 1},
    {'count': 0xFFFF, 'disk_count': 0xFFFF},
    {'size': subject.MAX_CENTRAL_DIRECTORY_BYTES + 1},
    {'size': 0xFFFFFFFF}, {'offset': 0xFFFFFFFF},
    {'disk': 1}, {'directory_disk': 1}, {'disk_count': 0},
    {'offset': 0}, {'size': 45}, {'comment': 1},
])
def test_directory_metadata_rejected_before_zipfile(monkeypatch, changes):
    raw, manifest = _one_frame()
    raw = _eocd(raw, **changes)
    _forbid_zipfile(monkeypatch)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, manifest)


def test_actual_directory_count_cannot_hide_behind_small_eocd_count(monkeypatch):
    raw = _zip([('000000.png', _png()), ('000001.png', _png()), ('extra.png', b'x')])
    raw = _eocd(raw, count=2, disk_count=2)
    _forbid_zipfile(monkeypatch)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, _manifest())


def test_declared_directory_count_requires_complete_records(monkeypatch):
    raw, _ = _one_frame()
    raw = _eocd(raw, count=2, disk_count=2)
    _forbid_zipfile(monkeypatch)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, _manifest())


def test_directory_size_limit_boundary_before_zipfile(monkeypatch):
    raw, manifest = _one_frame()
    size = struct.unpack_from('<I', raw, raw.rfind(b'PK\x05\x06') + 12)[0]
    monkeypatch.setattr(subject, 'MAX_CENTRAL_DIRECTORY_BYTES', size)
    assert len(inspect_ugoira_archive(raw, manifest).frames) == 1
    monkeypatch.setattr(subject, 'MAX_CENTRAL_DIRECTORY_BYTES', size - 1)
    _forbid_zipfile(monkeypatch)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, manifest)


@pytest.mark.parametrize('field,value', [
    ('compressed', 0xFFFFFFFF), ('size', 0xFFFFFFFF), ('local_offset', 0xFFFFFFFF), ('disk', 1),
])
def test_directory_missing_zip64_extra_and_split_entry_rejected_before_zipfile(monkeypatch, field, value):
    raw, manifest = _one_frame()
    raw = _central(raw, **{field: value})
    _forbid_zipfile(monkeypatch)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, manifest)


def test_zip64_locator_without_valid_end_record_rejected_before_zipfile(monkeypatch):
    raw, manifest = _one_frame()
    end = raw.rfind(b'PK\x05\x06')
    locator = struct.pack('<4sIQI', b'PK\x06\x07', 0, 0, 1)
    # Include the locator in the declared directory so even the offset/size
    # equation passes; the locator must still reference a real ZIP64 end record.
    size = struct.unpack_from('<I', raw, end + 12)[0]
    raw = _eocd(raw[:end] + locator + raw[end:], size=size + len(locator))
    _forbid_zipfile(monkeypatch)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, manifest)


@pytest.mark.parametrize('extra', [b'\x01\x00\x01\x00x', b'\x02\x00\x08\x00x', b'x'])
def test_inconsistent_zip64_or_malformed_extra_rejected_before_zipfile(monkeypatch, extra):
    entry = zipfile.ZipInfo('000000.png')
    entry.extra = extra
    raw = _zip([(entry, _png())])
    manifest = _manifest([{'file': '000000.png', 'delay': 50}])
    _forbid_zipfile(monkeypatch)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, manifest)


def test_regular_zip_comment_accepted_and_trailing_bytes_rejected(monkeypatch):
    raw, manifest = _one_frame()
    raw = _eocd(raw, comment=7) + b'comment'
    assert len(inspect_ugoira_archive(raw, manifest).frames) == 1
    _forbid_zipfile(monkeypatch)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw + b'trailing', manifest)


def test_archive_byte_limit_boundary_before_zipfile(monkeypatch):
    raw, manifest = _one_frame()
    monkeypatch.setattr(subject, 'MAX_ARCHIVE_BYTES', len(raw))
    assert len(inspect_ugoira_archive(raw, manifest).frames) == 1
    monkeypatch.setattr(subject, 'MAX_ARCHIVE_BYTES', len(raw) - 1)
    _forbid_zipfile(monkeypatch)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, manifest)


@pytest.mark.parametrize('field,value', [
    ('size', MAX_SINGLE_FRAME_BYTES + 1), ('compressed', MAX_SINGLE_FRAME_BYTES + 1),
    ('size', 0), ('compressed', 0),
    ('size', MAX_DECOMPRESSION_RATIO + 1),
])
def test_declared_frame_size_and_ratio_limits_before_read(monkeypatch, field, value):
    raw, manifest = _one_frame()
    if field == 'size':
        compressed = MAX_SINGLE_FRAME_BYTES if value > MAX_SINGLE_FRAME_BYTES else 1
        raw = _central(raw, compressed=compressed, size=value)
    else:
        raw = _central(raw, **{field: value})
    def forbidden(*args, **kwargs):
        pytest.fail('frame read before metadata limits rejected input')
    monkeypatch.setattr(subject.zipfile.ZipFile, 'open', forbidden)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, manifest)


def test_single_frame_byte_limit_boundary(monkeypatch):
    content = _png()
    raw, manifest = _one_frame(content)
    monkeypatch.setattr(subject, 'MAX_SINGLE_FRAME_BYTES', len(content))
    assert inspect_ugoira_archive(raw, manifest).total_uncompressed_bytes == len(content)
    monkeypatch.setattr(subject, 'MAX_SINGLE_FRAME_BYTES', len(content) - 1)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, manifest)


def test_total_frame_byte_limit_boundary(monkeypatch):
    raw = _zip([('000000.png', _png()), ('000001.png', _png())], compression=zipfile.ZIP_STORED)
    total = 2 * len(_png())
    monkeypatch.setattr(subject, 'MAX_TOTAL_FRAME_BYTES', total)
    assert inspect_ugoira_archive(raw, _manifest()).total_uncompressed_bytes == total
    monkeypatch.setattr(subject, 'MAX_TOTAL_FRAME_BYTES', total - 1)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, _manifest())


def test_real_total_byte_limit_rejects_small_zip_with_large_declarations(monkeypatch):
    count = MAX_TOTAL_FRAME_BYTES // MAX_SINGLE_FRAME_BYTES + 1
    rows = [{'file': f'{i:06}.png', 'delay': 1} for i in range(count)]
    raw = _zip([(row['file'], b'x') for row in rows], compression=zipfile.ZIP_STORED)
    for index in range(count):
        raw = _central(raw, index=index, size=MAX_SINGLE_FRAME_BYTES,
                       compressed=MAX_SINGLE_FRAME_BYTES // MAX_DECOMPRESSION_RATIO)
    def forbidden(*args, **kwargs):
        pytest.fail('frame read before aggregate byte limit rejected input')
    monkeypatch.setattr(subject.zipfile.ZipFile, 'open', forbidden)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, _manifest(rows))


def test_actual_deflate_ratio_limit_rejects_small_compressed_bomb(monkeypatch):
    raw, manifest = _one_frame(b'\x00' * 16384, compression=zipfile.ZIP_DEFLATED)
    def forbidden(*args, **kwargs):
        pytest.fail('compressed bomb read before ratio limit rejected input')
    monkeypatch.setattr(subject.zipfile.ZipFile, 'open', forbidden)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, manifest)


def test_ratio_limit_boundary_uses_compressed_and_uncompressed_sizes(monkeypatch):
    raw, manifest = _one_frame()
    monkeypatch.setattr(subject, 'MAX_DECOMPRESSION_RATIO', 1)
    assert len(inspect_ugoira_archive(raw, manifest).frames) == 1
    monkeypatch.setattr(subject, 'MAX_DECOMPRESSION_RATIO', 0)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, manifest)


def test_frame_pixel_limit_before_decode(monkeypatch):
    content = bytearray(_png())
    struct.pack_into('>II', content, 16, subject.MAX_FRAME_PIXELS + 1, 1)
    struct.pack_into('>I', content, 29, zlib.crc32(content[12:29]))
    raw, manifest = _one_frame(bytes(content))
    def forbidden(*args, **kwargs):
        pytest.fail('oversized image decoded before pixel limit check')
    monkeypatch.setattr(Image.Image, 'load', forbidden)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, manifest)


def test_frame_and_aggregate_pixel_limit_boundaries(monkeypatch):
    raw = _zip([('000000.png', _png()), ('000001.png', _png())], compression=zipfile.ZIP_STORED)
    monkeypatch.setattr(subject, 'MAX_FRAME_PIXELS', 12)
    monkeypatch.setattr(subject, 'MAX_TOTAL_DECODED_PIXELS', 24)
    assert len(inspect_ugoira_archive(raw, _manifest()).frames) == 2
    monkeypatch.setattr(subject, 'MAX_FRAME_PIXELS', 11)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, _manifest())
    monkeypatch.setattr(subject, 'MAX_FRAME_PIXELS', 12)
    monkeypatch.setattr(subject, 'MAX_TOTAL_DECODED_PIXELS', 23)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, _manifest())


def test_real_aggregate_pixel_limit_before_next_decode_without_large_images(monkeypatch):
    # Exercise the production budget with a header-only decoder stand-in.
    # No large bitmap is created or decoded by this test.
    assert subject.MAX_TOTAL_DECODED_PIXELS == 32 * subject.MAX_FRAME_PIXELS
    rows = [{'file': f'{i:06}.png', 'delay': 1} for i in range(33)]
    raw = _zip([(row['file'], _png()) for row in rows], compression=zipfile.ZIP_STORED)
    loaded = []
    class HeaderOnlyImage:
        format = 'PNG'
        width = subject.MAX_FRAME_PIXELS
        height = 1
        n_frames = 1

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def load(self):
            loaded.append(1)

    monkeypatch.setattr(subject.Image, 'open', lambda *args, **kwargs: HeaderOnlyImage())
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, _manifest(rows))
    assert len(loaded) == 32


def test_direct_manifest_frame_count_cannot_exceed_real_limit(monkeypatch):
    raw, manifest = _one_frame()
    forged = UgoiraManifest(manifest.work_id, manifest.archive_url,
                            (manifest.frames[0],) * (subject.MAX_UGOIRA_FRAMES + 1), 50)
    _forbid_zipfile(monkeypatch)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, forged)


@pytest.mark.parametrize('damage', ['crc', 'local_name', 'local_signature', 'encrypted', 'directory', 'fifo'])
def test_crc_header_encryption_and_file_type_rejections(damage):
    raw, manifest = _one_frame()
    if damage == 'crc':
        raw = _central(raw, crc=0)
    elif damage == 'encrypted':
        raw = _central(raw, flags=1)
    elif damage == 'directory':
        raw = _central(raw, attributes=(stat.S_IFDIR | 0o755) << 16)
    elif damage == 'fifo':
        raw = _central(raw, attributes=(stat.S_IFIFO | 0o600) << 16)
    else:
        result = bytearray(raw)
        result[30 if damage == 'local_name' else 0] ^= 1
        raw = bytes(result)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, manifest)


@pytest.mark.parametrize('extension,format_name', [
    ('png', 'PNG'), ('jpg', 'JPEG'), ('jpeg', 'JPEG'), ('webp', 'WEBP'),
])
def test_decoder_is_restricted_to_extension(monkeypatch, extension, format_name):
    output = BytesIO()
    Image.new('RGB', (4, 3), (33, 66, 99)).save(output, format=format_name)
    raw, manifest = _one_frame(output.getvalue(), filename='000000.' + extension)
    original_open = Image.open
    calls = []
    def restricted_open(*args, **kwargs):
        calls.append(kwargs.get('formats'))
        return original_open(*args, **kwargs)
    monkeypatch.setattr(subject.Image, 'open', restricted_open)
    assert len(inspect_ugoira_archive(raw, manifest).frames) == 1
    assert calls == [(format_name,)]


@pytest.mark.parametrize('format_name', ['JPEG', 'BMP', 'GIF'])
def test_real_image_with_wrong_extension_is_rejected(format_name):
    output = BytesIO()
    Image.new('RGB', (4, 3), (33, 66, 99)).save(output, format=format_name)
    raw, manifest = _one_frame(output.getvalue())
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, manifest)


def test_animated_png_rejected():
    output = BytesIO()
    with Image.new('RGB', (4, 3), 'red') as first, Image.new('RGB', (4, 3), 'blue') as second:
        first.save(output, format='PNG', save_all=True, append_images=[second], duration=50)
    raw, manifest = _one_frame(output.getvalue())
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, manifest)


def test_truncated_jpeg_rejected_during_full_decode():
    output = BytesIO()
    pixels = bytes((index * 37 + index // 96 * 13) % 256 for index in range(32 * 32 * 3))
    with Image.frombytes('RGB', (32, 32), pixels) as image:
        image.save(output, format='JPEG', quality=90)
    complete = output.getvalue()
    raw, manifest = _one_frame(complete, filename='000000.jpg')
    assert [(frame.width, frame.height) for frame in inspect_ugoira_archive(raw, manifest).frames] == [(32, 32)]

    # Keep the entire SOS header but only one byte of entropy-coded image data.
    # Removing EOI alone can leave a fully decodable JPEG, which is permitted.
    start_of_scan = complete.index(b'\xff\xda')
    scan_header_size = int.from_bytes(complete[start_of_scan + 2:start_of_scan + 4], 'big')
    scan_data_start = start_of_scan + 2 + scan_header_size
    assert scan_data_start + 1 < len(complete) - 2
    truncated = complete[:scan_data_start + 1]
    with Image.open(BytesIO(truncated), formats=('JPEG',)) as image:
        assert image.format == 'JPEG'
        assert image.size == (32, 32)
        with pytest.raises(OSError, match='truncated'):
            image.load()

    raw, manifest = _one_frame(truncated, filename='000000.jpg')
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, manifest)


@pytest.mark.parametrize('failure_site', ['zip', 'image'])
def test_internal_exceptions_do_not_leak_secrets(monkeypatch, failure_site):
    raw, manifest = _one_frame()
    secret = 'synthetic-cookie=secret; https://private.invalid/private-frame.png'
    def fail(*args, **kwargs):
        raise RuntimeError(secret)
    if failure_site == 'zip':
        monkeypatch.setattr(subject.zipfile.ZipFile, 'open', fail)
    else:
        monkeypatch.setattr(subject.Image, 'open', fail)
    with pytest.raises(MediaPayloadRejected) as caught:
        inspect_ugoira_archive(raw, manifest)
    assert str(caught.value) == 'unsupported or invalid Pixiv media metadata'
    assert secret not in repr(caught.value)
    assert secret not in ''.join(traceback.format_exception(caught.value))
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True


def _small_zip64(files=None, *, entry_fields=('size', 'compressed', 'local_offset'),
                 end_records=True, classic_sentinels=True, local_zip64=False,
                 descriptor=False, compression=zipfile.ZIP_STORED,
                 central_extra=None, local_extra=None, entry_values=None,
                 extensible_data=b'', comment=b''):
    """Build tiny ZIP64 representations without inflating data or global limits."""
    files = [('000000.png', _png())] if files is None else files
    local_parts = []
    central_parts = []
    offset = 0
    for filename, content in files:
        name = filename.encode('ascii')
        if compression == zipfile.ZIP_DEFLATED:
            compressor = zlib.compressobj(wbits=-15)
            compressed = compressor.compress(content) + compressor.flush()
        else:
            assert compression == zipfile.ZIP_STORED
            compressed = content
        crc = zlib.crc32(content)
        flags = 8 if descriptor else 0
        size, compressed_size = len(content), len(compressed)
        local_payload = struct.pack('<HHQQ', 1, 16,
                                    0 if descriptor else size,
                                    0 if descriptor else compressed_size) if local_zip64 else b''
        if local_extra is not None:
            local_payload = local_extra
        local_header = struct.pack('<4s5H3I2H', b'PK\x03\x04', 45, flags, compression, 0, 0,
                                   0 if descriptor else crc,
                                   0xFFFFFFFF if local_zip64 else (0 if descriptor else compressed_size),
                                   0xFFFFFFFF if local_zip64 else (0 if descriptor else size),
                                   len(name), len(local_payload))
        part = local_header + name + local_payload + compressed
        if descriptor:
            descriptor_format = '<4sIQQ' if local_zip64 else '<4sIII'
            part += struct.pack(descriptor_format, b'PK\x07\x08', crc, compressed_size, size)
        local_parts.append(part)
        values = {'size': size, 'compressed': compressed_size, 'local_offset': offset, 'disk': 0}
        if entry_values is not None:
            values.update(entry_values)
        extra_values = b''.join(struct.pack('<I' if field == 'disk' else '<Q', values[field])
                                for field in ('size', 'compressed', 'local_offset', 'disk')
                                if field in entry_fields)
        extra = struct.pack('<HH', 1, len(extra_values)) + extra_values if entry_fields else b''
        if central_extra is not None:
            extra = central_extra
        header = struct.pack('<4s6H3I5H2I', b'PK\x01\x02', 45, 45, flags, compression, 0, 0, crc,
                             0xFFFFFFFF if 'compressed' in entry_fields else values['compressed'],
                             0xFFFFFFFF if 'size' in entry_fields else values['size'],
                             len(name), len(extra), 0,
                             0xFFFF if 'disk' in entry_fields else values['disk'],
                             0, (stat.S_IFREG | 0o600) << 16,
                             0xFFFFFFFF if 'local_offset' in entry_fields else values['local_offset'])
        central_parts.append(header + name + extra)
        offset += len(part)
    body = b''.join(local_parts)
    directory = b''.join(central_parts)
    count = len(files)
    if end_records:
        record_offset = len(body) + len(directory)
        ending = struct.pack('<4sQ2H2I4Q', b'PK\x06\x06', 44 + len(extensible_data), 45, 45,
                             0, 0, count, count, len(directory), len(body)) + extensible_data
        ending += struct.pack('<4sIQI', b'PK\x06\x07', 0, record_offset, 1)
    else:
        ending = b''
    sentinel = end_records and classic_sentinels
    ending += struct.pack('<4s4H2IH', b'PK\x05\x06', 0, 0,
                          0xFFFF if sentinel else count, 0xFFFF if sentinel else count,
                          0xFFFFFFFF if sentinel else len(directory),
                          0xFFFFFFFF if sentinel else len(body), len(comment)) + comment
    return body + directory + ending


def _zip64_record(raw, **changes):
    result = bytearray(raw)
    end = raw.rfind(b'PK\x05\x06')
    position = struct.unpack_from('<Q', raw, end - 12)[0]
    offsets = {'record_size': 4, 'disk': 16, 'directory_disk': 20,
               'disk_count': 24, 'count': 32, 'size': 40, 'offset': 48}
    for name, value in changes.items():
        struct.pack_into('<I' if name in ('disk', 'directory_disk') else '<Q',
                         result, position + offsets[name], value)
    return bytes(result)


def _zip64_locator(raw, **changes):
    result = bytearray(raw)
    position = raw.rfind(b'PK\x05\x06') - 20
    offsets = {'disk': (4, 'I'), 'offset': (8, 'Q'), 'disks': (16, 'I')}
    for name, value in changes.items():
        offset, kind = offsets[name]
        struct.pack_into('<' + kind, result, position + offset, value)
    return bytes(result)


@pytest.mark.parametrize('end_records,classic_sentinels,local_zip64', [
    (True, True, False), (True, False, False), (True, True, True), (True, False, True),
    (False, False, False), (False, False, True),
])
@pytest.mark.parametrize('compression', [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_small_zip64_matches_classic_inspection(end_records, classic_sentinels, local_zip64, compression):
    files = [('000000.png', _png()), ('000001.png', _png(5, 4))]
    raw = _small_zip64(files, end_records=end_records, classic_sentinels=classic_sentinels,
                       local_zip64=local_zip64, compression=compression)
    # Confirm the hand-built fixture is independently readable by stdlib.
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        assert [(name, archive.read(name)) for name, _ in files] == files
    classic = _zip(files, compression=compression)
    assert inspect_ugoira_archive(raw, _manifest()) == inspect_ugoira_archive(classic, _manifest())


@pytest.mark.parametrize('entry_fields', [
    (), ('size',), ('compressed',), ('local_offset',), ('disk',),
    ('size', 'compressed'), ('size', 'compressed', 'local_offset', 'disk'),
])
def test_zip64_selective_central_fields(entry_fields):
    raw = _small_zip64(entry_fields=entry_fields)
    _, manifest = _one_frame()
    assert len(inspect_ugoira_archive(raw, manifest).frames) == 1


def test_zip64_with_extensible_data_and_comment():
    extension = b'synthetic-extension'
    raw = _small_zip64(extensible_data=struct.pack('<HI', 0xFFFF, len(extension)) + extension,
                       comment=b'synthetic-comment')
    _, manifest = _one_frame()
    assert len(inspect_ugoira_archive(raw, manifest).frames) == 1


def test_zip64_locator_does_not_adopt_a_fake_end_record_in_extensible_data():
    """Preflight and stdlib must agree on the locator's real ZIP64 record.

    A fake signature exactly 56 bytes before the locator must never make the
    stdlib use different central-directory sizes/counts from the validated one.
    Some older zipfile builds assume no extensible data; they must fail closed
    rather than accepting the fake record as a second directory.
    """
    fake = struct.pack('<4sQ2H2I4Q', b'PK\x06\x06', 44, 45, 45, 0, 0,
                       0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0)
    raw = _small_zip64(extensible_data=fake)
    _, manifest = _one_frame()
    result = inspect_ugoira_archive(raw, manifest)
    assert len(result.frames) == 1
    with zipfile.ZipFile(BytesIO(raw), 'r') as archive:
        assert archive.namelist() == ['000000.png']


@pytest.mark.parametrize('descriptor', [False, True])
def test_stdlib_forced_local_zip64_with_and_without_descriptor(descriptor):
    class NonSeekable(BytesIO):
        def seek(self, *args):
            raise OSError('synthetic nonseekable output')

    output = NonSeekable() if descriptor else BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as writer:
        with writer.open('000000.png', 'w', force_zip64=True) as stream:
            stream.write(_png())
    raw = output.getvalue()
    assert struct.unpack_from('<II', raw, 18) == (0xFFFFFFFF, 0xFFFFFFFF)
    _, manifest = _one_frame()
    assert len(inspect_ugoira_archive(raw, manifest).frames) == 1


def test_zip64_data_descriptor_with_unknown_local_sizes():
    raw = _small_zip64(local_zip64=True, descriptor=True)
    _, manifest = _one_frame()
    assert len(inspect_ugoira_archive(raw, manifest).frames) == 1


def test_unused_empty_zip64_extra_is_not_a_representation_rejection():
    raw = _small_zip64(entry_fields=(), central_extra=struct.pack('<HH', 1, 0),
                       local_extra=struct.pack('<HH', 1, 0))
    _, manifest = _one_frame()
    assert len(inspect_ugoira_archive(raw, manifest).frames) == 1


@pytest.mark.parametrize('changes', [
    {'count': subject.MAX_UGOIRA_FRAMES + 1, 'disk_count': subject.MAX_UGOIRA_FRAMES + 1},
    {'count': 2**63, 'disk_count': 2**63},
    {'count': 0, 'disk_count': 0}, {'disk_count': 2},
    {'size': subject.MAX_CENTRAL_DIRECTORY_BYTES + 1}, {'size': 2**63},
    {'offset': 2**63}, {'offset': 0}, {'disk': 1}, {'directory_disk': 1},
    {'record_size': 43}, {'record_size': 45}, {'record_size': 2**63},
])
def test_zip64_directory_limits_and_end_record_integrity_before_zipfile(monkeypatch, changes):
    raw = _zip64_record(_small_zip64(), **changes)
    _, manifest = _one_frame()
    _forbid_zipfile(monkeypatch)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, manifest)


@pytest.mark.parametrize('changes', [
    {'disk': 1}, {'disks': 0}, {'disks': 2}, {'offset': 2**63}, {'offset': 0},
])
def test_zip64_locator_ranges_and_disk_count_before_zipfile(monkeypatch, changes):
    raw = _zip64_locator(_small_zip64(), **changes)
    _, manifest = _one_frame()
    _forbid_zipfile(monkeypatch)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, manifest)


@pytest.mark.parametrize('changes', [
    {'count': 2}, {'disk_count': 2}, {'disk': 1}, {'directory_disk': 1}, {'size': 0}, {'offset': 0},
])
def test_zip64_classic_non_sentinel_values_must_agree(monkeypatch, changes):
    raw = _eocd(_small_zip64(), **changes)
    _, manifest = _one_frame()
    _forbid_zipfile(monkeypatch)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, manifest)


def test_zip64_actual_directory_count_cannot_hide_behind_declared_count(monkeypatch):
    raw = _small_zip64([('000000.png', _png()), ('000001.png', _png()), ('extra.png', b'x')])
    raw = _zip64_record(raw, count=2, disk_count=2)
    _forbid_zipfile(monkeypatch)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, _manifest())


def test_zip64_directory_byte_limit_boundary(monkeypatch):
    raw = _small_zip64()
    _, manifest = _one_frame()
    end = raw.rfind(b'PK\x05\x06')
    record = struct.unpack_from('<Q', raw, end - 12)[0]
    size = struct.unpack_from('<Q', raw, record + 40)[0]
    monkeypatch.setattr(subject, 'MAX_CENTRAL_DIRECTORY_BYTES', size)
    assert len(inspect_ugoira_archive(raw, manifest).frames) == 1
    monkeypatch.setattr(subject, 'MAX_CENTRAL_DIRECTORY_BYTES', size - 1)
    _forbid_zipfile(monkeypatch)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, manifest)


@pytest.mark.parametrize('damage', ['missing_locator', 'truncated_locator', 'record_signature',
                                  'duplicate_record', 'duplicate_locator', 'trailing_data'])
def test_zip64_missing_truncated_or_duplicate_end_records(monkeypatch, damage):
    raw = _small_zip64()
    end = raw.rfind(b'PK\x05\x06')
    record = struct.unpack_from('<Q', raw, end - 12)[0]
    if damage == 'missing_locator':
        raw = raw[:end - 20] + raw[end:]
    elif damage == 'truncated_locator':
        raw = raw[:end - 1] + raw[end:]
    elif damage == 'record_signature':
        raw = raw[:record] + b'BAD!' + raw[record + 4:]
    elif damage == 'duplicate_record':
        raw = raw[:end - 20] + raw[record:end - 20] + raw[end - 20:]
    elif damage == 'duplicate_locator':
        raw = raw[:end] + raw[end - 20:end] + raw[end:]
    else:
        raw += b'trailing'
    _, manifest = _one_frame()
    _forbid_zipfile(monkeypatch)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, manifest)


@pytest.mark.parametrize('extra', [
    b'', struct.pack('<HH', 1, 0), struct.pack('<HHQ', 1, 8, 1),
    struct.pack('<HH', 1, 25) + b'x' * 25,
    (struct.pack('<HH', 1, 24) + b'\x00' * 24) * 2,
    b'\x01', struct.pack('<HH', 1, 8) + b'x',
])
def test_zip64_missing_duplicate_or_inconsistent_central_extra_before_zipfile(monkeypatch, extra):
    raw = _small_zip64(central_extra=extra)
    _, manifest = _one_frame()
    _forbid_zipfile(monkeypatch)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, manifest)


@pytest.mark.parametrize('extra', [
    b'', struct.pack('<HH', 1, 0), struct.pack('<HHQ', 1, 8, 1),
    (struct.pack('<HHQQ', 1, 16, 1, 1)) * 2,
    struct.pack('<HHQQ', 1, 16, 0, 0), b'\x01', struct.pack('<HH', 1, 16) + b'x',
])
def test_zip64_local_extra_ranges_count_and_size_consistency_before_zipfile(monkeypatch, extra):
    raw = _small_zip64(local_zip64=True, local_extra=extra)
    _, manifest = _one_frame()
    _forbid_zipfile(monkeypatch)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, manifest)


@pytest.mark.parametrize('values', [
    {'size': MAX_SINGLE_FRAME_BYTES + 1, 'compressed': MAX_SINGLE_FRAME_BYTES},
    {'compressed': MAX_SINGLE_FRAME_BYTES + 1},
    {'size': 0}, {'compressed': 0},
    {'size': MAX_DECOMPRESSION_RATIO + 1, 'compressed': 1},
    {'size': 2**63}, {'compressed': 2**63}, {'local_offset': 2**63}, {'disk': 1},
])
def test_zip64_resolved_entry_values_keep_existing_resource_limits(monkeypatch, values):
    raw = _small_zip64(entry_fields=('size', 'compressed', 'local_offset', 'disk'), entry_values=values)
    _, manifest = _one_frame()
    def forbidden(*args, **kwargs):
        pytest.fail('ZIP64 frame read before resource or range limits rejected input')
    monkeypatch.setattr(subject.zipfile.ZipFile, 'open', forbidden)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, manifest)


def test_zip64_resolved_total_byte_budget_before_frame_read(monkeypatch):
    count = MAX_TOTAL_FRAME_BYTES // MAX_SINGLE_FRAME_BYTES + 1
    rows = [{'file': f'{i:06}.png', 'delay': 1} for i in range(count)]
    raw = _small_zip64([(row['file'], b'x') for row in rows], entry_values={
        'size': MAX_SINGLE_FRAME_BYTES,
        'compressed': MAX_SINGLE_FRAME_BYTES // MAX_DECOMPRESSION_RATIO,
    })
    def forbidden(*args, **kwargs):
        pytest.fail('ZIP64 frame read before aggregate byte limit rejected input')
    monkeypatch.setattr(subject.zipfile.ZipFile, 'open', forbidden)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, _manifest(rows))


@pytest.mark.parametrize('local_offset', [0, 1])
def test_zip64_duplicate_or_overlapping_local_headers_rejected_before_extra_scan(monkeypatch, local_offset):
    raw = bytearray(_small_zip64([('000000.png', _png()), ('000001.png', _png())]))
    end = raw.rfind(b'PK\x05\x06')
    record = struct.unpack_from('<Q', raw, end - 12)[0]
    directory = struct.unpack_from('<Q', raw, record + 48)[0]
    second = directory + 46 + sum(struct.unpack_from('<3H', raw, directory + 28))
    name_size = struct.unpack_from('<H', raw, second + 28)[0]
    # The first offset remains zero; only the second offset becomes 0 or 1.
    struct.pack_into('<Q', raw, second + 46 + name_size + 4 + 16, local_offset)
    raw = bytes(raw)
    _forbid_zipfile(monkeypatch)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(raw, _manifest())


def test_zip64_crc_failure_is_still_rejected_before_image_decode(monkeypatch):
    raw = bytearray(_small_zip64(local_zip64=True))
    name_size, extra_size = struct.unpack_from('<HH', raw, 26)
    raw[30 + name_size + extra_size] ^= 1
    _, manifest = _one_frame()
    def forbidden(*args, **kwargs):
        pytest.fail('image opened before ZIP64 frame CRC failure was rejected')
    monkeypatch.setattr(subject.Image, 'open', forbidden)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(bytes(raw), manifest)


def _ordinary_local_boundary_fixture(*, empty_zip64_extra, frame_count, compression, descriptor=False):
    files = [(f'{index:06}.png', _png()) for index in range(frame_count)]
    extra = struct.pack('<HH', 1, 0) if empty_zip64_extra else b''
    raw = _small_zip64(files, entry_fields=(), end_records=False,
                       local_extra=extra, central_extra=extra,
                       compression=compression, descriptor=descriptor)
    directory = struct.unpack_from('<I', raw, raw.rfind(b'PK\x05\x06') + 16)[0]
    name_size, extra_size = struct.unpack_from('<HH', raw, 26)
    data_start = 30 + name_size + extra_size
    compressed_size = struct.unpack_from('<I', raw, directory + 20)[0]
    if frame_count == 2:
        second = directory + 46 + sum(struct.unpack_from('<3H', raw, directory + 28))
        boundary = struct.unpack_from('<I', raw, second + 42)[0]
    else:
        boundary = directory
    manifest = _manifest([{'file': name, 'delay': 50} for name, _ in files])
    return raw, manifest, data_start, compressed_size, boundary


@pytest.mark.parametrize('empty_zip64_extra', [False, True])
@pytest.mark.parametrize('frame_count', [1, 2])
@pytest.mark.parametrize('compression', [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
@pytest.mark.parametrize('descriptor', [False, True])
def test_ordinary_local_data_within_or_exactly_at_next_boundary_is_accepted(
        empty_zip64_extra, frame_count, compression, descriptor):
    raw, manifest, data_start, compressed_size, boundary = _ordinary_local_boundary_fixture(
        empty_zip64_extra=empty_zip64_extra, frame_count=frame_count,
        compression=compression, descriptor=descriptor)
    if descriptor:
        assert data_start + compressed_size < boundary
    else:
        assert data_start + compressed_size == boundary
    assert len(inspect_ugoira_archive(raw, manifest).frames) == frame_count


@pytest.mark.parametrize('empty_zip64_extra', [False, True])
@pytest.mark.parametrize('frame_count', [1, 2])
@pytest.mark.parametrize('compression', [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_ordinary_local_data_one_byte_past_next_boundary_is_rejected_before_zipfile(
        monkeypatch, empty_zip64_extra, frame_count, compression):
    raw, manifest, data_start, _, boundary = _ordinary_local_boundary_fixture(
        empty_zip64_extra=empty_zip64_extra, frame_count=frame_count, compression=compression)
    oversized = boundary - data_start + 1
    damaged = bytearray(_central(raw, compressed=oversized))
    # Match local and central compressed sizes to isolate the byte-range gate.
    struct.pack_into('<I', damaged, 18, oversized)
    assert data_start + oversized == boundary + 1
    _forbid_zipfile(monkeypatch)
    with pytest.raises(MediaPayloadRejected):
        inspect_ugoira_archive(bytes(damaged), manifest)
