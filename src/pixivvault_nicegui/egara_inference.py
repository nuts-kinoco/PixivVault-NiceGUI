"""EgaraNet original ONNX inference, optional CUDA/CPU; no Pixiv or DB access.

The Flet model outputs use one identity for both providers:
  model=egara_net_fp32, kind=style/composition, page=1-based.
The original egara_bb.onnx provides both outputs. We intentionally do not
substitute CLIP, DINOv2, alternate preprocessing, half precision or a renamed
model; those would silently mix incompatible vector spaces.

Numpy/ONNX Runtime are *lazy* optional inference dependencies. Importing this
module, the UI or the ordinary saved-vector reader never loads the ONNX model.
"""
from __future__ import annotations

from contextlib import nullcontext
import io
from hashlib import sha256
import math
from pathlib import Path
from threading import RLock

from .managed_downloads import _safe_path, MAX_PAGE_BYTES, MAX_PIXELS
from .image_original import MAX_ORIGINAL_BYTES
from .gpu_arbiter import DEFAULT_GPU_ARBITER, GpuInferenceStopped, check_gpu_stop

MODEL = 'egara_net_fp32'
EGARA_BB_SHA256 = 'e50f28211feaef50e80b8bece55bbfae15d3319a70fbd568476bc84c8037f5af'
STYLE = 'style'
COMPOSITION = 'composition'
BACKBONE = 'layer_norm_48'
PREFIX_TOKENS = 5
SIDE = 512
# Managed receipts cap image bytes at 20 MiB, while an explicitly admitted
# legacy folder/ZIP original (including BMP->PNG) can be up to 24 MiB.
# Only the inference input cap is widened: managed storage is not weakened.
MAX_INFERENCE_IMAGE_BYTES = max(MAX_PAGE_BYTES, MAX_ORIGINAL_BYTES)
PROVIDER_NAMES = {'cuda': 'CUDAExecutionProvider', 'cpu': 'CPUExecutionProvider'}
DEFAULT_GPU_MEM_LIMIT_BYTES = 8 * 1024**3
# Match the CPU FP32 contract rather than silently enabling TF32. The limit
# bounds ORT's arena, not total driver/VRAM usage (CUDA EP documentation).
CUDA_OPTIONS = {'use_tf32': '0', 'gpu_mem_limit': str(DEFAULT_GPU_MEM_LIMIT_BYTES),
                'arena_extend_strategy': 'kSameAsRequested',
                'do_copy_in_default_stream': '1'}


class EgaraInferenceError(RuntimeError):
    """Fixed public message; do not return paths or original content."""


class EgaraBackendUnavailable(EgaraInferenceError):
    pass


class EgaraInvalidImage(EgaraInferenceError):
    pass


def preprocess(data: bytes):
    """Bit-compatible formula with old scan_style_embeddings.preprocess.

    Decode limits are *stricter* than the original unlimited Pillow setting.
    """
    if type(data) is not bytes or not 0 < len(data) <= MAX_INFERENCE_IMAGE_BYTES:
        raise EgaraInvalidImage('解析画像のサイズが不正です。')
    import numpy as np
    from PIL import Image, UnidentifiedImageError
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            if not width or not height or width * height > MAX_PIXELS:
                raise EgaraInvalidImage('解析画像の画素数が上限を超えています。')
            image = image.convert('RGB')
            scale = SIDE / max(width, height)
            new_width = max(16, round(int(width * scale) / 16) * 16)
            new_height = max(16, round(int(height * scale) / 16) * 16)
            image = image.resize((new_width, new_height), Image.Resampling.BICUBIC)
            mean = np.array([0.485, 0.456, 0.406], np.float32)
            std = np.array([0.229, 0.224, 0.225], np.float32)
            array = (np.asarray(image, np.float32) / 255.0 - mean) / std
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        if isinstance(exc, EgaraInvalidImage):
            raise
        raise EgaraInvalidImage('解析画像を読み込めません。') from None
    return array.transpose(2, 0, 1)[None]


def normalized_float32(vector):
    """The original scanner's np.float32 L2 and +1e-12 epsilon."""
    import numpy as np
    vector = np.asarray(vector, np.float32).ravel()
    if vector.size < 1 or not np.all(np.isfinite(vector)):
        raise EgaraInferenceError('モデルの出力が不正です。')
    norm = np.linalg.norm(vector)
    if not math.isfinite(float(norm)) or float(norm) <= 0:
        raise EgaraInferenceError('モデルの出力が不正です。')
    result = vector / (norm + 1e-12)
    if not np.all(np.isfinite(result)):
        raise EgaraInferenceError('モデルの出力が不正です。')
    return result.astype(np.float32, copy=False)


