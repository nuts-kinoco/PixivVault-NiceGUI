"""Read-only physical-point UIA identity check (staged checker helper).

No names, text, InvokePattern, pointer input, window activation or mutation.
Unlike System.Windows.Automation's managed point wrapper, this uses the native
POINT-by-value ABI under an explicitly scoped per-monitor-v2 thread context.
The caller must separately pin HWND/PID/birth and recheck immediately at input.
"""
from __future__ import annotations

import ctypes as C
from dataclasses import dataclass
import os
import uuid


I32 = C.c_int32
U32 = C.c_uint32
PTR = C.c_void_p
PPTR = C.POINTER(PTR)


class POINT(C.Structure):
    _fields_ = [('x', I32), ('y', I32)]


class GUID(C.Structure):
    _fields_ = [('a', U32), ('b', C.c_uint16), ('c', C.c_uint16), ('d', C.c_uint8*8)]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class RECT(C.Structure):
    _fields_ = [('left', I32), ('top', I32), ('right', I32), ('bottom', I32)]


class VARIANT(C.Structure):
    # Win64 VT_RECORD forces a 16-byte payload union. For VT_BSTR only the
    # first pointer is populated; plain pointer storage avoids a by-value
    # ctypes Union ABI. Exact shape matches the existing SDK read-only probe.
    _fields_ = [('vt', C.c_uint16), ('reserved1', C.c_uint16),
                ('reserved2', C.c_uint16), ('reserved3', C.c_uint16),
                ('bstrVal', PTR), ('unused_payload', PTR)]


@dataclass(frozen=True)
class NativeBounds:
    Left: int
    Top: int
    Right: int
    Bottom: int


@dataclass(frozen=True)
class NativeState:
    ProcessId: int
    ControlType: int
    Name: str
    IsEnabled: bool
    IsOffscreen: bool
    BoundingRectangle: NativeBounds


@dataclass(frozen=True)
class NativeElement:
    Current: NativeState
    runtime: tuple[int, ...]

    def GetRuntimeId(self):
        return self.runtime


KNOWN_TRAY_NAMES = frozenset({
    '非表示のアイコンを表示する 隠れているインジケーターを表示します',
    'Show hidden icons', '隠れているインジケーターを表示します',
    '隠れているインジケーターを表示', '隠れているアイコンを表示',
    'PixivVault', 'PixivVaultを開く', '終了',
})


class NativeHitError(RuntimeError):
    pass


def _require(condition, code):
    if not condition:
        raise NativeHitError(code)


def _checked(hr):
    _require(hr >= 0, 'native_uia_call_failed')


def _bind(dll, name, restype, *argtypes):
    function = getattr(dll, name)
    function.restype = restype
    function.argtypes = list(argtypes)
    return function


def _call(obj, slot, types=(), arguments=(), *, restype=I32):
    _require(bool(obj), 'native_uia_null_interface')
    address = C.cast(obj, C.POINTER(C.POINTER(PTR))).contents[slot]
    return C.WINFUNCTYPE(restype, PTR, *types)(address)(obj, *arguments)


