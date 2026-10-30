"""Read verified bundle text/images without executing EPUB markup or URLs."""
from io import BytesIO
import base64
import xml.etree.ElementTree as ET
import zipfile
from .managed_downloads import _read_bounded
from .novel_storage import NovelStorageIntegrityError


def read_bundle(folder, saved):
    text = None
    images = []
    for name in saved.output_names:
        if name.endswith('.txt'):
            text = _read_bounded(folder / name, 32 * 1024 * 1024).decode('utf-8-sig')
        elif name.startswith('images/'):
            images.append(_read_bounded(folder / name, 8 * 1024 * 1024))
    epub = next((name for name in saved.output_names if name.endswith('.epub')), None)
    if epub and (text is None or not images):
        with zipfile.ZipFile(BytesIO(_read_bounded(folder / epub, 32 * 1024 * 1024))) as archive:
            members = archive.infolist()
            if len(members) > 310 or sum(i.file_size for i in members) > 32 * 1024 * 1024:
                raise NovelStorageIntegrityError()
            if text is None:
                chapters = []
                for name in sorted(i.filename for i in members if i.filename.startswith('OEBPS/Text/') and i.filename.endswith('.xhtml')):
                    raw = archive.read(name)
                    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
                        raise NovelStorageIntegrityError()
                    body = ET.fromstring(raw).find('{http://www.w3.org/1999/xhtml}body')
                    def plain(node):
                        tag = node.tag.rsplit('}', 1)[-1]
                        if tag == 'br': return '\n'
                        if tag == 'hr': return '\n\n---\n\n'
                        if tag == 'img': return '[挿絵]'
                        value = (node.text or '') + ''.join(plain(c) + (c.tail or '') for c in node)
                        return ('《' + value + '》') if tag == 'rt' else value + ('\n' if tag in ('h1', 'p') else '')
                    chapters.append(plain(body))
                text = '\n\n'.join(chapters)
            if not images:
                images = [archive.read(i) for i in members if i.filename.startswith('OEBPS/Images/') and i.filename.endswith('.png')]
    if text is None or len(images) > 101 or sum(map(len, images)) > 32 * 1024 * 1024:
        raise NovelStorageIntegrityError()
    if any(not data.startswith(b'\x89PNG\r\n\x1a\n') for data in images):
        raise NovelStorageIntegrityError()
    return text, tuple('data:image/png;base64,' + base64.b64encode(data).decode('ascii') for data in images)