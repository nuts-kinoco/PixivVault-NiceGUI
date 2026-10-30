"""Persistent native CUDA options and CLI admission; generated paths only."""
from hashlib import sha256
from pathlib import Path
import sqlite3
import pytest
from nicegui import Client, ui
from pixivvault_nicegui.native_profile import NativeProfileStore, NativeProfileError, NativeProfileConflict, native_kwargs
from pixivvault_nicegui.bootstrap import parse_args
from pixivvault_nicegui.ui.native_profile_view import NativeProfileView


def files(tmp_path):
    parent = tmp_path / 'saved-parent'; parent.mkdir()
    input_root = tmp_path / 'inputs'; input_root.mkdir()
    cookie = input_root / 'cookie.txt'; cookie.write_bytes(b'not a real cookie')
    model = input_root / 'egara.onnx'; model.write_bytes(b'generated reference, never inferred')
    return parent, cookie, model


def test_gpu_profile_persists_without_model_load_or_storage_creation(tmp_path):
    parent, cookie, model = files(tmp_path)
    store = NativeProfileStore(tmp_path / 'profile')
    try:
        saved = store.update(save_parent=parent, cookie_file=cookie,
            similarity_model_file=model, similarity_provider='cuda',
            similarity_auto=True, expected_revision=0)
        assert saved.similarity_provider == 'cuda' and saved.similarity_auto is True
        assert store.snapshot() == saved == NativeProfileStore.peek(store.root)
        assert list(parent.iterdir()) == []
        assert model.read_bytes() == b'generated reference, never inferred'
        config = native_kwargs(saved, store.root)
        assert config['similarity_model_file'] == model
        assert config['similarity_index_root'] == parent/'PixivVault-NiceGUI'/'similarity-index'
        assert config['similarity_provider'] == 'cuda' and config['similarity_auto'] is True
        parsed = parse_args(['--native', '--profile-root', str(store.root)])
        assert parsed.similarity_provider == 'cuda'
        assert parsed.similarity_auto is True and parsed.similarity_model_file == model
    finally:
        store.close()


def test_old_profile_peek_is_compatible_and_never_migrates(tmp_path):
    store = NativeProfileStore(tmp_path/'profile')
    store.close()
    # No inference state existed before this upgrade.
    with sqlite3.connect(store.path) as db:
        db.execute('DROP TABLE IF EXISTS native_inference_state')
    before = store.path.read_bytes()
    result = NativeProfileStore.peek(store.root)
    assert result.similarity_model_file is None
    assert result.similarity_provider == 'auto' and result.similarity_auto is False
    assert store.path.read_bytes() == before


def test_gpu_profile_cas_and_invalid_options_preserve_prior_values(tmp_path):
    parent, cookie, model = files(tmp_path)
    store = NativeProfileStore(tmp_path/'profile')
    try:
        first = store.update(save_parent=parent, cookie_file=cookie,
                             similarity_model_file=model, expected_revision=0)
        with pytest.raises(NativeProfileConflict):
            store.update(similarity_provider='cuda', expected_revision=0)
        for invalid in ('directml', '', 'cuda:0'):
            with pytest.raises(NativeProfileError):
                store.update(similarity_provider=invalid, expected_revision=first.revision)
        assert store.snapshot() == first
        cleared = store.update(similarity_model_file=None, expected_revision=first.revision)
        assert 'similarity_index_root' not in native_kwargs(cleared, store.root)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_native_model_picker_and_cancel_only_change_draft(tmp_path):
    parent, cookie, model = files(tmp_path)
    store = NativeProfileStore(tmp_path/'profile')
    store.update(save_parent=parent, cookie_file=cookie, expected_revision=0)
    client = Client(ui.page('/_gpu_model_profile'))
    try:
        with client:
            view = NativeProfileView(store, pick_model=lambda: model)
            view.open()
            await view.choose_model()
            assert view.draft_model == model and store.snapshot().similarity_model_file is None
            view.cancel()
            view.open()
            assert view.draft_model is None
            await view.choose_model()
            view.provider_select.set_value('cuda')
            assert view.save(), view.status.text
            assert store.snapshot().similarity_model_file == model
            assert store.snapshot().similarity_provider == 'cuda'
        assert list(parent.iterdir()) == []
    finally:
        client.delete(); store.close()


def test_cli_gpu_requires_index_and_protects_model_root(tmp_path):
    parent, cookie, model = files(tmp_path)
    storage = tmp_path/'managed'
    base = ['--pixiv-cookie-file',str(cookie),'--pixiv-download-root',str(storage)]
    with pytest.raises(SystemExit):
        parse_args(base+['--similarity-model-file',str(model)])
    with pytest.raises(SystemExit):
        parse_args(base+['--similarity-model-file',str(model),
                        '--similarity-index-root',str(model.parent/'embeddings')])
    parsed = parse_args(base+['--similarity-model-file',str(model),
                             '--similarity-index-root',str(tmp_path/'index'),
                             '--similarity-provider','cpu'])
    assert parsed.similarity_provider == 'cpu'
    assert not storage.exists() and not (tmp_path/'index').exists()
