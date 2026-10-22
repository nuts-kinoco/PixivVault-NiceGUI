"""Safety and fixture contracts for the unlaunched legacy Flet baseline."""
import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest


SPEC = importlib.util.spec_from_file_location(
    'legacy_baseline_under_test', Path(__file__).resolve().parents[1] / 'tools/legacy_baseline.py')
baseline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(baseline)


def test_source_whitelist_and_forbidden_module_imports():
    assert 'gui.py' not in baseline.SOURCES
    assert 'main.py' not in baseline.SOURCES
    assert 'core.py' not in baseline.SOURCES
    assert 'pixiv_client.py' not in baseline.SOURCES
    assert 'database.py' not in baseline.SOURCES
    assert 'ranking_tab.py' in baseline.SOURCES
    assert 'artbook_viewer.py' in baseline.SOURCES
    assert {'server', 'core', 'database', 'pixiv_client'} <= baseline.BLOCKED


def test_rejects_unknown_import_and_does_not_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(baseline, 'ROOT', tmp_path)
    src = tmp_path / 'untrusted'
    src.mkdir()
    for name in baseline.SOURCES:
        (src / name).write_text('import os\n', encoding='utf8')
    (src / 'ranking_tab.py').write_text('import imaginary_network_module\n')
    output = tmp_path / 'artifacts' / 'output'
    with pytest.raises(ValueError, match='unrecognized imports'):
        baseline.prepare(src, output)
    assert not (output / 'source').exists()


def test_fake_client_uses_fixture_bytes_and_has_no_external_fallback(tmp_path):
    image = b'\x89PNG\r\n\x1a\nfixture'
    (tmp_path / 'demo-0001-thumb.png').write_bytes(image)
    (tmp_path / 'demo-0001-0-view.png').write_bytes(image)
    baseline.FakeClient.fixtures = tmp_path
    baseline.FakeClient.id_map = {1: 'demo-0001'}
    baseline.FakeClient.rows = ({'illust_id': 1, 'title': 'synthetic',
                                 'rank': 1, 'page_count': 1},)
    client = baseline.FakeClient()
    assert client.get_ranking(mode='daily', content='illust', page=1) == list(client.rows)
    assert client.get_image_urls(1) == ['fixture:demo-0001:0:view']
    assert client.download_thumbnail_as_data_uri('fixture:demo-0001:0:thumb').startswith('data:image/png;base64,')
    with pytest.raises(RuntimeError, match='external image URL prohibited'):
        client.download_thumbnail_as_data_uri('https://example.com/image.png')
    with pytest.raises(RuntimeError, match='unexpected API'):
        client.add_illust_bookmark('demo-0001')


def test_fake_db_rejects_unknown_reads_and_keeps_changes_local():
    db = baseline.FakeDB()
    assert db.get_setting('grid_card_size_mode', 'large') == 'large'
    db.set_setting('grid_card_size_mode', 'small')
    assert db.get_setting('grid_card_size_mode') == 'small'
    with pytest.raises(RuntimeError, match='unexpected access'):
        db.remove_hidden_work('abc')


def test_manifest_hash_is_deterministic(tmp_path):
    data = b'fixture'
    assert baseline.sha(data) == baseline.sha(bytes(data))
    assert baseline.imports_in(b'import os\nfrom core import a\n') == {'os', 'core'}


def test_v1_preparation_cannot_overwrite_existing_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(baseline, 'ROOT', tmp_path)
    source = tmp_path / 'source'
    source.mkdir()
    output = tmp_path / 'artifacts' / 'baseline'
    output.mkdir(parents=True)
    existing = b'[{"work_id": "demo-0001"}]'
    (output / 'fixture.json').write_bytes(existing)
    with pytest.raises(RuntimeError, match='v1 fixture is immutable'):
        baseline.prepare(source, output)
    assert (output / 'fixture.json').read_bytes() == existing


