"""Read-only, memory-bounded verification of Pixiv ugoira ZIP frames.

This module never extracts archive paths, writes output, opens a network
connection, or logs frame names. Only a validated frame manifest is accepted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
import hashlib
import stat
import struct
import zipfile

from PIL import Image

from .media_parity_policy import (
    UgoiraManifest, UgoiraFrame, MediaPayloadRejected,
    MAX_UGOIRA_FRAMES, parse_ugoira_manifest,
)


MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_CENTRAL_DIRECTORY_BYTES = 2 * 1024 * 1024
MAX_SINGLE_FRAME_BYTES = 8 * 1024 * 1024
MAX_TOTAL_FRAME_BYTES = 128 * 1024 * 1024
MAX_FRAME_PIXELS = 16_000_000
MAX_DECOMPRESSION_RATIO = 64
MAX_TOTAL_DECODED_PIXELS = 512_000_000
SUPPORTED_TYPES = {'.png': 'PNG', '.jpg': 'JPEG', '.jpeg': 'JPEG', '.webp': 'WEBP'}


def _zip64_extra(raw_zip: bytes, start: int, end: int) -> tuple[int, int] | None:
    """Validate bounded TLVs; return the sole ZIP64 payload's offset and size."""
    payload = None
    while start < end:
        if start + 4 > end:
            raise MediaPayloadRejected()
        kind, size = struct.unpack_from('<HH', raw_zip, start)
        start += 4
        if start + size > end:
            raise MediaPayloadRejected()
        if kind == 0x0001:
            if payload is not None:
                raise MediaPayloadRejected()
            payload = (start, size)
        start += size
    return payload


def _check_local_zip64(raw_zip: bytes, offset: int, boundary: int,
                       sizes: tuple[int, int], flags: int) -> None:
    """Check ZIP64 local sizes, including unknown sizes with a data descriptor."""
    if offset + 30 > boundary:
        raise MediaPayloadRejected()
    header = struct.unpack_from('<4s5H3I2H', raw_zip, offset)
    extra_start = offset + 30 + header[9]
    extra_end = extra_start + header[10]
    # Check every representation before any optional-extra early return.
    if (header[0] != b'PK\x03\x04' or extra_end > boundary
            or extra_end + sizes[1] > boundary):
        raise MediaPayloadRejected()
    payload = _zip64_extra(raw_zip, extra_start, extra_end)
    local_sizes = (header[8], header[7])  # uncompressed, compressed
    needs_zip64 = 0xFFFFFFFF in local_sizes
    if payload is None:
        if needs_zip64:
            raise MediaPayloadRejected()
        return
    start, size = payload
    if size == 0 and not needs_zip64:
        return  # A well-formed but unused extra field carries no size values.
    # Local ZIP64 headers carry both sizes, unlike selective central extras.
    if size != 16 or bool(header[2] & 8) != bool(flags & 8):
        raise MediaPayloadRejected()
    values = struct.unpack_from('<QQ', raw_zip, start)
    for local, value, expected in zip(local_sizes, values, sizes):
        allowed = (0, expected) if flags & 8 else (expected,)
        if value not in allowed or (local != 0xFFFFFFFF and local not in allowed):
            raise MediaPayloadRejected()


