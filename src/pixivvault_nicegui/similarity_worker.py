"""Single-thread, event-driven Egara similarity analysis over owned saved pages.

The caller owns page enumeration, read-only inputs, model and storage. This
module imports no DB, has no network or filesystem operations and does not
start a thread until explicitly asked. Page numbers are 1-based.
"""
from __future__ import annotations

import math
from threading import Event, RLock, Thread, current_thread
from typing import Callable

from .egara_inference import EgaraInferenceError, EgaraInvalidImage
from .local_media_adapter import MediaAmbiguous, MediaMissing, MediaUnavailable


_ERROR_CODES = frozenset(('analysis_error', 'iterator_error', 'invalid_page',
                          'model_error', 'invalid_image', 'missing_input'))
# These fixed diagnostics come from the local read-only media catalog. Unlike
# a corrupt page, a closed/obsolete catalog or missing index is a shared source
# failure and must retain the existing five-error fail-stop protection.
_SYSTEMIC_MEDIA_FAILURES = frozenset((
    'catalog closed', 'library index not yet prepared', 'obsolete library index',
    'saved snapshot changed', 'obsolete image request',
))


class SimilarityWorker:
    """One instance owns exactly one worker thread for its whole lifetime.

    A scan visits the finite page_iterator() stream once and then waits on an
    Event for idle_seconds, or for wake(). True from analyze_page means newly
    indexed, False means previously indexed. Other results are failures.

    pause()/stop() immediately invalidate the in-flight is_current callback.
    In-flight computation is cooperative; it must use this callback to avoid
    publishing output after a pause, stop or a superseding resume generation.
    """

    def __init__(self, page_iterator: Callable, analyze_page: Callable, *, idle_seconds=60):
        if not callable(page_iterator) or not callable(analyze_page):
            raise TypeError('page iterator and analyzer must be callable')
        if (type(idle_seconds) not in (int, float) or not math.isfinite(idle_seconds)
                or idle_seconds <= 0):
            raise ValueError('positive finite idle interval required')
        self._page_iterator = page_iterator
        self._analyze_page = analyze_page
        self._idle_seconds = float(idle_seconds)
        self._lock = RLock()
        self._signal = Event()
        self._thread: Thread | None = None
        self._started = False
        self._stop_requested = False
        self._phase = 'idle'
        self._generation = 0
        self._indexed = 0
        self._resumed = 0
        self._errors = 0
        self._examined = 0
        self._consecutive_failures = 0
        self._error_code: str | None = None

    def status(self) -> dict:
        """Only bounded counters and fixed status codes; no IDs or exceptions."""
        with self._lock:
            return {
                'phase': self._phase,
                'indexed': self._indexed,
                'resumed': self._resumed,
                'errors': self._errors,
                'examined': self._examined,
                'error_code': self._error_code,
            }

    def start(self) -> None:
        """Start once. Repeated calls never spawn an additional worker."""
        with self._lock:
            if self._stop_requested or self._phase == 'stopped':
                raise RuntimeError('similarity worker already stopped')
            if self._started:
                return
            self._started = True
            self._thread = Thread(target=self._run, name='pixivvault-similarity', daemon=True)
            self._thread.start()

    def wake(self) -> None:
        with self._lock:
            if not self._stop_requested and self._phase not in ('stopped', 'failed'):
                self._signal.set()

    def pause(self) -> None:
        with self._lock:
            if not self._stop_requested and self._phase in ('running', 'idle'):
                self._phase = 'paused'
                self._generation += 1
                self._signal.set()

    def resume(self) -> None:
        with self._lock:
            if not self._stop_requested and self._phase in ('paused', 'failed'):
                self._phase = 'idle'
                self._generation += 1
                self._consecutive_failures = 0
                self._signal.set()

    def stop(self, timeout=150) -> None:
        """Cooperative stop plus verified join; a timeout is NEVER success."""
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError('invalid worker stop timeout')
        with self._lock:
            thread = self._thread
            if thread is current_thread():
                raise RuntimeError('worker cannot join itself')
            self._stop_requested = True
            self._generation += 1
            self._signal.set()
            if thread is None:
                self._phase = 'stopped'
                return
        thread.join(timeout=timeout)
        if thread.is_alive():
            raise TimeoutError('similarity worker shutdown not confirmed')
        with self._lock:
            self._phase = 'stopped'

    def _is_current(self, generation: int) -> bool:
        with self._lock:
            return (not self._stop_requested and self._phase == 'running'
                    and self._generation == generation)

    def _record_failure(self, code: str, generation: int, *, page_error: bool = False) -> None:
        assert code in _ERROR_CODES
        with self._lock:
            if not self._is_current(generation):
                return
            self._errors += 1
            self._error_code = code
            if page_error:
                # A corrupt/missing *individual* input must not turn a finite
                # scan into a failed worker. The next valid page is still
                # actionable; these failures do not count toward the guard
                # against repeated, unclassified/systemic errors.
                self._consecutive_failures = 0
            else:
                self._consecutive_failures += 1
            if code == 'model_error' or (not page_error and self._consecutive_failures >= 5):
                self._phase = 'failed'
                self._generation += 1
                self._signal.set()

    def _scan_once(self, generation: int) -> None:
        pages = None
        try:
            pages = self._page_iterator()
            for entry in pages:
                if not self._is_current(generation):
                    return
                if (type(entry) not in (tuple, list) or len(entry) != 2
                        or type(entry[0]) is not str or not entry[0]
                        or type(entry[1]) is not int or entry[1] < 1):
                    self._record_failure('invalid_page', generation)
                    continue
                work_id, page = entry
                with self._lock:
                    if not self._is_current(generation):
                        return
                    self._examined += 1

                def current() -> bool:
                    return self._is_current(generation)

                try:
                    result = self._analyze_page(work_id, page, is_current=current)
                    if type(result) is not bool:
                        raise ValueError('analyzer must return boolean')
                except EgaraInvalidImage:
                    # EgaraInvalidImage subclasses EgaraInferenceError but is
                    # specific to this one image, not a broken model/backend.
                    self._record_failure('invalid_image', generation, page_error=True)
                    continue
                except (MediaMissing, MediaAmbiguous):
                    # A particular legacy image may be missing or ambiguous.
                    self._record_failure('missing_input', generation, page_error=True)
                    continue
                except MediaUnavailable as error:
                    # Image read/size/ZIP failures are page-local. A shared
                    # catalog/index failure is not: five persistent failures
                    # still park the worker instead of scanning indefinitely.
                    systemic = str(error) in _SYSTEMIC_MEDIA_FAILURES
                    self._record_failure('analysis_error' if systemic else 'missing_input',
                                         generation, page_error=not systemic)
                    continue
                except EgaraInferenceError:
                    self._record_failure('model_error', generation)
                    return
                except Exception:
                    self._record_failure('analysis_error', generation)
                    continue
                with self._lock:
                    if not self._is_current(generation):
                        return
                    if result:
                        self._indexed += 1
                    else:
                        self._resumed += 1
                    self._consecutive_failures = 0
                    self._error_code = None
        except EgaraInferenceError:
            self._record_failure('model_error', generation)
        except Exception:
            self._record_failure('iterator_error', generation)
        finally:
            # A generator page iterator can pin a read-only source for the
            # entire scan. Close it on pause/stop/failure as well as EOF.
            close = getattr(pages, 'close', None)
            if callable(close):
                try:
                    close()
                except Exception:
                    self._record_failure('iterator_error', generation)

    def _run(self) -> None:
        try:
            while True:
                with self._lock:
                    if self._stop_requested:
                        return
                    if self._phase in ('paused', 'failed'):
                        self._signal.clear()
                        can_scan = False
                    else:
                        self._phase = 'running'
                        generation = self._generation
                        self._signal.clear()
                        can_scan = True
                if not can_scan:
                    self._signal.wait()
                    continue

                self._scan_once(generation)
                with self._lock:
                    if self._stop_requested:
                        return
                    if self._phase == 'running':
                        self._phase = 'idle'
                    # An explicit wake during enumeration must not be lost;
                    # the Event is only cleared at the beginning of a scan.
                    signaled = self._signal.is_set()
                if signaled:
                    continue
                self._signal.wait(self._idle_seconds)
        finally:
            with self._lock:
                self._phase = 'stopped'
