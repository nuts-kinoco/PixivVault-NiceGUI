"""Version 2 bundle receipt validation; v1 generations remain readable unchanged."""
import hashlib
import json
import re
from pathlib import PurePosixPath
from .novel_storage import NovelStorageError, _safe, _json_bytes, _read_bounded
from .managed_downloads import _json_unique

LIMIT = 32 * 1024 * 1024


def valid_name(name):
    if type(name) is not str or len(name.encode('utf-16-le')) > 400:
        return False
    if re.fullmatch(r'images/(?:[1-9][0-9]{0,19}-[1-9][0-9]{0,19}|cover_[1-9][0-9]{0,19})\.png', name):
        return True
    return (len(PurePosixPath(name).parts) == 1 and
            re.fullmatch(r'[^<>:"/\\|?*\x00-\x1f\x7f]+\([1-9][0-9]{0,19}\)\.(txt|epub)', name) is not None)


def inventory(folder):
    """Bounded depth, no symlink/reparse/hardlink, no unrecognized paths."""
    found = {}
    for child in folder.iterdir():
        if child.name == 'images':
            _safe(child)
            for asset in child.iterdir():
                name = 'images/' + asset.name
                if not valid_name(name):
                    raise NovelStorageError()
                _safe(asset, file=True)
                found[name] = asset.stat().st_size
                if len(found) > 105:
                    raise NovelStorageError()
        else:
            if child.name not in {'novel.txt', 'novel.epub', 'receipt.json', 'completion.json'} and not valid_name(child.name):
                raise NovelStorageError()
            _safe(child, file=True)
            found[child.name] = child.stat().st_size
        if len(found) > 105:
            raise NovelStorageError()
    return found


def validate_metadata(metadata):
    if type(metadata) is not dict or set(metadata) != {'title', 'author', 'chapters', 'series_id'}:
        raise NovelStorageError()
    for key in ('title', 'author'):
        text = metadata[key]
        if type(text) is not str or not 0 < len(text) <= 256 or any(ord(c) < 32 for c in text):
            raise NovelStorageError()
    ids = metadata['chapters']
    if type(ids) is not list or not 1 <= len(ids) <= 100 or len(set(ids)) != len(ids) or any(type(x) is not str or not re.fullmatch(r'[1-9][0-9]{0,19}', x) for x in ids):
        raise NovelStorageError()
    sid = metadata['series_id']
    if sid is not None and (type(sid) is not str or not re.fullmatch(r'[1-9][0-9]{0,19}', sid)):
        raise NovelStorageError()


def validate_bundle(payloads, fmt, metadata):
    validate_metadata(metadata)
    if type(payloads) is not dict or not 1 <= len(payloads) <= 103 or fmt not in ('TXT', 'EPUB', 'BOTH'):
        raise NovelStorageError()
    if any(not valid_name(k) or type(v) is not bytes or not 0 < len(v) <= LIMIT for k, v in payloads.items()) or sum(map(len, payloads.values())) > LIMIT:
        raise NovelStorageError()
    extensions = [PurePosixPath(k).suffix for k in payloads if '/' not in k]
    if sorted(extensions) != {'TXT': ['.txt'], 'EPUB': ['.epub'], 'BOTH': ['.epub', '.txt']}[fmt]:
        raise NovelStorageError()


def verify(folder, generation, receipt, completion):
    obj = json.loads(receipt, object_pairs_hook=_json_unique)
    if (type(obj) is not dict or set(obj) != {'schema','kind','work_id','author_id','format','outputs','metadata'}
            or type(obj['schema']) is not int or obj['schema'] != 2 or obj['kind'] != 'novel-export'
            or any(type(obj[k]) is not str or not re.fullmatch(r'[1-9][0-9]{0,19}', obj[k]) for k in ('work_id','author_id'))
            or not re.fullmatch('novel-' + obj['work_id'] + r'-[a-f0-9]{32}', generation)):
        raise NovelStorageError()
    rows = obj['outputs']
    if type(rows) is not list or not 1 <= len(rows) <= 103:
        raise NovelStorageError()
    payloads = {}
    for row in rows:
        if (type(row) is not dict or set(row) != {'name','bytes','sha256'} or not valid_name(row['name'])
                or row['name'] in payloads or type(row['bytes']) is not int or not 0 < row['bytes'] <= LIMIT
                or type(row['sha256']) is not str or not re.fullmatch(r'[a-f0-9]{64}', row['sha256'])):
            raise NovelStorageError()
        if sum(map(len, payloads.values())) + row['bytes'] > LIMIT:
            raise NovelStorageError()
        data = _read_bounded(folder / row['name'], LIMIT)
        if len(data) != row['bytes'] or hashlib.sha256(data).hexdigest() != row['sha256']:
            raise NovelStorageError()
        payloads[row['name']] = data
    validate_bundle(payloads, obj['format'], obj['metadata'])
    if obj['metadata']['chapters'][0] != obj['work_id']:
        raise NovelStorageError()
    if (folder / 'images').exists() and not any(name.startswith('images/') for name in payloads):
        raise NovelStorageError()
    if set(inventory(folder)) != set(payloads) | {'receipt.json','completion.json'}:
        raise NovelStorageError()
    if _read_bounded(folder / 'receipt.json', 65536) != receipt or _read_bounded(folder / 'completion.json', 1024) != completion:
        raise NovelStorageError()
    if completion != _json_bytes({'schema':1,'generation':generation,'receipt_sha256':hashlib.sha256(receipt).hexdigest()}):
        raise NovelStorageError()
    return obj