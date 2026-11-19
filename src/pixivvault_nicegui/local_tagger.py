"""Read-only verification and lazy local WD tagger inference."""
from __future__ import annotations

import hashlib
import json
import csv
import io
import importlib.util
import ntpath
import os
from pathlib import Path
from threading import RLock
from datetime import datetime, timezone


DEFAULT_MODEL = r'S:\EMAKINOCO-Models\tagger\wd-swinv2-tagger-v3'
FILES = ('model.onnx', 'selected_tags.csv')


def _drive_type(root):
    if os.name != 'nt':
        return 3
    import ctypes
    return ctypes.windll.kernel32.GetDriveTypeW(root)


def _path_key(folder):
    text = os.fspath(folder)
    if not text:
        return None
    text = text.replace('/', '\\')
    if text.startswith('\\\\?\\'):
        text = text[4:]
        drive, tail = ntpath.splitdrive(text)
        if len(drive) != 2 or drive[1] != ':' or not tail.startswith('\\'):
            return None
    if text.startswith('\\'):
        return None
    text = ntpath.normcase(ntpath.normpath(ntpath.abspath(text)))
    drive, _ = ntpath.splitdrive(text)
    if not drive or not drive.endswith(':') or _drive_type(drive + '\\') == 4:
        return None
    return text


def _local_folder(folder):
    return _path_key(folder) is not None


def _sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _identity(path):
    resolved = path.resolve()
    key = _path_key(str(resolved))
    if key is None:
        raise NonLocalModelPath()
    stat = path.stat()
    return [key, stat.st_size, stat.st_mtime_ns]


class NonLocalModelPath(ValueError):
    pass


def verify_model(folder, receipt=None):
    """Return a status and reusable receipt, never write to the model folder."""
    if not _local_folder(folder):
        return '利用不可（ローカルのフォルダを指定）', None
    root = Path(folder)
    manifest = root / 'MODEL_SOURCE.json'
    try:
        paths = [manifest, *(root / name for name in FILES)]
        identities = [_identity(path) for path in paths]
        if isinstance(receipt, dict) and receipt.get('identities') == identities and receipt.get('verified') is True:
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
    except NonLocalModelPath:
        return '利用不可（ローカルのフォルダを指定）', None
    except FileNotFoundError:
        return '未検出', None
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return '読込失敗', None


