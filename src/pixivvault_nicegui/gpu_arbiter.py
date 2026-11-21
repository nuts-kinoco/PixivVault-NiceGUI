"""In-process scheduling and cooperative release for CUDA inference."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Condition, Event, get_ident
from weakref import WeakSet


class GpuInferenceStopped(RuntimeError):
    """The GPU job was cancelled at a cooperative boundary."""


def check_gpu_stop(stop):
    if stop is not None and stop.is_set():
        raise GpuInferenceStopped('GPU inference stopped')


@dataclass
class _Operation:
    generation: int | None
    acquired: bool = False


class GpuInferenceArbiter:
    """One lease at a time. Higher priorities win among waiters, FIFO on ties.

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
        self._operation = ContextVar('gpu_inference_operation', default=None)
        self._sessions = WeakSet()

    @contextmanager
    def stop_scope(self):
        """Keep separate leases in one request within the same stop generation."""
        if self._operation.get() is not None:
            yield
            return
        with self._condition:
            operation = _Operation(None if self._stopping else self._generation)
        token = self._operation.set(operation)
        try:
            yield
            with self._condition:
                if operation.acquired and operation.generation != self._generation:
                    raise GpuInferenceStopped('GPU inference stopped')
        finally:
            self._operation.reset(token)

    @contextmanager
    def acquire(self, owner, *, priority: int = 0):
        if type(priority) is not int:
            raise ValueError('GPU job priority must be an integer')
        stop = Event()
        waiter = (priority, stop)
        with self._condition:
            if self._active_thread == get_ident():
                raise RuntimeError('GPU leases cannot be nested')
            operation = self._operation.get()
            if self._stopping or (operation is not None and operation.generation != self._generation):
                raise GpuInferenceStopped('GPU inference stopped')
            if operation is not None:
                operation.acquired = True
            self._waiting.append(waiter)
            self._condition.notify_all()
            try:
                self._condition.wait_for(lambda: stop.is_set() or (
                    self._active is None and not self._releasing and
                    min(self._waiting, key=lambda item: -item[0]) is waiter))
                check_gpu_stop(stop)
                self._active = (owner, stop)
                self._active_thread = get_ident()
            finally:
                self._waiting.remove(waiter)
                self._condition.notify_all()
        try:
            yield stop
        finally:
            with self._condition:
                self._active = None
                self._active_thread = None
                self._condition.notify_all()
                check_gpu_stop(stop)

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
            if not stop and (self._active or self._waiting or self._releasing):
                return False
            if stop and self._active_thread == get_ident():
                raise RuntimeError('cannot stop GPU inference from its lease')
            self._condition.wait_for(lambda: not self._releasing)
            self._releasing = True
            self._stopping = stop
            if stop:
                self._generation += 1
                if self._active is not None:
                    self._active[1].set()
                for _, event in self._waiting:
                    event.set()
                self._condition.notify_all()
        try:
            with self._condition:
                if stop:
                    self._condition.wait_for(lambda: self._active is None and not self._waiting)
                sessions = tuple(self._sessions)
            for owner in sessions:
                owner.release_session()
                self.forget_session(owner)
        finally:
            with self._condition:
                self._stopping = self._releasing = False
                self._condition.notify_all()
        return True


DEFAULT_GPU_ARBITER = GpuInferenceArbiter()
