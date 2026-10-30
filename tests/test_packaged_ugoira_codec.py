"""Packaged dispatch and token-bound synthetic CPU codec requests."""
import builtins
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

from pixivvault_nicegui import ugoira_mp4 as subject
from pixivvault_nicegui import ugoira_mp4_worker as worker
from pixivvault_nicegui.job_service import JobCancelled
from pixivvault_nicegui.managed_ugoira import ManagedUgoiraJobs
from pixivvault_nicegui.ugoira_archive import inspect_ugoira_archive
from pixivvault_nicegui.ugoira_fixtures import FixtureUgoiraReader

TOKEN_ENV = 'PIXIVVAULT_UGOIRA_CODEC_TOKEN'
TOKEN = 'ab' * 16


def fixture():
    reader = FixtureUgoiraReader()
    manifest = reader.get_ugoira_manifest('123')
    raw = reader.get_ugoira_archive(manifest)
    return raw, inspect_ugoira_archive(raw, manifest)


def request(tmp_path, monkeypatch):
    stage = tmp_path / 'codec'
    stage.mkdir()
    raw, inspection = fixture()
    plan = subject.mp4_plan(inspection)
    plan.update(protocol=1, request_token=TOKEN, source_sha256=hashlib.sha256(raw).hexdigest())
    (stage / 'source.zip').write_bytes(raw)
    (stage / 'plan.json').write_text(json.dumps(plan), encoding='utf-8')
    (stage / 'codec.log').write_bytes(b'')
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    return stage


