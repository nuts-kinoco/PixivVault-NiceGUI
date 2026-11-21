"""Read-only verification and lazy local WD tagger inference."""
from __future__ import annotations

import hashlib
import json
import csv
from contextlib import nullcontext
import io
import importlib.util
import ntpath
import os
from pathlib import Path
from threading import RLock
from datetime import datetime, timezone

from .gpu_arbiter import DEFAULT_GPU_ARBITER, GpuInferenceStopped, check_gpu_stop, cuda_available

DEFAULT_MODEL = r'S:\EMAKINOCO-Models\tagger\wd-swinv2-tagger-v3'
FILES = ('model.onnx', 'selected_tags.csv')
CUDA_PROVIDER = 'CUDAExecutionProvider'
CPU_PROVIDER = 'CPUExecutionProvider'
DEFAULT_GPU_MEM_LIMIT_BYTES = 2 * 1024**3  # CUDA arena only, in bytes.


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


def verify_model(folder, receipt=None, *, on_identity_change=None):
    """Return a status and reusable receipt, never write to the model folder."""
    if not _local_folder(folder):
        if on_identity_change is not None:
            on_identity_change()
        return '利用不可（ローカルのフォルダを指定）', None
    root = Path(folder)
    manifest = root / 'MODEL_SOURCE.json'
    try:
        paths = [manifest, *(root / name for name in FILES)]
        identities = [_identity(path) for path in paths]
        if isinstance(receipt, dict) and receipt.get('identities') == identities and receipt.get('verified') is True:
            return '利用可能', receipt
        if on_identity_change is not None:
            on_identity_change()
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


class _SessionLoadFailed(RuntimeError):
    pass


class _ModelChanged(RuntimeError):
    pass


