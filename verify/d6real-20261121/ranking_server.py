"""Launch the ordinary Flet or NiceGUI ranking screen on its own loopback port."""

from contextlib import closing, contextmanager
import json
import logging
import os
from pathlib import Path
import sys
import traceback

from preflight import SCRATCH, WORKTREE, copied_path
from live_gate import Ledger
from prepare_live import selected_media
from rank_adapters import RankingWire
from rank_trace import RenderTrace, instrument_nice, instrument_original
from timing import StopRun

PORTS = {"original": 22830, "nice": 22840}
ORIGINAL_IMAGE = "D6 ranking first thumbnail"


@contextmanager
def nice_scope(root):
    from pixivvault_nicegui.managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
    from pixivvault_nicegui.managed_network_stop_shared_scope import open_existing_windows_user_stop
    from pixivvault_nicegui.managed_network_stop_startup import attach_selected_histories
    from pixivvault_nicegui.test_owner_runtime import isolated_test_workspace

    with isolated_test_workspace(root):
        profile = ManagedProfileNetworkStopScope(root / "profile")
        shared = open_existing_windows_user_stop()
        attach_selected_histories(shared, selected_media(root / "storage"))
        profile.shared_owner = shared
        profile.require_allowed()
        yield profile, shared


def original_server(root, mode, product_check):
    import datetime
    import webbrowser

    original = root / "original"
    os.chdir(original)
    os.environ["PIXIVVAULT_DB_PATH"] = str(original / "ranking.sqlite3")
    os.environ["PV_NO_BROWSER_COOKIES"] = "1"
    os.environ["FLET_FORCE_WEB_SERVER"] = "1"
    os.environ["FLET_SERVER_PORT"] = str(PORTS["original"])
    os.environ["FLET_SERVER_IP"] = "127.0.0.1"
    sys.path.insert(0, str(original))
    wire = RankingWire(root, "original", original / "cookies.txt", fake=mode == "fake")
    wire.install_original(product_check)
    import flet as ft
    from database import Database
    from pixiv_client import PixivClient

    db = Database()
    db.set_setting("last_sync_date", datetime.datetime.now().isoformat())
    db.set_setting("api_retry_count", "1")
    if mode != "probe":
        try:
            identity = PixivClient(db=db).get_my_user_id()
        except (StopRun, ImportError):
            raise
        except Exception:
            evidence = wire.ledger.snapshot()
            events = evidence["events"]
            if events and events[-1]["app"] == "original" and events[-1]["kind"] == "identity":
                if events[-1]["status"] == 200 and not evidence["stop"]:
                    raise StopRun("authentication") from None
            raise
        if identity != wire.claimed:
            raise StopRun("authentication")
    if mode == "auth":
        return
    instrument_original(original, RenderTrace(root / "original-render.jsonl"))
    import gui

    image_init = ft.Image.__init__

    def measured_image(self, *args, **kwargs):
        uri = kwargs.get("src")
        if wire.image_bytes and isinstance(uri, str) and uri.startswith("data:image/"):
            if uri.partition(",")[2] == wire.image_bytes:
                kwargs["semantics_label"] = ORIGINAL_IMAGE
        image_init(self, *args, **kwargs)

    ft.Image.__init__ = measured_image
    # The normal screen and click handlers run without a scheduler or an
    # extension listener. Keep its ordinary ranking-tab initialization.
    gui.TAB_INITIAL = gui.TAB_RANKING
    webbrowser.open = lambda *args, **kwargs: False
    ft.run(lambda page: gui.main_window(page, db=db), assets_dir="assets",
           view=ft.AppView.WEB_BROWSER, host="127.0.0.1", port=PORTS["original"], no_cdn=True)


def nice_server(root, mode):
    from nicegui import ui
    from pixivvault_nicegui.bootstrap import register_pages
    from pixivvault_nicegui.managed_network_stop_shared_transport import create_shared_profile_reader
    from pixivvault_nicegui.native_profile import NativeProfileStore
    from pixivvault_nicegui.native_startup import NativeProfileBinding
    from pixivvault_nicegui.services import ApplicationServices

    cookie = root / "credentials" / "cookies.txt"
    wire = RankingWire(root, "nice", cookie, fake=mode == "fake")
    wire.install_nice()
    with nice_scope(root) as (profile, shared):
        if mode == "auth":
            reader = create_shared_profile_reader(profile, cookie, shared.gate,
                        shared_owner_check=shared._check_owner)
            try:
                if reader.require_identity() != wire.claimed:
                    raise StopRun("authentication")
            finally:
                reader.close()
            return
        # Measure the user-triggered ranking path without startup discovery
        # refill. The isolated discovery DB is still used for ranking tags.
        ApplicationServices.start_discovery_initial_load = lambda self: None
        instrument_nice(RenderTrace(root / "nice-render.jsonl"))
        base = root / "storage" / "PixivVault-NiceGUI"
        with closing(NativeProfileStore(profile.root)) as store:
            binding = NativeProfileBinding(store, launch_snapshot=store.snapshot())
            binding.managed_network_stop_scope = profile
            binding.shared_network_stop_scope = shared
            binding.auth_stop_required = binding.auth_shared_stop_required = True
            register_pages(pixiv_cookie_file=cookie, managed_network_stop_scope=profile,
                           shared_network_stop_scope=shared, native_profile=binding,
                           startup_tab="ranking", discovery_root=base / "discovery",
                           preferences_root=root / "ranking-preferences",
                           image_cache_root=root / "ranking-image-cache")
            if mode != "check":
                ui.run(host="127.0.0.1", port=PORTS["nice"], show=False, reload=False,
                       native=False, access_log=False, uvicorn_logging_level="critical")


def main():
    root = copied_path(SCRATCH, Path(sys.argv[1]))
    side, mode = sys.argv[2:]
    permitted = {"original": ("auth", "serve", "probe", "fake"),
                 "nice": ("auth", "serve", "check", "probe", "fake")}
    if side not in permitted or mode not in permitted[side]:
        raise ValueError("invalid measurement mode")
    if (mode == "fake") != (root / "fake-wire-only").is_file():
        raise ValueError("fixture root requires fake transport")
    ledger = Ledger(root)
    if mode == "probe" and not ledger.snapshot()["stop"]:
        raise ValueError("screen probe requires a stopped ledger")
    logging.disable(logging.CRITICAL)
    try:
        if side == "original":
            with nice_scope(root) as (profile, _shared):
                original_server(root, mode, profile.require_allowed)
        else:
            nice_server(root, mode)
        result = {"status": "ok"}
    except Exception as error:
        # Application error messages can contain request text or used paths.
        if isinstance(error, StopRun):
            ledger.halt(error.reason)
        else:
            current = ledger.snapshot()["stop"]
            if not current:
                from pixivvault_nicegui.pixiv_read_client import AuthenticationMissing
                if isinstance(error, AuthenticationMissing):
                    ledger.halt("authentication")
                else:
                    ledger.halt("transport")
        result = {"status": "stopped", "classification": type(error).__name__,
                  "stop": ledger.snapshot()["stop"],
                  "frames": [[Path(f.filename).name, f.lineno] for f in traceback.extract_tb(error.__traceback__)[-7:]]}
    (root / f"{side}-{mode}.json").write_text(json.dumps(result), encoding="ascii")
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