def entry_module():
    path = Path(__file__).resolve().parents[1] / 'tools' / 'native_entry.py'
    spec = importlib.util.spec_from_file_location('_packaged_codec_native_entry', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_owner_selects_bundled_codec_and_preserves_override(tmp_path, monkeypatch):
    executable = tmp_path / 'PixivVault-NiceGUI.exe'
    executable.write_bytes(b'synthetic executable identity only')
    monkeypatch.setattr(subject.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(subject.sys, 'executable', str(executable))
    monkeypatch.setattr(subject.importlib.util, 'find_spec', lambda name: object())
    owner = ManagedUgoiraJobs(tmp_path / 'owned')
    try:
        assert Path(owner.codec_python) == executable
    finally:
        owner.close()
    explicit = tmp_path / 'explicit-python.exe'
    assert subject.default_codec_python(explicit) is explicit
    monkeypatch.setattr(subject.importlib.util, 'find_spec', lambda name: None)
    assert subject.default_codec_python() is None
    monkeypatch.setattr(subject.sys, 'frozen', False)
    assert subject.default_codec_python() is None


def test_frozen_self_and_explicit_python_commands_remain_distinct(tmp_path, monkeypatch):
    executable, interpreter, stage = tmp_path / 'app.exe', tmp_path / 'python.exe', tmp_path / 'codec'
    monkeypatch.setattr(subject.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(subject.sys, 'executable', str(executable))
    assert subject.codec_command(executable, stage) == [str(executable), '--internal-ugoira-codec', str(stage)]
    assert subject.codec_command(interpreter, stage) == [str(interpreter), '-I', '-B',
        str(Path(subject.__file__).with_name('ugoira_mp4_worker.py')), str(stage)]


@pytest.mark.parametrize('valid', [True, False])
def test_codec_entry_never_imports_profile_or_ui(tmp_path, monkeypatch, valid):
    entry = entry_module()
    calls = []
    monkeypatch.setattr(entry.multiprocessing, 'freeze_support', lambda: None)
    monkeypatch.setattr(worker, 'main', lambda folder: calls.append(folder))
    monkeypatch.setattr(sys, 'argv', ['app.exe', '--internal-ugoira-codec'] + ([str(tmp_path)] if valid else []))
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name in ('pixivvault_nicegui.bootstrap', 'pixivvault_nicegui.native_startup'):
            pytest.fail('codec child reached the UI/profile bootstrap')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    assert entry.main() == (0 if valid else 2)
    assert calls == ([str(tmp_path)] if valid else [])


def test_parent_refuses_nonempty_stage_before_any_input_write(tmp_path):
    raw, inspection = fixture()
    stage = tmp_path / 'codec'
    stage.mkdir()
    protected = stage / 'animation.mp4'
    protected.write_bytes(b'protected synthetic output')
    with pytest.raises(subject.UgoiraMp4Error):
        subject.render_mp4(raw, inspection, stage, Path(sys.executable), lambda: None)
    assert {p.name for p in stage.iterdir()} == {'animation.mp4'}
    assert protected.read_bytes() == b'protected synthetic output'


@pytest.mark.parametrize('name', ['animation.mp4', 'verification.json', 'decoded_frames.zip'])
def test_worker_preserves_preexisting_outputs(tmp_path, monkeypatch, name):
    stage = request(tmp_path, monkeypatch)
    (stage / name).write_bytes(b'protected synthetic bytes')
    before = {p.name: p.read_bytes() for p in stage.iterdir()}
    with pytest.raises(ValueError):
        worker.main(stage)
    assert {p.name: p.read_bytes() for p in stage.iterdir()} == before


@pytest.mark.parametrize('fault', ['token', 'source', 'budget'])
def test_worker_rejects_changed_or_unbounded_request_without_output(tmp_path, monkeypatch, fault):
    stage = request(tmp_path, monkeypatch)
    if fault == 'token':
        monkeypatch.setenv(TOKEN_ENV, 'cd' * 16)
    elif fault == 'source':
        source = stage / 'source.zip'
        source.write_bytes(source.read_bytes() + b'changed')
    else:
        plan = json.loads((stage / 'plan.json').read_text(encoding='utf-8'))
        plan['max_archive_bytes'] = 2 ** 40
        (stage / 'plan.json').write_text(json.dumps(plan), encoding='utf-8')
    with pytest.raises(ValueError):
        worker.main(stage)
    assert {p.name for p in stage.iterdir()} == {'source.zip', 'plan.json', 'codec.log'}


def test_worker_rejects_reparse_stage(tmp_path, monkeypatch):
    stage = request(tmp_path, monkeypatch)
    original = Path.lstat
    class Reparse:
        def __init__(self, value): self.value = value
        def __getattr__(self, name):
            return 0x400 if name == 'st_file_attributes' else getattr(self.value, name)
    def lstat(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        return Reparse(value) if path == stage else value
    monkeypatch.setattr(Path, 'lstat', lstat)
    with pytest.raises(ValueError):
        worker.main(stage)
    assert {p.name for p in stage.iterdir()} == {'source.zip', 'plan.json', 'codec.log'}


def test_cancellation_reaps_only_the_owned_codec_child(tmp_path, monkeypatch):
    raw, inspection = fixture()
    stage = tmp_path / 'codec'
    stage.mkdir()
    observed = {}
    class Child:
        alive = True
        returncode = None
        def poll(self): return None if self.alive else self.returncode
        def terminate(self):
            observed['terminated'] = True
            self.alive, self.returncode = False, -15
        def wait(self, timeout):
            observed['waited'] = timeout
            return self.returncode
        def kill(self): pytest.fail('normal codec termination required a forced kill')
    def launch(argv, **kwargs):
        observed.update(argv=argv, env=kwargs['env'])
        return Child()
    def stop(): raise JobCancelled('synthetic cancellation')
    monkeypatch.setattr(subject.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(subject.subprocess, 'Popen', launch)
    with pytest.raises(JobCancelled):
        subject.render_mp4(raw, inspection, stage, Path(sys.executable), stop)
    assert observed['argv'] == [sys.executable, '--internal-ugoira-codec', str(stage)]
    assert observed['terminated'] and observed['waited'] == 5
    plan = json.loads((stage / 'plan.json').read_text(encoding='utf-8'))
    assert observed['env'][TOKEN_ENV] == plan['request_token']
    assert 'PYTHONPATH' not in observed['env'] and not (stage / 'animation.mp4').exists()


def test_real_cpu_conversion_preserves_mp4v_cfr_and_six_file_budget(tmp_path):
    configured = os.environ.get('PV_TEST_CODEC_PYTHON')
    if not configured:
        pytest.skip('explicit existing CPU OpenCV runtime not configured')
    raw, inspection = fixture()
    stage = tmp_path / 'codec'
    stage.mkdir()
    data, report, decoded = subject.render_mp4(raw, inspection, stage, Path(configured), lambda: None)
    assert data and decoded
    assert report['codec'] == 'mp4v' and (report['width'], report['height']) == (96, 64)
    assert report['frame_count'] == 3 and report['fps'] == pytest.approx(10)
    assert report['source_duration_ms'] == 300 and report['encoded_duration_ms'] == pytest.approx(300, abs=1)
    assert report['timing'] == 'legacy-average-delay-cfr-clamped-1-60'
    assert report['alpha'] == 'discarded-as-original'
    assert report['mean_rgb'][0][0] > 240 and report['mean_rgb'][2][2] > 240
    assert {p.name for p in stage.iterdir()} == {'source.zip', 'plan.json', 'codec.log',
        'animation.mp4', 'verification.json', 'decoded_frames.zip'}
    assert len(subject.verify_decoded(decoded, inspection).frames) == 3
