"""N06/N07 restoration using a strict, entirely synthetic HKCU registry."""
from copy import deepcopy

import pytest

from pixivvault_nicegui.desktop_integration import (
    DesktopActionRejected, WindowsProtocolRegistration,
)


class Registry:
    """Small winreg model: types, key hierarchy, failures and handles are real contracts."""
    HKEY_CURRENT_USER = 'synthetic-HKCU'
    REG_NONE, REG_SZ, REG_EXPAND_SZ, REG_BINARY = 0, 1, 2, 3
    REG_DWORD, REG_MULTI_SZ, REG_QWORD = 4, 7, 11
    KEY_READ, KEY_WRITE, KEY_ALL_ACCESS = 0x20019, 0x20006, 0xF003F

    class Key:
        def __init__(self, registry, path, access):
            self.registry, self.path, self.access = registry, path, access
            self.closed = False
        def __enter__(self):
            assert not self.closed
            return self
        def __exit__(self, *_args):
            self.closed = True

    def __init__(self):
        self.values = {r'Software': {}, r'Software\Classes': {}}
        self.types = {}
        self.deleted, self.mutations = [], []
        self.before_mutation = None
        self.revision = 0

    def _path(self, parent, path):
        if isinstance(parent, self.Key):
            assert parent.registry is self and not parent.closed
            path = parent.path + ('\\' + path if path else '')
        else:
            assert parent == self.HKEY_CURRENT_USER
        return path.rstrip('\\')

    def _existing(self, path):
        for actual in self.values:
            if actual.casefold() == path.casefold():
                return actual
        raise FileNotFoundError('synthetic key is absent')

    def _name(self, key, name):
        assert key.registry is self and not key.closed
        for actual in self.values[key.path]:
            if actual.casefold() == name.casefold():
                return actual
        raise FileNotFoundError('synthetic value is absent')

    def _before(self, operation, path, name=None, value=None):
        if self.before_mutation is not None:
            self.before_mutation(operation, path, name, value)

    def _changed(self, operation, path, name=None):
        self.revision += 1
        self.mutations.append((operation, path, name))

    def CreateKey(self, parent, path):
        path = self._path(parent, path)
        self._before('create', path)
        built = ''
        for part in path.split('\\'):
            candidate = built + ('\\' if built else '') + part
            try:
                built = self._existing(candidate)
            except FileNotFoundError:
                built = candidate
                self.values[built] = {}
                self._changed('create', built)
        return self.Key(self, built, self.KEY_ALL_ACCESS)

    def CreateKeyEx(self, parent, path, reserved=0, access=KEY_WRITE):
        assert reserved == 0
        key = self.CreateKey(parent, path)
        key.access = access
        return key

    def OpenKey(self, parent, path, reserved=0, access=KEY_READ):
        assert reserved == 0
        return self.Key(self, self._existing(self._path(parent, path)), access)

    def SetValueEx(self, key, name, reserved, kind, value):
        assert reserved == 0 and not key.closed and key.registry is self
        assert key.access & 0x2  # KEY_SET_VALUE
        self._before('set', key.path, name, value)
        try:
            actual = self._name(key, name)
        except FileNotFoundError:
            actual = name
        self.values[key.path][actual] = deepcopy(value)
        self.types[(key.path, actual)] = kind
        self._changed('set', key.path, actual)

    def QueryValueEx(self, key, name):
        actual = self._name(key, name)
        return deepcopy(self.values[key.path][actual]), self.types.get((key.path, actual), self.REG_SZ)

    def DeleteValue(self, key, name):
        assert key.access & 0x2 and not key.closed
        actual = self._name(key, name)
        self._before('delete_value', key.path, actual)
        del self.values[key.path][actual]
        self.types.pop((key.path, actual), None)
        self._changed('delete_value', key.path, actual)

    def _children(self, path):
        prefix = path.casefold() + '\\'
        return sorted(p for p in self.values
                      if p.casefold().startswith(prefix) and '\\' not in p[len(path) + 1:])

    def QueryInfoKey(self, key):
        assert key.registry is self and not key.closed
        return len(self._children(key.path)), len(self.values[key.path]), self.revision

    def EnumKey(self, key, index):
        try:
            return self._children(key.path)[index].rsplit('\\', 1)[1]
        except IndexError:
            raise OSError('synthetic enumeration complete') from None

    def EnumValue(self, key, index):
        try:
            name = list(self.values[key.path])[index]
        except IndexError:
            raise OSError('synthetic enumeration complete') from None
        value, kind = self.QueryValueEx(key, name)
        return name, value, kind

    def DeleteKey(self, parent, path):
        actual = self._existing(self._path(parent, path))
        if self._children(actual):
            raise OSError('synthetic key has subkeys')
        self._before('delete_key', actual)
        for name in self.values[actual]:
            self.types.pop((actual, name), None)
        del self.values[actual]
        self.deleted.append(actual)
        self._changed('delete_key', actual)

    def seed(self, path, values):
        with self.CreateKey(self.HKEY_CURRENT_USER, path) as key:
            for name, (kind, value) in values.items():
                self.SetValueEx(key, name, 0, kind, value)

    def snapshot(self):
        return deepcopy({path: {name: (self.types.get((path, name), self.REG_SZ), value)
                                for name, value in values.items()}
                         for path, values in self.values.items()})