def test_marshal_keeps_async_and_fallback_separate_and_preserves_key():
    class Dispatcher:
        def __init__(self):
            self.calls = []
        def post(self, fn, **kwargs):
            self.calls.append((fn, kwargs))
    dispatcher = Dispatcher()
    async def async_fn():
        return None
    fallback = lambda: None
    baseline.marshal_with_dispatcher(dispatcher, async_fn, fallback)
    baseline.marshal_with_dispatcher(dispatcher, async_fn, fallback, key='sample')
    assert dispatcher.calls == [
        (async_fn, {'fallback': fallback}),
        (async_fn, {'fallback': fallback, 'key': 'sample'}),
    ]


def test_output_boundaries_and_png_integrity(tmp_path, monkeypatch):
    monkeypatch.setattr(baseline, 'ROOT', tmp_path)
    good = tmp_path / 'artifacts' / 'baseline-v2'
    assert baseline.safe_output(good) == good.resolve()
    for bad in (tmp_path, tmp_path / 'artifacts', tmp_path / 'source',
                tmp_path / 'artifacts' / '..' / 'outside',
                tmp_path / 'artifacts' / '.venv' / 'data'):
        with pytest.raises(ValueError):
            baseline.safe_output(bad)
    fixture_dir = good / 'fixture'
    fixture_dir.mkdir(parents=True)
    payload = b'PNG-test'
    (fixture_dir / 'image.png').write_bytes(payload)
    fixture = [{'thumbnail': {'file': 'image.png', 'sha256': baseline.sha(payload)}, 'pages': []}]
    baseline.verify_pngs(fixture, fixture_dir)
    (fixture_dir / 'image.png').write_bytes(b'tampered')
    with pytest.raises(RuntimeError, match='hash mismatch'):
        baseline.verify_pngs(fixture, fixture_dir)


def test_shared_warm_cache_validates_urls_and_separates_fixture_roots(tmp_path):
    first, second = tmp_path / 'a', tmp_path / 'b'
    first.mkdir(); second.mkdir()
    (first / 'demo-0001-thumb.png').write_bytes(b'first')
    (second / 'demo-0001-thumb.png').write_bytes(b'second')
    (first / 'demo-0001-0-view.png').write_bytes(b'view')
    baseline.FakeClient.rows = ({'illust_id': 1, 'page_count': 1,
                                 'url': 'fixture:demo-0001:0:thumb'},)
    baseline.FakeClient.id_map = {1: 'demo-0001'}
    baseline.FakeClient._image_cache.clear()
    baseline.FakeClient.fixtures = first
    a, b = baseline.FakeClient(), baseline.FakeClient()
    warm = a.download_thumbnail_as_data_uri('fixture:demo-0001:0:thumb')
    (first / 'demo-0001-thumb.png').write_bytes(b'changed')
    assert b.download_thumbnail_as_data_uri('fixture:demo-0001:0:thumb') == warm
    baseline.FakeClient.fixtures = second
    assert b.download_thumbnail_as_data_uri('fixture:demo-0001:0:thumb') != warm
    for invalid in ('https://example.invalid/image', 'fixture:../demo-0001:0:thumb',
                    'fixture:demo-0002:0:thumb', 'fixture:demo-0001:00:thumb',
                    'fixture:demo-0001:1:thumb', 'fixture:demo-0001:1:view',
                    'fixture:demo-0001:0:unknown', 'fixture:demo-0001:0:view:extra'):
        with pytest.raises(RuntimeError):
            b.download_thumbnail_as_data_uri(invalid)


def test_warm_three_pages_includes_thumbnails_and_every_view(tmp_path):
    baseline.FakeClient.fixtures = tmp_path
    baseline.FakeClient.rows = tuple({'illust_id': i, 'page_count': 2,
        'url': f'fixture:demo-{i:04d}:0:thumb'} for i in range(1, 121))
    baseline.FakeClient.id_map = {i: f'demo-{i:04d}' for i in range(1, 121)}
    baseline.FakeClient._image_cache.clear()
    for i in range(1, 121):
        (tmp_path / f'demo-{i:04d}-thumb.png').write_bytes(b'thumb')
        for j in (0, 1):
            (tmp_path / f'demo-{i:04d}-{j}-view.png').write_bytes(b'view')
    assert baseline.warm_fixture_images(baseline.FakeClient()) == 360
    assert len(baseline.FakeClient._image_cache) == 360


