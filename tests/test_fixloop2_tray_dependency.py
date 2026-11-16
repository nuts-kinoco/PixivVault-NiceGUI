"""A standard Windows install includes the tray backend."""
from pathlib import Path
import tomllib


def test_windows_base_install_includes_tray_backend():
    metadata = tomllib.loads((Path(__file__).parents[1] / 'pyproject.toml').read_text(encoding='utf-8'))
    deps = metadata['project']['dependencies']
    assert any(item.startswith('pystray==') and "sys_platform == 'win32'" in item for item in deps)
