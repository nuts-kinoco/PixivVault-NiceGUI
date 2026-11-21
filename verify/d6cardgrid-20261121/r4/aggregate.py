"""Compare the measured partial-overlap cases and their browser request order."""
import json
from pathlib import Path
from statistics import median

root = Path(__file__).resolve().parent
result = {}
for variant in ("before", "after"):
    rows, requests = [], []
    for batch in (1, 2):
        directory = root / f"{variant}-{batch}"
        rows.extend(json.loads((directory / "raw.json").read_text(encoding="utf-8")))
        requests.extend(json.loads((directory / "image-trace.json").read_text(encoding="utf-8")))
    assert len(requests) == 6
    order = [[entry["rank"] for entry in trace] for trace in requests]
    status = {
        "first_thumbnail_request_positions": [trial.index(1) + 1 for trial in order],
        "first_requested_thumbnail_rank": [trial[0] for trial in order],
        "overlap_requests": [trial.count(21) for trial in order],
        "overlap_kept_image_node": [r["overlap_kept_image_node"] for r in rows if r["case"] == "mode"],
    }
    cases = {}
    for case in dict.fromkeys(row["case"] for row in rows):
        trials = [row for row in rows if row["case"] == case]
        assert len(trials) == 6
        cases[case] = {
            field: round(median(row[field] for row in trials), 2)
            for field in ("card", "api", "list_ms", "image_ms", "paint_ms", "residual_ms", "screenshot_ms")
        }
    result[variant] = dict(requests=status, cases=cases)
(root / "timing-summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