@pytest.mark.parametrize('padding', [
    (-1, 0, 0, 0), (0, 0, 0, -1), (float('nan'), 0, 0, 0),
    (float('inf'), 0, 0, 0), (float('-inf'), 0, 0, 0),
    (640, 0, 640, 0), (0, 360, 0, 360), (0, 0, 0),
])
def test_invalid_padding_rejected_before_artifact_access(tmp_path, monkeypatch, padding):
    safe_output = Mock(side_effect=AssertionError('must validate before artifact access'))
    monkeypatch.setattr(baseline, 'safe_output', safe_output)
    with pytest.raises(ValueError, match='page-padding'):
        baseline.launch(tmp_path / 'unused-source', tmp_path / 'unused-output', 1280, 720,
                        page_padding=padding)
    safe_output.assert_not_called()


def test_prepare_only_rejects_padding_instead_of_ignoring_it(tmp_path, monkeypatch):
    prepare = Mock()
    monkeypatch.setattr(baseline, 'prepare', prepare)
    with pytest.raises(SystemExit) as error:
        baseline.main(['--source', str(tmp_path), '--prepare-only', '--page-padding', '0', '0', '0', '0'])
    assert error.value.code == 2
    prepare.assert_not_called()


@pytest.mark.parametrize('padding', [None, (0, 0, 0, 0), (10.5714, 106.5, 9.4286, 4.8571)])
def test_launcher_padding_keeps_original_controls_and_default(tmp_path, monkeypatch, capsys, padding):
    monkeypatch.setattr(baseline, 'ROOT', tmp_path)
    monkeypatch.chdir(tmp_path)
    source = tmp_path / 'original'
    source.mkdir()
    output = tmp_path / 'artifacts' / 'baseline'
    copied = output / 'source'
    copied.mkdir(parents=True)
    copied_bytes = {name: f'# synthetic test copy: {name}\n'.encode() for name in baseline.SOURCES}
    for name, content in copied_bytes.items():
        (copied / name).write_bytes(content)
    fixture = b'[]'
    (output / 'fixture.json').write_bytes(fixture)
    (output / 'manifest.json').write_text(json.dumps({
        'source_files': {name: baseline.sha(content) for name, content in copied_bytes.items()},
        'fixture_sha256': baseline.sha(fixture),
    }), encoding='utf-8')
    ft, ranking, dispatcher = Mock(), Mock(), Mock()
    monkeypatch.setitem(sys.modules, 'flet', ft)
    monkeypatch.setitem(sys.modules, 'ranking_tab', ranking)
    monkeypatch.setitem(sys.modules, 'ui_dispatch', dispatcher)
    monkeypatch.setattr(baseline, 'isolate_imports', Mock())
    argv = ['--source', str(source), '--output', str(output), '--launch']
    if padding is not None:
        argv += ['--page-padding', *(str(value) for value in padding)]
    assert baseline.main(argv) == 0
    ft.run.assert_called_once()
    page = Mock()
    original_padding = object()
    page.padding = original_padding
    ft.run.call_args.args[0](page)
    if padding is None:
        ft.Padding.assert_not_called()
        assert page.padding is original_padding
    else:
        ft.Padding.assert_called_once_with(*map(float, padding))
        assert page.padding is ft.Padding.return_value
    built = ranking.build_ranking_tab.return_value
    page.add.assert_called_once_with(built.tab_ranking_bg)
    built.on_tab_open.assert_called_once()
    assert built.tab_ranking_bg.visible is True
    metadata = json.loads(capsys.readouterr().out)
    assert metadata['page_padding_logical_px'] == (None if padding is None else list(padding))
    for name, content in copied_bytes.items():
        assert (output / 'source' / name).read_bytes() == content