def _check_zip_directory(raw_zip: bytes, expected_count: int) -> None:
    """Bound real directory bytes/records before ZipFile allocates any ZipInfo.

    ZIP64 changes representation, not our resource limits. Resolve only the
    referenced single-disk records and bounded extra fields, with exact extents.
    Mirror zipfile's EOCD selection so its parser cannot select another record.
    """
    end = len(raw_zip) - 22
    if end < 0:
        raise MediaPayloadRejected()
    if raw_zip[end:end + 4] != b'PK\x05\x06' or raw_zip[-2:] != b'\x00\x00':
        end = raw_zip.rfind(b'PK\x05\x06', max(0, len(raw_zip) - 22 - 65535))
    if end < 0 or end + 22 > len(raw_zip):
        raise MediaPayloadRejected()
    (_, disk, directory_disk, disk_count, count, directory_size,
     directory_offset, comment_size) = struct.unpack_from('<4s4H2IH', raw_zip, end)
    if end + 22 + comment_size != len(raw_zip):
        raise MediaPayloadRejected()
    directory_end = end
    if end >= 20 and raw_zip[end - 20:end - 16] == b'PK\x06\x07':
        locator = end - 20
        _, locator_disk, record_offset, disks = struct.unpack_from('<4sIQI', raw_zip, locator)
        if locator_disk != 0 or disks != 1 or record_offset + 56 > locator:
            raise MediaPayloadRejected()
        record = struct.unpack_from('<4sQ2H2I4Q', raw_zip, record_offset)
        if (record[0] != b'PK\x06\x06' or record[1] < 44
                or record_offset + 12 + record[1] != locator):
            raise MediaPayloadRejected()
        # Non-sentinel classic values must agree with their ZIP64 equivalents.
        classic = (disk, directory_disk, disk_count, count, directory_size, directory_offset)
        markers = (0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF)
        if any(old != marker and old != new for old, marker, new in zip(classic, markers, record[4:])):
            raise MediaPayloadRejected()
        disk, directory_disk, disk_count, count, directory_size, directory_offset = record[4:]
        directory_end = record_offset
    elif (disk == 0xFFFF or directory_disk == 0xFFFF or disk_count == 0xFFFF or count == 0xFFFF
          or directory_size == 0xFFFFFFFF or directory_offset == 0xFFFFFFFF):
        raise MediaPayloadRejected()
    if (disk != 0 or directory_disk != 0 or disk_count != count
            or not 1 <= count <= MAX_UGOIRA_FRAMES or count != expected_count
            or not 46 * count <= directory_size <= MAX_CENTRAL_DIRECTORY_BYTES
            or directory_offset + directory_size != directory_end):
        raise MediaPayloadRejected()
    local_entries: list[tuple[int, int, int, int]] = []
    position = directory_offset
    for _ in range(count):
        if position + 46 > directory_end:
            raise MediaPayloadRejected()
        header = struct.unpack_from('<4s6H3I5H2I', raw_zip, position)
        name_size, extra_size, entry_comment_size = header[10:13]
        extra_start = position + 46 + name_size
        extra_end = extra_start + extra_size
        next_position = extra_end + entry_comment_size
        if header[0] != b'PK\x01\x02' or next_position > directory_end:
            raise MediaPayloadRejected()
        values = [header[9], header[8], header[16], header[13]]
        markers = (0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFF)
        widths = (8, 8, 8, 4)
        required = sum(width for value, marker, width in zip(values, markers, widths) if value == marker)
        payload = _zip64_extra(raw_zip, extra_start, extra_end)
        if payload is None:
            if required:
                raise MediaPayloadRejected()
        else:
            start, size = payload
            if size != required:
                raise MediaPayloadRejected()
            for index, (marker, width) in enumerate(zip(markers, widths)):
                if values[index] == marker:
                    values[index] = struct.unpack_from('<Q' if width == 8 else '<I', raw_zip, start)[0]
                    start += width
        file_size, compressed_size, local_offset, entry_disk = values
        if entry_disk != 0 or local_offset >= directory_offset:
            raise MediaPayloadRejected()
        local_entries.append((local_offset, file_size, compressed_size, header[3]))
        position = next_position
    if position != directory_end:
        raise MediaPayloadRejected()
    # Bound local TLV scanning by disjoint header regions as well. Repeated or
    # overlapping local offsets cannot amplify scans of the same extra bytes.
    local_entries.sort()
    for index, (offset, file_size, compressed_size, flags) in enumerate(local_entries):
        boundary = local_entries[index + 1][0] if index + 1 < len(local_entries) else directory_offset
        _check_local_zip64(raw_zip, offset, boundary, (file_size, compressed_size), flags)


@dataclass(frozen=True, slots=True)
class InspectedFrame:
    filename: str = field(repr=False)
    width: int
    height: int
    bytes_count: int
    sha256: str = field(repr=False)
    delay_ms: int


@dataclass(frozen=True, slots=True)
class InspectedUgoira:
    work_id: str = field(repr=False)
    frames: tuple[InspectedFrame, ...] = field(repr=False)
    total_uncompressed_bytes: int
    duration_ms: int


