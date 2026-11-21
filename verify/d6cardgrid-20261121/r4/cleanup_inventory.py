"""Build the round-four cleanup allowlist without moving or deleting any files."""
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import stat
import subprocess

evidence = Path(__file__).resolve().parent
repository = evidence.parents[2]
temp = Path("S:/Work/_tmp/PV/d6cg")
snapshot = json.loads((evidence / "process-check.json").read_text(encoding="ascii"))
assert not snapshot["still_running"] and not snapshot["listening"]
tracked = {
    repository / file
    for file in subprocess.check_output(
        ["git", "ls-files", "-z", str(evidence.relative_to(repository))], cwd=repository
    ).decode("utf-8").split("\0")
    if file
}
retained = tracked | {evidence / "cleanup_dry_run.csv", evidence / "CLEANUP.md"}


def size_and_links(path):
    info = path.lstat()
    if info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        return info.st_size, 1
    if not path.is_dir():
        return info.st_size, 0
    size = links = 0
    for item in path.iterdir():
        item_size, item_links = size_and_links(item)
        size += item_size
        links += item_links
    return size, links


def evidence_items(folder):
    for item in folder.iterdir():
        if item in retained:
            yield item, "KEEP", "retained candidate evidence"
        elif item.is_dir() and any(item in path.parents for path in retained):
            yield from evidence_items(item)
        else:
            yield item, "DELETE", "regenerable diagnostic; outside retained evidence"


def owned_items():
    for item in temp.iterdir():
        if item.name.startswith("r4-"):
            yield item, "DELETE", "round-four source archive copy, profile, harness or test fixture"
    yield from evidence_items(evidence)


rows = []
for item, action, reason in owned_items():
    # Some pytest fixtures deliberately create paths beyond Win32 MAX_PATH.
    filesystem_path = Path("\\\\?\\" + str(item))
    size, links = size_and_links(filesystem_path)
    modified = datetime.fromtimestamp(item.lstat().st_mtime, timezone.utc).isoformat()
    rows.append((action, str(item), size, modified, links, reason))

rows.sort(key=lambda row: (row[0] != "DELETE", -row[2], row[1]))
with (evidence / "cleanup_dry_run.csv").open("w", encoding="utf-8", newline="") as output:
    writer = csv.writer(output)
    writer.writerow(("action", "absolute_path", "bytes", "modified_utc", "reparse_points", "reason"))
    writer.writerows(rows)
