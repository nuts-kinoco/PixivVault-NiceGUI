"""Aggregate three measured variants of the mode-switch initial image order."""
import json
from pathlib import Path
from statistics import median

root = Path(__file__).resolve().parent
results = {}
for variant in ("before", "one-row", "after"):
    rows, orders = [], []
    for batch in (1, 2):
        directory = root / f"{variant}-{batch}"
        rows.extend(json.loads((directory / "raw.json").read_text(encoding="utf-8")))
        orders.extend(json.loads((directory / "image-trace.json").read_text(encoding="utf-8")))
    assert len(orders) == 6
    request_order = [[request["rank"] for request in trace] for trace in orders]
    results[variant] = {
        "first_thumbnail_request_positions": [order.index(1) + 1 for order in request_order],
        "first_requested_thumbnail_rank": [order[0] for order in request_order],
        "cases": {},
    }
    for case in dict.fromkeys(row["case"] for row in rows):
        samples = [row for row in rows if row["case"] == case]
        assert len(samples) == 6
        results[variant]["cases"][case] = {
            metric: round(median(row[metric] for row in samples), 2)
            for metric in ("card", "api", "list_ms", "image_ms", "paint_ms", "residual_ms", "screenshot_ms")
        }
(root / "timing-summary.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
