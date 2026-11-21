"""Alternate both ranking UIs and export only numeric or fixed-code evidence."""

from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
from time import perf_counter, sleep
from urllib.parse import urlsplit

from live_gate import Ledger
from preflight import SCRATCH, copied_path
from ranking_server import ORIGINAL_IMAGE, PORTS, nice_scope
from timing import StopRun, summarize_pairs

HERE = Path(__file__).resolve().parent
PYTHON = {"original": Path("C:/Users/nat/miniconda3/envs/stenv/python.exe"),
          "nice": Path(sys.executable)}

ORIGINAL_STATUS = r"""() => {
    const roots = [document];
    let result = '';
    for (const root of roots) {
      for (const el of root.querySelectorAll('*')) if (el.shadowRoot) roots.push(el.shadowRoot);
      for (const el of root.querySelectorAll('flt-semantics')) {
        const text = el.getAttribute('aria-label') || el.textContent || '';
        if (text.trim() === '取得中…') return 'loading';
        const match = text.match(/取得時刻:\s*\d\d:\d\d\s*\d+\s*件/);
        if (match) result = match[0];
      }
    }
    return result;
}"""
NICE_CARD = '[data-testid="ranking-view"] .pv-card'
NICE_STATUS = '[data-testid="ranking-notice"]'


def save(root, name, value):
    (root / name).write_text(json.dumps(value, ensure_ascii=True, indent=2), encoding="ascii")


