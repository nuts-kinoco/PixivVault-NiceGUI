"""Own one synthetic server and one headless browser to verify durable learning.

Example: python tools/check_learning_persistence_browser.py --channel chromium
The runner creates a fresh directory below --output and explicitly passes its
absolute path as the app's --learning-sandbox. It accepts no existing DB or URL.
Screenshots, synthetic SQLite data, and process diagnostics stay under --output.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback

from run_browser_checks import (
    HOST, ROOT, child_environment, free_port, port_released, source_hashes,
    stop_child, wait_for_health,
)


CHOICES = (("見送る", "skip"), ("合わない", "hate"),
           ("いいね", "like"), ("SuperLike", "superLike"))
COUNTERS = ("superlike_count", "like_count", "skip_count", "hate_count", "shown_count")
DB_NAME = "learning.sqlite3"


def verify(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class OwnedSandbox:
    """Only open the fixed DB inside the fresh directory minted by this runner."""

    def __init__(self, output: Path) -> None:
        self.output = output.resolve(strict=True)
        self.path = Path(tempfile.mkdtemp(prefix="learning-sandbox-", dir=self.output)).resolve()
        self.db_path = self.path / DB_NAME

    def _uri(self, mode: str) -> str:
        if (self.path.resolve(strict=True) != self.path or self.path.parent != self.output or
                self.db_path.parent != self.path or self.db_path.name != DB_NAME or
                self.db_path.is_symlink() or self.db_path.resolve(strict=True) != self.db_path):
            raise ValueError("refusing a DB outside this runner's fresh sandbox")
        if not self.db_path.is_file():
            raise ValueError("synthetic DB is not a regular file")
        return self.db_path.as_uri() + f"?mode={mode}"

    def snapshot(self) -> dict:
        connection = sqlite3.connect(self._uri("ro"), uri=True, timeout=1)
        connection.row_factory = sqlite3.Row
        try:
            queries = {
                "history": "SELECT work_id, verdict, reason, tags, author_id, source, opened, bookmarked "
                           "FROM discovery_swipe_history ORDER BY work_id",
                "tags": "SELECT tag, superlike_count, like_count, skip_count, hate_count, shown_count "
                        "FROM discovery_preference ORDER BY tag",
                "authors": "SELECT author_id, superlike_count, like_count, skip_count, hate_count, "
                           "shown_count, consecutive_skip_count FROM discovery_author_preference ORDER BY author_id",
                "revisions": "SELECT work_id, revision FROM learning_adapter_revision ORDER BY work_id",
            }
            connection.execute("BEGIN")
            return {name: [dict(row) for row in connection.execute(sql)]
                    for name, sql in queries.items()}
        finally:
            connection.close()

    @contextmanager
    def write_lock(self):
        connection = sqlite3.connect(self._uri("rw"), uri=True, timeout=1, isolation_level=None)
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()


def history_map(snapshot: dict) -> dict[str, str]:
    return {row["work_id"]: row["verdict"] for row in snapshot["history"]}


def verify_aggregates(snapshot: dict) -> None:
    """Independently account for this run's reason-free synthetic judgments."""
    expected_tags: dict[str, dict[str, int]] = {}
    expected_authors: dict[str, dict[str, int]] = {}
    for row in snapshot["history"]:
        verify(row["work_id"].startswith("demo-"), "non-synthetic work ID in owned DB")
        verify(row["reason"] is None and not row["opened"] and not row["bookmarked"],
               "unexpected synthetic judgment metadata")
        column = {"skip": "skip_count", "hate": "hate_count", "like": "like_count",
                  "superLike": "superlike_count"}[row["verdict"]]
        for tag in set(json.loads(row["tags"] or "[]")):
            counts = expected_tags.setdefault(tag, dict.fromkeys(COUNTERS, 0))
            counts["shown_count"] += 1
            counts[column] += 1
        if row["author_id"]:
            counts = expected_authors.setdefault(row["author_id"], dict.fromkeys(COUNTERS, 0))
            counts["shown_count"] += 1
            if row["verdict"] != "hate":
                counts[column] += 1
    actual_tags = {row["tag"]: {key: row[key] for key in COUNTERS} for row in snapshot["tags"]}
    actual_authors = {row["author_id"]: {key: row[key] for key in COUNTERS}
                      for row in snapshot["authors"]}
    verify(actual_tags == expected_tags, f"tag counters disagree with committed history: {snapshot!r}")
    verify(actual_authors == expected_authors, f"author counters disagree with committed history: {snapshot!r}")


