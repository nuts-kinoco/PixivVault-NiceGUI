import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from pixivvault_nicegui import managed_generations as mg
from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.managed_downloads import ManagedDownloadError, ManagedPixivDownloads
from test_v_dl_cap_20261117 import make_root, journal_of, mark_of, make_generation, png, publish, seed_index


HERE = Path(__file__).resolve()
BASELINE = '6a1d2ec754f8fafc0cceabead690203e8b2cda92'
SWITCH = 82


def spawn(*args):
    return subprocess.Popen([sys.executable, '-B', str(HERE), *map(str, args)])


def cleanup(children):
    for child in children:
        if child.poll() is None:
            child.terminate()
    for child in children:
        child.wait(timeout=15)


def await_start(root, identity, waiting):
    (root.parent / f'ready-{identity}').touch()
    assert waiting or not (root.parent / 'go').exists()
    deadline = time.monotonic() + 20
    while not (root.parent / 'go').exists():
        assert time.monotonic() < deadline, 'child waited too long for barrier'
        time.sleep(0.01)


def wait_children_ready(root, children, ids):
    end = time.monotonic() + 20
    while not all((root.parent / f'ready-{i}').exists() for i in ids):
        assert all(child.poll() is None for child in children)
        assert time.monotonic() < end, 'parent waited too long for barrier'
        time.sleep(0.01)
    (root.parent / 'go').touch()


def direct_child(root, wid):
    mg._MAX_ENTRIES = 1

    class Context:
        def checkpoint(self): pass
        def progress(self, *_): pass

    class Client:
        def verify_identity(self): self._verified_identity = '99'; return self._verified_identity

        def get_artwork_original_pages(self, wid):
            await_start(root, wid, True)  # after assert_room; neither writer has yet published
            return (f'https://i.pximg.net/synthetic/{wid}/1.png',)

        def get_image_bytes(self, url): return png()

    def record(work_id):
        return RemoteBookmark(work_id, 'illustration', 'show', 'T' + work_id, '25', 'A', (), '',
                              '2026-10-29T00:00:00Z', 1, 0, 0)

    writer = ManagedPixivDownloads(root, Client(), resolve_record=record)
    try:
        result = writer.run(SimpleNamespace(kind='pixiv-illust', target_id=wid,
                                            job_id='%032x' % int(wid)), Context())
        print(f'direct {wid}: committed={result.startswith("outputs/")}')
    except ManagedDownloadError as error:
        if error.reason_code == 'storage_capacity':
            sys.exit(83)
        raise
    finally:
        writer.close()


def test_two_direct_writers_do_not_make_full_store_unopenable(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    mg.ManagedGenerations(root)
    children = [spawn('direct', root, wid) for wid in (1000, 1001)]
    try:
        wait_children_ready(root, children, ('1000', '1001'))
        codes = sorted(c.wait(timeout=25) for c in children)
    finally:
        cleanup(children)
    monkeypatch.setattr(mg, '_MAX_ENTRIES', 1)
    try:
        reopened = mg.ManagedGenerations(root).entry_count()
    except ManagedDownloadError as error:
        reopened = (str(error), error.reason_code)
    print(f'direct: exits={codes}, outputs={len(list((root / "outputs").iterdir()))}, reopen={reopened}')
    assert codes == [0, 83] and reopened == 1



if __name__ == '__main__':
    task, folder, *args = sys.argv[1:]
    if task == 'direct':
        direct_child(Path(folder), *args)
