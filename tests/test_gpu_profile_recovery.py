"""Independent-review startup failures, all generated configuration state."""
import sqlite3
import pytest
from nicegui import Client, ui
from test_native_gpu_profile import files
from test_native_profile_integration import _fake_native
from pixivvault_nicegui import bootstrap, native_startup
from pixivvault_nicegui.native_profile import NativeProfileStore, NativeProfileError
from pixivvault_nicegui.ui.native_profile_view import NativeProfileView


def test_model_without_any_saved_image_source_is_rejected_before_commit(tmp_path):
    parent, _, model=files(tmp_path)
    store=NativeProfileStore(tmp_path/'profile')
    try:
        with pytest.raises(NativeProfileError):
            store.update(save_parent=parent,similarity_model_file=model,expected_revision=0)
        assert store.snapshot().revision==0
        assert not list(parent.iterdir())
    finally:
        store.close()


def test_old_inconsistent_model_profile_opens_repair_instead_of_exit(tmp_path,monkeypatch):
    parent,cookie,model=files(tmp_path)
    store=NativeProfileStore(tmp_path/'profile')
    store.update(save_parent=parent,cookie_file=cookie,similarity_model_file=model,expected_revision=0)
    store.close()
    with sqlite3.connect(store.path) as connection:
        connection.execute('UPDATE native_profile_state SET cookie_file=NULL')
    before=store.path.read_bytes()
    config=bootstrap.parse_args(['--native','--profile-root',str(store.root)])
    assert config.profile_reference_unavailable is True
    assert config.similarity_index_root is None
    assert store.path.read_bytes()==before
    calls=_fake_native(monkeypatch)
    recovered=[]
    monkeypatch.setattr(native_startup,'register_profile_recovery',lambda binding:recovered.append(binding.store.snapshot()))
    monkeypatch.setattr(bootstrap,'register_pages',lambda **kw:pytest.fail('unavailable source was launched'))
    assert bootstrap.main(['--native','--profile-root',str(store.root)])==0
    assert len(calls)==len(recovered)==1
    assert recovered[0].similarity_model_file==model and not recovered[0].cookie_file
    assert not list(parent.iterdir())


def test_auto_without_model_is_never_silently_saved(tmp_path):
    parent,cookie,_=files(tmp_path)
    store=NativeProfileStore(tmp_path/'profile')
    try:
        with pytest.raises(NativeProfileError):
            store.update(save_parent=parent,cookie_file=cookie,similarity_auto=True,expected_revision=0)
        assert not store.snapshot().similarity_auto
    finally:
        store.close()


@pytest.mark.asyncio
async def test_clearing_model_explicitly_clears_auto_checkbox_and_next_launch(tmp_path):
    parent,cookie,model=files(tmp_path)
    store=NativeProfileStore(tmp_path/'profile')
    store.update(save_parent=parent,cookie_file=cookie,similarity_model_file=model,
                 similarity_auto=True,expected_revision=0)
    client=Client(ui.page('/_gpu_clear_model'))
    try:
        with client:
            view=NativeProfileView(store)
            view.open()
            assert view.auto_switch.value is True
            view.clear_model()
            assert view.auto_switch.value is False
            assert view.save(),view.status.text
            assert store.snapshot().similarity_model_file is None
            assert store.snapshot().similarity_auto is False
    finally:
        client.delete();store.close()


def test_uri_launch_keeps_explicit_cpu_mode_and_automatic_flag(tmp_path,monkeypatch):
    parent,cookie,model=files(tmp_path)
    _fake_native(monkeypatch)
    registrations=[]
    monkeypatch.setattr(bootstrap,'register_pages',lambda **kw:registrations.append(kw))
    assert bootstrap.main(['--native','--pixiv-cookie-file',str(cookie),
        '--pixiv-download-root',str(tmp_path/'managed'),
        '--similarity-index-root',str(tmp_path/'index'),
        '--similarity-model-file',str(model),'--similarity-provider','cpu','--similarity-auto'])==0
    command=registrations[0]['protocol_command']
    # build_protocol_command deliberately uses Windows CRT list2cmdline:
    # tokens without spaces are unquoted but remain distinct argv entries.
    assert '--similarity-provider cpu' in command
    assert '--similarity-auto' in command
