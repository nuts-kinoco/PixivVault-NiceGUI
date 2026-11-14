"""CurrentUser DPAPI and an independently published autounlock record.

Never stores plaintext key material or writes a Vault generation.
"""
import base64
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from .managed_downloads import _safe_path, _write, _read_bounded, _fsync_dir, _json_unique
from .fs_paths import native_io_path

class AutoUnlockError(RuntimeError):
    pass

def _dpapi(data,entropy,*,decrypt=False):
    if os.name!='nt':raise AutoUnlockError('dpapi-unavailable')
    if not isinstance(data,bytes) or not data or len(data)>32768:
        raise AutoUnlockError('dpapi-input-rejected')
    class Blob(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
    source_buffer=ctypes.create_string_buffer(data)
    entropy_buffer=ctypes.create_string_buffer(entropy)
    source=Blob(len(data),ctypes.cast(source_buffer,ctypes.POINTER(ctypes.c_ubyte)))
    extra=Blob(len(entropy),ctypes.cast(entropy_buffer,ctypes.POINTER(ctypes.c_ubyte)))
    output=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    free=kernel.LocalFree;free.argtypes=[ctypes.c_void_p];free.restype=ctypes.c_void_p
    fn=crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.POINTER(Blob),
                 ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    fn.restype=wintypes.BOOL
    try:
        # UI forbidden; LOCAL_MACHINE is deliberately absent (CurrentUser).
        if not fn(ctypes.byref(source),None,ctypes.byref(extra),None,None,1,ctypes.byref(output)):
            raise AutoUnlockError('dpapi-unprotect-failed' if decrypt else 'dpapi-protect-failed')
        return ctypes.string_at(output.data,output.size)
    finally:
        ctypes.memset(source_buffer,0,len(source_buffer))
        if output.data:
            ctypes.memset(output.data,0,output.size)
            free(ctypes.cast(output.data,ctypes.c_void_p))

class AutoUnlockRecord:
    """An OFF record overrides legacy ON; missing and invalid are distinct."""
    def __init__(self,root):
        self.root=Path(root).absolute()
        self.path=self.root/'autounlock-currentuser.json'
        self.entropy=hashlib.sha256(b'PixivVault-autounlock-currentuser-v1\0'+str(self.root).casefold().encode()).digest()

    @staticmethod
    def validate(state,vault):
        if (not isinstance(state,dict) or set(state)!={'purpose','vault','enabled','inherited','key'}
                or state['purpose']!='pixivvault-autounlock-currentuser-v1'
                or state['vault']!=vault or type(state['enabled']) is not bool
                or type(state['inherited']) is not bool or type(state['key']) is not str):
            raise AutoUnlockError('autounlock-record-invalid')
        try:key=base64.b64decode(state['key'],validate=True)
        except Exception:raise AutoUnlockError('autounlock-record-invalid') from None
        if (len(key)!=32 if state['enabled'] else key!=b''):
            raise AutoUnlockError('autounlock-record-invalid')
        return state

    def read(self,vault):
        _safe_path(self.path,file=True)
        if not native_io_path(self.path).exists():return None
        try:
            envelope=json.loads(_read_bounded(self.path,65536),object_pairs_hook=_json_unique)
            if set(envelope)!={'version','protected'} or envelope['version']!=1:raise ValueError()
            raw=_dpapi(base64.b64decode(envelope['protected'],validate=True),self.entropy,decrypt=True)
            return self.validate(json.loads(raw,object_pairs_hook=_json_unique),vault)
        except AutoUnlockError:raise
        except Exception:raise AutoUnlockError('autounlock-read-unconfirmed') from None

    def write(self,vault,*,enabled,key=None,inherited=False):
        state={'purpose':'pixivvault-autounlock-currentuser-v1','vault':vault,
               'enabled':enabled,'inherited':inherited,
               'key':base64.b64encode(key).decode('ascii') if key is not None else ''}
        self.validate(state,vault)
        raw=json.dumps(state,separators=(',',':')).encode()
        protected=_dpapi(raw,self.entropy)
        packet=json.dumps({'version':1,'protected':base64.b64encode(protected).decode('ascii')}).encode()
        temporary=self.root/('autounlock-currentuser-'+uuid4().hex+'.next')
        try:
            _write(temporary,packet)
            _safe_path(self.path,file=True)
            os.replace(native_io_path(temporary),native_io_path(self.path));_fsync_dir(self.root)
        except Exception:
            # A post-replace failure may have committed. Only readback can
            # confirm that exact desired state; never retry the write.
            try:
                if self.read(vault)==state:return state
            except Exception:pass
            raise AutoUnlockError('autounlock-write-unconfirmed') from None
        if self.read(vault)!=state:raise AutoUnlockError('autounlock-write-unconfirmed')
        return state
