"""Read-only verification and lazy local WD tagger inference."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


DEFAULT_MODEL = r'S:\EMAKINOCO-Models\tagger\wd-swinv2-tagger-v3'
FILES = ('model.onnx', 'selected_tags.csv')


def _sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _identity(path):
    stat = path.stat()
    return [str(path.resolve()), stat.st_size, stat.st_mtime_ns]


def verify_model(folder, receipt=None):
    """Return a status and reusable receipt, never write to the model folder."""
    root = Path(folder)
    manifest = root / 'MODEL_SOURCE.json'
    try:
        paths = [manifest, *(root / name for name in FILES)]
        identities = [_identity(path) for path in paths]
        if receipt and receipt.get('identities') == identities and receipt.get('verified') is True:
            return '利用可能', receipt
        source = json.loads(manifest.read_text(encoding='utf-8'))
        entries = {item['path']: item['sha256'] for item in source['files']}
        for name in FILES:
            expected = entries[name]
            if not isinstance(expected, str) or len(expected) != 64:
                raise ValueError('invalid digest')
            if _sha256(root / name) != expected.lower():
                return 'SHA 不一致', None
        if [_identity(path) for path in paths] != identities:
            return '読込失敗', None
        return '利用可能', {'identities': identities, 'verified': True}
    except FileNotFoundError:
        return '未検出', None
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return '読込失敗', None
