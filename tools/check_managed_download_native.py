"""Read-only managed storage checks and private native UI acceptance.

Never put image metadata, identities, URLs or browser errors into reports.
"""
from __future__ import annotations

from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat

from PIL import Image
from datetime import datetime
APPLICATION_ID = 0x50564A31
SCHEMA_VERSION = 2

MARKER = ".pixivvault-managed-pixiv-downloads.json"
IDENTITY = {"application": "pixivvault-nicegui", "purpose": "managed-pixiv-originals", "version": 1}
FOLDER = re.compile(r"work-([1-9][0-9]{0,19})-[a-f0-9]{32}\Z")
SHA = re.compile(r"[a-f0-9]{64}\Z")
EXT = {"png": "png", "jpeg": "jpg", "webp": "webp"}



if __package__:
    from .job_store_schema import _job_schema,_job_payload,_job_bindings
else:
    from job_store_schema import _job_schema,_job_payload,_job_bindings


def _safe(path: Path) -> None:
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError("unsafe storage link")
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue
        if getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("unsafe storage reparse point")


def _read(path: Path, limit: int) -> bytes:
    _safe(path)
    with path.open("rb") as source:
        info = source.fileno()
        metadata = os.fstat(info)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or not 0 < metadata.st_size <= limit:
            raise ValueError("invalid stored file")
        content = source.read(limit + 1)
    if not 0 < len(content) <= limit:
        raise ValueError("stored file exceeds limit")
    return content


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate receipt key")
        result[key] = value
    return result


def _inside(path: Path, other: Path) -> bool:
    return path == other or other in path.parents


def preflight(root: Path, cookie_file: Path, *, restart: bool) -> dict:
    root, cookie_file = Path(root), Path(cookie_file)
    if not root.is_absolute() or root.parent == root or not cookie_file.is_absolute():
        raise ValueError("absolute managed root and cookie file required")
    _safe(root)
    _safe(cookie_file)
    if _inside(root, cookie_file.parent) or _inside(cookie_file.parent, root):
        raise ValueError("managed root overlaps cookie parent")
    if not cookie_file.is_file() or not root.parent.is_dir():
        raise ValueError("missing prerequisite")
    if not restart:
        if root.exists():
            raise ValueError("initial managed root already exists")
        return {"fresh": True, "receipts": 0, "pages": 0, "jobs": 0,
                "_signature": (), "_cookie_signature": hashlib.sha256(_read(cookie_file, 1024 * 1024)).digest()}
    state = verify_storage(root)
    state["_cookie_signature"] = hashlib.sha256(_read(cookie_file, 1024 * 1024)).digest()
    return state


def cookie_unchanged(cookie_file: Path, before: dict) -> bool:
    return hashlib.sha256(_read(cookie_file, 1024 * 1024)).digest() == before["_cookie_signature"]


