"""Explicit Windows desktop actions, injectable without starting native apps.

Import and construction perform no registry writes, launch, tray creation or
window operations. The shell must supply an explicit native capability.
"""
from __future__ import annotations

from pathlib import Path
import hashlib
import json
import os
import re
from threading import Lock, RLock
from .fs_paths import native_io_path, logical_path


class DesktopUnavailable(RuntimeError):
    pass


class DesktopActionRejected(RuntimeError):
    pass


def protocol_command(executable, script=None):
    """Original HKCU pixivvault command, using the current packaged/development entry."""
    parts=[str(executable)] + ([str(script)] if script is not None else [])
    if any(not value or any(c in value for c in ('"','\r','\n','\0')) for value in parts):
        raise DesktopActionRejected('起動コマンドが不正です。')
    return ' '.join('"'+value+'"' for value in parts)+' "%1"'


class WindowsProtocolRegistration:
    """Own only three URI values; persist the restore point before changing them.

    The receipt is separate from the URI tree. Its phase survives a process
    restart, and an external change never authorizes deletion of another tree.
    """
    KEY = r'Software\Classes\pixivvault'
    RECEIPT_ROOT = r'Software\PixivVault-NiceGUI\ProtocolRegistrations'
    _FORMAT = 'pixivvault-nicegui-uri-restore-v1'
    _STATE = 'State'
    _MAX_RECEIPT = 128 * 1024
    _lock = RLock()

    def __init__(self, command, *, registry=None):
        self.command = command
        self.registry = registry
        self._validate_command(command)

    @staticmethod
    def _validate_command(command):
        if (type(command) is not str or not command or len(command) > 32767
                or any(c in command for c in ('\0', '\r', '\n'))):
            raise DesktopActionRejected('起動コマンドが不正です。')

    def _registry(self):
        if self.registry is None:
            if os.name != 'nt':
                raise DesktopUnavailable('Windowsでのみ利用できます。')
            import winreg
            return winreg
        return self.registry

    def _paths(self):
        match = re.fullmatch(r'Software\\Classes\\([a-z][a-z0-9+.-]{0,62})',
                             self.KEY, flags=re.IGNORECASE | re.ASCII)
        if match is None:
            raise DesktopActionRejected('登録先が不正です。')
        keys = tuple(self.KEY + suffix for suffix in ('', r'\shell', r'\shell\open',
                                                      r'\shell\open\command'))
        parents = (r'Software\PixivVault-NiceGUI', self.RECEIPT_ROOT)
        return keys, self.RECEIPT_ROOT + '\\' + match[1].lower(), parents

    @staticmethod
    def _exists(registry, path):
        try:
            with registry.OpenKey(registry.HKEY_CURRENT_USER, path):
                return True
        except FileNotFoundError:
            return False

    @staticmethod
    def _pack(value, kind):
        if type(kind) is not int or not 0 <= kind <= 0xffffffff:
            raise DesktopActionRejected('元の登録値を保存できません。')
        if type(value) is str:
            return [kind, 'text', value]
        if type(value) is bytes:
            return [kind, 'bytes', value.hex()]
        if type(value) is int and 0 <= value < 2**64:
            return [kind, 'number', value]
        if type(value) is list and all(type(v) is str for v in value):
            return [kind, 'texts', list(value)]
        if value is None:
            return [kind, 'none', None]
        raise DesktopActionRejected('元の登録値を保存できません。')

    @classmethod
    def _unpack(cls, encoded):
        if type(encoded) is not list or len(encoded) != 3:
            raise DesktopActionRejected('復元情報が不正です。')
        kind, tag, data = encoded
        if tag == 'bytes' and type(data) is str:
            value = bytes.fromhex(data)
        elif tag == 'text' and type(data) is str:
            value = data
        elif tag == 'number' and type(data) is int:
            value = data
        elif tag == 'texts' and type(data) is list and all(type(v) is str for v in data):
            value = list(data)
        elif tag == 'none' and data is None:
            value = None
        else:
            raise DesktopActionRejected('復元情報が不正です。')
        if cls._pack(value, kind) != encoded:
            raise DesktopActionRejected('復元情報が不正です。')
        return value, kind

    def _slots(self):
        return ((self.KEY, ''), (self.KEY, 'URL Protocol'),
                (self.KEY + r'\shell\open\command', ''))

    def _value(self, registry, slot):
        path, name = self._slots()[slot]
        try:
            with registry.OpenKey(registry.HKEY_CURRENT_USER, path) as key:
                value, kind = registry.QueryValueEx(key, name)
                return self._pack(value, kind)
        except FileNotFoundError:
            return None

    def _values(self, registry):
        return [self._value(registry, slot) for slot in range(3)]

    @staticmethod
    def _installed(registry, command):
        return [[registry.REG_SZ, 'text', value]
                for value in ('URL:PixivVault Protocol', '', command)]

    @classmethod
    def _serialize(cls, record):
        payload = json.dumps(record, sort_keys=True, ensure_ascii=True,
                             separators=(',', ':'), allow_nan=False)
        raw = json.dumps({'payload': record,
                          'sha256': hashlib.sha256(payload.encode('ascii')).hexdigest()},
                         sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False)
        if len(raw) > cls._MAX_RECEIPT:
            raise DesktopActionRejected('復元情報が大きすぎるため登録を変更しません。')
        return raw

    def _load(self, registry):
        _keys, path, _parents = self._paths()
        try:
            opened = registry.OpenKey(registry.HKEY_CURRENT_USER, path)
        except FileNotFoundError:
            return None, None
        with opened as key:
            # An existing unrelated value/key is never adopted as our receipt.
            count_keys, count_values, _stamp = registry.QueryInfoKey(key)
            if (count_keys, count_values) != (0, 1):
                raise DesktopActionRejected('復元情報の保存先を確認できません。')
            try:
                raw, kind = registry.QueryValueEx(key, self._STATE)
            except FileNotFoundError:
                raise DesktopActionRejected('復元情報の保存先を確認できません。') from None
        try:
            if kind != registry.REG_SZ or type(raw) is not str or len(raw) > self._MAX_RECEIPT:
                raise ValueError()
            envelope = json.loads(raw)
            if type(envelope) is not dict or set(envelope) != {'payload', 'sha256'}:
                raise ValueError()
            record = envelope['payload']
            required = {'format', 'key', 'owner', 'phase', 'original_keys', 'original_values',
                        'before', 'after', 'receipt_parents'}
            if type(record) is not dict or set(record) != required:
                raise ValueError()
            if (record['format'] != self._FORMAT or record['key'] != self.KEY.casefold()
                    or record['phase'] not in ('installing', 'installed', 'restoring')):
                raise ValueError()
            self._validate_command(record['owner'])
            for name, size in (('original_keys', 4), ('receipt_parents', 2)):
                flags = record[name]
                if type(flags) is not list or len(flags) != size or any(type(v) is not bool for v in flags):
                    raise ValueError()
            for name in ('original_values', 'before', 'after'):
                values = record[name]
                if type(values) is not list or len(values) != 3:
                    raise ValueError()
                for value in values:
                    if value is not None:
                        self._unpack(value)
            flags = record['original_keys']
            if any(flags[i] and not flags[i - 1] for i in range(1, 4)):
                raise ValueError()
            if any(record['original_values'][i] is not None and not flags[0 if i < 2 else 3]
                   for i in range(3)):
                raise ValueError()
            if record['after'] != self._installed(registry, record['owner']):
                raise ValueError()
            if self._serialize(record) != raw:
                raise ValueError()
            return record, raw
        except Exception:
            raise DesktopActionRejected('復元情報が不正です。既存登録は変更しません。') from None

    @staticmethod
    def _prune_empty(registry, path):
        try:
            with registry.OpenKey(registry.HKEY_CURRENT_USER, path) as key:
                children, values, _stamp = registry.QueryInfoKey(key)
            if children == values == 0:
                registry.DeleteKey(registry.HKEY_CURRENT_USER, path)
        except FileNotFoundError:
            pass

    def _save(self, registry, record, expected):
        raw = self._serialize(record)
        _current, observed = self._load(registry)
        if observed != expected:
            raise DesktopActionRejected('復元情報が別の操作で変更されました。')
        _keys, path, parents = self._paths()
        if expected is None:
            try:
                with registry.CreateKeyEx(registry.HKEY_CURRENT_USER, path, 0,
                                          registry.KEY_READ | registry.KEY_WRITE) as key:
                    children, values, _stamp = registry.QueryInfoKey(key)
                    if children or values:
                        raise DesktopActionRejected('復元情報の保存先が変更されました。')
                    registry.SetValueEx(key, self._STATE, 0, registry.REG_SZ, raw)
            except Exception:
                # Source values have not changed before this first durable save.
                # Remove only empty keys created for this attempted receipt.
                try:
                    self._prune_empty(registry, path)
                    for parent, existed in reversed(tuple(zip(parents, record['receipt_parents']))):
                        if not existed:
                            self._prune_empty(registry, parent)
                except OSError:
                    pass
                raise
        else:
            with registry.OpenKey(registry.HKEY_CURRENT_USER, path, 0,
                                  registry.KEY_READ | registry.KEY_WRITE) as key:
                if registry.QueryValueEx(key, self._STATE) != (expected, registry.REG_SZ):
                    raise DesktopActionRejected('復元情報が別の操作で変更されました。')
                registry.SetValueEx(key, self._STATE, 0, registry.REG_SZ, raw)
        _checked, observed = self._load(registry)
        if observed != raw:
            raise DesktopActionRejected('復元情報の保存を確認できません。')
        return raw

    def _assert_values(self, registry, allowed):
        current = self._values(registry)
        if any(not any(value == candidate[i] for candidate in allowed)
               for i, value in enumerate(current)):
            raise DesktopActionRejected('別の起動設定に変更されています。既存登録は変更しません。')
        return current

    def _apply(self, registry, desired, allowed, *, restoring=False):
        self._assert_values(registry, allowed)
        for slot in ((2, 1, 0) if restoring else (0, 1, 2)):
            current = self._value(registry, slot)
            if current == desired[slot]:
                continue
            if not any(current == candidate[slot] for candidate in allowed):
                raise DesktopActionRejected('別の起動設定に変更されています。')
            path, name = self._slots()[slot]
            if desired[slot] is None:
                try:
                    with registry.OpenKey(registry.HKEY_CURRENT_USER, path, 0,
                                          registry.KEY_READ | registry.KEY_WRITE) as key:
                        registry.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
            else:
                value, kind = self._unpack(desired[slot])
                with registry.CreateKeyEx(registry.HKEY_CURRENT_USER, path, 0,
                                          registry.KEY_READ | registry.KEY_WRITE) as key:
                    registry.SetValueEx(key, name, 0, kind, value)
            if self._value(registry, slot) != desired[slot]:
                raise DesktopActionRejected('登録値の変更結果を確認できません。')
        self._assert_values(registry, (desired,))

    def _new_record(self, registry):
        keys, _path, parents = self._paths()
        existed = [self._exists(registry, path) for path in keys]
        values = self._values(registry)
        parent_flags = [self._exists(registry, path) for path in parents]
        if existed != [self._exists(registry, path) for path in keys] or values != self._values(registry):
            raise DesktopActionRejected('起動設定が変更されました。もう一度確認してください。')
        return dict(format=self._FORMAT, key=self.KEY.casefold(), owner=self.command,
                    phase='installing', original_keys=existed, original_values=values,
                    before=values, after=self._installed(registry, self.command),
                    receipt_parents=parent_flags)

    def _finish_restore(self, registry, record, raw):
        desired = record['original_values']
        self._apply(registry, desired, (record['before'], record['after'], desired), restoring=True)
        keys, path, parents = self._paths()
        # Original empty keys must still exist. Created keys are removed only
        # if no later application added a value or a child to them.
        for key_path, existed in zip(keys, record['original_keys']):
            if existed and not self._exists(registry, key_path):
                with registry.CreateKeyEx(registry.HKEY_CURRENT_USER, key_path, 0,
                                          registry.KEY_READ | registry.KEY_WRITE):
                    pass
        for key_path, existed in reversed(tuple(zip(keys, record['original_keys']))):
            if not existed:
                self._prune_empty(registry, key_path)
        self._assert_values(registry, (desired,))
        _current, observed = self._load(registry)
        if observed != raw:
            raise DesktopActionRejected('復元情報が別の操作で変更されました。')
        # The validated receipt contains our single value and no children.
        # Delete it only after source restoration and pruning have completed.
        registry.DeleteKey(registry.HKEY_CURRENT_USER, path)
        for parent, existed in reversed(tuple(zip(parents, record['receipt_parents']))):
            if not existed:
                try:
                    self._prune_empty(registry, parent)
                except OSError:
                    # An empty app-side parent is not an active URI setting.
                    pass

    def is_registered(self):
        registry = self._registry()
        with self._lock:
            try:
                record, _raw = self._load(registry)
                return (record is not None and record['phase'] == 'installed'
                        and record['owner'] == self.command
                        and self._values(registry) == record['after'])
            except DesktopActionRejected:
                raise
            except Exception:
                raise DesktopActionRejected('自動起動の登録状態を確認できません。') from None

    def register(self):
        registry = self._registry()
        with self._lock:
            try:
                record, raw = self._load(registry)
                if record is not None and record['phase'] == 'restoring':
                    if record['owner'] != self.command:
                        raise DesktopActionRejected('別の起動設定の解除が未完了です。')
                    self._finish_restore(registry, record, raw)
                    record, raw = None, None
                if record is None:
                    record = self._new_record(registry)
                    raw = self._save(registry, record, None)
                elif record['phase'] == 'installed':
                    self._assert_values(registry, (record['after'],))
                    if record['owner'] == self.command:
                        return
                    # An owned upgrade changes only the installed command;
                    # its first foreign restore point remains unchanged.
                    record = dict(record, owner=self.command, phase='installing',
                                  before=record['after'], after=self._installed(registry, self.command))
                    raw = self._save(registry, record, raw)
                elif record['owner'] != self.command:
                    raise DesktopActionRejected('別の起動設定の登録が未完了です。')
                self._apply(registry, record['after'], (record['before'], record['after']))
                record = dict(record, phase='installed')
                self._save(registry, record, raw)
            except DesktopActionRejected:
                raise
            except Exception:
                raise DesktopActionRejected('自動起動の登録が完了していません。同じ起動設定で再試行するか解除してください。') from None

    def unregister(self):
        registry = self._registry()
        with self._lock:
            try:
                record, raw = self._load(registry)
                if record is None:
                    return  # Matching text alone is not evidence of ownership.
                if record['owner'] != self.command:
                    raise DesktopActionRejected('別の起動設定が登録されています。')
                if record['phase'] == 'installed':
                    restoring_from = self._assert_values(registry, (record['after'],))
                elif record['phase'] == 'installing':
                    restoring_from = self._assert_values(registry, (record['before'], record['after']))
                if record['phase'] != 'restoring':
                    # The restore phase may resume only from its verified
                    # starting values, not from an earlier install/upgrade's
                    # predecessor command. Keep the first original restore
                    # point unchanged in original_values/original_keys.
                    record = dict(record, phase='restoring', before=restoring_from)
                    raw = self._save(registry, record, raw)
                self._finish_restore(registry, record, raw)
            except DesktopActionRejected:
                raise
            except Exception:
                raise DesktopActionRejected('元の登録への復元が完了していません。同じ起動設定でもう一度解除してください。') from None


