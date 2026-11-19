"""Reproduce rollback of a vault compacted before the v5 commitment marker existed.

Run only with an empty, isolated S: directory supplied as argv[1].
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

from pixivvault_nicegui import managed_generations as current
from test_v_dl_cap_20261117 import make_root, publish, seed_index

base = Path(sys.argv[1])
base.mkdir()
oldfile = base / 'old_managed_generations.py'
oldfile.write_bytes(subprocess.check_output([
    'git', 'show', '099203e:src/pixivvault_nicegui/managed_generations.py',
]))
spec = importlib.util.spec_from_file_location('pixivvault_nicegui.actual_old_mg', oldfile)
old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old)
root = make_root(base)
seed_index(root, 0)
index = root / 'active-generations.json'
prior = index.read_bytes()
old._COMPACT_RECORDS = 1
old._JOURNAL_RETAIN_SECONDS = -1
writer = old.ManagedGenerations(root)
publish(writer, root, '1000')
writer._compact(writer._loaded)
print('before index rollback:', sorted(writer.entries()), flush=True)
index.write_bytes(prior)
try:
    reopened = current.ManagedGenerations(root)
except current.ManagedDownloadError:
    print('reopened: rejected')
else:
    actual = sorted(reopened.entries())
    print('reopened:', actual, flush=True)
    assert actual == ['1000'], 'published work disappeared after restoring the old index'