class SyntheticApp:
    """Restart only our own child, on its allocated loopback port."""

    def __init__(self, sandbox: OwnedSandbox, output: Path, result: dict) -> None:
        self.sandbox = sandbox
        self.output = output
        self.port = free_port()
        self.url = f"http://{HOST}:{self.port}"
        self.process = None
        self.log = None
        self.record = None
        result["app"] = {"port": self.port, "instances": [], "port_released": False}
        self.evidence = result["app"]

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            raise RuntimeError("owned synthetic server is already running")
        self.log = (self.output / "app-stderr.log").open("ab")
        try:
            self.process = subprocess.Popen(
                [sys.executable, "-m", "pixivvault_nicegui", "--host", HOST,
                 "--port", str(self.port), "--learning-sandbox", str(self.sandbox.path)],
                cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=self.log, env=child_environment(),
            )
        except BaseException:
            self.log.close()
            self.log = None
            raise
        self.record = {"pid": self.process.pid, "returncode": None, "port_released": False}
        self.evidence["instances"].append(self.record)
        self.evidence["port_released"] = False
        wait_for_health(self.url, self.process)

    def stop(self) -> None:
        try:
            stop_child(self.process)
        finally:
            if self.log is not None:
                self.log.close()
                self.log = None
            released = port_released(self.port)
            self.evidence["port_released"] = released
            if self.record is not None:
                self.record.update(returncode=self.process.poll(), port_released=released)
        if self.process is not None and self.process.poll() is None:
            raise RuntimeError("owned synthetic server is still alive")
        if not released:
            raise RuntimeError("owned synthetic server port still accepting connections")

    def restart(self) -> None:
        self.stop()
        self.start()


def open_learning(page, url: str):
    from playwright.sync_api import expect
    page.goto(url, wait_until="domcontentloaded", timeout=20_000)
    page.get_by_test_id("tab-learning").click()
    root = page.locator(".pv-learning[data-storage-mode=sandbox]")
    expect(root).to_be_visible()
    ready(root)
    return root


def ready(root) -> None:
    from playwright.sync_api import expect
    expect(root).to_have_attribute("data-saving", "false", timeout=15_000)
    image = root.locator("img").first
    expect(image).to_be_visible(timeout=15_000)
    image.evaluate("el => el.decode()")
    verify(image.evaluate("el => el.complete && el.naturalWidth > 0"), "synthetic image did not decode")
    for label, _ in CHOICES:
        expect(root.get_by_role("button", name=label, exact=True)).to_be_enabled(timeout=15_000)


def progress(root, count: int) -> None:
    from playwright.sync_api import expect
    expect(root.locator(".pv-counter")).to_have_text(re.compile(rf"^{count} /"), timeout=15_000)


def display_snapshot(root) -> dict:
    return {
        "work_id": root.get_attribute("data-work-id"),
        "generation": root.get_attribute("data-generation"),
        "counter": root.locator(".pv-counter").inner_text(),
        "image": root.locator("img").first.evaluate("el => el.currentSrc"),
    }


def capture(page, output: Path, result: dict, name: str) -> None:
    page.screenshot(path=str(output / name), full_page=True, timeout=10_000)
    result["screenshots"].append(name)


def checkpoint(sandbox: OwnedSandbox, result: dict, name: str, expected: dict[str, str]) -> dict:
    snapshot = sandbox.snapshot()
    result["database"][name] = snapshot
    verify(history_map(snapshot) == expected, f"{name}: unexpected committed judgments")
    verify_aggregates(snapshot)
    return snapshot


def wait_for_commit(sandbox: OwnedSandbox, expected: dict[str, str], timeout: float = 15) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        snapshot = sandbox.snapshot()
        if history_map(snapshot) == expected:
            verify_aggregates(snapshot)
            return snapshot
        if time.monotonic() >= deadline:
            raise TimeoutError(f"accepted save did not commit after page close: {snapshot!r}")
        time.sleep(0.05)