class WindowsLauncher:
    def open_default(self,target):
        if os.name!='nt': raise DesktopUnavailable('Windowsでのみ利用できます。')
        os.startfile(str(target))

    def open_with(self,executable,target):
        if os.name!='nt': raise DesktopUnavailable('Windowsでのみ利用できます。')
        import subprocess
        subprocess.Popen([str(executable),str(target)],shell=False)


class DesktopIntegration:
    """Only saved targets in explicitly configured roots can be launched."""
    def __init__(self, *, allowed_roots=(), launcher=None, protocol=None, environment=None, exists=None):
        roots=tuple(native_io_path(p) for p in allowed_roots)
        if any(node.is_symlink() or (node.exists() and getattr(node.lstat(),'st_file_attributes',0)&0x400)
               for root in roots for node in (root,*root.parents)):
            raise DesktopActionRejected('保存先にリンクは使用できません。')
        self.allowed_roots=tuple(logical_path(root.resolve()) for root in roots)
        self.launcher=launcher
        self.protocol=protocol
        self.environment=dict(os.environ if environment is None else environment)
        self.exists=exists or (lambda p:Path(p).is_file())

    def set_uri_registration(self,enabled,*,confirmed=False):
        if type(enabled) is not bool or confirmed is not True:
            raise DesktopActionRejected('自動起動の変更には明示確認が必要です。')
        if self.protocol is None:
            raise DesktopUnavailable('自動起動の接続がありません。')
        if enabled: self.protocol.register()
        else: self.protocol.unregister()
        if self.protocol.is_registered()!=enabled:
            raise DesktopActionRejected('自動起動の変更結果を確認できません。')

    def _honeyview(self):
        # Same lookup priority as original library_tab.py:848-851.
        candidates=[('ProgramFiles','Honeyview/Honeyview.exe'),('ProgramFiles(x86)','Honeyview/Honeyview.exe'),('LOCALAPPDATA','Programs/Honeyview/Honeyview.exe')]
        for key,relative in candidates:
            root=self.environment.get(key)
            if root:
                path=Path(root)/relative
                if self.exists(path): return path
        return None

    def open_saved(self,target):
        if self.launcher is None: raise DesktopUnavailable('外部ビューアの接続がありません。')
        original=native_io_path(target)
        if any(node.is_symlink() or (node.exists() and getattr(node.lstat(),'st_file_attributes',0)&0x400) for node in (original,*original.parents)):
            raise DesktopActionRejected('保存場所にリンクは使用できません。')
        target=logical_path(original.resolve())
        if not any(target==root or target.is_relative_to(root) for root in self.allowed_roots):
            raise DesktopActionRejected('保存先の範囲外です。')
        # Compare canonical logical paths, but perform filesystem checks with
        # native spelling so a long target cannot be mistaken for a missing one.
        io_target=native_io_path(target)
        if not io_target.exists(): raise DesktopActionRejected('保存場所が見つかりませんでした。')
        if not io_target.is_dir() and target.suffix.lower() not in {'.zip','.jpg','.jpeg','.png','.gif','.webp','.bmp','.tif','.tiff'}:
            raise DesktopActionRejected('対応する保存画像ではありません。')
        viewer=self._honeyview() if io_target.is_file() and target.suffix.lower()=='.zip' else None
        try:
            if viewer is not None: self.launcher.open_with(viewer,target)
            else: self.launcher.open_default(target)
        except Exception:
            raise DesktopActionRejected('ファイルを開けませんでした。') from None