def unpack_outputs(outputs, backbone_index: int):
    import numpy as np
    try:
        style = normalized_float32(outputs[0][0])
        tokens = np.asarray(outputs[backbone_index][0], dtype=np.float32)
        if tokens.ndim != 2 or tokens.shape[0] <= PREFIX_TOKENS:
            raise EgaraInferenceError('構図の出力が不正です。')
        composition = normalized_float32(tokens[PREFIX_TOKENS:, :].mean(axis=0))
    except (IndexError, TypeError, ValueError, OverflowError) as exc:
        raise EgaraInferenceError('モデルの出力が不正です。') from None
    if style.size != 1024 or composition.size != 1024:
        raise EgaraInferenceError('互換モデルのベクトル次元が違います。')
    return {STYLE: style, COMPOSITION: composition}


class EgaraInference:
    """Lazy single-session predictor. Explicit 'cuda' never silently uses CPU.

    'auto' may fall back only when CUDA cannot load/run, and records the actual
    provider; it never labels a CPU result as GPU. Individual CUDA graph nodes
    may still execute on CPU: real GPU-node proof comes from an ORT trace on a
    synthetic image during release acceptance, not merely get_providers().
    """

    def __init__(self, model_path, *, provider: str = 'auto', threads: int = 2,
                 session_factory=None, expected_sha256=EGARA_BB_SHA256,
                 gpu_mem_limit_bytes: int = DEFAULT_GPU_MEM_LIMIT_BYTES, allow_runtime_fallback: bool = True,
                 gpu_arbiter=None, gpu_priority: int = 0):
        if provider not in ('auto', 'cuda', 'cpu') or type(threads) is not int or not 1 <= threads <= 32:
            raise ValueError('invalid Egara inference provider')
        if type(gpu_mem_limit_bytes) is not int or gpu_mem_limit_bytes <= 0:
            raise ValueError('invalid CUDA arena limit')
        if type(allow_runtime_fallback) is not bool:
            raise ValueError('invalid runtime fallback setting')
        if type(gpu_priority) is not int:
            raise ValueError('GPU job priority must be an integer')
        self.gpu_mem_limit_bytes = gpu_mem_limit_bytes
        self.allow_runtime_fallback = allow_runtime_fallback
        self._gpu_arbiter = DEFAULT_GPU_ARBITER if gpu_arbiter is None else gpu_arbiter
        self._gpu_priority = gpu_priority
        self.model_path = Path(model_path)
        if not self.model_path.is_absolute():
            raise EgaraBackendUnavailable('モデルの絶対パスが必要です。')
        _safe_path(self.model_path, file=True)
        if not self.model_path.is_file() or not 0 < self.model_path.stat().st_size <= 2 * 1024**3:
            raise EgaraBackendUnavailable('モデルを検証できません。')
        self._identity = self._file_identity()
        if type(expected_sha256) is not str or len(expected_sha256) != 64:
            raise ValueError('invalid model digest')
        digest = sha256()
        with self.model_path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
                digest.update(chunk)
        if self._identity != self._file_identity() or digest.hexdigest() != expected_sha256:
            raise EgaraBackendUnavailable('互換モデルの重みを確認できません。')
        self.model_sha256 = digest.hexdigest()
        self.provider_requested = provider
        self.provider_active = None
        self.fallback_reason = None
        self.threads = threads
        self._factory = session_factory
        self._session = None
        self._input_name = None
        self._backbone_index = None
        self._lock = RLock()
        self._closed = False

    def _file_identity(self):
        _safe_path(self.model_path, file=True)
        stat = self.model_path.stat()
        return stat.st_ino, stat.st_size, stat.st_mtime_ns

    def _load(self, provider, stop):
        import onnxruntime as ort
        if provider == 'cuda' and hasattr(ort, 'preload_dlls'):
            # Official NVIDIA site-packages are installed in an optional,
            # isolated runtime. This does not download drivers or data.
            check_gpu_stop(stop)
            ort.preload_dlls(directory='')
        selected = PROVIDER_NAMES[provider]
        if selected not in ort.get_available_providers():
            raise EgaraBackendUnavailable('選択した推論バックエンドが利用できません。')
        options = ort.SessionOptions()
        options.intra_op_num_threads = self.threads
        options.inter_op_num_threads = 1
        check_gpu_stop(stop)
        if self._factory is None:
            # Explicit CPU tail is allowed for shape ops; session or one CUDA
            # kernel alone is not evidence that the whole graph ran on GPU.
            providers = ([(selected, dict(CUDA_OPTIONS, gpu_mem_limit=str(self.gpu_mem_limit_bytes))),
                          PROVIDER_NAMES['cpu']] if provider == 'cuda'
                         else [selected])
            session = ort.InferenceSession(str(self.model_path), options, providers=providers)
        else:
            session = self._factory(self.model_path, options, provider)
        if selected not in session.get_providers():
            raise EgaraBackendUnavailable('推論バックエンドがCPUへ切り替わりました。')
        if hasattr(session, 'disable_fallback'):
            session.disable_fallback()
        try:
            inputs = session.get_inputs()
            outputs = session.get_outputs()
            names = [item.name for item in outputs]
            if len(inputs) != 1 or not inputs[0].name or BACKBONE not in names or len(outputs) < 2:
                raise EgaraInferenceError('互換モデルの出力を確認できません。')
            self._input_name = inputs[0].name
            self._backbone_index = names.index(BACKBONE)
        except (AttributeError, IndexError, TypeError):
            raise EgaraInferenceError('互換モデルの入出力を確認できません。') from None
        return session

    def _job(self):
        if self.provider_requested == 'cpu':
            return nullcontext()
        return self._gpu_arbiter.acquire(self, priority=self._gpu_priority)

    def _ensure(self, stop):
        check_gpu_stop(stop)
        if self._closed:
            raise EgaraBackendUnavailable('推論は終了しています。')
        if self._session is not None:
            return
        if self._identity != self._file_identity():
            raise EgaraBackendUnavailable('モデルが変更されています。')
        first = 'cpu' if self.provider_requested == 'cpu' else 'cuda'
        self.fallback_reason = None
        try:
            self._session = self._load(first, stop)
        except GpuInferenceStopped:
            raise
        except Exception as error:
            if self.provider_requested != 'auto':
                raise EgaraBackendUnavailable('指定した推論バックエンドを利用できません。') from None
            self.fallback_reason = type(error).__name__
        if self._session is not None:
            self.provider_active = first
            if first == 'cuda':
                self._gpu_arbiter.retain_session(self)
            return
        check_gpu_stop(stop)
        try:
            self._session = self._load('cpu', stop)
            self.provider_active = 'cpu'
        except GpuInferenceStopped:
            raise
        except Exception:
            raise EgaraBackendUnavailable('CPUによる推論も利用できません。') from None

    def ensure(self):
        with self._job() as stop, self._lock:
            self._ensure(stop)
        return self

    def infer(self, data: bytes):
        return self.infer_with_meta(data)[0]

    def infer_with_meta(self, data: bytes):
        """Return the vectors and actual session provider from the same run."""
        with self._job() as stop, self._lock:
            if self._closed:
                raise EgaraBackendUnavailable('推論は終了しています。')
            if self._identity != self._file_identity():
                raise EgaraBackendUnavailable('モデルが変更されています。')
            array = preprocess(data)
            self._ensure(stop)
            check_gpu_stop(stop)
            try:
                outputs = self._session.run(None, {self._input_name: array})
            except Exception as error:
                if self.provider_active != 'cuda':
                    raise EgaraInferenceError('推論に失敗しました。') from None
                self.fallback_reason = type(error).__name__
                outputs = None
            # Leave the except block before releasing CUDA: the traceback must
            # not retain its session/allocations while creating a CPU session.
            if outputs is None:
                self.release_session()
                check_gpu_stop(stop)
                if self.provider_requested != 'auto' or not self.allow_runtime_fallback:
                    raise EgaraInferenceError('CUDAによる推論に失敗しました。')
                try:
                    self._session = self._load('cpu', stop)
                    self.provider_active = 'cpu'
                    check_gpu_stop(stop)
                    outputs = self._session.run(None, {self._input_name: array})
                except GpuInferenceStopped:
                    raise
                except Exception:
                    raise EgaraInferenceError('CPUによる再試行に失敗しました。') from None
            check_gpu_stop(stop)
            vectors = unpack_outputs(outputs, self._backbone_index)
            return ({kind: vector.astype('<f4', copy=False).tobytes() for kind, vector in vectors.items()},
                    self.provider_active)

    def release_session(self):
        """Drop the session and allow the next operation to load it again."""
        with self._lock:
            was_cuda = self.provider_active == 'cuda'
            self._session = None
            self._input_name = None
            self._backbone_index = None
            self.provider_active = None
            if was_cuda:
                self._gpu_arbiter.forget_session(self)

    def close(self):
        with self._lock:
            self._closed = True
            self.release_session()
