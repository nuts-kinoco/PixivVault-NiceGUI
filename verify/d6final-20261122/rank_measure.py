"""Alternate both ranking UIs and export only numeric or fixed-code evidence."""

from contextlib import ExitStack, contextmanager
import argparse
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
from time import perf_counter, sleep
from urllib.parse import urlsplit

from live_gate import Ledger, require_operator_confirmation
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
    receipt = "owned-servers.json"
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
        page.wait_for_function(r"""([heading, card, requireTransition]) => {
            const status = document.querySelector(heading);
            const first = document.querySelector(card);
            return status && /\d+\s*件/.test(status.textContent) && first &&
                (!requireTransition || window.__d6SawLoading);
        }""", arg=[NICE_STATUS, NICE_CARD, require_transition], timeout=18000)
    two_frames(page)
    return perf_counter()


def image_ready(page, side):
    decoded = None
    if side == "original":
        image = page.locator(f'flt-semantics[aria-label="{ORIGINAL_IMAGE}"]')
        image.or_(page.get_by_label(ORIGINAL_IMAGE, exact=True)).first.wait_for(
            state="visible", timeout=14000)
        two_frames(page)
    else:
        page.wait_for_function("""cardSelector => {
            const image = document.querySelector(cardSelector + ' .pv-thumb img');
            return image && image.complete && image.naturalWidth > 0;
        }""", arg=NICE_CARD, timeout=14000)
        decoded = page.evaluate("""async cardSelector => {
          const image = document.querySelector(cardSelector + ' .pv-thumb img');
          const begin = performance.now();
          await image.decode();
          const decodeTime = performance.now() - begin;
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          return decodeTime;
        }""", NICE_CARD)
    return perf_counter(), decoded


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
    from pixivvault_nicegui.managed_network_stop_gate import (
        ManagedNetworkGateBusy, ManagedNetworkGateStorageError, ManagedNetworkStopped,
    )

    deadline = perf_counter() + 3
    while True:
        reason = ledger.snapshot()["stop"]
        if reason:
            raise StopRun(reason)
        try:
            with nice_scope(ledger.root):
                return
        except ManagedNetworkGateBusy:
            if perf_counter() < deadline:
                sleep(0.025)
                continue
        except (ManagedNetworkGateStorageError, ManagedNetworkStopped):
            pass
        ledger.halt("product_stop")
        raise StopRun("product_stop") from None


def render_stages(root, side, begin, end):
    path = root / f"{side}-render.jsonl"
    events = [json.loads(line) for line in path.read_text(encoding="ascii").splitlines()]
    selected = [e for e in events if begin <= e["start"] <= e["end"] <= end]
    if not selected:
        raise StopRun("transport")
    return {
        "render_ms": sum((e["end"] - e["start"]) * 1000 for e in selected),
        "card_call_ms": sum(e["card_call_ms"] for e in selected),
        "card_calls": sum(e["card_calls"] for e in selected),
        "cards_created": sum(e["cards_created"] for e in selected),
    }


def inspect_host(root, phase, path, *, deadline=None):
    """Take load readings between timing intervals, pausing on external work."""
    while True:
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-File", str(HERE / "host_check.ps1"),
             "-EvidencePath", str(path), "-RunRoot", str(root)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW, check=True,
        )
        result = json.loads(path.read_text(encoding="ascii"))
        result["phase"] = phase
        remaining = (deadline - datetime.now().astimezone()).total_seconds() if deadline else 0
        result["wait_expired"] = not result["quiet"] and deadline is not None and remaining <= 0
        with (root / "host-observations.jsonl").open("a", encoding="ascii") as trace:
            trace.write(json.dumps(result, ensure_ascii=True) + "\n")
        if result["quiet"] or remaining <= 0:
            return
        sleep(min(20, remaining))


def recheck_profile_gate(root):
    from pixivvault_nicegui.managed_network_stop_history_readonly import read_existing_stop_history_readonly

    snapshot = read_existing_stop_history_readonly(root / "profile", owner_kind="native")
    save(root, "runtime-profile-stop.json", {
        "observed_at": datetime.now().astimezone().isoformat(), "state": snapshot.state,
        "status": snapshot.status_code, "revision": snapshot.revision,
    })
    if snapshot.state != "observed_unblocked":
        raise StopRun("product_stop")


def sample(ledger, page, button, side, pair, observe_image):
    check_product_stop(ledger)
    if side == "original":
        page.evaluate("() => window.__d6SawLoading = false")
    else:
        page.evaluate("""selector => {
            window.__d6SawLoading = false;
            const status = document.querySelector(selector);
            window.__d6StatusObserver?.disconnect();
            window.__d6StatusObserver = new MutationObserver(records => {
                if (status.textContent.trim() === '取得中…' || records.some(record =>
                    record.oldValue?.trim() === '取得中…' || [...record.addedNodes].some(
                        node => node.textContent.trim() === '取得中…'))) window.__d6SawLoading = true;
            });
            window.__d6StatusObserver.observe(status, {childList: true, subtree: true,
                characterData: true, characterDataOldValue: true});
        }""", NICE_STATUS)
    button.scroll_into_view_if_needed()
    box = button.bounding_box()
    if not box:
        raise StopRun("transport")
    begin = perf_counter()
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    metadata = metadata_ready(page, side, require_transition=True)
    paint, decode_wait = image_ready(page, side) if observe_image else (metadata, None)
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
    stage = render_stages(ledger.root, side, begin, metadata)
    stage["metadata_ms"] = (metadata - begin) * 1000
    stage["metadata_to_image_ms"] = (paint - metadata) * 1000 if observe_image else None
    stage["decode_wait_ms"] = decode_wait
    if side == "original":
        status = page.evaluate(ORIGINAL_STATUS)
    else:
        status = page.locator(NICE_STATUS).text_content()
    match = re.search(r"(\d+)\s*件", status or "")
    if match is None:
        raise StopRun("transport")
    stage["result_count"] = int(match[1])
    return {
        "app": side, "pair": pair, "operation": "ranking_official", "condition": "warm",
        "start_ms": 0, "paint_ms": (paint - begin) * 1000, "network_ms": api + images,
        "api_ms": api, "image_ms": images,
        "pace_ms": [interval(e["queued"], e["start"]) for e in events],
    }, stage


