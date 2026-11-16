"""Source-only test launcher boundary; no CLI/env override in the product entry."""
from contextlib import contextmanager
from pathlib import Path
import sys

_root = None

def require_inside(path):
    if _root is None:
        return
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('test path must be absolute')
    from .managed_network_stop_gate import _reject_links
    _reject_links(path)
    resolved = path.resolve()
    if not resolved.is_relative_to(_root) or resolved == _root:
        raise ValueError('path outside isolated test workspace')

def validate_config(config):
    if _root is None:
        return
    if getattr(config, 'startup_uri', None) or getattr(config, 'extension_bridge', False):
        raise ValueError('external launch integrations unavailable in test workspace')
    from dataclasses import fields, is_dataclass
    values = (getattr(config, f.name) for f in fields(config)) if is_dataclass(config) else vars(config).values()
    for value in values:
        if isinstance(value, Path):
            require_inside(value)

def owner_root():
    return None if _root is None else _root / 'shared-stop-owner'

@contextmanager
def isolated_test_workspace(root):
    """Used only by tools/launch_test_owner.py, never persisted in preferences."""
    global _root
    if getattr(sys, 'frozen', False) or _root is not None:
        raise ValueError('isolated source test launch required')
    root = Path(root)
    if not root.is_absolute() or root == root.parent:
        raise ValueError('absolute test workspace required')
    from .managed_network_stop_gate import _reject_links
    _reject_links(root)
    if not root.is_dir():
        raise ValueError('existing test workspace required')
    marker = root / '.pixivvault-isolated-test-workspace'
    _reject_links(marker)
    if marker.read_bytes() != b'PixivVault isolated source test workspace v1\n':
        raise ValueError('test workspace marker required')
    _root = root.resolve()
    try:
        yield
    finally:
        _root = None
