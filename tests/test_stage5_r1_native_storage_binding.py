"""Generated native profile cannot adopt another profile's managed save base."""

import time
from types import SimpleNamespace

import pytest

from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkGateStorageError
from pixivvault_nicegui.managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
from pixivvault_nicegui.managed_network_stop_storage_binding import (
    OWNER_MARKER, bind_new_native_storage, prepare_native_storage_binding,
    require_native_storage_binding,
)
from pixivvault_nicegui.native_profile import NativeProfileStore, provision_storage
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui import bootstrap
from pixivvault_nicegui.managed_network_stop_shared_transport import create_shared_profile_reader
from stage5_generated_shared_owner import generated_shared_scope


def _profile(tmp_path, name, cookie, save_parent, *, prepare=False):
    path = tmp_path / name
    store = NativeProfileStore(path)
    store.close()
    scope = ManagedProfileNetworkStopScope(path)
    store = NativeProfileStore(path)
    try:
        saved = store.update(cookie_file=cookie, save_parent=save_parent,
                             expected_revision=0)
        if prepare:
            prepare_native_storage_binding(scope, save_parent)
        roots = provision_storage(saved, path)
    finally:
        store.close()
    return scope, roots


def _cookie(tmp_path):
    folder = tmp_path / "generated-credential"
    folder.mkdir()
    cookie = folder / "generated-cookie.txt"
    cookie.write_text(
        "# Netscape HTTP Cookie File\n"
        f".pixiv.net\tTRUE\t/\tTRUE\t{int(time.time()) + 86400}\tPHPSESSID\t55_generatedonly\n",
        encoding="utf-8",
    )
    return cookie


def test_storage_parent_stays_associated_to_original_profile(tmp_path):
    cookie = _cookie(tmp_path)
    parent = tmp_path / "generated-save"
    parent.mkdir()
    first, roots = _profile(tmp_path, "generated-profile-a", cookie, parent, prepare=True)
    bind_new_native_storage(first, parent)
    assert (parent / "PixivVault-NiceGUI" / OWNER_MARKER).is_file()
    require_native_storage_binding(first, roots["pixiv_download_root"])
    bind_new_native_storage(first, parent)  # restart

    second, _ = _profile(tmp_path, "generated-profile-b", cookie, parent)
    with pytest.raises(ManagedNetworkGateStorageError):
        bind_new_native_storage(second, parent)
    with pytest.raises(ManagedNetworkGateStorageError):
        require_native_storage_binding(second, roots["pixiv_download_root"])


def test_existing_nonempty_unbound_native_base_requires_reviewed_migration(tmp_path):
    cookie = _cookie(tmp_path)
    parent = tmp_path / "generated-save"
    parent.mkdir()
    scope, roots = _profile(tmp_path, "generated-profile", cookie, parent, prepare=True)
    roots["pixiv_download_root"].mkdir()
    with pytest.raises(ManagedNetworkGateStorageError):
        bind_new_native_storage(scope, parent)
    assert not (parent / "PixivVault-NiceGUI" / OWNER_MARKER).exists()


def test_existing_empty_marked_base_cannot_be_silently_adopted(tmp_path):
    cookie = _cookie(tmp_path)
    parent = tmp_path / "generated-existing-save"
    parent.mkdir()
    scope, _roots = _profile(tmp_path, "generated-new-profile", cookie, parent)
    # This is the exact on-disk state of a base provisioned earlier by another
    # profile that never started a media job. Its old auth-stop can live only
    # in the other profile: empty media folders prove nothing about that stop.
    with pytest.raises(ManagedNetworkGateStorageError, match="migration"):
        bind_new_native_storage(scope, parent)
    assert not (parent / "PixivVault-NiceGUI" / OWNER_MARKER).exists()


def test_product_requires_storage_binding_when_using_native_root(tmp_path):
    cookie = _cookie(tmp_path)
    parent = tmp_path / "generated-save"
    parent.mkdir()
    scope, roots = _profile(tmp_path, "generated-profile", cookie, parent, prepare=True)
    shared = generated_shared_scope(tmp_path)
    scope.shared_owner = shared
    reader = create_shared_profile_reader(scope, cookie, shared.gate,
        transport=lambda *_: pytest.fail("network forbidden"),
        shared_owner_check=shared._check_owner)
    try:
        with pytest.raises(ManagedNetworkGateStorageError):
            rejected = ApplicationServices(
                live_client=reader, pixiv_download_root=roots["pixiv_download_root"],
                managed_network_stop_scope=scope,
                shared_network_stop_scope=shared,
            )
            rejected.close()
        bind_new_native_storage(scope, parent)
        app = ApplicationServices(
            live_client=reader, pixiv_download_root=roots["pixiv_download_root"],
            managed_network_stop_scope=scope,
            shared_network_stop_scope=shared,
        )
        app.close()
    finally:
        reader.close()


def test_interrupted_first_use_binding_is_recovered_only_by_original_profile(tmp_path):
    cookie = _cookie(tmp_path)
    parent = tmp_path / "generated-save"
    parent.mkdir()
    scope, roots = _profile(tmp_path, "generated-profile", cookie, parent,
                            prepare=True)
    # Simulate a process lost after storage was provisioned but before bind.
    assert not (parent / "PixivVault-NiceGUI" / OWNER_MARKER).exists()
    resumed = ManagedProfileNetworkStopScope(scope.root)
    prepare_native_storage_binding(resumed, parent)
    bind_new_native_storage(resumed, parent)
    require_native_storage_binding(resumed, roots["pixiv_download_root"])

    another, _ = _profile(tmp_path, "generated-other-profile", cookie, parent)
    with pytest.raises(ManagedNetworkGateStorageError):
        prepare_native_storage_binding(another, parent)


