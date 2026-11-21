"""Aggregate raw observations into same-fixture medians for both candidates."""
import json
from pathlib import Path
from statistics import median

evidence = Path(__file__).resolve().parent
results = {}
for variant in ("before", "after"):
    observations = []
    for batch in (1, 2):
        path = evidence / f"paired-{variant}-{batch}" / "raw.json"
        observations.extend(json.loads(path.read_text(encoding="utf-8")))
    cases = {}
    for case in dict.fromkeys(row["case"] for row in observations):
        rows = [row for row in observations if row["case"] == case]
        assert len(rows) == 6
        cases[case] = {
            metric: round(median(row[metric] for row in rows), 2)
            for metric in ("card", "api", "list_ms", "image_ms", "paint_ms", "residual_ms", "screenshot_ms")
        }
    results[variant] = cases
(evidence / "paired-summary.json").write_text(
    json.dumps(results, indent=2) + "\n", encoding="utf-8"
)
