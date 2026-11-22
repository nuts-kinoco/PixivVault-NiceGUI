"""In-process scheduling and cooperative release for CUDA inference."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Callable
from dataclasses import dataclass
import json
from math import isfinite
import os
from pathlib import Path
import subprocess
import sys
from time import time
from threading import Condition, Event, get_ident
from weakref import WeakSet


class GpuInferenceStopped(Exception):
    """The GPU job was cancelled at a cooperative boundary."""


def check_gpu_stop(stop):
    if stop is not None and stop.is_set():
        raise GpuInferenceStopped('GPU inference stopped')


_SHARED_LEASE_SCRIPT = Path(r'S:\Work\_tmp\_gpu_lease\lease.py')


def cuda_memory_pressure(error):
    message = str(error).lower()
    return isinstance(error, MemoryError) or any(marker in message for marker in (
        'out of memory', 'cuda_error_out_of_memory', 'cudaerrormemoryallocation',
        'cudnn_status_alloc_failed',
        'cublas_status_alloc_failed', 'failed to allocate memory for requested buffer of size',
        'failed to find a free memory block despite calling extend',
    )) or ('available memory of ' in message and ' is smaller than requested bytes of ' in message)


class SharedGpuLeaseError(RuntimeError):
    """The shared lease helper failed or returned an invalid response."""


class SharedGpuLeaseReleaseError(SharedGpuLeaseError):
    """A granted lease's final release could not be confirmed."""