class LocalTagger:
    def __init__(self, folder, *, session_factory=None, tensor_factory=None, providers=None, receipt=None,
                 gpu_mem_limit_bytes: int = DEFAULT_GPU_MEM_LIMIT_BYTES, gpu_arbiter=None):
        if type(gpu_mem_limit_bytes) is not int or gpu_mem_limit_bytes <= 0:
            raise ValueError('invalid CUDA arena limit')
        self.folder = Path(folder)
        self.session_factory = session_factory
        self.tensor_factory = tensor_factory
        self.providers = providers
        self.gpu_mem_limit_bytes = gpu_mem_limit_bytes
        self._gpu_arbiter = DEFAULT_GPU_ARBITER if gpu_arbiter is None else gpu_arbiter
        self.receipt = receipt
        self.status = '未検出'
        self.session = None
        self._cuda_session = False
        self.tags = None
        self.cache = {}
        self._lock = RLock()
        self._work_lock = RLock()
        self._closed = False
        self._load_failed = False
        self._retry_pending = False
        self._session_verified = False
        self.on_identity_change = None

    def _runtime_available(self):
        return (self.session_factory is not None and self.tensor_factory is not None) or (
            importlib.util.find_spec('onnxruntime') is not None and importlib.util.find_spec('numpy') is not None)

    def _verify_model(self):
        if self._closed:
            self.status = '読込失敗'
            return
        status, receipt = verify_model(self.folder, self.receipt, on_identity_change=self.on_identity_change)
        if self.receipt != receipt:
            self.release_session()
            self.cache.clear()
            self._session_verified = False
        self.receipt = receipt
        if status == '利用可能':
            if not self._runtime_available():
                status = 'onnxruntime なし'
            elif self._load_failed or self._retry_pending:
                status = '読込失敗'
        self.status = status

    def _provider_names(self):
        return [provider if isinstance(provider, str) else provider[0]
                for provider in (self.providers or ())]

    def _use_cuda(self):
        if self.session is not None:
            return self._cuda_session
        names = self._provider_names()
        if names and CUDA_PROVIDER not in names:
            return False
        if self.session_factory is not None:
            return CUDA_PROVIDER in names
        try:
            import onnxruntime as ort
            if cuda_available(ort):
                return True
        except GpuInferenceStopped:
            raise
        except Exception:
            if not names:
                return False
        if names:
            raise _SessionLoadFailed('requested inference provider unavailable')
        return False

    def _read_tags(self):
        with (self.folder / 'selected_tags.csv').open(encoding='utf-8-sig', newline='') as stream:
            tags = list(csv.DictReader(stream))
        if not tags or any(not {'name', 'category'} <= row.keys() for row in tags):
            raise ValueError('invalid tags')
        return tags

    def _load_session(self, tags, use_cuda, stop):
        session = None
        try:
            if self.session_factory is not None:
                check_gpu_stop(stop)
                session = self.session_factory(self.folder / 'model.onnx')
                active = session.get_providers() if hasattr(session, 'get_providers') else ()
                if (CUDA_PROVIDER in active) != use_cuda:
                    raise ValueError('factory session provider mismatch')
            else:
                import onnxruntime as ort
                providers = self.providers or ([CUDA_PROVIDER, CPU_PROVIDER] if use_cuda else [CPU_PROVIDER])
                selected = CUDA_PROVIDER if use_cuda else (
                    self._provider_names()[0] if self.providers else CPU_PROVIDER)
                if use_cuda:
                    providers = [
                        (CUDA_PROVIDER, dict({} if isinstance(provider, str) else provider[1],
                                             gpu_mem_limit=str(self.gpu_mem_limit_bytes)))
                        if name == CUDA_PROVIDER else provider
                        for name, provider in zip(self._provider_names() or providers, providers)
                    ]
                check_gpu_stop(stop)
                session = ort.InferenceSession(str(self.folder / 'model.onnx'), providers=providers)
                active = session.get_providers()
                if selected not in active or (CUDA_PROVIDER in active) != use_cuda:
                    raise ValueError('requested inference provider not active')
            if use_cuda:
                if hasattr(session, 'disable_fallback'):
                    session.disable_fallback()
                self._gpu_arbiter.retain_session(self)
            self._cuda_session = use_cuda
            self.tags, self.session = tags, session
            return
        except GpuInferenceStopped:
            raise
        except Exception:
            # Drop the native session and the original validation traceback
            # before raising or attempting a CPU load.
            pass
        finally:
            session = None
        raise _SessionLoadFailed('tagger session could not be loaded')

    def _session_work(self, receipt, tags, use_cuda, stop, tensor, *, predict):
        if self._closed:
            raise _SessionLoadFailed('tagger is closed')
        # Close the window between SHA verification and lease acquisition.
        identities = receipt.get('identities')
        if identities is not None:
            try:
                unchanged = identities == [_identity(self.folder / name)
                                           for name in ('MODEL_SOURCE.json', *FILES)]
            except (OSError, ValueError):
                unchanged = False
            if not unchanged:
                raise _ModelChanged()
        loaded = self.session is None
        if loaded:
            self._load_session(tags, use_cuda, stop)
            return None, True
        if not predict:
            return None, False
        failed = False
        try:
            inputs = {self.session.get_inputs()[0].name: tensor}
            check_gpu_stop(stop)
            outputs = self.session.run(None, inputs)
        except GpuInferenceStopped:
            raise
        except Exception:
            failed = True
        if failed:
            if use_cuda:
                # Keep the owner registered so release_all()/release_idle()
                # can clear the retry request even though the session is gone.
                self.session = self.tags = None
                self._cuda_session = False
                self._retry_pending = True
            else:
                self.release_session()
                self._load_failed = True
            check_gpu_stop(stop)
            self.status = '読込失敗'
            return None, False
        check_gpu_stop(stop)
        return outputs, False

    def _job(self, use_cuda):
        return self._gpu_arbiter.acquire(self, priority=10) if use_cuda else nullcontext()

    def _backend_work(self, receipt, tags, use_cuda, tensor, *, predict):
        with self._job(use_cuda) as stop, self._lock:
            outputs, loaded = self._session_work(receipt, tags, use_cuda, stop, tensor, predict=predict)
        if not loaded:
            return outputs
        # Recheck the receipt before using new weights. A changed identity
        # can require hashing the model, so this check is outside the lease.
        with self._lock:
            status, checked = verify_model(self.folder, receipt, on_identity_change=self.on_identity_change)
            if checked != receipt:
                self.release_session()
                self.status = status if status != '利用可能' else '検証中'
                self.receipt = checked
                self._session_verified = False
                return None
            self._session_verified = True
        if not predict:
            return None
        with self._job(use_cuda) as stop, self._lock:
            if self._closed or self.session is None:
                self.status = '検証中'
                return None
            outputs, _ = self._session_work(receipt, tags, use_cuda, stop, tensor, predict=True)
            return outputs

    @staticmethod
    def _format(tags, outputs):
        if outputs is None:
            return None
        scores = outputs[0][0]
        if len(scores) != len(tags):
            raise ValueError('tag count mismatch')
        return sorted(((row['name'], float(score)) for row, score in zip(tags, scores)
            if (row['category'] == '0' and score >= .35) or (row['category'] == '4' and score >= .85)),
            key=lambda pair: pair[1], reverse=True)

    def _operate(self, data=None, *, predict=False, load_session=True):
        key = None
        try:
            with self._gpu_arbiter.stop_scope():
                with self._lock:
                    self._verify_model()
                    if self.status != '利用可能' or not load_session:
                        return None, None
                    if not predict and self.session is not None:
                        return None, None
                    receipt, tags = self.receipt, self.tags
                    if predict:
                        key = hashlib.sha256(data).hexdigest()
                        if key in self.cache:
                            return key, list(self.cache[key])
                tensor = None
                if predict:
                    try:
                        image = prepare_image(data)
                    except Exception:
                        return None, None
                    if self.tensor_factory is None:
                        import numpy as np
                        tensor = np.asarray(image, dtype=np.float32)[None, ...]
                    else:
                        tensor = self.tensor_factory(image)
                tags = tags if tags is not None else self._read_tags()
                with self._lock:
                    use_cuda = self._use_cuda()
                if use_cuda:
                    try:
                        with self._gpu_arbiter.keep_sessions():
                            outputs = self._backend_work(receipt, tags, True, tensor, predict=predict)
                    except _SessionLoadFailed:
                        if self.providers:
                            raise
                    else:
                        return key, self._format(tags, outputs)
            outputs = self._backend_work(receipt, tags, False, tensor, predict=predict)
            return key, self._format(tags, outputs)
        except GpuInferenceStopped:
            raise
        except _ModelChanged:
            with self._lock:
                self.release_session()
                self.cache.clear()
                self._verify_model()
            return None, None
        except Exception:
            with self._lock:
                self.release_session()
                self._load_failed = True
                self.status = '読込失敗'
            return None, None

    def verify(self, *, load_session=True):
        with self._work_lock:
            self._operate(load_session=load_session)
            return self.status

    def predict(self, data):
        with self._work_lock:
            key, result = self._operate(data, predict=True)
            if result is None:
                return []
            with self._lock:
                if not self._closed:
                    self.cache[key] = tuple(result)
            return result

    def release_session(self):
        """Drop the model session while retaining verification and predictions."""
        with self._lock:
            self.session = self.tags = None
            self._load_failed = False
            if self._cuda_session or self._retry_pending:
                self._cuda_session = self._retry_pending = False
                self._gpu_arbiter.forget_session(self)

    def close(self):
        with self._lock:
            self._closed = True
            self.release_session()
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
        # Invalidation can arrive while the inference lock is held.
        self._fingerprint_lock = RLock()
        self._verification_epoch = 0
        self._fingerprint = None
        self._listeners = set()
        self._unsubscribe = preferences.subscribe_local_tagger_settings(self.invalidate)

    def subscribe_invalidation(self, invalidate):
        with self._fingerprint_lock:
            self._listeners.add(invalidate)
        def unsubscribe():
            with self._fingerprint_lock:
                self._listeners.discard(invalidate)
        return unsubscribe

    def invalidate(self):
        self._invalidate_fingerprint(force=True)

    def _invalidate_fingerprint(self, *, force=False):
        with self._fingerprint_lock:
            if self._fingerprint is None and not force:
                return
            self._fingerprint = None
            self._verification_epoch += 1
            for invalidate in self._listeners:
                invalidate()

    def _verified_fingerprint(self, epoch, receipt, *, publish=True):
        if not receipt:
            self._invalidate_fingerprint()
            return None
        identity = json.dumps(receipt, sort_keys=True, separators=(',', ':')).encode('utf-8')
        fingerprint = hashlib.sha256(identity).hexdigest()
        with self._fingerprint_lock:
            if epoch != self._verification_epoch:
                return None
            if self._fingerprint is not None and self._fingerprint != fingerprint:
                self._invalidate_fingerprint()
            if publish:
                self._fingerprint = fingerprint
            return fingerprint

    def _current(self):
        snapshot = self.preferences.snapshot()
        path = snapshot.values['local_tagger_path']
        provider = self.provider()
        if self.tagger is None or _path_key(self.tagger.folder) != _path_key(path) or self._provider != provider:
            if self.tagger is not None:
                self._invalidate_fingerprint()
                self.tagger.close()
            receipt = self.preferences.read_tagger_verification().get('receipt')
            providers = {'cpu': ['CPUExecutionProvider'], 'cuda': ['CUDAExecutionProvider', 'CPUExecutionProvider']}.get(provider)
            self.tagger = LocalTagger(path, providers=providers, receipt=receipt)
            self._provider = provider
        self.tagger.on_identity_change = self._invalidate_fingerprint
        return snapshot

    @property
    def usable(self):
        with self._fingerprint_lock:
            return self._fingerprint is not None and self.status == '利用可能'

    def _verify(self, snapshot, epoch, *, load_session=True):
        enabled = snapshot.values['local_tagger_enabled'] == '1'
        self.status = '検証中'
        self.status = self.tagger.verify(load_session=enabled and load_session)
        valid = enabled and self.status == '利用可能'
        session_verified = self.tagger._session_verified
        if valid and not session_verified:
            self.status = '検証中'
        return self._verified_fingerprint(epoch, self.tagger.receipt if valid else None,
                                          publish=session_verified)

    def verify(self):
        with self._lock:
            if self._closed:
                return '読込失敗'
            epoch = self._verification_epoch
            snapshot = self._current()
            self._verify(snapshot, epoch)
            self.preferences.write_tagger_verification({'receipt': self.tagger.receipt,
                'verified_at': datetime.now(timezone.utc).isoformat()})
            return self.status

    def predict(self, data):
        result = self.predict_with_fingerprint(data)
        return result[1] if result is not None else []

    def current_fingerprint(self, *, load_session=True):
        # Explicit verification event. LocalTagView readers use its published
        # snapshot, while the worker uses this method for periodic revalidation.
        with self._lock:
            if self._closed:
                return None
            epoch = self._verification_epoch
            snapshot = self._current()
            if snapshot.values['local_tagger_enabled'] != '1':
                return None
            return self._verify(snapshot, epoch, load_session=load_session)

    def metadata_fingerprint(self):
        return self.current_fingerprint(load_session=False)

    def predict_with_fingerprint(self, data):
        with self._lock:
            if self._closed:
                return None
            epoch = self._verification_epoch
            snapshot = self._current()
            tagger = self.tagger
            with tagger._gpu_arbiter.stop_scope():
                before = self._verify(snapshot, epoch, load_session=False)
                if before is None:
                    return None
                epoch = self._verification_epoch
                tags = tagger.predict(data)
                self.status = tagger.status
                if self.status != '利用可能':
                    self._invalidate_fingerprint()
                    return None
                snapshot = self._current()
                if (self.tagger is not tagger or self._verify(snapshot, epoch, load_session=False) != before
                        or self._verification_epoch != epoch):
                    return None
                return before, tags

    def try_current_fingerprint(self):
        """Revalidate the current publication without waiting for inference."""
        with self._fingerprint_lock:
            if self._closed:
                return None
            epoch, fingerprint = self._verification_epoch, self._fingerprint
            tagger = self.tagger
            receipt = tagger.receipt if tagger is not None else None

        def same_publication():
            return (epoch == self._verification_epoch and fingerprint == self._fingerprint
                    and self.tagger is tagger and (tagger is None or tagger.receipt is receipt))

        try:
            snapshot = self.preferences.snapshot()
            path = snapshot.values['local_tagger_path']
            if snapshot.values['local_tagger_enabled'] != '1':
                return None
            if not _local_folder(path):
                raise NonLocalModelPath()
            if not isinstance(receipt, dict) or receipt.get('verified') is not True:
                return None
            folder = Path(path)
            paths = [folder / 'MODEL_SOURCE.json', *(folder / name for name in FILES)]
            if receipt.get('identities') == [_identity(item) for item in paths]:
                with self._fingerprint_lock:
                    return fingerprint if same_publication() else None
            status = '検証中'
        except NonLocalModelPath:
            status = '利用不可（ローカルのフォルダを指定）'
        except FileNotFoundError:
            status = '未検出'
        except (OSError, ValueError, TypeError, RuntimeError):
            status = '読込失敗'
        with self._fingerprint_lock:
            if same_publication():
                self._invalidate_fingerprint()
                self.status = status
        return None

    def close(self):
        self._unsubscribe()
        with self._lock:
            self._closed = True
            self._invalidate_fingerprint()
            if self.tagger is not None:
                self.tagger.close()
                self.tagger = None
