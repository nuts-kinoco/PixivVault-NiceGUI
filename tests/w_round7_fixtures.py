import ctypes
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PIL import Image

from pixivvault_nicegui import managed_generations as mg
from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.managed_download_catalog import build_managed_library
from pixivvault_nicegui.managed_downloads import ManagedDownloadError, ManagedPixivDownloads
from pixivvault_nicegui.duplicate_inspection import ManagedDuplicateInspection

DATA = io.BytesIO()
Image.new('RGB', (2, 2), (30, 120, 85)).save(DATA, 'PNG')
PNG = DATA.getvalue()

class Client:
    def verify_identity(self):
        self._verified_identity = '6400'
        return self._verified_identity
    def get_artwork_original_pages(self, wid):
        return ('https://i.pximg.net/synthetic/' + wid + '.png',)
    def get_image_bytes(self, url):
        return PNG

class Context:
    def checkpoint(self):
        pass
    def progress(self, *_):
        pass

def record(wid):
    return RemoteBookmark(wid, 'illustration', 'show', 'work ' + wid, '1000', 'synthetic',
                          (), '', '2026-08-01T00:00:00Z', 1, 0, 0)

def open_downloads(root):
    return ManagedPixivDownloads(root, Client(), resolve_record=record)

def save(writer, wid):
    return writer.run(SimpleNamespace(kind='pixiv-illust', target_id=wid, job_id='%032x' % int(wid)),
                      Context())

def state(writer):
    return {row['work_id']: (relative, digest) for relative,row,digest in writer.saved_receipts()}

def digest(root, ids):
    for relative, expected in ids.values():
        assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == expected

def make_store(path, second=True):
    writer = open_downloads(path)
    save(writer, '101')
    if second:
        save(writer, '102')
    return writer

def forbid(*args, **kwargs):
    raise AssertionError('network disabled in isolated child')