class SharedGpuLease:
    """Use the v2 helper off the UI thread. Never log the helper's output."""

    def __init__(self, script=_SHARED_LEASE_SCRIPT):
        self._script = script

    def _call(self, *arguments, timeout):
        try:
            return subprocess.run(
                [sys._base_executable, '-B', '-X', 'utf8', str(self._script), *arguments,
                 '--owner', 'PV', '--pid', str(os.getpid())],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                encoding='utf-8', errors='replace', timeout=timeout,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        except (OSError, subprocess.SubprocessError):
            raise SharedGpuLeaseError('shared GPU lease helper unavailable') from None

    @contextmanager
    def acquire(self, cancel):
        # v2 polls every 20s. Let the helper return its token before handling
        # cancellation so a grant concurrent with stop can still be released.
        while True:
            check_gpu_stop(cancel)
            result = self._call('acquire', '--vram-gib', '6', '--minutes', '30',
                                '--wait', '1', timeout=65)
            if result.returncode != 2:
                break
        prefix = 'acquired '
        if result.returncode != 0 or not result.stdout.startswith(prefix):
            raise SharedGpuLeaseError('shared GPU lease could not be acquired')
        try:
            record = json.loads(result.stdout[len(prefix):])
            token = record['token']
            if type(token) is not str or not token:
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise SharedGpuLeaseError('invalid shared GPU lease response') from None
        try:
            check_gpu_stop(cancel)
            expires = record.get('expires')
            if type(expires) not in (int, float) or not isfinite(expires) or expires <= time():
                raise SharedGpuLeaseError('shared GPU lease deadline unavailable')
            yield expires
        finally:
            try:
                released = self._call('release', '--token', token, timeout=35)
            except SharedGpuLeaseError:
                raise SharedGpuLeaseReleaseError('shared GPU lease release unconfirmed') from None
            if (released.returncode and not
                    released.stdout.startswith(('token mismatch', 'not holder'))):
                raise SharedGpuLeaseReleaseError('shared GPU lease release unconfirmed')


def cuda_available(ort):
    """Probe an optional CUDA build before requesting an inference lease."""
    get_device = getattr(ort, 'get_device', None)
    if get_device is not None and get_device() == 'CPU':
        return False
    if hasattr(ort, 'preload_dlls'):
        ort.preload_dlls(directory='')
    return 'CUDAExecutionProvider' in ort.get_available_providers()


@dataclass
class _Operation:
    generation: int | None
    acquired: bool = False
    cancel: Event | None = None
    checkpoint: Callable[[], None] | None = None


@dataclass(eq=False)
class _Waiter:
    priority: int
    arrival: int
    stop: Event


class GpuInferenceArbiter:
    """One lease at a time, normally by priority and FIFO on ties.

    After eight other grants, the oldest waiter goes next.
    Hold a lease around CUDA session creation and execution. The yielded event
    is set by release_all(); check it before starting another indivisible step.
    A native call already in progress drains before its session can be released.
    """

    def __init__(self):
        self._condition = Condition()
        self._waiting = []
        self._active = None
        self._active_thread = None
        self._releasing = False
        self._stopping = False
        self._generation = 0
        self._grants = 0
        self._retaining = 0
        self._operation = ContextVar('gpu_inference_operation', default=None)
        self._sessions = WeakSet()
        self._stop_listeners = set()

    @contextmanager
    def stop_scope(self, *, cancel=None, checkpoint=None):
        """Keep separate leases in one request within the same stop generation."""
        if self._operation.get() is not None:
            yield
            return
        with self._condition:
            operation = _Operation(None if self._stopping else self._generation, cancel=cancel, checkpoint=checkpoint)
        token = self._operation.set(operation)
        try:
            yield
            self.check_scope()
        finally:
            self._operation.reset(token)

    def check_scope(self):
        with self._condition:
            operation = self._operation.get()
            if operation is not None:
                check_gpu_stop(operation.cancel)
            if operation is not None and operation.acquired and operation.generation != self._generation:
                raise GpuInferenceStopped('GPU inference stopped')
        if operation is not None and operation.checkpoint is not None:
            operation.checkpoint()

    @contextmanager
    def keep_sessions(self):
        """Prevent idle eviction while a CUDA session is being verified."""
        with self._condition:
            self._retaining += 1
        try:
            yield
        finally:
            with self._condition:
                self._retaining -= 1

    @contextmanager
    def acquire(self, owner, *, priority: int = 0):
        if type(priority) is not int:
            raise ValueError('GPU job priority must be an integer')
        with self._condition:
            if self._active_thread == get_ident():
                raise RuntimeError('GPU leases cannot be nested')
            operation = self._operation.get()
            stop = operation.cancel if operation is not None and operation.cancel is not None else Event()
            check_gpu_stop(stop)
            if self._stopping or (operation is not None and operation.generation != self._generation):
                raise GpuInferenceStopped('GPU inference stopped')
            waiter = _Waiter(priority, self._grants, stop)
            self._waiting.append(waiter)
            self._condition.notify_all()
            try:
                self._condition.wait_for(lambda: stop.is_set() or (
                    self._active is None and not self._releasing and
                    self._next_waiter() is waiter))
                check_gpu_stop(stop)
                self._active = (owner, stop)
                self._active_thread = get_ident()
                self._grants += 1
            finally:
                self._waiting.remove(waiter)
                self._condition.notify_all()
        completed = False
        try:
            self.check_scope()
            yield stop
            completed = True
        finally:
            with self._condition:
                self._active = None
                self._active_thread = None
                self._condition.notify_all()
                check_gpu_stop(stop)
                if completed and operation is not None:
                    operation.acquired = True

    def _next_waiter(self):
        oldest = self._waiting[0]
        if self._grants - oldest.arrival >= 8:
            return oldest
        return max(self._waiting, key=lambda waiter: waiter.priority)

    def retain_session(self, owner):
        """Remember a loaded CUDA session without keeping its owner alive."""
        with self._condition:
            if (self._active is None or self._active[0] is not owner or
                    self._active_thread != get_ident()):
                raise RuntimeError('CUDA session requires a GPU lease')
            self._sessions.add(owner)

    def forget_session(self, owner):
        with self._condition:
            self._sessions.discard(owner)

    def cancel(self, stop):
        """Cancel a single owner's scope and wake it if waiting for a lease."""
        with self._condition:
            stop.set()
            self._condition.notify_all()

    def subscribe_stop(self, callback, *, generation):
        """Run a synchronous fence callback on shared stop, outside the lease lock."""
        with self._condition:
            if self._stopping or generation != self._generation:
                raise GpuInferenceStopped('GPU inference stopped')
            self._stop_listeners.add(callback)

        def unsubscribe():
            with self._condition:
                self._stop_listeners.discard(callback)

        return unsubscribe

    def release_idle(self):
        """Release retained sessions if neither a job nor a waiter needs them."""
        return self._release(stop=False)

    def release_all(self):
        """Cancel waiters, drain the executing job and release retained sessions.

        Call from the control thread, outside any GPU lease. New acquisitions
        may resume after this call returns.
        """
        self._release(stop=True)

    def _release(self, *, stop):
        with self._condition:
            if not stop and (self._active or self._waiting or self._releasing or self._retaining):
                return False
            if stop and self._active_thread == get_ident():
                raise RuntimeError('cannot stop GPU inference from its lease')
            self._condition.wait_for(lambda: not self._releasing)
            self._releasing = True
            self._stopping = stop
            listeners = tuple(self._stop_listeners) if stop else ()
            if stop:
                self._generation += 1
                if self._active is not None:
                    self._active[1].set()
                for waiter in self._waiting:
                    waiter.stop.set()
                self._condition.notify_all()
        try:
            failures = []
            for fence in listeners:
                try:
                    fence()
                except Exception as error:
                    failures.append(error)
            with self._condition:
                if stop:
                    self._condition.wait_for(lambda: self._active is None and not self._waiting)
                sessions = tuple(self._sessions)
            for owner in sessions:
                try:
                    owner.release_session()
                except Exception as error:
                    failures.append(error)
                else:
                    self.forget_session(owner)
            if failures:
                raise ExceptionGroup('GPU session release failed', failures)
        finally:
            with self._condition:
                self._stopping = self._releasing = False
                self._condition.notify_all()
        return True


DEFAULT_GPU_ARBITER = GpuInferenceArbiter()