def verify_storage(root: Path) -> dict:
    root = Path(root)
    _safe(root)
    if not root.is_dir():
        raise ValueError("missing managed root")
    marker = json.loads(_read(root / MARKER, 4096), object_pairs_hook=_unique)
    if marker != IDENTITY:
        raise ValueError("managed marker mismatch")
    outputs = root / "outputs"
    if not outputs.is_dir():
        raise ValueError("missing completed storage")
    seen = set()
    work_receipts = {}
    page_count = 0
    receipt_count = 0
    signature = []
    receipt_paths = set()
    folders = []
    for folder in outputs.iterdir():
        if len(folders) >= 10000:
            raise ValueError("completed storage limit exceeded")
        folders.append(folder)
    for folder in sorted(folders):
        _safe(folder)
        matched = FOLDER.fullmatch(folder.name)
        if not folder.is_dir() or matched is None:
            raise ValueError("invalid completed structure")
        receipt_bytes = _read(folder / "receipt.json", 512 * 1024)
        data = json.loads(receipt_bytes, object_pairs_hook=_unique)
        expected = {"version", "source", "work_id", "author_id", "author_name", "title",
                    "tags", "created_at", "saved_at", "illustration_type", "x_restrict", "page_count", "files"}
        if (type(data) is not dict or set(data) != expected or type(data["version"]) is not int or data["version"] != 1
                or data["source"] != "pixiv-original" or data["work_id"] != matched[1]
                or data["work_id"] in seen or not re.fullmatch(r"[1-9][0-9]{0,19}", data["author_id"])
                or type(data["page_count"]) is not int or not 1 <= data["page_count"] <= 1000
                or type(data["files"]) is not list or len(data["files"]) != data["page_count"]):
            raise ValueError("invalid completed receipt")
        seen.add(data["work_id"])
        if (type(data["illustration_type"]) is not int or data["illustration_type"] not in (0, 1)
                or type(data["x_restrict"]) is not int or data["x_restrict"] not in (0, 1, 2)
                or type(data["tags"]) is not list or len(data["tags"]) > 100
                or any(type(tag) is not str or len(tag) > 128 for tag in data["tags"])):
            raise ValueError("invalid receipt metadata")
        for key, limit in (("title", 256), ("author_name", 256), ("created_at", 128), ("saved_at", 64)):
            if type(data[key]) is not str or len(data[key]) > limit or any(ord(c) < 32 for c in data[key]):
                raise ValueError("invalid receipt metadata")
        if datetime.fromisoformat(data["saved_at"]).tzinfo is None:
            raise ValueError("invalid receipt date")
        expected_files = {"receipt.json"}
        for number, item in enumerate(data["files"], 1):
            if type(item) is not dict or set(item) != {"name", "bytes", "sha256", "format", "width", "height"}:
                raise ValueError("invalid page entry")
            ext = EXT.get(item["format"]) if type(item["format"]) is str else None
            name = f"page-{number}.{ext}"
            if (ext is None or item["name"] != name or type(item["bytes"]) is not int
                    or not 0 < item["bytes"] <= 20 * 1024 * 1024
                    or type(item["sha256"]) is not str or not SHA.fullmatch(item["sha256"])
                    or type(item["width"]) is not int or type(item["height"]) is not int
                    or not 0 < item["width"] * item["height"] <= 28_000_000):
                raise ValueError("invalid page metadata")
            raw = _read(folder / name, 20 * 1024 * 1024)
            if len(raw) != item["bytes"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
                raise ValueError("stored page checksum mismatch")
            with Image.open(BytesIO(raw)) as image:
                kind = "JPEG" if item["format"] == "jpeg" else item["format"].upper()
                if image.format != kind or image.size != (item["width"], item["height"]):
                    raise ValueError("stored page format mismatch")
                image.load()
            expected_files.add(name)
            signature.append(hashlib.sha256(raw).digest())
            page_count += 1
        if {entry.name for entry in folder.iterdir()} != expected_files:
            raise ValueError("unexpected completed files")
        receipt_count += 1
        receipt_paths.add(f"outputs/{folder.name}/receipt.json")
        work_receipts[f"outputs/{folder.name}/receipt.json"] = (data["work_id"], data["page_count"])
        signature.append(hashlib.sha256(receipt_bytes).digest())
    db = root / "jobs.sqlite3"
    if not db.is_file():
        raise ValueError("managed job database missing")
    _safe(db)
    if db.lstat().st_nlink != 1 or db.lstat().st_size > 32 * 1024 * 1024:
        raise ValueError("managed database file rejected")
    uri = db.as_uri() + "?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    try:
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("managed job database invalid")
        schema_version=_job_schema(connection)
        live_jobs={}
        jobs = 0
        done_results = set()
        durable_ids = set()
        for job_id, payload in connection.execute("SELECT job_id,payload FROM jobs ORDER BY ordinal"):
            job=_job_payload(payload,job_id)
            live_jobs[job_id]=job
            # This checker creates one fresh, successful job. It does not
            # certify arbitrary existing stores with unrelated failed work.
            if job.get("state") != "done" or job["attempt"] != 1:
                raise ValueError("managed acceptance job is not a single completed attempt")
            if job["state"] == "done":
                if (type(job.get("result")) is not str or job["result"] not in receipt_paths
                        or job.get("kind") != "pixiv-illust" or
                        not re.fullmatch(r"[1-9][0-9]{0,19}", str(job.get("target_id", "")))
                        or job["done"] != job["total"] or job["total"] < 1
                        or (job["target_id"], job["total"]) != work_receipts[job["result"]]):
                    raise ValueError("done job does not match saved receipt")
                done_results.add(job["result"])
            durable_ids.add(job_id)
            signature.append(hashlib.sha256(payload.encode("utf-8")).digest())
            jobs += 1
        if done_results != receipt_paths:
            raise ValueError("saved receipts and jobs disagree")
        accepted_ids = set()
        for operation, job_id, fingerprint in _job_bindings(connection,live_jobs):
            if job_id not in durable_ids or type(operation) is not str or not 0 < len(operation) <= 128:
                raise ValueError("managed operation ledger mismatch")
            accepted_ids.add(job_id)
            # Verify that the accepted input survived, without reporting its
            # private record or opaque identity in the public result.
            path = root / "requests" / (hashlib.sha256(operation.encode("utf-8")).hexdigest() + ".json")
            intent = _read(path, 128 * 1024)
            spec = json.loads(intent, object_pairs_hook=_unique)
            request_fingerprint = json.loads(fingerprint,object_pairs_hook=_unique)
            if request_fingerprint!=[live_jobs[job_id][k] for k in ("kind","target_id","title")]:
                raise ValueError("managed job fingerprint mismatch")
            if (type(spec) is not dict or request_fingerprint !=
                    ["pixiv-illust", spec.get("remote_id"), spec.get("title")]):
                raise ValueError("managed accepted input mismatch")
            signature.append(hashlib.sha256(intent).digest())
            signature.append(hashlib.sha256(json.dumps([operation, job_id, fingerprint]).encode()).digest())
        if schema_version==2:
            history_ids=set()
            for job_id,payload in connection.execute('SELECT job_id,payload FROM job_history ORDER BY job_id'):
                history=_job_payload(payload,job_id)
                if (job_id not in accepted_ids or history['state'] not in ('done','error','stopped')
                    or history['paused_from'] is not None or history!=live_jobs.get(job_id)):
                    raise ValueError('managed terminal history mismatch')
                history_ids.add(job_id);signature.append(hashlib.sha256(payload.encode('utf-8')).digest())
            if history_ids!=accepted_ids:raise ValueError('managed terminal history missing')
        if accepted_ids != durable_ids or jobs != 1 or receipt_count != 1:
            raise ValueError("managed acceptance expected one durable saved artwork")
    finally:
        connection.close()
    return {"fresh": False, "receipts": receipt_count, "pages": page_count,
            "jobs": jobs, "_signature": tuple(signature)}


def inspect_managed(page, guard, root: Path, *, restart: bool, before: dict) -> dict:
    """Only derived booleans and counts leave this function; private identities stay local."""
    result = {"passed": False, "phase": "preflight", "selected": False,
              "queue_done": False, "viewer_decoded": False, "synthetic_return": False}
    try:
        return _inspect_managed(page, guard, root, restart=restart, before=before, result=result)
    except Exception as exc:
        result["passed"] = False
        result["synthetic_return"] = False
        result["error_class"] = type(exc).__name__
        if result.get("phase") == "synthetic_return":
            try:
                result["return_checks"] = guard.run(page.evaluate, """() => {
                    const visible = e => !!e && !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
                    const item = id => document.querySelector('[data-testid="'+id+'"]');
                    const tab = item('tab-learning');
                    const learning = document.querySelector('.pv-learning[data-active="true"]');
                    return {
                        learning_tab_active: tab?.getAttribute('aria-current')==='page',
                        learning_focused: document.activeElement===learning,
                        learning_synthetic: visible(learning) &&
                            learning.dataset.storageMode==='memory' &&
                            /^demo-[0-9]{4}$/.test(learning.dataset.workId||''),
                        library_hidden: !visible(item('library-view')),
                        download_hidden: !visible(item('managed-download-view')),
                        viewer_hidden: !visible(item('library-viewer'))
                    };
                }""", with_timeout=False)
            except Exception:
                result["return_checks"] = {"diagnostic_unavailable": True}
        return result


def _inspect_managed(page, guard, root: Path, *, restart: bool, before: dict, result: dict) -> dict:
    from playwright.sync_api import expect
    run = guard.run
    if restart:
        selected_pages = before["pages"]
        run(page.get_by_test_id("tab-queue").click)
        run(expect(page.get_by_test_id("queue-view").get_by_test_id("queue-filter-done")).to_have_text,
            re.compile(r"完了 [1-9][0-9]*"))
        if verify_storage(root)["_signature"] != before["_signature"]:
            raise AssertionError("restart storage changed")
    else:
        result["phase"] = "refresh"
        run(page.get_by_test_id("tab-download").click)
        download = page.get_by_test_id("managed-download-view")
        run(download.get_by_test_id("managed-download-refresh").click)
        run(expect(download.locator(".pv-download-item").first).to_be_visible)
        choices = download.locator(".pv-download-item[data-page-count][data-x-restrict='0']")
        count = run(choices.count, with_timeout=False)
        selected = None
        single_page = None
        for number in range(min(count, 50)):
            candidate = choices.nth(number)
            pages = run(candidate.get_attribute, "data-page-count")
            if pages is not None and 2 <= int(pages) <= 3:
                selected = candidate
                break
            if pages == "1" and single_page is None:
                single_page = candidate
        if selected is None:
            selected = single_page
        if selected is None:
            raise AssertionError("no eligible normal bookmark")
        result["phase"] = "select"
        selected_id = run(selected.get_attribute, "data-work-id")
        selected_pages = int(run(selected.get_attribute, "data-page-count"))
        run(selected.get_by_test_id("managed-download-select").click)
        run(expect(selected.get_by_test_id("managed-download-select")).to_be_checked)
        run(expect(download.get_by_test_id("managed-download-selection")).to_have_text, "対象 1 件")
        result["selected"] = True
        run(download.get_by_test_id("managed-download-submit").click)
        result["phase"] = "queue"
        run(page.get_by_test_id("tab-queue").click)
        run(expect(page.get_by_test_id("queue-view").get_by_test_id("queue-filter-done")).to_have_text,
            re.compile(r"完了 [1-9][0-9]*"))
        after = verify_storage(root)
        if after["receipts"] != before["receipts"] + 1:
            raise AssertionError("completed receipt count mismatch")
    result["queue_done"] = True
    result["phase"] = "library"
    run(page.get_by_test_id("tab-library").click)
    library = page.get_by_test_id("library-view")
    if not restart:
        run(library.get_by_test_id("library-db-refresh").click)
    authors = library.locator(".pv-library-author-card")
    run(expect(authors.first).to_be_visible)
    if restart:
        chosen_author = authors.first
    else:
        # Search is local to the UI and never leaves the process or report.
        search = library.get_by_placeholder("作品名・作者・タグで検索")
        generation = run(library.get_attribute, "data-library-generation")
        run(search.fill, selected_id)
        run(expect(library).not_to_have_attribute, "data-library-generation", generation)
        run(expect(authors.first).to_be_visible)
        chosen_author = authors.first
    result["phase"] = "author"
    run(chosen_author.get_by_test_id("library-author-open").click)
    card = (library.locator(f'.pv-library-page-card[data-work-id="{selected_id}"][data-page-no="0"]')
            if not restart else library.locator(".pv-library-page-card").first)
    selected_id = run(card.get_attribute, "data-work-id")
    result["phase"] = "open_viewer"
    run(card.click)
    viewer = page.get_by_test_id("library-viewer")
    run(expect(viewer).to_be_visible)
    result["phase"] = "decode"
    image = page.get_by_test_id("library-viewer-image").locator("img:visible")
    run(expect(image).to_have_attribute, "src", re.compile(
        rf"/media/{re.escape(selected_id)}/0/view\.png\?catalog="))
    decoded = guard.decode(image)
    result["viewer_decoded"] = decoded["naturalWidth"] > 0
    if selected_pages > 1:
        run(page.keyboard.press, "ArrowRight", with_timeout=False)
        run(expect(viewer.locator(".pv-counter")).to_have_text, f"2/{selected_pages}")
        run(expect(image).to_have_attribute, "src", re.compile(rf"/media/{re.escape(selected_id)}/1/view\.png\?catalog="))
        guard.decode(image)
        run(page.keyboard.press, "ArrowLeft", with_timeout=False)
        run(expect(viewer.locator(".pv-counter")).to_have_text, f"1/{selected_pages}")
        run(expect(image).to_have_attribute, "src", re.compile(rf"/media/{re.escape(selected_id)}/0/view\.png\?catalog="))
        guard.decode(image)
        result["multiple_pages_validated"] = True
    run(viewer.get_by_role("button", name="閉じる", exact=True).click)
    run(expect(card).to_be_focused)
    result["focus_restored"] = True
    result["phase"] = "synthetic_return"
    # Live-cookie mode uses real ranking data. Its empty or fetched ranking
    # must not be mistaken for the synthetic 50-card demo. This CLI does not
    # configure discovery or learning storage, so the memory learning surface
    # is the explicit synthetic destination.
    run(page.get_by_test_id("tab-learning").click)
    learning = page.locator('.pv-learning[data-active="true"]')
    run(expect(page.get_by_test_id("tab-learning")).to_have_attribute, "aria-current", "page")
    run(expect(learning).to_have_attribute, "data-storage-mode", "memory")
    run(expect(learning).to_have_attribute, "data-work-id", re.compile(r"^demo-[0-9]{4}$"))
    run(expect(learning).to_be_focused)
    run(expect(library).to_have_attribute, "data-active", "false")
    download_panel = page.get_by_test_id("managed-download-view")
    # A restart can view saved files without constructing this lazy tab.
    if run(download_panel.count, with_timeout=False):
        run(expect(download_panel).to_be_hidden)
    run(expect(viewer).to_be_hidden)
    run(expect(learning.get_by_role("button", name="いいね", exact=True)).to_be_enabled)
    guard.decode(learning.locator(".pv-view-art img:visible"))
    # Capture remains forbidden until every visible work identity and every
    # paintable image is synthetic and all private surfaces are hidden.
    condition = r"""() => {
       const visible = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
       const learning = document.querySelector('.pv-learning[data-active="true"]');
       if (!learning || !visible(learning) || learning.dataset.storageMode !== 'memory' ||
           !/^demo-[0-9]{4}$/.test(learning.dataset.workId || '')) return false;
       const identities = [...document.querySelectorAll('[data-work-id]')]
           .filter(e => visible(e) && e.dataset.workId);
       if (!identities.every(e => /^demo-[0-9]{4}$/.test(e.dataset.workId))) return false;
       const privateViews = [...document.querySelectorAll(
         '[data-testid="library-view"], [data-testid="managed-download-view"], [data-testid="queue-view"], .pv-viewer')];
       if (privateViews.some(visible)) return false;
       const imgs = [...document.querySelectorAll('img')].filter(img => {
          const r = img.getBoundingClientRect();
          return visible(img) && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
       });
       return imgs.length > 0 && imgs.every(img => {
         const source = new URL(img.currentSrc || img.src, document.baseURI);
         return learning.contains(img) && source.origin === location.origin &&
           source.pathname === '/media/' + learning.dataset.workId + '/0/view.png' &&
           img.complete && img.naturalWidth > 0 && img.naturalHeight > 0;
       });
    }"""
    result["phase"] = "synthetic_identity"
    run(page.wait_for_function, condition)
    safe = run(page.evaluate, condition, with_timeout=False)
    if safe is not True:
        raise AssertionError("synthetic learning restore unproven")
    result["return_checks"] = {
        "learning_tab_active": True, "learning_focused": True,
        "learning_synthetic": True, "library_hidden": True,
        "download_hidden": True, "viewer_hidden": True,
    }
    result["synthetic_return"] = True
    result["passed"] = True
    result["phase"] = "done"
    return result