def wait_for(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError('timed out waiting for writer')

def fold_child(root, cut, sync):
    gen = mg.ManagedGenerations(root)
    if cut == 'pair':
        def new(*args, **kwargs):
            new_original(*args, **kwargs)
            os._exit(71)
        new_original = gen._new_journal
        gen._new_journal = new
    else:
        replace = mg._replace_retrying
        def boundary(source, target, patience=5.0):
            if cut == 'candidate' and target.name == mg._GENERATION_FLOOR:
                os._exit(72)
            if cut == 'floor' and target.name == mg._V5_HEAD:
                os._exit(73)
            if cut == 'installed-journal' and target.parent == root and target.suffix == '.mark':
                os._exit(74)
            if cut == 'head' and target == gen.index_path:
                os._exit(75)
            if cut == 'before-commit-live' and target.name == mg._GENERATION_FLOOR:
                (sync / 'paused').touch()
                wait_for(lambda: (sync / 'release').exists())
            return replace(source, target, patience)
        mg._replace_retrying = boundary
    gen._compact(gen._loaded)

def read_child(root):
    try:
        print(json.dumps(state(open_downloads(root))), flush=True)
    except ManagedDownloadError as error:
        print(str(error), flush=True)
        sys.exit(81)

def run_child(*args, **kwargs):
    return subprocess.Popen([sys.executable, '-B', __file__, *map(str,args)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kwargs)

def read_in_child(root):
    with run_child('read',root) as child:
        out, err = child.communicate(timeout=12)
        assert not err, err
        return child.returncode, out.strip()

@pytest.mark.parametrize('point, exit_code', [('pair',71), ('candidate',72), ('floor',73),
                                             ('installed-journal',74), ('head',75)])
def test_interrupted_process_fold_preserves_committed_digests(tmp_path, point, exit_code):
    root = tmp_path / 'root'
    writer = make_store(root)
    before = state(writer)
    index = root / 'active-generations.json'
    old = index.read_bytes()
    with run_child('fold',root,point,tmp_path) as child:
        out, err = child.communicate(timeout=15)
        assert child.returncode == exit_code, (out,err)
    assert index.read_bytes() == old
    code, out = read_in_child(root)
    assert (code, json.loads(out)) == (0, {k:list(v) for k,v in before.items()})
    assert (index.read_bytes() == old) == (point in ('pair','candidate'))
    assert state(writer) == before
    digest(root, before)
    save(writer,'103')
    assert set(state(open_downloads(root))) == {'101','102','103'}

def test_separate_reader_opens_before_fold_commit(tmp_path):
    root = tmp_path / 'root'
    writer = make_store(root)
    before = state(writer)
    with run_child('fold',root,'before-commit-live',tmp_path) as child:
        try:
            wait_for(lambda: (tmp_path / 'paused').exists())
            code,out = read_in_child(root)
            assert (code,json.loads(out)) == (0,{k:list(v) for k,v in before.items()})
        finally:
            (tmp_path / 'release').touch()
            out,err = child.communicate(timeout=15)
        assert child.returncode == 0, (out,err)
    assert state(writer) == before

@pytest.mark.skipif(os.name != 'nt', reason='Windows sharing flags')
@pytest.mark.parametrize('blocked_name', ['active-generations.json', 'active-generations.head'])
def test_separate_reader_during_committed_fold_with_windows_sharing_delay(tmp_path, blocked_name):
    from ctypes import wintypes
    root = tmp_path / 'root'
    writer = make_store(root)
    before = state(writer)
    floor_path = root / 'active-generations.floor'
    old_floor = floor_path.read_bytes()
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    handle = create(str(root / blocked_name), 0x80000000, 0x3, None, 3, 0x80, None)
    assert handle not in (None,wintypes.HANDLE(-1).value)
    with run_child('fold',root,'none',tmp_path) as child:
        try:
            wait_for(lambda: floor_path.read_bytes() != old_floor)
            assert child.poll() is None
            cold = read_in_child(root)
            try:
                warm = state(writer)
            except ManagedDownloadError as error:
                warm = str(error)
            assert child.poll() is None
        finally:
            close(handle)
            out,err = child.communicate(timeout=15)
        assert child.returncode == 0, (out,err)
    assert state(open_downloads(root)) == before
    digest(root, before)
    print(f'{blocked_name}: during fold cold={cold}, warm={warm}; after release=both receipts')
    assert cold[0] == 0 and warm == before

@pytest.mark.parametrize('files', [('index',), ('index','head'), ('journal','mark'),
                                   ('journal','mark','head')])
def test_partial_rollback_retains_or_refuses_second_save(tmp_path, files):
    root = tmp_path / 'root'
    writer = make_store(root, second=False)
    gen = writer._generations
    base = root / 'active-generations.json'
    jid = json.loads(base.read_bytes())['journal']['id']
    controls = {'index': base, 'head': root / 'active-generations.head',
                'journal': root / f'active-generations-{jid}.journal',
                'mark': root / f'active-generations-{jid}.mark'}
    snapshots = {kind: controls[kind].read_bytes() for kind in files}
    if 'index' in files:
        gen.entries()
        gen._compact(gen._loaded)
    save(writer,'102')
    before = state(writer)
    assert set(before) == {'101','102'}
    floor = (root / 'active-generations.floor').read_bytes()
    for kind, data in snapshots.items():
        controls[kind].write_bytes(data)
    assert (root / 'active-generations.floor').read_bytes() == floor
    digest(root,before)
    cold = read_in_child(root)
    try:
        warm = state(writer)
    except ManagedDownloadError as error:
        warm = str(error)
    print(f'{files}: cold={cold}; warm={warm}; both original receipt digests intact')
    assert cold[0] == 81 or set(json.loads(cold[1])) == set(before)
    assert not isinstance(warm, dict) or warm == before

def add_compatible_legacy_output(root, wid):
    sample = root / save(open_downloads(root), '103')
    old = json.loads(sample.read_bytes())
    del old['generation']
    old['work_id'] = wid
    old['bookmark_count'] = 0
    folder = root / 'outputs' / ('work-' + wid + '-' + 'a' * 32)
    folder.mkdir()
    (folder / 'page-1.png').write_bytes(PNG)
    receipt = folder / 'receipt.json'
    receipt.write_text(json.dumps(old), encoding='utf-8')
    return receipt

def test_generationless_receipt_and_new_receipt_read_together(tmp_path):
    root = tmp_path / 'root'
    writer = make_store(root, second=False)
    old = add_compatible_legacy_output(root,'201')
    old_bytes = old.read_bytes()
    gen = writer._generations
    gen.entries()
    gen._compact(gen._loaded)
    opened = open_downloads(root)
    receipts = {receipt['work_id']: (relative,receipt,sha)
                for relative,receipt,sha in opened.saved_receipts()}
    assert set(receipts) == {'101','103','201'}
    assert 'generation' not in receipts['201'][1]
    assert receipts['201'][1]['bookmark_count'] == 0
    assert receipts['101'][1]['generation'] < gen.committed_generation()
    for relative, receipt, sha in receipts.values():
        assert opened.read_saved_page(relative,0,sha) == PNG
    _, library, _ = build_managed_library(opened)
    try:
        assert library.artwork_for('201').bookmark_count == 0
        assert library.get('201',0,'view').data
        assert library.get('101',0,'view').data
    finally:
        library.close()
    assert old.read_bytes() == old_bytes

def test_calls_cover_validation_on_reader_writer_fold_quarantine_and_capacity(tmp_path):
    root = tmp_path / 'root'
    writer = make_store(root)
    gen = writer._generations
    events = []
    original = mg.ManagedGenerations.assert_consistent
    def checked(self, loaded, **kwargs):
        events.append((loaded.generation, loaded.journal_records))
        return original(self,loaded,**kwargs)
    with patch.object(mg.ManagedGenerations,'assert_consistent',checked):
        def exercise(label, action):
            events.clear()
            action()
            print(label, list(events))
            assert events
        exercise('new open',lambda: open_downloads(root))
        exercise('warm read',lambda: state(writer))
        exercise('append',lambda: save(writer,'103'))
        exercise('fold',lambda: gen._compact(gen._loaded))
        inspection = ManagedDuplicateInspection(writer)
        try:
            scan = inspection.scan()
            plan = inspection.plan(scan, {group.group_id: {'101:1'} for group in scan.groups
                                          if any(item.key == '101:1' for item in group.items)})
            def quarantine():
                result = inspection.execute(plan, operation_id='a' * 32)
                assert len(result.committed) == 1
            exercise('quarantine execute',quarantine)
            exercise('quarantine snapshot',writer.quarantine.snapshot)
            exercise('visible page check',lambda: gen.assert_page_visible('102',0))
        finally:
            inspection.close()
        with patch.object(mg,'_MAX_ENTRIES',3):
            exercise('capacity status',lambda: gen.capacity())
            def room():
                with pytest.raises(ManagedDownloadError) as err:
                    gen.assert_room('104')
                assert err.value.reason_code == 'storage_capacity'
            exercise('capacity admission',room)


def files_snapshot(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            if p.is_file() else None for p in root.rglob('*')}

@pytest.mark.parametrize('kind', ['legacy-index','legacy-journal'])
def test_interrupted_fold_recovery_respects_legacy_rejection_without_writes(tmp_path, kind):
    root = tmp_path / 'root'
    writer = make_store(root)
    before = state(writer)
    with run_child('fold',root,'head',tmp_path) as child:
        out,err = child.communicate(timeout=15)
        assert child.returncode == 75, (out,err)
    if kind == 'legacy-index':
        (root / 'active-generations.json').write_text('{"version":4,"entries":{}}',encoding='ascii')
    else:
        legacy = root / 'active-generations.journal'
        legacy.write_bytes(b'{"kind":"pixivvault-generation-journal","version":1}\n')
    old = files_snapshot(root)
    code, cold = read_in_child(root)
    assert code == 81
    assert files_snapshot(root) == old
    try:
        warm = state(writer)
    except ManagedDownloadError as error:
        warm = str(error)
    new = files_snapshot(root)
    changed = sorted(k for k in new.keys() | old.keys() if old.get(k) != new.get(k) or
                     (k in new) != (k in old))
    print(f'{kind}: cold=refused/no changes; warm={warm}; changed={changed}')
    digest(root, before)
    assert isinstance(warm, str) and not changed

def capacity_child(root, wid, sync):
    mg._MAX_ENTRIES = 2
    class PausingClient(Client):
        def get_artwork_original_pages(self, requested):
            (sync / f'ready-{wid}').touch()
            wait_for(lambda: (sync / 'release').exists())
            return super().get_artwork_original_pages(requested)
    writer = ManagedPixivDownloads(root,PausingClient(),resolve_record=record)
    try:
        print(save(writer,wid), flush=True)
    except ManagedDownloadError as error:
        print(error.reason_code, flush=True)
        sys.exit(82 if error.reason_code == 'storage_capacity' else 83)

def test_two_independent_real_saves_for_one_remaining_slot(tmp_path):
    root = tmp_path / 'root'
    writer = make_store(root,second=False)
    first = state(writer)['101']
    with run_child('capacity',root,'102',tmp_path) as a, run_child('capacity',root,'103',tmp_path) as b:
        try:
            wait_for(lambda: all((tmp_path / f'ready-{wid}').exists() for wid in ('102','103')))
        finally:
            (tmp_path / 'release').touch()
            results = [(child.communicate(timeout=15),child.returncode) for child in (a,b)]
    assert sorted(code for _,code in results) == [0,82], results
    with patch.object(mg,'_MAX_ENTRIES',2):
        opened = open_downloads(root)
        assert opened._generations.capacity()['headroom'] == 0
        results_on_disk = state(opened)
    assert len(results_on_disk) == 2 and results_on_disk['101'] == first
    assert len(list((root / 'generations').iterdir())) == 2
    digest(root,results_on_disk)
    print('capacity: two processes admitted; one saved, one storage_capacity; reopen has two receipts')

@pytest.mark.parametrize('boundary', ['new-open','read','append','fold','quarantine','capacity'])
def test_damaged_current_journal_refused_without_writes_at_each_boundary(tmp_path, boundary):
    root = tmp_path / 'root'
    writer = make_store(root,second=False)
    data = root / 'active-generations.json'
    jid = json.loads(data.read_bytes())['journal']['id']
    journal = root / f'active-generations-{jid}.journal'
    first = journal.read_bytes()
    save(writer,'102')
    gen = writer._generations
    state(writer)
    inspection = ManagedDuplicateInspection(writer)
    scan = inspection.scan()
    plan = inspection.plan(scan, {group.group_id: {'101:1'} for group in scan.groups
                                  if any(item.key == '101:1' for item in group.items)})
    actions = {'new-open': lambda: open_downloads(root),
               'read': lambda: state(writer),
               'append': lambda: save(writer,'103'),
               'fold': lambda: gen._compact(gen._loaded),
               'quarantine': lambda: inspection.execute(plan,operation_id='b' * 32),
               'capacity': lambda: gen.assert_room('103')}
    journal.write_bytes(first)
    before = files_snapshot(root)
    try:
        with pytest.raises((ManagedDownloadError, ValueError)):
            actions[boundary]()
        assert files_snapshot(root) == before
    finally:
        inspection.close()

if __name__ == '__main__':
    socket.socket.connect = forbid
    socket.socket.connect_ex = forbid
    socket.create_connection = forbid
    cmd, folder, *other = sys.argv[1:]
    if cmd == 'read':
        read_child(Path(folder))
    elif cmd == 'capacity':
        capacity_child(Path(folder), other[0], Path(other[1]))
    elif cmd == 'fold':
        fold_child(Path(folder), other[0], Path(other[1]))