def measure(root, repetitions, *, fake=False, wait_until=None):
    from playwright.sync_api import sync_playwright

    ledger = Ledger(root)
    if fake != (root / "fake-wire-only").is_file():
        raise ValueError("fixture root requires fake transport")
    state = ledger.snapshot() if fake else require_operator_confirmation(ledger)
    if state["events"] or state["inflight"]:
        raise ValueError("measurement requires an unused ledger")
    if state["stop"]:
        raise StopRun(state["stop"])
    paths = {side: urlsplit(f"http://127.0.0.1:{port}").netloc for side, port in PORTS.items()}
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(Path(os.environ["LOCALAPPDATA"]) / "ms-playwright"))
    os.environ.update(TEMP=str(root), TMP=str(root), TMPDIR=str(root))
    owned, rows, stages = {}, [], []
    phase = "launch"
    blocked = 0
    image_visibility = {}
    warmup = {}
    measurement_date = date.today()
    try:
        if not fake:
            if wait_until is None:
                wait_until = datetime.now().astimezone() + timedelta(hours=1)
            phase = "before_launch"
            inspect_host(root, phase, root / "host-latest.json", deadline=wait_until)
            recheck_profile_gate(root)
        phase = "launch"
        check_product_stop(ledger)
        with ExitStack() as stack:
            mode = "fake" if fake else "serve"
            urls = {side: stack.enter_context(server(root, side, owned, mode)) for side in PORTS}
            playwright = stack.enter_context(sync_playwright())
            owned["playwright"] = {"pid": playwright._impl_obj._connection._transport._proc.pid}
            save(root, "owned-servers.json", owned)
            context = playwright.chromium.launch_persistent_context(
                str(root / "browser"), headless=True, env=environment(root, "browser"),
                viewport={"width": 1280, "height": 1000}, service_workers="block",
                args=["--proxy-server=http://127.0.0.1:1", "--disable-background-networking",
                      "--disable-component-update", "--no-first-run",
                      "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"],
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
                metadata = metadata_ready(page, side)
                phase = f"{side}_warm_image"
                try:
                    first_paint, decode_wait = image_ready(page, side)
                    warmup[side] = {"metadata_to_image_ms": (first_paint - metadata) * 1000,
                                    "decode_wait_ms": decode_wait}
                    image_visibility[side] = True
                except Exception as error:
                    if ledger.snapshot()["stop"] or type(error).__name__ != "TimeoutError":
                        raise
                    image_visibility[side] = False
                check_product_stop(ledger)
            observe_image = all(image_visibility.values())
            phase = "process_inventory"
            inspect_host(root, phase, root / "processes-running.json", deadline=wait_until)
            for index in range(repetitions):
                if index and not fake:
                    phase = f"before_pair_{index + 1}"
                    inspect_host(root, phase, root / "host-latest.json", deadline=wait_until)
                for side in (("original", "nice") if index % 2 == 0 else ("nice", "original")):
                    if date.today() != measurement_date:
                        raise StopRun("transport")
                    phase = f"{side}_measure"
                    row, stage = sample(ledger, *pages[side], side, index + 1, observe_image)
                    if date.today() != measurement_date:
                        raise StopRun("transport")
                    if len(stages) % 2 and stage["result_count"] != stages[-1]["result_count"]:
                        raise StopRun("transport")
                    rows.append(row)
                    stages.append({"app": side, "pair": index + 1, **stage})
                    save(root, "ranking-samples.json", rows)
                    save(root, "ranking-metadata.json", stages)
        result = {"status": "completed"}
    except Exception as error:
        state = ledger.snapshot()
        if not state["stop"]:
            ledger.halt(error.reason if isinstance(error, StopRun) else "transport")
        result = {"status": "stopped", "phase": phase, "classification": type(error).__name__,
                  "stop": ledger.snapshot()["stop"]}
    result["date"] = measurement_date.isoformat()
    result["viewport"] = [1280, 1000]
    result["browser_external_denied"] = blocked
    result["first_image_observed"] = image_visibility
    result["warmup_image"] = warmup
    result["timing_endpoint"] = "first_image" if image_visibility == {side: True for side in PORTS} else "metadata"
    save(root, "ranking-outcome.json", result)
    save(root, "ranking-summary.json", summarize_pairs(rows))
    return 0 if result["status"] == "completed" else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("root", type=Path)
    parser.add_argument("repetitions", type=int, nargs="?", default=10)
    parser.add_argument("--fake", action="store_true")
    parser.add_argument("--host-wait-until", type=datetime.fromisoformat)
    args = parser.parse_args()
    root = copied_path(SCRATCH, args.root)
    if not (1 if args.fake else 10) <= args.repetitions <= 20:
        raise SystemExit("10 to 20 pairs required")
    if args.host_wait_until is not None and args.host_wait_until.utcoffset() is None:
        parser.error("host wait deadline must have its time zone")
    raise SystemExit(measure(root, args.repetitions, fake=args.fake,
                             wait_until=args.host_wait_until))
