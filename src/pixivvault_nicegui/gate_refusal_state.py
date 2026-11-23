"""Process-local signals for physical gates. No storage or gate locks."""
from collections import Counter
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, field
import sys
from threading import Lock


@dataclass(slots=True)
class _GateActivity:
    confirmations: set = field(default_factory=set)


_activity = {}
_refusal_generations = Counter()
# Counter.update with an iterable uses the C accumulator for standard keys
# on GIL-enabled CPython. Other runtimes serialize only these memory counters.
_atomic_counter = (nullcontext() if sys.implementation.name == 'cpython'
                   and sys._is_gil_enabled() else Lock())
_route = ContextVar('gate_refusal_route', default=(None, None))
_held = ContextVar('gate_refusal_scope', default=())
_confirmation = ContextVar('gate_confirmation', default=None)


def gate_file(gate):
    if gate is not None and gate._database_identity is not None and gate._lock_identity is not None:
        return gate._database_identity, gate._lock_identity
    return None


def _for_gate(files):
    return _activity.setdefault(files, _GateActivity())


def refusal_generations():
    with _atomic_counter:
        return dict.copy(_refusal_generations)


@contextmanager
def gate_refusal_scope(gate):
    files = gate_file(gate)
    if files is None:
        yield
        return
    token = _held.set((*_held.get(), files))
    try:
        yield
    finally:
        _held.reset(token)


@contextmanager
def gate_request_route(gates):
    route = tuple(map(gate_file, gates)) if gates is not None else (None, None)
    routes = _route.set(route)
    manual = _confirmation.set(None)
    try:
        yield
    finally:
        _confirmation.reset(manual)
        _route.reset(routes)


@contextmanager
def gate_observation():
    held = _held.set(())
    try:
        with gate_request_route(None):
            yield
    finally:
        _held.reset(held)


@contextmanager
def confirmation_attempt(route, own_refusals):
    routes = _route.set(route)
    manual = _confirmation.set(own_refusals)
    try:
        yield
    finally:
        _confirmation.reset(manual)
        _route.reset(routes)


def record_gate_refusal(authority=None, *, stop=False):
    held = _held.get()
    route = _route.get()
    if stop:
        keys = (*held, *route)
    elif held:
        keys = held[-1:]
    elif authority == 'local':
        keys = route[:1]
    elif authority == 'shared':
        keys = route[1:]
    else:
        keys = route
    own = _confirmation.get()
    keys = set(keys) - {None}
    with _atomic_counter:
        _refusal_generations.update(keys)
    if own is not None:
        for files in keys:
            own[files] = own.get(files, 0) + 1


@contextmanager
def confirmation_lock(files):
    if _confirmation.get() is None or files is None:
        yield
        return
    state = _for_gate(files)
    token = object()
    state.confirmations.add(token)
    try:
        yield
    finally:
        state.confirmations.discard(token)


def confirmation_holders(files):
    state = _activity.get(files)
    return frozenset(state.confirmations) if state is not None else frozenset()