def scenarios(context, app: SyntheticApp, sandbox: OwnedSandbox, output: Path, result: dict) -> None:
    from playwright.sync_api import expect
    expected: dict[str, str] = {}
    page = context.new_page()
    root = open_learning(page, app.url)
    progress(root, 0)
    undo = lambda node: node.get_by_role("button", name="ひとつ戻す（Undo）", exact=True)
    expect(undo(root)).to_be_disabled()
    judged_ids = []
    for count, (label, choice) in enumerate(CHOICES, 1):
        work_id = root.get_attribute("data-work-id")
        verify(bool(work_id), "learning work ID missing")
        judged_ids.append(work_id)
        root.get_by_role("button", name=label, exact=True).click()
        progress(root, count)
        ready(root)
        expect(root).not_to_have_attribute("data-work-id", work_id)
        expected[work_id] = choice
        checkpoint(sandbox, result, f"choice_{choice}", expected)
    result["passed"].append("4 choices commit to legacy history and tag/author counters")

    undo(root).click()
    expected.pop(judged_ids[-1])
    progress(root, 3)
    ready(root)
    expect(root).to_have_attribute("data-work-id", judged_ids[-1])
    expect(undo(root)).to_be_disabled()
    before_reload = checkpoint(sandbox, result, "one_step_undo", expected)
    capture(page, output, result, "four-choices-one-undo.png")
    result["passed"].append("Undo removes only the latest judgment; previous 3 remain; second Undo disabled")

    page.reload(wait_until="domcontentloaded")
    page.get_by_test_id("tab-learning").click()
    root = page.locator(".pv-learning[data-storage-mode=sandbox]")
    ready(root)
    progress(root, 3)
    expect(root).to_have_attribute("data-work-id", judged_ids[-1])
    expect(undo(root)).to_be_disabled()
    verify(checkpoint(sandbox, result, "page_reload", expected) == before_reload,
           "reload changed committed history or aggregates")
    result["passed"].append("reload excludes committed judgments and starts without session Undo")

    page.goto("about:blank")
    app.restart()
    root = open_learning(page, app.url)
    progress(root, 3)
    expect(root).to_have_attribute("data-work-id", judged_ids[-1])
    expect(undo(root)).to_be_disabled()
    verify(checkpoint(sandbox, result, "server_restart", expected) == before_reload,
           "restart changed committed history or aggregates")
    capture(page, output, result, "server-restart.png")
    result["passed"].append("own server restart preserves judgments, exclusions, and session-only Undo")

    before_failure = display_snapshot(root)
    before_lock = sandbox.snapshot()
    with sandbox.write_lock():
        root.get_by_role("button", name="いいね", exact=True).click()
        expect(root).to_have_attribute("data-saving", "true", timeout=3_000)
        verify(display_snapshot(root) == before_failure, "save advanced before SQLite commit")
        page.get_by_test_id("tab-ranking").click()
        expect(root).to_be_hidden(timeout=2_000)
        gallery = page.locator(".pv-gallery")
        gallery.get_by_placeholder("作品名・作者・タグで検索").fill("0001")
        gallery.get_by_role("button", name="検索", exact=True).click()
        expect(gallery.locator(".pv-counter")).to_have_text("1 / 1 件表示", timeout=2_000)
        expect(root).to_have_attribute("data-saving", "true", timeout=1_000)
        result["passed"].append("ranking tab and server search remain responsive while SQLite writer waits")
        page.get_by_test_id("tab-learning").click()
        expect(root.locator(".pv-learning-status")).to_contain_text("保存できませんでした", timeout=15_000)
        expect(root).to_have_attribute("data-saving", "false")
        verify(display_snapshot(root) == before_failure, "failed save changed work, index, generation, or image")
        expect(root.get_by_role("button", name="保存を再試行", exact=True)).to_be_enabled()
        verify(sandbox.snapshot() == before_lock, "locked failed transaction changed history or aggregates")
        capture(page, output, result, "real-lock-failure.png")
    result["database"]["lock_failure"] = sandbox.snapshot()
    result["passed"].append("real BEGIN IMMEDIATE contention fails without advancing image/index or DB")

    root.get_by_role("button", name="保存を再試行", exact=True).click()
    expected[before_failure["work_id"]] = "like"
    progress(root, 4)
    ready(root)
    expect(root).not_to_have_attribute("data-work-id", before_failure["work_id"])
    expect(root.get_by_role("button", name="保存を再試行", exact=True)).to_be_hidden()
    checkpoint(sandbox, result, "retry_after_unlock", expected)
    result["passed"].append("retry after unlock commits once without duplicate aggregate increments")

    old_generation = root.get_attribute("data-generation")
    root.get_by_role("button", name="保存状態を再読込", exact=True).click()
    expect(root).not_to_have_attribute("data-generation", old_generation)
    ready(root)
    expect(undo(root)).to_be_disabled()
    progress(root, 4)
    result["passed"].append("explicit storage reload restores exclusions and clears session Undo")

    second = context.new_page()
    second_root = open_learning(second, app.url)
    shared_id = root.get_attribute("data-work-id")
    expect(second_root).to_have_attribute("data-work-id", shared_id)
    root.get_by_role("button", name="いいね", exact=True).click()
    progress(root, 5)
    ready(root)
    expected[shared_id] = "like"
    checkpoint(sandbox, result, "page_a_like", expected)
    expect(second_root).to_have_attribute("data-work-id", shared_id)
    second_root.get_by_role("button", name="SuperLike", exact=True).click()
    progress(second_root, 5)
    ready(second_root)
    expected[shared_id] = "superLike"
    latest = checkpoint(sandbox, result, "page_b_superlike", expected)
    undo(root).click()
    expect(root.locator(".pv-learning-status")).to_contain_text("別の画面", timeout=15_000)
    ready(root)
    progress(root, 5)
    expect(undo(root)).to_be_disabled()
    expect(root).not_to_have_attribute("data-work-id", shared_id)
    verify(checkpoint(sandbox, result, "stale_undo_rejected", expected) == latest,
           "stale Undo changed the newer verdict, aggregates, or revision")
    capture(page, output, result, "conflicting-pages.png")
    result["passed"].append("two-page stale Undo rejected; latest superLike and counters preserved; UI reloads")
    second.close()

    closing_id = root.get_attribute("data-work-id")
    before_close = display_snapshot(root)
    with sandbox.write_lock():
        root.get_by_role("button", name="見送る", exact=True).click()
        expect(root).to_have_attribute("data-saving", "true", timeout=3_000)
        verify(display_snapshot(root) == before_close, "close test advanced before DB commit")
        verify(closing_id not in history_map(sandbox.snapshot()), "close test was not in flight")
        page.close()
        verify(page.is_closed(), "in-flight page was not closed")
    expected[closing_id] = "skip"
    result["database"]["commit_after_page_close"] = wait_for_commit(sandbox, expected)
    reopened = context.new_page()
    reopened_root = open_learning(reopened, app.url)
    progress(reopened_root, 6)
    expect(reopened_root).not_to_have_attribute("data-work-id", closing_id)
    verify(reopened_root.get_attribute("data-work-id") not in expected,
           "new page proposed an already committed work")
    expect(undo(reopened_root)).to_be_disabled()
    checkpoint(sandbox, result, "new_page_after_close", expected)
    capture(reopened, output, result, "learning-persistence-final.png")
    result["passed"].append("accepted in-flight write survives page close; new page excludes committed work")