SCHEME = 'pv-synthetic-n06-n07'
KEY = r'Software\Classes' + '\\' + SCHEME
RECEIPT = r'Software\PixivVault-NiceGUI\ProtocolRegistrations' + '\\' + SCHEME
COMMAND = '"S:/Synthetic Candidate/app.exe" -- "%1"'
PREVIOUS = '"S:/Synthetic Previous/app.exe" "%1"'


def adapter(registry, command=COMMAND):
    result = WindowsProtocolRegistration(command, registry=registry)
    # Only an injected synthetic registry. Native acceptance uses a separate,
    # explicitly owned test scheme; the real pixivvault scheme is never touched.
    result.KEY = KEY
    return result


def foreign(registry, *, command=PREVIOUS, extras=True):
    registry.seed(KEY, {
        '': (registry.REG_MULTI_SZ, ['Original', 'Description']),
        'URL Protocol': (registry.REG_BINARY, b'original\x00protocol'),
    })
    registry.seed(KEY + r'\shell\open\command', {
        '': (registry.REG_EXPAND_SZ, command),
    })
    if extras:
        registry.seed(KEY + r'\DefaultIcon', {'': (registry.REG_EXPAND_SZ, '%TEMP%/original.ico')})
        registry.seed(KEY + r'\shell\edit\command', {'': (registry.REG_SZ, 'synthetic alternate verb')})
        registry.seed(KEY + r'\shell\open\command', {'ForeignFlag': (registry.REG_DWORD, 9)})


def fail_once(registry, *, operation, path, name=None, value=None):
    hit = []
    def before(actual_operation, actual_path, actual_name, actual_value):
        if (not hit and actual_operation == operation and actual_path == path
                and (name is None or actual_name == name)
                and (value is None or actual_value == value)):
            hit.append(True)
            raise OSError('synthetic backend detail which must not escape')
    registry.before_mutation = before
    return hit


def test_foreign_values_types_and_additional_subkeys_are_restored_after_restart():
    registry = Registry()
    foreign(registry)
    before = registry.snapshot()
    owner = adapter(registry)
    mutations = len(registry.mutations)
    assert not owner.is_registered() and len(registry.mutations) == mutations
    owner.register()
    assert owner.is_registered()
    with registry.OpenKey(registry.HKEY_CURRENT_USER, KEY + r'\shell\open\command') as key:
        assert registry.QueryValueEx(key, '') == (COMMAND, registry.REG_SZ)
        assert registry.QueryValueEx(key, 'ForeignFlag') == (9, registry.REG_DWORD)
    # A process restart cannot lose the original values or their registry types.
    reopened = adapter(registry)
    reopened.unregister()
    assert not reopened.is_registered()
    assert registry.snapshot() == before


def test_matching_command_without_an_ownership_receipt_is_never_deleted():
    registry = Registry()
    foreign(registry, command=COMMAND, extras=False)
    before = registry.snapshot()
    owner = adapter(registry)
    owner.unregister()
    assert registry.snapshot() == before
    assert registry.deleted == []


@pytest.mark.parametrize('path,name,value', [
    (KEY, '', 'changed external description'),
    (KEY, 'URL Protocol', 'changed external marker'),
    (KEY + r'\shell\open\command', '', 'changed external command'),
])
def test_external_change_to_an_owned_value_blocks_restoration_without_any_write(path, name, value):
    registry = Registry()
    owner = adapter(registry)
    owner.register()
    registry.seed(path, {name: (registry.REG_SZ, value)})
    before, count = registry.snapshot(), len(registry.mutations)
    with pytest.raises(DesktopActionRejected):
        owner.unregister()
    assert registry.snapshot() == before and len(registry.mutations) == count


