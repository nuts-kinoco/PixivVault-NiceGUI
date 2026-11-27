"""Synthetic native bootstrap must not leak a live reader on partial failure."""

import time
from types import SimpleNamespace

import pytest

from pixivvault_nicegui import bootstrap
from pixivvault_nicegui.managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopGate
from pixivvault_nicegui.native_profile import NativeProfileStore
from pixivvault_nicegui.native_startup import NativeProfileBinding


def test_second_profile_reader_construction_failure_closes_first(tmp_path, monkeypatch):
    profile = tmp_path / "generated-profile"
    store = NativeProfileStore(profile)
    scope = ManagedProfileNetworkStopScope(profile)
    cookie_dir = tmp_path / "generated-credential"
    cookie_dir.mkdir()
    cookie = cookie_dir / "generated-cookie.txt"
    cookie.write_text(
        "# Netscape HTTP Cookie File\n"
        f".pixiv.net\tTRUE\t/\tTRUE\t{int(time.time())+86400}\tPHPSESSID\t55_generatedonly\n",
        encoding="utf-8",
    )
    store.update(cookie_file=cookie, expected_revision=0)
    binding = NativeProfileBinding(store)
    readers = []
    real_create = scope._create_reader_locked

    def fail_second(path, gate_revision, *, transport=None):
        if readers:
            raise RuntimeError("synthetic second reader failure")
        reader = real_create(path, gate_revision, transport=lambda *_args: pytest.fail("network not allowed"))
        readers.append(reader)
        return reader

    monkeypatch.setattr(scope, "_create_reader_locked", fail_second)
    monkeypatch.setattr(bootstrap, "_registered", False)
    # A generated, explicitly injected shared owner is required before an
    # authenticated reader may be constructed. This lifecycle test does not
    # open or provision any real Windows user stop state.
    generated_shared_gate = ManagedNetworkStopGate(tmp_path / "generated-shared" / "stop.sqlite3")
    generated_shared = SimpleNamespace(gate=generated_shared_gate, _check_owner=lambda: None)
    try:
        with pytest.raises(RuntimeError, match="synthetic second reader failure"):
            bootstrap.register_pages(
                pixiv_cookie_file=cookie,
                pixiv_download_root=tmp_path / "generated-images",
                novel_download_root=tmp_path / "generated-novels",
                native_profile=binding,
                managed_network_stop_scope=scope,
                shared_network_stop_scope=generated_shared,
            )
        assert len(readers) == 1
        assert readers[0]._closed, "the first owned HTTPS client must be closed on second-reader failure"
    finally:
        for reader in readers:
            if not reader._closed:
                reader.close()
        store.close()
