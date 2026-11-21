"""Append shadow observations and embed already acquired bytes on CUDA."""
from collections import OrderedDict, deque
from hashlib import sha256
import logging
import struct
from threading import Condition, Lock, Thread, current_thread

from .egara_inference import (
    EGARA_BB_SHA256, STYLE, EgaraInference, EgaraInferenceError, EgaraInvalidImage,
)
from .gpu_arbiter import DEFAULT_GPU_ARBITER, GpuInferenceStopped
from .local_tag_view import _gpu_stop_generation
from .visual_preference import EmbeddingKey


_LOG = logging.getLogger(__name__)
_PREPROCESSING_VERSION = 'egara-style-fp32-v1'
_GPU_PRIORITY = -10  # Similarity uses 0 and local tags use 10.
_MAX_IMAGE_BYTES = 24 * 1024 * 1024
_MAX_QUEUED_BYTES = 2 * _MAX_IMAGE_BYTES
_MAX_QUEUED_IMAGES = 32
_MAX_OBSERVED_WORKS = 1024
_VARIANTS = {
    'regular_view': 'view',
    'thumb_192': 'thumb',
    'listing_thumb_fallback': 'thumb_fallback',
}


class VisualPreferenceWorker:
    """Opt-in observer. Inference begins only after an explicit start().

    The services owner can attach this observer for metadata-only recording.
    Services owns the external GPU lease and must stop the worker before purging
    the store or switching accounts. A fresh start resumes an explicit stop,
    a shared GPU release or a CUDA failure. There is no CPU fallback.
    """

    def __init__(self, store, model_path, *, model_sha=EGARA_BB_SHA256, gpu_arbiter=None):
        self.store = store
        self._model_path = model_path
        self._model_sha = model_sha
        self._arbiter = DEFAULT_GPU_ARBITER if gpu_arbiter is None else gpu_arbiter
        self._condition = Condition()
        self._lifecycle = Lock()
        self._events_lock = Lock()
        self._thread = None
        self._queue = deque()
        self._queued_bytes = 0
        self._observed = OrderedDict()
        self._phase = 'disabled'
        self._gpu_generation = None
        self._error = None
        self._indexed = self._reused = self._invalid_images = self._dropped = 0
        self._capture_gaps = self._pending_gaps = 0

    def status(self):
        with self._condition:
            if self._phase == 'running' and self._gpu_released():
                self._condition.notify_all()
            return dict(phase=self._phase, error=self._error, indexed=self._indexed,
                        reused=self._reused, invalid_images=self._invalid_images,
                        dropped=self._dropped, pending=len(self._queue),
                        capture_gaps=self._capture_gaps)

    def start(self):
        with self._lifecycle:
            generation = _gpu_stop_generation(self._arbiter)
            if generation is None:
                raise RuntimeError('GPU sessions are being released')
            with self._condition:
                if self._phase == 'running' and self._gpu_generation == generation:
                    return
                if self._phase == 'stopping':
                    raise RuntimeError('visual preference worker is stopping')
                thread = self._thread
                self._condition.notify_all()
            if thread is not None:
                self._join(thread, 30)
            with self._condition:
                self._gpu_generation = generation
                self._error = None
                self._phase = 'running'
                self._thread = Thread(target=self._run, name='pv-visual-preference', daemon=True)
                self._thread.start()

    def stop(self, timeout=30):
        with self._lifecycle:
            with self._condition:
                thread = self._thread
                if thread is None:
                    return
                if thread is current_thread():
                    raise RuntimeError('visual preference worker cannot join itself')
                self._phase = 'stopping'
                self._condition.notify_all()
            self._invalidate('off')
            self._join(thread, timeout)
            with self._condition:
                self._thread = None
                self._phase = 'stopped'

    @staticmethod
    def _join(thread, timeout):
        thread.join(timeout)
        if thread.is_alive():
            raise TimeoutError('visual preference worker shutdown not confirmed')

    def _gpu_released(self):
        return self._gpu_generation != _gpu_stop_generation(self._arbiter)

    def _check_running(self, *, raise_on_release=True):
        with self._condition:
            if self._gpu_released():
                if raise_on_release:
                    raise GpuInferenceStopped()
                return False
            return self._phase == 'running'

    def _record_event(self, kind, snapshot, *, invalidate=False):
        # Called on the learning FIFO. A failed append creates an explicit gap
        # on the next successful write, and never rolls back the judgment.
        with self._events_lock:
            try:
                if invalidate:
                    self.store.invalidate(kind)
                payload = snapshot()
                if self._pending_gaps:
                    self.store.append_event('capture_gap', {'count': self._pending_gaps})
                    self._pending_gaps = 0
                self.store.append_event(kind, payload)
            except Exception:
                self._pending_gaps += 1
                with self._condition:
                    self._capture_gaps += 1
                raise

    def judgment_changed(self, kind, snapshot):
        self._record_event(kind, snapshot, invalidate=True)

    def ranking_finalized(self, snapshot):
        def payload():
            candidates = snapshot()
            with self._condition:
                for row in candidates:
                    variant = self._observed.get((row['work_id'], 0))
                    # No observation means unknown. This tracks acquisition,
                    # since a cache entry can be evicted before exposure.
                    row['image_available'] = True if variant is not None else None
                    row['image_variant'] = variant
            return {'candidates': candidates}

        self._record_event('ranking', payload)

    def candidate_exposed(self, snapshot, actual_variant):
        self._record_event('exposure', lambda: dict(snapshot(), image_available=True,
                           image_variant=_VARIANTS.get(actual_variant)))

    def submit_image(self, work_id, page, image):
        """Capture the delivered bytes, using the returned variant as lineage."""
        variant = _VARIANTS.get(image.variant)
        data = image.data
        if variant is None or type(data) is not bytes or not 0 < len(data) <= _MAX_IMAGE_BYTES:
            return False
        with self._condition:
            self._observed[work_id, page] = variant
            self._observed.move_to_end((work_id, page))
            if len(self._observed) > _MAX_OBSERVED_WORKS:
                self._observed.popitem(last=False)
            if self._phase != 'running':
                return False
            if self._gpu_released():
                self._condition.notify_all()
                return False
            if (len(self._queue) >= _MAX_QUEUED_IMAGES
                    or self._queued_bytes + len(data) > _MAX_QUEUED_BYTES):
                self._dropped += 1
                return False
            self._queue.append((work_id, page, variant, data))
            self._queued_bytes += len(data)
            self._condition.notify()
            return True

    def _invalidate(self, reason):
        try:
            self.store.invalidate(reason)
        except Exception:
            _LOG.warning('visual preference generation invalidation failed')

    def _run(self):
        predictor = None
        phase, error = 'stopped', None
        try:
            while True:
                with self._condition:
                    # The shared arbiter wakes leases, while an idle worker
                    # owns no lease. Check the epoch even without submissions.
                    while self._phase == 'running' and not self._queue and not self._gpu_released():
                        self._condition.wait(timeout=.5)
                    if self._phase != 'running':
                        return
                    if self._gpu_released():
                        raise GpuInferenceStopped()
                    work_id, page, variant, data = self._queue.popleft()
                    self._queued_bytes -= len(data)
                epoch = self.store.capture_embedding_epoch()
                key = EmbeddingKey(work_id, page, variant, sha256(data).hexdigest(),
                                   self._model_sha, _PREPROCESSING_VERSION)
                if self.store.has_candidate_embedding(key):
                    with self._condition:
                        self._reused += 1
                    continue
                try:
                    with self._arbiter.stop_scope():
                        if predictor is None:
                            predictor = EgaraInference(self._model_path, provider='cuda',
                                expected_sha256=self._model_sha, allow_runtime_fallback=False,
                                gpu_arbiter=self._arbiter, gpu_priority=_GPU_PRIORITY)
                        if not self._check_running():
                            return
                        vectors, provider = predictor.infer_with_meta(data)
                        if provider != 'cuda' or len(vectors.get(STYLE, b'')) != 4096:
                            raise EgaraInferenceError('invalid CUDA style output')
                        vector = struct.unpack('<1024f', vectors[STYLE])
                except EgaraInvalidImage:
                    with self._condition:
                        self._invalid_images += 1
                    continue
                if not self._check_running():
                    return
                if self.store.put_candidate_embedding(epoch, key, vector,
                        is_current=lambda: self._check_running(raise_on_release=False)):
                    with self._condition:
                        self._indexed += 1
        except GpuInferenceStopped:
            phase = 'paused'
            self._invalidate('off')
        except EgaraInferenceError:
            phase, error = 'failed', 'cuda_failure'
            self._invalidate('gpu_failure')
            _LOG.warning('visual preference CUDA inference failed')
        except Exception:
            phase, error = 'failed', 'worker_failure'
            self._invalidate('gpu_failure')
            _LOG.warning('visual preference worker failed')
        finally:
            try:
                if predictor is not None:
                    predictor.close()
            finally:
                with self._condition:
                    self._dropped += len(self._queue)
                    self._queue.clear()
                    self._queued_bytes = 0
                    if self._phase != 'stopping':
                        self._phase, self._error = phase, error