def test_later_foreign_values_and_subkeys_survive_unregister_of_a_new_scheme():
    registry = Registry()
    owner = adapter(registry)
    owner.register()
    registry.seed(KEY, {'OtherSetting': (registry.REG_DWORD, 17)})
    registry.seed(KEY + r'\shell\open\command', {'DelegateExecute': (registry.REG_SZ, 'synthetic-guid')})
    registry.seed(KEY + r'\DefaultIcon', {'': (registry.REG_SZ, 'synthetic new icon')})
    owner.unregister()
    assert registry.values[KEY] == {'OtherSetting': 17}
    assert registry.values[KEY + r'\shell\open\command'] == {'DelegateExecute': 'synthetic-guid'}
    assert registry.values[KEY + r'\DefaultIcon'] == {'': 'synthetic new icon'}
    assert not owner.is_registered()


def test_failed_registration_keeps_a_durable_restore_record_and_restart_can_undo():
    registry = Registry()
    foreign(registry)
    before = registry.snapshot()
    hit = fail_once(registry, operation='set', path=KEY + r'\shell\open\command',
                    name='', value=COMMAND)
    with pytest.raises((DesktopActionRejected, OSError)) as error:
        adapter(registry).register()
    assert hit
    reopened = adapter(registry)
    reopened.unregister()
    assert registry.snapshot() == before
    assert isinstance(error.value, DesktopActionRejected)
    assert 'backend detail' not in str(error.value)


def test_failed_unregister_resumes_mixed_restoration_after_restart():
    registry = Registry()
    foreign(registry)
    before = registry.snapshot()
    adapter(registry).register()
    hit = fail_once(registry, operation='set', path=KEY,
                    name='URL Protocol', value=b'original\x00protocol')
    with pytest.raises(DesktopActionRejected) as error:
        adapter(registry).unregister()
    assert hit and 'backend detail' not in str(error.value)
    adapter(registry).unregister()
    assert registry.snapshot() == before


def test_new_owned_command_keeps_the_first_restore_point_and_old_owner_cannot_disable_it():
    registry = Registry()
    foreign(registry)
    before = registry.snapshot()
    first = adapter(registry)
    first.register()
    second = adapter(registry, '"S:/Synthetic Candidate v2/app.exe" -- "%1"')
    second.register()
    active = registry.snapshot()
    with pytest.raises(DesktopActionRejected):
        first.unregister()
    assert registry.snapshot() == active and second.is_registered()
    second.unregister()
    assert registry.snapshot() == before


def test_an_existing_unowned_receipt_key_is_not_adopted_or_overwritten():
    registry = Registry()
    foreign(registry)
    registry.seed(RECEIPT, {'ForeignSetting': (registry.REG_SZ, 'keep untouched')})
    before, count = registry.snapshot(), len(registry.mutations)
    with pytest.raises(DesktopActionRejected):
        adapter(registry).register()
    assert registry.snapshot() == before and len(registry.mutations) == count


def test_interrupted_upgrade_restore_does_not_reclaim_a_later_old_owner_command():
    registry = Registry()
    foreign(registry)
    adapter(registry).register()
    upgraded_command = '"S:/Synthetic Candidate v2/app.exe" -- "%1"'
    adapter(registry, upgraded_command).register()
    command_path = KEY + r'\shell\open\command'
    # Enter restoring durably, but fail before any source value is restored.
    hit = fail_once(registry, operation='set', path=command_path,
                    name='', value=PREVIOUS)
    with pytest.raises(DesktopActionRejected):
        adapter(registry, upgraded_command).unregister()
    assert hit

    # Another application now selects the previous installed command. This is
    # a new external edit, not an unfinished step of B -> original restoration.
    registry.before_mutation = None
    registry.seed(command_path, {'': (registry.REG_SZ, COMMAND)})
    before, count = registry.snapshot(), len(registry.mutations)
    with pytest.raises(DesktopActionRejected):
        adapter(registry, upgraded_command).unregister()
    assert registry.snapshot() == before
    assert len(registry.mutations) == count