class TrayController:
    """Keep the restore route until the native owner actually reports closed."""
    def __init__(self,preferences,*,show,hide,request_shutdown,factory):
        self.preferences=preferences
        self.show=show
        self.hide=hide
        self.request_shutdown=request_shutdown
        self.factory=factory
        self.icon=None
        self.closed=False
        self.exit_pending=False
        self._state_lock=RLock()
        self._close_lock=Lock()

    def start(self):
        with self._state_lock:
            if self.closed: raise DesktopUnavailable('トレイは終了しています。')
            if self.icon is None:
                self.icon=self.factory(self.restore,self.exit_from_tray)
            return self.icon is not None

    def minimize(self):
        with self._state_lock:
            enabled=(not self.closed and self.icon is not None
                and self.preferences.get_setting('minimize_to_tray','1')=='1')
        if enabled:
            self.hide()
            return True
        return False

    def restore(self):
        with self._state_lock:
            enabled=not self.closed
        if enabled: self.show()

    def close_requested(self, *, confirmed=False):
        with self._state_lock:
            if self.closed: return True
        if not confirmed: return False
        return self.exit_from_tray()

    def exit_from_tray(self):
        with self._state_lock:
            if self.closed or self.exit_pending: return False
            self.exit_pending=True
        try:
            accepted=self.request_shutdown() is True
        except Exception:
            accepted=False
        if not accepted:
            self.exit_request_unconfirmed()
        return accepted

    def exit_request_unconfirmed(self):
        with self._state_lock:
            if not self.closed:self.exit_pending=False

    def complete_close(self):
        # Native "closed" and application resource cleanup may converge here.
        # Retain a failed icon owner so a later cleanup can retry its stop.
        with self._close_lock:
            with self._state_lock:
                self.closed=True
                self.exit_pending=False
                icon=self.icon
            if icon is not None:
                icon.stop()
                with self._state_lock:
                    if self.icon is icon:self.icon=None


class PystrayFactory:
    """Optional runtime dependency; import failure is surfaced, never installed."""
    def __call__(self,on_show,on_exit):
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError:
            raise DesktopUnavailable('タスクトレイの実行依存がありません。') from None
        image=Image.new('RGB',(64,64),(52,152,219))
        ImageDraw.Draw(image).rectangle((16,16,48,48),fill=(41,128,185))
        icon=pystray.Icon('PixivVault',image,'PixivVault',pystray.Menu(
            pystray.MenuItem('PixivVaultを開く',lambda *_:on_show(),default=True),
            pystray.MenuItem('終了',lambda *_:on_exit()),
        ))
        icon.run_detached()
        return icon