def test_real_native_bootstrap_save_parent_before_cookie_can_then_add_cookie(
    tmp_path, monkeypatch,
):
    """No real UI/network: run the actual Native main startup and owner path."""
    from nicegui import app, ui
    import pixivvault_nicegui.native_layout as layout

    real_find_spec = bootstrap.importlib.util.find_spec
    monkeypatch.setattr(
        bootstrap.importlib.util, "find_spec",
        lambda name, *args, **kwargs:
            object() if name == "webview" else real_find_spec(name, *args, **kwargs),
    )
    monkeypatch.setattr(
        layout, "current_startup_layout",
        lambda: SimpleNamespace(width=960, height=720, x=0, y=0),
    )
    monkeypatch.setattr(app.native, "window_args", {})
    monkeypatch.setattr(app.native, "settings", {})
    starts, registrations = [], []
    monkeypatch.setattr(ui, "run", lambda **kwargs: starts.append(kwargs))
    monkeypatch.setattr(bootstrap, "register_pages",
                        lambda **kwargs: registrations.append(kwargs))

    profile = tmp_path / "generated-profile"
    parent = tmp_path / "generated-save-parent"
    parent.mkdir()
    # Every first-use Native launch, even before a Cookie is selected, needs
    # its separately reviewed user STOP owner. This owner is synthetic only.
    import hashlib
    import json
    from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopGate, _file_identity
    from pixivvault_nicegui import managed_network_stop_shared_scope as userstop
    owner_dir = tmp_path / "generated-reviewed-user-owner"
    owner_dir.mkdir()
    gate = ManagedNetworkStopGate(owner_dir / userstop._GATE_FILE)
    marker = dict(
        schema=userstop._OWNER_SCHEMA,
        state="sealed",
        root_identity=list(userstop._file_identity_directory(owner_dir)),
        gate_identity=list(_file_identity(gate.path)),
        lock_identity=list(_file_identity(gate.lock_path)),
        history_proof=hashlib.sha256(b"synthetic-generated-only-history").hexdigest(),
    )
    (owner_dir / userstop._OWNER_FILE).write_text(json.dumps(marker), encoding="utf-8")
    monkeypatch.setattr(userstop, "open_existing_windows_user_stop",
                        lambda: userstop.ManagedUserNetworkStopScope(owner_dir))
    store = NativeProfileStore(profile)
    store.close()
    args = ["--native", "--profile-root", str(profile)]
    assert bootstrap.main(args) == 0  # newly created unconfigured profile
    store = NativeProfileStore(profile)
    try:
        store.update(save_parent=parent, expected_revision=0)
    finally:
        store.close()
    assert bootstrap.main(args) == 0  # create and bind before any Cookie
    assert (parent / "PixivVault-NiceGUI" / OWNER_MARKER).is_file()

    cookie = _cookie(tmp_path)
    store = NativeProfileStore(profile)
    try:
        store.update(cookie_file=cookie, expected_revision=1)
    finally:
        store.close()
    assert bootstrap.main(args) == 0  # same parent, now with Cookie
    assert len(starts) == len(registrations) == 3
    # This is the actual native bootstrap wiring, not a unit fixture which
    # manually injects safety attributes into NativeProfileBinding. Removing
    # either startup assignment must fail this regression.
    for accepted in registrations:
        binding = accepted["native_profile"]
        assert binding.auth_stop_required is True
        assert (binding.managed_network_stop_scope
                is accepted["managed_network_stop_scope"])
        assert binding.auth_shared_stop_required is True
    assert (registrations[-1]["shared_network_stop_scope"]
            is registrations[-1]["native_profile"].shared_network_stop_scope)


def test_old_configured_profile_without_stop_scope_shows_offline_recovery(
    tmp_path, monkeypatch,
):
    from nicegui import app, ui
    from pixivvault_nicegui import native_layout, native_startup

    cookie = _cookie(tmp_path)
    profile = tmp_path / "generated-legacy-profile"
    store = NativeProfileStore(profile)
    try:
        store.update(cookie_file=cookie, expected_revision=0)
    finally:
        store.close()
    # It is an OLD configured profile: there is intentionally no scope marker.
    actual_spec = bootstrap.importlib.util.find_spec
    monkeypatch.setattr(
        bootstrap.importlib.util, "find_spec",
        lambda name, *a, **kw:
            object() if name == "webview" else actual_spec(name, *a, **kw),
    )
    monkeypatch.setattr(
        native_layout, "current_startup_layout",
        lambda: SimpleNamespace(width=960, height=720, x=0, y=0),
    )
    monkeypatch.setattr(app.native, "window_args", {})
    monkeypatch.setattr(app.native, "settings", {})
    ui_runs, recovery = [], []
    monkeypatch.setattr(ui, "run", lambda **kw: ui_runs.append(kw))
    monkeypatch.setattr(
        bootstrap, "register_pages",
        lambda **kw: pytest.fail("stop migration cannot launch media"),
    )
    monkeypatch.setattr(
        native_startup, "register_profile_recovery",
        lambda binding, **kw: recovery.append((binding, kw)),
    )
    assert bootstrap.main(["--native", "--profile-root", str(profile)]) == 0
    assert len(ui_runs) == len(recovery) == 1
    assert recovery[0][1] == {"stop_migration_required": True}
    assert recovery[0][0]._closed
