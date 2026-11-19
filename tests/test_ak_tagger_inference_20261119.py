import io
from types import SimpleNamespace
from PIL import Image
from pixivvault_nicegui.local_tagger import LocalTagger, prepare_image
from test_ak_tagger_20261119 import make_model


def png():
    output = io.BytesIO()
    Image.new('RGB', (4, 2), (255, 0, 0)).save(output, format='PNG')
    return output.getvalue()


def test_preprocessing_is_white_padded_bgr_without_numpy():
    data = prepare_image(png())
    assert data.size == (448, 448)
    assert data.getpixel((0, 0)) == (255, 255, 255)
    assert data.getpixel((224, 224)) == (0, 0, 255)


def test_fake_session_thresholds_and_sha_cache(tmp_path):
    make_model(tmp_path)
    (tmp_path / 'selected_tags.csv').write_text(
        'tag_id,name,category,count\n0,general,0,1\n1,character,4,1\n2,rating,9,1\n', encoding='utf-8')
    # Rebuild the manifest after changing the CSV.
    import json, hashlib
    manifest = tmp_path / 'MODEL_SOURCE.json'
    source = json.loads(manifest.read_text(encoding='utf-8'))
    for row in source['files']:
        row['sha256'] = hashlib.sha256((tmp_path / row['path']).read_bytes()).hexdigest()
    manifest.write_text(json.dumps(source), encoding='utf-8')
    class Fake:
        calls = 0
        def get_inputs(self): return [SimpleNamespace(name='input')]
        def run(self, outputs, inputs):
            self.calls += 1
            assert inputs['input'].getpixel((224, 224)) == (0, 0, 255)
            return [[(.5, .9, .99)]]
    fake = Fake()
    tagger = LocalTagger(tmp_path, session_factory=lambda _: fake, tensor_factory=lambda image: image)
    assert tagger.predict(png()) == [('character', .9), ('general', .5)]
    assert tagger.predict(png()) == [('character', .9), ('general', .5)]
    assert fake.calls == 1
    tagger.close()
    assert tagger.session is None


def test_missing_runtime_status(tmp_path, monkeypatch):
    make_model(tmp_path)
    tagger = LocalTagger(tmp_path)
    monkeypatch.setattr(tagger, '_runtime_available', lambda: False)
    assert tagger.verify() == 'onnxruntime なし'
    assert tagger.predict(png()) == []


def test_load_failure_remains_visible(tmp_path):
    make_model(tmp_path)
    def fail(_):
        raise RuntimeError('bad model')
    tagger = LocalTagger(tmp_path, session_factory=fail, tensor_factory=lambda image: image)
    assert tagger.predict(png()) == []
    assert tagger.status == '読込失敗'
    assert tagger.verify() == '読込失敗'


def test_explicit_cuda_does_not_silently_fall_back(tmp_path, monkeypatch):
    import sys
    make_model(tmp_path)
    monkeypatch.setitem(sys.modules, 'onnxruntime', SimpleNamespace(get_available_providers=lambda: ['CPUExecutionProvider']))
    tagger = LocalTagger(tmp_path, providers=['CUDAExecutionProvider', 'CPUExecutionProvider'], tensor_factory=lambda image: image)
    monkeypatch.setattr(tagger, '_runtime_available', lambda: True)
    assert tagger.predict(png()) == []
    assert tagger.status == '読込失敗'
