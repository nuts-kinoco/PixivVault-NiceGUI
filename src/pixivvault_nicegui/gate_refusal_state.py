"""Process-local signals for physical gates. No storage or gate locks."""
from collections import Counter
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
import sys
from threading import Lock


_refusal_events = Counter()
_confirmation_events = Counter()
# Counter.update with an iterable uses the C accumulator for standard keys
# on GIL-enabled CPython. Other runtimes serialize only these memory counters.
_atomic_counter = (nullcontext() if sys.implementation.name == 'cpython'
                   and sys._is_gil_enabled() else Lock())
_route = ContextVar('gate_refusal_route', default=(None, None))
_held = ContextVar('gate_refusal_scope', default=())
_observing = ContextVar('gate_refusal_observing', default=False)


def gate_file(gate):
    # First-use startup can collect historical STOPs before the live gate
    # exists. Its collector has no physical files to record.
    database = getattr(gate, '_database_identity', None)
    lock = getattr(gate, '_lock_identity', None)
    if database is not None and lock is not None:
        return database, lock
    return None


def refusal_snapshot():
    with _atomic_counter:
        return dict.copy(_refusal_events)


def refusal_generations():
    return {files: value for (files, event), value in refusal_snapshot().items() if event == 'refused'}


def refusal_changed(files, before, after):
    refused, entered, exited = ((files, event) for event in ('refused', 'entered', 'exited'))
    return (before.get(refused, 0) != after.get(refused, 0)
            or after.get(entered, 0) != after.get(exited, 0))


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
    with gate_refusal_route(route):
        yield


@contextmanager
def gate_observation():
    observing = _observing.set(True)
    try:
        yield
    finally:
        _observing.reset(observing)


@contextmanager
def gate_refusal_route(route):
    routes = _route.set(route)
    observing = _observing.set(False)
    try:
        yield
    finally:
        _observing.reset(observing)
        _route.reset(routes)


def _refusal_keys(authority=None, *, stop=False):
    if _observing.get():
        return ()
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
    keys = set(keys) - {None}
    if not keys:
        # An absent required owner can still deny an otherwise bound route.
        keys = set(route) - {None}
    return keys


def record_gate_refusal(authority=None, *, stop=False):
    events = tuple((files, 'refused') for files in _refusal_keys(authority, stop=stop))
    with _atomic_counter:
        _refusal_events.update(events)


@contextmanager
def pending_refusal_check(authority=None):
    # A native call can yield after the OS has refused it and before Python
    # receives the error. Keep that gate ineligible until the result is known.
    keys = _refusal_keys(authority)
    entered = tuple((files, 'entered') for files in keys)
    exited = tuple((files, 'exited') for files in keys)
    with _atomic_counter:
        _refusal_events.update(entered)
    try:
        yield
    finally:
        with _atomic_counter:
            _refusal_events.update(exited)


@contextmanager
def confirmation_io(gates):
    """Register planned physical owners before any admission I/O."""
    keys = set(map(gate_file, gates)) - {None}
    started = tuple((files, True) for files in keys)
    finished = tuple((files, False) for files in keys)
    with _atomic_counter:
        _confirmation_events.update(started)
    try:
        yield
    finally:
        with _atomic_counter:
            _confirmation_events.update(finished)


def confirmation_generations():
    with _atomic_counter:
        return dict.copy(_confirmation_events)


def confirmation_overlapped(files, before, after):
    start, finish = (files, True), (files, False)
    return (before.get(start, 0) != before.get(finish, 0)
            or before.get(start, 0) != after.get(start, 0)
            or before.get(finish, 0) != after.get(finish, 0))