def environment(root, side):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1",
               TEMP=str(SCRATCH), TMP=str(SCRATCH), TMPDIR=str(SCRATCH))
    for name in ("USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        path = root / "environment" / side / name
        path.mkdir(parents=True, exist_ok=True)
        env[name] = str(path)
    return env


@contextmanager
def server(root, side, owned, mode="serve"):
    port = PORTS[side]
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))
    process = subprocess.Popen(
        [str(PYTHON[side]), "-B", str(HERE / "ranking_server.py"), str(root), side, mode],
        cwd=root, env=environment(root, side), stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    receipt = "owned-servers.json" if mode == "serve" else "probe-servers.json"
    try:
        owned[side] = {"pid": process.pid, "port": port}
        save(root, receipt, owned)
        deadline = perf_counter() + 45
        while perf_counter() < deadline:
            if process.poll() is not None:
                raise StopRun("transport")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    break
            except OSError:
                sleep(0.15)
        else:
            raise StopRun("transport")
        yield f"http://127.0.0.1:{port}/"
    finally:
        if process.poll() is None:
            # The exact Popen root and its children are owned by this run.
            subprocess.run(["taskkill.exe", "/F", "/PID", str(process.pid), "/T"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=10, check=False)
        owned[side]["exit"] = process.wait(timeout=12)
        save(root, receipt, owned)


def two_frames(page):
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")


def original_ready(page, require_transition=False):
    page.wait_for_function(f"""requireTransition => {{
      const current = ({ORIGINAL_STATUS})();
      if (current === 'loading') window.__d6SawLoading = true;
      return current && current !== 'loading' && (!requireTransition || window.__d6SawLoading);
    }}""", arg=require_transition, timeout=18000)


def metadata_ready(page, side, require_transition=False):
    if side == "original":
        original_ready(page, require_transition)
    else:
        page.wait_for_function(r"""([heading, card]) => {
            const status = document.querySelector(heading);
            const first = document.querySelector(card);
            return status && /\d+\s*件/.test(status.textContent) &&
                first && (!window.__d6PreviousCard || first !== window.__d6PreviousCard);
        }""", arg=[NICE_STATUS, NICE_CARD], timeout=18000)
    two_frames(page)
    return perf_counter()


def image_ready(page, side):
    if side == "original":
        image = page.locator(f'flt-semantics[aria-label="{ORIGINAL_IMAGE}"]')
        image.or_(page.get_by_label(ORIGINAL_IMAGE, exact=True)).first.wait_for(
            state="visible", timeout=14000)
    else:
        page.wait_for_function("""cardSelector => {
            const image = document.querySelector(cardSelector + ' .pv-thumb img');
            return image && image.complete && image.naturalWidth > 0;
        }""", arg=NICE_CARD, timeout=14000)
    two_frames(page)
    return perf_counter()


def open_page(context, side, url):
    page = context.new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=25000)
    if side == "original":
        page.locator("flt-semantics-placeholder").first.evaluate("(element) => element.click()", timeout=18000)
        label = page.locator('flt-semantics[aria-label="更新"]')
        button = label.or_(page.locator("flt-semantics").filter(has_text=re.compile(r"^更新$"))).first
    else:
        page.wait_for_function("() => window.did_handshake === true", timeout=18000)
        button = page.get_by_test_id("ranking-fetch")
    button.wait_for(state="visible", timeout=15000)
    return page, button


def check_product_stop(ledger):
    from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkGateStorageError, ManagedNetworkStopped

    try:
        with nice_scope(ledger.root):
            pass
    except (ManagedNetworkGateStorageError, ManagedNetworkStopped):
        ledger.halt("product_stop")
        raise StopRun("product_stop") from None


def sample(ledger, page, button, side, pair, observe_image):
    check_product_stop(ledger)
    if side == "original":
        page.evaluate("() => window.__d6SawLoading = false")
    else:
        page.evaluate("selector => window.__d6PreviousCard = document.querySelector(selector)", NICE_CARD)
    button.scroll_into_view_if_needed()
    box = button.bounding_box()
    if not box:
        raise StopRun("transport")
    begin = perf_counter()
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    metadata = metadata_ready(page, side, require_transition=True)
    paint = image_ready(page, side) if observe_image else metadata
    check_product_stop(ledger)
    state = ledger.snapshot()
    if state["stop"]:
        raise StopRun(state["stop"])
    events = [event for event in state["events"]
              if event["app"] == side and begin <= event["start"] <= event["end"] <= paint]
    if sum(event["kind"] == "ranking" and event["status"] == 200 for event in events) != 1:
        raise StopRun("transport")

    def interval(start, end):
        return [(max(begin, start) - begin) * 1000, (end - begin) * 1000]

    api = [interval(e["start"], e["end"]) for e in events if e["kind"] != "image"]
    images = [interval(e["start"], e["end"]) for e in events if e["kind"] == "image"]
    return {
        "app": side, "pair": pair, "operation": "ranking_official", "condition": "warm",
        "start_ms": 0, "paint_ms": (paint - begin) * 1000, "network_ms": api + images,
        "api_ms": api, "image_ms": images,
        "pace_ms": [interval(e["queued"], e["start"]) for e in events],
    }, (metadata - begin) * 1000


def measure(root, repetitions):
    from playwright.sync_api import sync_playwright

    ledger = Ledger(root)
    state = ledger.snapshot()
    if state["stop"]:
        raise StopRun(state["stop"])
    paths = {side: urlsplit(f"http://127.0.0.1:{port}").netloc for side, port in PORTS.items()}
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(Path(os.environ["LOCALAPPDATA"]) / "ms-playwright"))
    owned, rows, stages = {}, [], []
    phase = "launch"
    blocked = 0
    image_visibility = {}
    try:
        check_product_stop(ledger)
        with ExitStack() as stack:
            urls = {side: stack.enter_context(server(root, side, owned)) for side in PORTS}
            playwright = stack.enter_context(sync_playwright())
            owned["playwright"] = {"pid": playwright._impl_obj._connection._transport._proc.pid}
            save(root, "owned-servers.json", owned)
            context = playwright.chromium.launch_persistent_context(
                str(root / "browser"), headless=True, env=environment(root, "browser"),
                viewport={"width": 1280, "height": 1000}, service_workers="block",
                args=["--proxy-server=http://127.0.0.1:1", "--disable-background-networking",
                      "--disable-component-update", "--no-first-run"],
                timeout=25000,
            )
            stack.callback(context.close)

            def browser_route(request):
                nonlocal blocked
                url = urlsplit(request.request.url)
                if url.scheme == "http" and url.netloc in paths.values():
                    request.continue_()
                else:
                    blocked += 1
                    request.abort()

            context.route("**/*", browser_route)
            pages = {}
            for side, url in urls.items():
                phase = f"{side}_open"
                page, button = pages[side] = open_page(context, side, url)
                phase = f"{side}_warm"
                metadata_ready(page, side)
                phase = f"{side}_warm_image"
                try:
                    image_ready(page, side)
                    image_visibility[side] = True
                except Exception as error:
                    if ledger.snapshot()["stop"] or type(error).__name__ != "TimeoutError":
                        raise
                    image_visibility[side] = False
                check_product_stop(ledger)
            observe_image = all(image_visibility.values())
            for index in range(repetitions):
                for side in (("original", "nice") if index % 2 == 0 else ("nice", "original")):
                    phase = f"{side}_measure"
                    row, metadata = sample(ledger, *pages[side], side, index + 1, observe_image)
                    rows.append(row)
                    stages.append({"app": side, "pair": index + 1, "metadata_ms": metadata})
                    save(root, "ranking-samples.json", rows)
                    save(root, "ranking-metadata.json", stages)
        result = {"status": "completed"}
    except Exception as error:
        state = ledger.snapshot()
        if not state["stop"]:
            ledger.halt(error.reason if isinstance(error, StopRun) else "transport")
        result = {"status": "stopped", "phase": phase, "classification": type(error).__name__,
                  "stop": ledger.snapshot()["stop"]}
    result["browser_external_denied"] = blocked
    result["first_image_observed"] = image_visibility
    result["timing_endpoint"] = "first_image" if image_visibility == {side: True for side in PORTS} else "metadata"
    save(root, "ranking-outcome.json", result)
    save(root, "ranking-summary.json", summarize_pairs(rows))
    return 0 if result["status"] == "completed" else 2


if __name__ == "__main__":
    root = copied_path(SCRATCH, Path(sys.argv[1]))
    repetitions = int(sys.argv[2]) if len(sys.argv) == 3 else 10
    if not 10 <= repetitions <= 20:
        raise SystemExit("10 to 20 pairs required")
    raise SystemExit(measure(root, repetitions))
