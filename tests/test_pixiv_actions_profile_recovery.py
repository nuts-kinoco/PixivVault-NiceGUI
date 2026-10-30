"""An explicit action flag must not bypass safe native reference recovery."""
import pytest

from pixivvault_nicegui import bootstrap, native_startup
from pixivvault_nicegui.native_profile import NativeProfileStore
from test_pixiv_actions_capability import stub_server_and_native


def test_enabled_profile_with_missing_cookie_opens_recovery_without_actions(tmp_path, monkeypatch):
    credentials = tmp_path / 'credentials'
    credentials.mkdir()
    cookie = credentials / 'synthetic-cookie.txt'
    cookie.write_text('synthetic reference only', encoding='utf-8')
    save_parent = tmp_path / 'save-parent'
    save_parent.mkdir()
    root = tmp_path / 'profile'
    profile = NativeProfileStore(root)
    try:
        saved = profile.update(cookie_file=cookie, save_parent=save_parent,
                               expected_revision=profile.snapshot().revision)
    finally:
        profile.close()
    cookie.unlink()  # Only this test's newly generated reference is removed.
    args = ['--native', '--profile-root', str(root), '--enable-pixiv-actions']
    config = bootstrap.parse_args(args)
    assert config.profile_reference_unavailable and config.enable_pixiv_actions
    assert config.pixiv_cookie_file is None
    normal_registration = stub_server_and_native(monkeypatch)
    recovered = []
    monkeypatch.setattr(native_startup, 'register_profile_recovery',
                        lambda binding, **kwargs: recovered.append((binding, kwargs)))
    from pixivvault_nicegui import pixiv_actions
    monkeypatch.setattr(pixiv_actions, 'PixivActions',
                        lambda *_args, **_kwargs: pytest.fail('recovery must not create a writer owner'))
    assert bootstrap.main(args) == 0
    assert len(recovered) == 1 and normal_registration == []
    assert NativeProfileStore.peek(root) == saved
    assert not cookie.exists()
    assert list(save_parent.iterdir()) == []
    # A standalone action flag still has no implicit Cookie/account authority.
    with pytest.raises(SystemExit):
        bootstrap.parse_args(['--enable-pixiv-actions', '--check'])