def exercise_browser(app: SyntheticApp, sandbox: OwnedSandbox, output: Path, result: dict, channel: str) -> None:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=None if channel == "chromium" else channel, headless=True)
        result["browser_version"] = browser.version
        result["browser_launches"] += 1
        try:
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            def watch(page):
                page.on("console", lambda message: result["console_errors"].append(message.text)
                        if message.type == "error" else None)
                page.on("pageerror", lambda error: result["page_errors"].append(str(error)))
            context.on("page", watch)
            try:
                scenarios(context, app, sandbox, output, result)
            except BaseException:
                for page in reversed(context.pages):
                    if not page.is_closed():
                        try:
                            capture(page, output, result, "failure.png")
                        except Exception as exc:
                            result["screenshot_error"] = str(exc)
                        break
                raise
            finally:
                context.close()
        finally:
            browser.close()


def run(*, channel: str = "chromium", output: Path = Path("artifacts/learning-persistence-browser")) -> int:
    output.mkdir(parents=True, exist_ok=True)
    output = output.resolve()
    before = source_hashes()
    sandbox = OwnedSandbox(output)
    result = {
        "started_at": datetime.now(timezone.utc).isoformat(), "channel": channel,
        "sandbox": str(sandbox.path), "database_file": DB_NAME,
        "browser_launches": 0, "browser_version": None, "passed": [], "screenshots": [],
        "database": {}, "console_errors": [], "page_errors": [], "failure": None, "success": False,
    }
    app = SyntheticApp(sandbox, output, result)
    result["url"] = app.url
    try:
        app.start()
        exercise_browser(app, sandbox, output, result, channel)
    except Exception:
        result["failure"] = traceback.format_exc()
    finally:
        try:
            app.stop()
        except Exception:
            result["failure"] = (result["failure"] or "") + "\ncleanup: " + traceback.format_exc()
        after = source_hashes()
        result.update(source_before=before, source_after=after, source_changed=before != after)
        if before != after:
            result["failure"] = (result["failure"] or "") + "\nruntime sources changed during browser checks"
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        result["passed_count"] = len(result["passed"])
        result["success"] = (result["failure"] is None and not result["console_errors"]
                             and not result["page_errors"])
        (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"persistence checks passed: {result['passed_count']} | console errors: "
          f"{len(result['console_errors'])} | page errors: {len(result['page_errors'])}")
    if result["failure"]:
        print(result["failure"], file=sys.stderr)
    return 0 if result["success"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", choices=("chromium", "msedge"), default="chromium")
    parser.add_argument("--output", type=Path, default=Path("artifacts/learning-persistence-browser"))
    args = parser.parse_args()
    return run(channel=args.channel, output=args.output)


if __name__ == "__main__":
    sys.exit(main())
