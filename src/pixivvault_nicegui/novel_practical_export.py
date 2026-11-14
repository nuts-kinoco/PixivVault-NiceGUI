"""Original Flet novel output semantics, without HTTP or filesystem writes.

Produces an output bundle for the pending durable asset-schema integration.
The existing v1 receipt writer must not be given these arbitrary filenames.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
import re
from xml.sax.saxutils import escape
import xml.etree.ElementTree as ET
import zipfile

from PIL import Image
from .media_parity_policy import MediaPayloadRejected, NovelDocument
from .novel_export import _validated, render_novel_epub
from .novel_series_export import render_novel_series_epub

_TOKEN = re.compile(r'\[\[rb:([^\r\n]*?) > ([^\r\n]*?)\]\]|\[newpage\]|\[uploadedimage:([1-9][0-9]{0,19})\]')
_XHTML = 'http://www.w3.org/1999/xhtml'
_OPF = 'http://www.idpf.org/2007/opf'
_MAX_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class NovelAsset:
    identifier: str
    data: bytes = field(repr=False)


def _png(asset):
    if type(asset) is not NovelAsset or type(asset.data) is not bytes or not 0 < len(asset.data) <= 8 * 1024 * 1024:
        raise MediaPayloadRejected()
    with Image.open(BytesIO(asset.data)) as image:
        if (image.format not in ('PNG', 'JPEG') or getattr(image, 'n_frames', 1) != 1
                or not 0 < image.width <= 8192 or not 0 < image.height <= 8192
                or image.width * image.height > 16_000_000):
            raise MediaPayloadRejected()
        image.load()
        output = BytesIO()
        image.convert('RGB').save(output, format='PNG')
        value = output.getvalue()
    if len(value) > 8 * 1024 * 1024:
        raise MediaPayloadRejected()
    return value


def output_stem(title, work_id):
    if type(title) is not str or not title or len(title) > 256 or type(work_id) is not str or not re.fullmatch(r'[1-9][0-9]{0,19}', work_id):
        raise MediaPayloadRejected()
    # Match the adopted title(ID) convention while keeping one Windows component.
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', '_', title).strip(' .') or '_'
    while len(safe.encode('utf-16-le')) > 300:
        safe = safe[:-1]
    return f'{safe}({work_id})'


def _content(text, image_names):
    plain, html = [], []
    end = 0
    for match in _TOKEN.finditer(text):
        literal = text[end:match.start()]
        plain.append(literal)
        html.append(escape(literal).replace('\n', '<br/>\n'))
        if match.group(1) is not None:
            base, reading = match.group(1), match.group(2)
            plain.append(base + '《' + reading + '》')
            html.append('<ruby>' + escape(base) + '<rt>' + escape(reading) + '</rt></ruby>')
        elif match.group(3) is not None:
            name = image_names.get(match.group(3))
            if name is None:
                raise MediaPayloadRejected()
            plain.append('[挿絵: images/' + name + ']')
            html.append('<img src="../Images/' + name + '" alt="挿絵"/>')
        else:
            plain.append('\n\n---\n\n')
            html.append('<hr/>')
        end = match.end()
    plain.append(text[end:])
    html.append(escape(text[end:]).replace('\n', '<br/>\n'))
    return ''.join(plain), ''.join(html)


def render_practical_bundle(documents, *, author_name, output_format, assets=None,
                            cover=None, series_title=None, expected_work_ids=None):
    """Return named UTF-8-BOM TXT / EPUB and normalized local PNG bytes.

    For a series the caller supplies the accepted explicit chapter order, not a
    claim inferred from seriesNavData. The finite existing series exporter is
    reused for its ID, order, duplicate and aggregate budget validation.
    """
    if type(documents) not in (tuple, list) or not 1 <= len(documents) <= 100 or output_format not in ('TXT', 'EPUB', 'BOTH'):
        raise MediaPayloadRejected()
    documents = tuple(documents)
    validated = [_validated(doc, author_name) for doc in documents]
    if len(documents) > 1:
        if type(expected_work_ids) is not tuple or expected_work_ids != tuple(d.work_id for d in documents):
            raise MediaPayloadRejected()
        epub = render_novel_series_epub(documents, author_name=author_name, series_title=series_title)
        title, identity = series_title, documents[0].series_id
    else:
        epub = render_novel_epub(documents[0], author_name=author_name)
        title, identity = documents[0].title, documents[0].work_id
    if assets is None:
        assets = {}
    if type(assets) is not dict or len(assets) > 100:
        raise MediaPayloadRejected()
    # A (work_id, image_id) key prevents equal image IDs in different chapters
    # from silently reusing another chapter's picture.
    normalized = {}
    allowed_ids = {d.work_id for d in documents}
    for key, asset in assets.items():
        if (type(key) is not tuple or len(key) != 2 or key[0] not in allowed_ids
                or type(key[1]) is not str or not re.fullmatch(r'[1-9][0-9]{0,19}', key[1])
                or type(asset) is not NovelAsset or asset.identifier != key[1]):
            raise MediaPayloadRejected()
        normalized[key] = _png(asset)
        if sum(map(len, normalized.values())) > _MAX_BYTES:
            raise MediaPayloadRejected()
    images = {f'{wid}-{iid}.png': data for (wid, iid), data in normalized.items()}
    cover_name = None
    if cover is not None:
        if type(cover) is not NovelAsset or cover.identifier != 'cover':
            raise MediaPayloadRejected()
        cover_name = 'cover_' + identity + '.png'
        images[cover_name] = _png(cover)
    if sum(map(len, images.values())) > _MAX_BYTES:
        raise MediaPayloadRejected()
    txt_sections, chapter_bytes = [], {}
    for number, (doc, (_, _, content)) in enumerate(zip(documents, validated), 1):
        names = {iid: f'{wid}-{iid}.png' for wid, iid in normalized if wid == doc.work_id}
        plain, markup = _content(content, names)
        txt_sections.append(plain if len(documents) == 1 else doc.title + '\n\n' + plain)
        path = 'OEBPS/Text/chapter.xhtml' if len(documents) == 1 else f'OEBPS/Text/chapter-{number:03d}.xhtml'
        chapter_bytes[path] = ('<?xml version="1.0" encoding="UTF-8"?>'
            f'<html xmlns="{_XHTML}" xml:lang="ja"><head><title>' + escape(doc.title)
            + '</title></head><body><h1>' + escape(doc.title) + '</h1><div>' + markup
            + '</div></body></html>').encode('utf-8')
    with zipfile.ZipFile(BytesIO(epub)) as archive:
        members = {item.filename: archive.read(item) for item in archive.infolist()}
    members.update(chapter_bytes)
    opf = ET.fromstring(members['OEBPS/content.opf'])
    manifest = opf.find(f'{{{_OPF}}}manifest')
    for index, (name, data) in enumerate(images.items()):
        asset_id = 'cover-image' if name == cover_name else f'image-{index}'
        ET.SubElement(manifest, f'{{{_OPF}}}item', {'id': asset_id, 'href': 'Images/' + name, 'media-type': 'image/png'})
        members['OEBPS/Images/' + name] = data
    if cover_name:
        metadata = opf.find(f'{{{_OPF}}}metadata')
        ET.SubElement(metadata, f'{{{_OPF}}}meta', {'name': 'cover', 'content': 'cover-image'})
    members['OEBPS/content.opf'] = ET.tostring(opf, encoding='utf-8', xml_declaration=True)
    if sum(map(len, members.values())) > _MAX_BYTES:
        raise MediaPayloadRejected()
    output = BytesIO()
    with zipfile.ZipFile(output, 'w', allowZip64=False) as archive:
        for name in ['mimetype', *sorted(set(members) - {'mimetype'})]:
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_STORED if name == 'mimetype' else zipfile.ZIP_DEFLATED
            archive.writestr(info, members[name])
    stem = output_stem(title, identity)
    result = {}
    if output_format in ('TXT', 'BOTH'):
        result[stem + '.txt'] = '\n\n---\n\n'.join(txt_sections).encode('utf-8-sig')
        result.update({'images/' + name: data for name, data in images.items()})
    if output_format in ('EPUB', 'BOTH'):
        result[stem + '.epub'] = output.getvalue()
    if sum(map(len, result.values())) > _MAX_BYTES:
        raise MediaPayloadRejected()
    return result