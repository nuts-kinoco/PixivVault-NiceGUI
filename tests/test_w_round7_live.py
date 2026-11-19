import hashlib
import json

import pytest

from pixivvault_nicegui import managed_generations as mg
from w_round7_fixtures import (
    ManagedDownloadError, make_store, run_child, state,
    test_separate_reader_during_committed_fold_with_windows_sharing_delay,
)


@pytest.mark.parametrize('change', ['entries', 'exclusions'])
def test_pending_candidate_with_changed_state_does_not_expose_old_view(tmp_path, monkeypatch, change):
    root = tmp_path / 'root'
    writer = make_store(root)
    with run_child('fold', root, 'floor', tmp_path) as child:
        out, err = child.communicate(timeout=15)
        assert child.returncode == 73, (out, err)
    floor_path = root / 'active-generations.floor'
    floor = json.loads(floor_path.read_bytes())
    candidate_path = root / 'generation-incomplete' / floor['candidate']
    candidate = json.loads(candidate_path.read_bytes())
    if change == 'entries':
        candidate['entries'].pop('102')
    else:
        candidate['exclusions'] = {'101': {'0': 'changed'}}
    data = mg._canonical(candidate)
    candidate_path.write_bytes(data)
    floor['digest'] = hashlib.sha256(data).hexdigest()
    floor_path.write_bytes(mg._canonical(floor))

    def busy():
        raise ManagedDownloadError('managed root writer fence busy')

    monkeypatch.setattr(writer._generations, '_recover_head', busy)
    with pytest.raises(ManagedDownloadError):
        state(writer)
