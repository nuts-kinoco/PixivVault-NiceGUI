import json
from pathlib import Path
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

import pytest

from w_round7_fixtures import (
    ManagedDownloadError, PNG, digest, forbid, make_store, mg, open_downloads,
    state, wait_for,
)


def read_at_boundary(mode, root, sync):
    if mode == 'warm':
        reader = open_downloads(root)
    paused = False
    refresh = mg.ManagedGenerations._refresh

    def pause_once(self):
        nonlocal paused
        if not paused:
            paused = True
            (sync / 'reader-ready').touch()
            wait_for(lambda: (sync / 'release-reader').exists(), timeout=12)
        return refresh(self)

    try:
        with patch.object(mg.ManagedGenerations, '_refresh', pause_once):
            if mode == 'cold':
                reader = open_downloads(root)
            print(json.dumps(state(reader)), flush=True)
    except ManagedDownloadError as error:
        print(str(error), flush=True)
        return 81
    return 0


@pytest.mark.parametrize('mode', ['cold', 'warm'])
@pytest.mark.parametrize('target_name', [mg._V5_HEAD, 'active-generations.json'])
def test_fold_commits_between_reader_precheck_and_validation(tmp_path, mode, target_name):
    root = tmp_path / 'root'
    writer = make_store(root)
    gen = writer._generations
    before = state(writer)
    old_index = gen.index_path.read_bytes()
    old_floor = gen._read_floor()
    writer_at_replace = Event()
    release_writer = Event()
    replace = mg._replace_retrying

    def pause_replace(source, target, patience=5.0):
        if target == root / target_name:
            writer_at_replace.set()
            assert release_writer.wait(12)
        return replace(source, target, patience)

    with subprocess.Popen(
        [sys.executable, '-B', __file__, mode, str(root), str(tmp_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8',
    ) as reader:
        try:
            wait_for(lambda: (tmp_path / 'reader-ready').exists())
            with patch.object(mg, '_replace_retrying', pause_replace), ThreadPoolExecutor(1) as pool:
                fold = pool.submit(gen._compact, gen._loaded)
                try:
                    assert writer_at_replace.wait(6)
                    floor = gen._read_floor()
                    assert floor['generation'] == old_floor['generation'] + 1
                    assert gen.index_path.read_bytes() == old_index
                    candidate = gen.stage_dir / floor['candidate']
                    assert json.loads(candidate.read_bytes())['entries'] == gen._loaded.indexed
                    (tmp_path / 'release-reader').touch()
                    out, err = reader.communicate(timeout=8)
                    assert not err, err
                    assert not fold.done()
                finally:
                    release_writer.set()
                fold.result(timeout=8)
        finally:
            (tmp_path / 'release-reader').touch()

    reopened = open_downloads(root)
    assert state(reopened) == before
    digest(root, before)
    for relative, sha in before.values():
        assert reopened.read_saved_page(relative, 0, sha) == PNG
    print(f'{mode}/{target_name}: during={reader.returncode}: {out.strip()}; after=both original receipts and pages')
    assert reader.returncode == 0, out
    assert json.loads(out) == {key: list(value) for key, value in before.items()}


if __name__ == '__main__':
    socket.socket.connect = forbid
    socket.socket.connect_ex = forbid
    socket.create_connection = forbid
    mode, root, sync = sys.argv[1:]
    sys.exit(read_at_boundary(mode, Path(root), Path(sync)))