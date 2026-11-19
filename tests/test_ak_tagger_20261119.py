import hashlib
import json

from pixivvault_nicegui.local_tagger import verify_model


def make_model(root):
    files = {'model.onnx': b'model', 'selected_tags.csv': b'tag_id,name,category,count\n0,cat,0,1\n'}
    for name, data in files.items():
        (root / name).write_bytes(data)
    (root / 'MODEL_SOURCE.json').write_text(json.dumps({'files': [
        {'path': name, 'sha256': hashlib.sha256(data).hexdigest()} for name, data in files.items()
    ]}), encoding='utf-8')


def test_missing_and_digest_mismatch(tmp_path):
    assert verify_model(tmp_path / 'absent')[0] == '未検出'
    make_model(tmp_path)
    (tmp_path / 'model.onnx').write_bytes(b'changed')
    assert verify_model(tmp_path)[0] == 'SHA 不一致'


def test_verified_identity_can_be_reused_and_detects_change(tmp_path, monkeypatch):
    make_model(tmp_path)
    status, receipt = verify_model(tmp_path)
    assert status == '利用可能'
    import pixivvault_nicegui.local_tagger as tagger
    monkeypatch.setattr(tagger, '_sha256', lambda _: (_ for _ in ()).throw(AssertionError('rehash')))
    assert verify_model(tmp_path, receipt)[0] == '利用可能'
    (tmp_path / 'model.onnx').write_bytes(b'changed')
    monkeypatch.undo()
    assert verify_model(tmp_path, receipt)[0] == 'SHA 不一致'


def test_invalid_manifest_fails_closed(tmp_path):
    make_model(tmp_path)
    (tmp_path / 'MODEL_SOURCE.json').write_text('[]', encoding='utf-8')
    assert verify_model(tmp_path)[0] == '読込失敗'