def native_named_elements(hwnd, name, control_type, expected_pid):
    """Bounded exact-name lookup within an already-owned HWND, never CLR.

    Returned records carry no live COM handles. Input authorization must use
    native_point_matches again, plus the caller's HWND/PID/birth checks.
    Only the application's known icon/menu and system-chevron names are legal.
    """
    _require(os.name == 'nt' and C.sizeof(PTR) == 8, 'native_uia_requires_windows_x64')
    _require(type(hwnd) is int and hwnd > 0 and name in KNOWN_TRAY_NAMES
             and control_type in (50000, 50011)
             and type(expected_pid) is int and expected_pid > 0,
             'native_uia_lookup_not_allowed')
    _require(C.sizeof(VARIANT) == 24 and VARIANT.bstrVal.offset == 8,
             'native_uia_variant_layout')
    ole, auto, user = C.WinDLL('ole32'), C.WinDLL('oleaut32'), C.WinDLL('user32')
    initialize = _bind(ole, 'CoInitializeEx', I32, PTR, U32)
    uninitialize = _bind(ole, 'CoUninitialize', None)
    create = _bind(ole, 'CoCreateInstance', I32, C.POINTER(GUID), PTR, U32, C.POINTER(GUID), PPTR)
    set_dpi = _bind(user, 'SetThreadDpiAwarenessContext', PTR, PTR)
    get_dpi = _bind(user, 'GetThreadDpiAwarenessContext', PTR)
    equal_dpi = _bind(user, 'AreDpiAwarenessContextsEqual', I32, PTR, PTR)
    alloc = _bind(auto, 'SysAllocStringLen', PTR, PTR, U32)
    clear_variant = _bind(auto, 'VariantClear', I32, C.POINTER(VARIANT))
    dim = _bind(auto, 'SafeArrayGetDim', U32, PTR)
    size = _bind(auto, 'SafeArrayGetElemsize', U32, PTR)
    lower_bound = _bind(auto, 'SafeArrayGetLBound', I32, PTR, U32, C.POINTER(I32))
    upper_bound = _bind(auto, 'SafeArrayGetUBound', I32, PTR, U32, C.POINTER(I32))
    access = _bind(auto, 'SafeArrayAccessData', I32, PTR, PPTR)
    unaccess = _bind(auto, 'SafeArrayUnaccessData', I32, PTR)
    destroy = _bind(auto, 'SafeArrayDestroy', I32, PTR)
    references, initialized, previous_dpi = [], False, None

    def out(obj, slot, types=(), args=()):
        value = PTR()
        hr = _call(obj, slot, (*types, PPTR), (*args, C.byref(value)))
        if value.value:
            references.append(value.value)
        _checked(hr)
        _require(value.value, 'native_uia_output_null')
        return value.value

    def integer(obj, slot):
        value = I32()
        _checked(_call(obj, slot, (C.POINTER(I32),), (C.byref(value),)))
        return int(value.value)

    def runtime_id(element):
        array = PTR()
        hr = _call(element, 4, (PPTR,), (C.byref(array),))
        try:
            _checked(hr)
            _require(array.value and dim(array) == 1 and size(array) == 4,
                     'native_uia_runtime_array_invalid')
            low, high, data = I32(), I32(), PTR()
            _checked(lower_bound(array, 1, C.byref(low)))
            _checked(upper_bound(array, 1, C.byref(high)))
            count = high.value-low.value+1
            _require(0 < count <= 64, 'native_uia_runtime_array_limit')
            _checked(access(array, C.byref(data)))
            try:
                _require(data.value, 'native_uia_runtime_data_null')
                values = C.cast(data, C.POINTER(I32))
                return tuple(int(values[i]) for i in range(count))
            finally:
                _checked(unaccess(array))
        finally:
            if array.value:
                _checked(destroy(array))

    try:
        previous_dpi = set_dpi(PTR(-4))
        _require(previous_dpi and equal_dpi(get_dpi(), PTR(-4)), 'native_uia_dpi_unavailable')
        hr = initialize(None, 0)
        if hr != -2147417850:
            _checked(hr)
            initialized = True
        clsid = GUID.parse('ff48dba4-60ef-4201-aa87-54103eef594e')
        iid = GUID.parse('30cbe57d-d9d0-452a-ab13-7ac5ac4825ee')
        automation = PTR()
        hr = create(C.byref(clsid), None, 1, C.byref(iid), C.byref(automation))
        if automation.value:
            references.append(automation.value)
        _checked(hr)
        root = out(automation.value, 6, (PTR,), (hwnd,))
        _require(integer(root, 20) == expected_pid, 'native_uia_root_owner_changed')
        variant = VARIANT()
        encoded = name.encode('utf-16-le')
        pointer = alloc(None, len(encoded)//2)
        _require(pointer, 'native_uia_bstr_allocation_failed')
        variant.vt, variant.bstrVal = 8, pointer
        try:
            C.memmove(pointer, encoded, len(encoded))
            condition = out(automation.value, 23, (I32, VARIANT), (30005, variant))
        finally:
            _checked(clear_variant(C.byref(variant)))
        array = out(root, 6, (I32, PTR), (4, condition))
        length = integer(array, 3)
        _require(0 <= length <= 64, 'native_uia_exact_name_result_limit')
        result = []
        for number in range(length):
            element = out(array, 4, (I32,), (number,))
            pid, kind = integer(element, 20), integer(element, 21)
            if pid != expected_pid or kind != control_type:
                continue
            enabled, offscreen = bool(integer(element, 28)), bool(integer(element, 38))
            if not enabled or offscreen:
                continue
            bounds = RECT()
            _checked(_call(element, 43, (C.POINTER(RECT),), (C.byref(bounds),)))
            if bounds.right <= bounds.left or bounds.bottom <= bounds.top:
                continue
            result.append(NativeElement(NativeState(pid, kind, name, enabled, offscreen,
                NativeBounds(bounds.left, bounds.top, bounds.right, bounds.bottom)), runtime_id(element)))
        return tuple(result)
    finally:
        release_failed = False
        for pointer in reversed(references):
            try:
                _call(pointer, 2, restype=U32)
            except Exception:
                release_failed = True
        if initialized:
            uninitialize()
        restored = True
        if previous_dpi:
            restored = bool(set_dpi(previous_dpi) and equal_dpi(get_dpi(), previous_dpi))
        _require(not release_failed and restored, 'native_uia_cleanup_failed')


def native_point_matches(point, expected_runtime_id, expected_pid, *, max_parents=8):
    """Return exact-target/descendant hit, or False; errors never authorize input.

    IUIAutomation: slot7 ElementFromPoint, slot16 RawViewWalker.
    IUIAutomationElement: slot4 GetRuntimeId, slot20 CurrentProcessId.
    IUIAutomationTreeWalker: slot3 GetParentElement.
    ABI agrees with the already-verified WinSDK UIAutomationClient.h probe.
    """
    _require(os.name == 'nt' and C.sizeof(PTR) == 8, 'native_uia_requires_windows_x64')
    _require(isinstance(point, (tuple, list)) and len(point) == 2
             and all(type(n) is int and -(2**31) <= n < 2**31 for n in point),
             'native_uia_bad_point')
    expected = tuple(expected_runtime_id)
    _require(0 < len(expected) <= 64 and all(type(n) is int for n in expected)
             and type(expected_pid) is int and expected_pid > 0
             and type(max_parents) is int and 0 <= max_parents <= 16,
             'native_uia_bad_identity')
    ole = C.WinDLL('ole32')
    auto = C.WinDLL('oleaut32')
    user = C.WinDLL('user32', use_last_error=True)
    initialize = _bind(ole, 'CoInitializeEx', I32, PTR, U32)
    uninitialize = _bind(ole, 'CoUninitialize', None)
    create = _bind(ole, 'CoCreateInstance', I32, C.POINTER(GUID), PTR,
                   U32, C.POINTER(GUID), PPTR)
    set_dpi = _bind(user, 'SetThreadDpiAwarenessContext', PTR, PTR)
    get_dpi = _bind(user, 'GetThreadDpiAwarenessContext', PTR)
    equal_dpi = _bind(user, 'AreDpiAwarenessContextsEqual', I32, PTR, PTR)
    dim = _bind(auto, 'SafeArrayGetDim', U32, PTR)
    size = _bind(auto, 'SafeArrayGetElemsize', U32, PTR)
    lower_bound = _bind(auto, 'SafeArrayGetLBound', I32, PTR, U32, C.POINTER(I32))
    upper_bound = _bind(auto, 'SafeArrayGetUBound', I32, PTR, U32, C.POINTER(I32))
    access = _bind(auto, 'SafeArrayAccessData', I32, PTR, PPTR)
    unaccess = _bind(auto, 'SafeArrayUnaccessData', I32, PTR)
    destroy = _bind(auto, 'SafeArrayDestroy', I32, PTR)
    references = []
    com_owned = False
    previous_dpi = None

    def out(obj, slot, types=(), args=(), optional=False):
        result = PTR()
        hr = _call(obj, slot, (*types, PPTR), (*args, C.byref(result)))
        if result.value:
            references.append(result.value)
        _checked(hr)
        _require(optional or bool(result.value), 'native_uia_no_element')
        return result.value

    def runtime_id(element):
        array = PTR()
        hr = _call(element, 4, (PPTR,), (C.byref(array),))
        try:
            _checked(hr)
            _require(array.value and dim(array) == 1 and size(array) == 4,
                     'native_uia_runtime_array_invalid')
            low, high, data = I32(), I32(), PTR()
            _checked(lower_bound(array, 1, C.byref(low)))
            _checked(upper_bound(array, 1, C.byref(high)))
            count = high.value-low.value+1
            _require(0 < count <= 64, 'native_uia_runtime_array_limit')
            _checked(access(array, C.byref(data)))
            try:
                _require(data.value, 'native_uia_runtime_data_null')
                values = C.cast(data, C.POINTER(I32))
                return tuple(int(values[i]) for i in range(count))
            finally:
                _checked(unaccess(array))
        finally:
            if array.value:
                _checked(destroy(array))

    try:
        previous_dpi = set_dpi(PTR(-4))
        _require(previous_dpi and equal_dpi(get_dpi(), PTR(-4)), 'native_uia_dpi_unavailable')
        hr = initialize(None, 0)
        # CLR may have already initialized this exact thread as STA. In that
        # case keep its apartment; do not uninitialize somebody else's COM.
        if hr != -2147417850:  # RPC_E_CHANGED_MODE
            _checked(hr)
            com_owned = True
        clsid = GUID.parse('ff48dba4-60ef-4201-aa87-54103eef594e')
        iid = GUID.parse('30cbe57d-d9d0-452a-ab13-7ac5ac4825ee')
        automation = PTR()
        hr = create(C.byref(clsid), None, 1, C.byref(iid), C.byref(automation))
        if automation.value:
            references.append(automation.value)
        _checked(hr)
        walker = out(automation.value, 16)
        element = out(automation.value, 7, (POINT,), (POINT(*point),))
        seen = set()
        for depth in range(max_parents+1):
            if not element:
                return False
            pid = I32()
            _checked(_call(element, 20, (C.POINTER(I32),), (C.byref(pid),)))
            if pid.value != expected_pid:
                return False
            found = runtime_id(element)
            if found == expected:
                return True
            if found in seen or depth == max_parents:
                return False
            seen.add(found)
            element = out(walker, 3, (PTR,), (element,), optional=True)
        return False
    finally:
        release_failed = False
        for ptr in reversed(references):
            try:
                _call(ptr, 2, restype=U32)
            except Exception:
                release_failed = True
        if com_owned:
            uninitialize()
        restored = True
        if previous_dpi:
            restored = bool(set_dpi(previous_dpi) and equal_dpi(get_dpi(), previous_dpi))
        _require(not release_failed and restored, 'native_uia_cleanup_failed')