def inspect_ugoira_archive(raw_zip: object, manifest: UgoiraManifest) -> InspectedUgoira:
    """Reject hostile ZIPs before any managed-root writer may consume them.

    This performs a bounded *full read and decode* of each image. A successful
    validation doesn't authorize writing to the original PixivVault folder.
    """
    if type(manifest) is not UgoiraManifest or type(raw_zip) is not bytes or not 0 < len(raw_zip) <= MAX_ARCHIVE_BYTES:
        raise MediaPayloadRejected()
    try:
        if (type(manifest.frames) is not tuple or not 1 <= len(manifest.frames) <= MAX_UGOIRA_FRAMES
                or any(type(frame) is not UgoiraFrame for frame in manifest.frames)):
            raise MediaPayloadRejected()
        # Frozen dataclasses may still be constructed directly without passing
        # through our M1 parser. Re-validate identity, timing and filenames.
        canonical = parse_ugoira_manifest({
            'originalSrc': manifest.archive_url,
            'frames': [{'file': frame.filename, 'delay': frame.delay_ms}
                       for frame in manifest.frames],
        }, work_id=manifest.work_id)
        if canonical != manifest:
            raise MediaPayloadRejected()
        expected_names = {frame.filename for frame in manifest.frames}
        _check_zip_directory(raw_zip, len(manifest.frames))
        with zipfile.ZipFile(BytesIO(raw_zip), 'r', allowZip64=True) as archive:
            entries = archive.infolist()
            if len(entries) != len(manifest.frames):
                raise MediaPayloadRejected()
            indexed: dict[str, zipfile.ZipInfo] = {}
            total_bytes = 0
            for entry in entries:
                name = entry.filename
                if name in indexed or entry.is_dir() or name not in expected_names:
                    raise MediaPayloadRejected()
                unix_mode = (entry.external_attr >> 16) & 0xFFFF
                if (entry.flag_bits & 0x1 or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                        or stat.S_IFMT(unix_mode) not in (0, stat.S_IFREG) or
                        not 0 < entry.file_size <= MAX_SINGLE_FRAME_BYTES or
                        not 0 < entry.compress_size <= MAX_SINGLE_FRAME_BYTES or
                        entry.file_size > entry.compress_size * MAX_DECOMPRESSION_RATIO):
                    raise MediaPayloadRejected()
                total_bytes += entry.file_size
                if total_bytes > MAX_TOTAL_FRAME_BYTES:
                    raise MediaPayloadRejected()
                indexed[name] = entry
            details: list[InspectedFrame] = []
            total_pixels = 0
            for frame in manifest.frames:
                entry = indexed[frame.filename]
                with archive.open(entry, 'r') as stream:
                    content = stream.read(MAX_SINGLE_FRAME_BYTES + 1)
                if len(content) != entry.file_size:
                    raise MediaPayloadRejected()
                # Pillow may expose remote image/codec strings in exception
                # messages, so sanitize everything below into a fixed error.
                expected = SUPPORTED_TYPES.get('.' + frame.filename.rsplit('.', 1)[-1])
                if expected is None:
                    raise MediaPayloadRejected()
                with Image.open(BytesIO(content), formats=(expected,)) as image:
                    if (image.format != expected or
                            not 0 < image.width * image.height <= MAX_FRAME_PIXELS or
                            getattr(image, 'n_frames', 1) != 1):
                        raise MediaPayloadRejected()
                    total_pixels += image.width * image.height
                    if total_pixels > MAX_TOTAL_DECODED_PIXELS:
                        raise MediaPayloadRejected()
                    image.load()
                    details.append(InspectedFrame(frame.filename, image.width, image.height,
                                                   len(content), hashlib.sha256(content).hexdigest(),
                                                   frame.delay_ms))
            return InspectedUgoira(manifest.work_id, tuple(details), total_bytes, manifest.duration_ms)
    except MediaPayloadRejected:
        raise
    except Exception:
        # Includes zlib/Pillow codec exceptions that may echo input filenames.
        raise MediaPayloadRejected() from None


__all__ = ['InspectedFrame', 'InspectedUgoira', 'inspect_ugoira_archive']