def prepare_image(data):
    """Produce a BGR PIL image; tensor construction is deferred to the runtime."""
    from PIL import Image
    with Image.open(io.BytesIO(data)) as source:
        image = source.convert('RGBA')
        background = Image.new('RGBA', image.size, 'white')
        background.alpha_composite(image)
        image = background.convert('RGB')
        side = max(image.size)
        square = Image.new('RGB', (side, side), 'white')
        square.paste(image, ((side - image.width) // 2, (side - image.height) // 2))
        square = square.resize((448, 448), Image.Resampling.BICUBIC)
        r, g, b = square.split()
        return Image.merge('RGB', (b, g, r))


class LocalTagger:
    def __init__(self, folder, *, session_factory=None, tensor_factory=None, providers=None, receipt=None):
        self.folder = Path(folder)
        self.session_factory = session_factory
        self.tensor_factory = tensor_factory
        self.providers = providers
        self.receipt = receipt
        self.status = '未検出'
        self.session = None
        self.tags = None
        self.cache = {}
        self._lock = RLock()
        self._closed = False
        self._load_failed = False

    def _runtime_available(self):
        return (self.session_factory is not None and self.tensor_factory is not None) or (
            importlib.util.find_spec('onnxruntime') is not None and importlib.util.find_spec('numpy') is not None)

    def verify(self):
        with self._lock:
            if self._closed:
                return '読込失敗'
            old_receipt = self.receipt
            self.status = '検証中'
            status, receipt = verify_model(self.folder, self.receipt)
            self.receipt = receipt
            if old_receipt != receipt:
                self.session = self.tags = None
                self.cache.clear()
                self._load_failed = False
            self.status = status if status != '利用可能' or self._runtime_available() else 'onnxruntime なし'
            if self.status == '利用可能' and self._load_failed:
                self.status = '読込失敗'
            return self.status

    def predict(self, data):
        with self._lock:
            if self.verify() != '利用可能':
                return []
            key = hashlib.sha256(data).hexdigest()
            if key in self.cache:
                return list(self.cache[key])
            try:
                image = prepare_image(data)
            except Exception:
                return []
            try:
                if self.session is None:
                    with (self.folder / 'selected_tags.csv').open(encoding='utf-8-sig', newline='') as stream:
                        tags = list(csv.DictReader(stream))
                    if not tags or any(not {'name', 'category'} <= row.keys() for row in tags):
                        raise ValueError('invalid tags')
                    if self.session_factory is None:
                        import onnxruntime as ort
                        available = ort.get_available_providers()
                        providers = self.providers or (['CUDAExecutionProvider', 'CPUExecutionProvider']
                            if 'CUDAExecutionProvider' in available else ['CPUExecutionProvider'])
                        if providers[0] not in available:
                            raise RuntimeError('requested inference provider unavailable')
                        session = ort.InferenceSession(str(self.folder / 'model.onnx'), providers=providers)
                        if providers[0] not in session.get_providers():
                            raise RuntimeError('requested inference provider not active')
                    else:
                        session = self.session_factory(self.folder / 'model.onnx')
                    self.tags, self.session = tags, session
                if self.tensor_factory is None:
                    import numpy as np
                    tensor = np.asarray(image, dtype=np.float32)[None, ...]
                else:
                    tensor = self.tensor_factory(image)
                scores = self.session.run(None, {self.session.get_inputs()[0].name: tensor})[0][0]
                if len(scores) != len(self.tags):
                    raise ValueError('tag count mismatch')
                result = sorted(((row['name'], float(score)) for row, score in zip(self.tags, scores)
                    if (row['category'] == '0' and score >= .35) or (row['category'] == '4' and score >= .85)),
                    key=lambda pair: pair[1], reverse=True)
                self.cache[key] = tuple(result)
                return result
            except Exception:
                self.session = self.tags = None
                self._load_failed = True
                self.status = '読込失敗'
                return []

    def close(self):
        with self._lock:
            self._closed = True
            self.session = self.tags = None
            self.cache.clear()


class TaggerManager:
    """Own one model session and save verification evidence in preferences."""
    def __init__(self, preferences, *, provider=lambda: 'auto'):
        self.preferences = preferences
        self.provider = provider
        self.tagger = None
        self.status = '未検出'
        self._lock = RLock()
        self._closed = False

    def _current(self):
        snapshot = self.preferences.snapshot()
        path = snapshot.values['local_tagger_path']
        provider = self.provider()
        if self.tagger is None or _path_key(self.tagger.folder) != _path_key(path) or self._provider != provider:
            if self.tagger is not None:
                self.tagger.close()
            receipt = self.preferences.read_tagger_verification().get('receipt')
            providers = {'cpu': ['CPUExecutionProvider'], 'cuda': ['CUDAExecutionProvider', 'CPUExecutionProvider']}.get(provider)
            self.tagger = LocalTagger(path, providers=providers, receipt=receipt)
            self._provider = provider
        return snapshot

    def verify(self):
        with self._lock:
            if self._closed:
                return '読込失敗'
            snapshot = self._current()
            self.status = '検証中'
            self.status = self.tagger.verify()
            self.preferences.write_tagger_verification({'receipt': self.tagger.receipt,
                'verified_at': datetime.now(timezone.utc).isoformat()})
            return self.status

    def predict(self, data):
        with self._lock:
            if self._closed:
                return []
            snapshot = self._current()
            if snapshot.values['local_tagger_enabled'] != '1' or self.verify() != '利用可能':
                return []
            receipt = self.tagger.receipt
            path = snapshot.values['local_tagger_path']
            result = self.tagger.predict(data)
            self.status = self.tagger.status
            current = self.preferences.snapshot()
            if current.values['local_tagger_enabled'] != '1' or _path_key(current.values['local_tagger_path']) != _path_key(path):
                return []
            status, current_receipt = verify_model(path, receipt)
            if status != '利用可能' or current_receipt != receipt:
                return []
            return result

    def current_fingerprint(self):
        with self._lock:
            if self._closed:
                return None
            snapshot = self._current()
            if snapshot.values['local_tagger_enabled'] != '1':
                return None
            status = self.tagger.verify()
            self.status = status
            if status not in ('利用可能', 'onnxruntime なし') or not self.tagger.receipt:
                return None
            identity = json.dumps(self.tagger.receipt, sort_keys=True, separators=(',', ':')).encode('utf-8')
            return hashlib.sha256(identity).hexdigest()

    def predict_with_fingerprint(self, data):
        with self._lock:
            before = self.current_fingerprint()
            if before is None:
                return None
            tags = self.predict(data)
            if self.status != '利用可能' or self.current_fingerprint() != before:
                return None
            return before, tags

    def try_current_fingerprint(self):
        try:
            if self._closed:
                return None
            snapshot = self.preferences.snapshot()
            path = snapshot.values['local_tagger_path']
            if snapshot.values['local_tagger_enabled'] != '1' or not _local_folder(path):
                return None
            receipt = self.preferences.read_tagger_verification().get('receipt')
            if not isinstance(receipt, dict) or receipt.get('verified') is not True:
                return None
            folder = Path(path)
            paths = [folder / 'MODEL_SOURCE.json', *(folder / name for name in FILES)]
            if receipt.get('identities') != [_identity(item) for item in paths]:
                self.status = verify_model(path, receipt)[0]
                return None
            current = self.preferences.snapshot()
            if current.values['local_tagger_enabled'] != '1' or _path_key(current.values['local_tagger_path']) != _path_key(path):
                return None
            identity = json.dumps(receipt, sort_keys=True, separators=(',', ':')).encode('utf-8')
            return hashlib.sha256(identity).hexdigest()
        except NonLocalModelPath:
            self.status = '利用不可（ローカルのフォルダを指定）'
            return None
        except (OSError, ValueError, TypeError, RuntimeError):
            return None

    def close(self):
        with self._lock:
            self._closed = True
            if self.tagger is not None:
                self.tagger.close()
                self.tagger = None
