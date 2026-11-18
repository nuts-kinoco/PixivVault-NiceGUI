"""An interrupted first journal creation must not strand the managed vault."""
import subprocess
import sys

from pixivvault_nicegui import managed_generations as mg
from test_v_dl_cap_20261117 import make_root, publish, seed_index


def test_first_save_crash_before_journal_rename_can_reopen_and_save(tmp_path):
    root = make_root(tmp_path)
    seed_index(root, 0)  # an existing empty pre-journal pointer
    code = '''
import os, sys
from pathlib import Path
from pixivvault_nicegui import managed_generations as mg
from test_v_dl_cap_20261117 import publish
root = Path(sys.argv[1])
g = mg.ManagedGenerations(root)
original = mg._replace_retrying
def crash(src, dst, patience=5.0):
    if Path(src).name.startswith('journal-'):
        os._exit(9)
    return original(src, dst, patience)
mg._replace_retrying = crash
publish(g, root, '777')
'''
    child = subprocess.run([sys.executable, '-B', '-c', code, str(root)], timeout=30)
    assert child.returncode == 9
    assert list((root / 'generation-incomplete').glob('journal-*.tmp'))
    reopened = mg.ManagedGenerations(root)
    assert reopened.entries() == {}
    publish(reopened, root, '778')
    assert set(mg.ManagedGenerations(root).entries()) == {'778'}
