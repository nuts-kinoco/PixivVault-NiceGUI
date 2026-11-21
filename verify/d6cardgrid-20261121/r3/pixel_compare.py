"""Compare the two fixed ranking viewports against the recorded R2 candidate."""
import json
from pathlib import Path

from PIL import Image, ImageChops

evidence = Path(__file__).resolve().parent
result = {}
for width in (1280, 1920):
    with (
        Image.open(evidence.parent / "r2" / f"stable-{width}.png") as before,
        Image.open(evidence / f"contract-{width}" / "stable.png") as after,
    ):
        assert before.size == after.size
        mask = ImageChops.difference(before.convert("RGB"), after.convert("RGB"))
        red, green, blue = mask.split()
        combined = ImageChops.lighter(ImageChops.lighter(red, green), blue)
        changed = sum(combined.histogram()[1:])
        result[str(width)] = {
            "size": list(before.size),
            "changed_pixels": changed,
            "fraction": changed / (before.width * before.height),
            "bounding_box": combined.getbbox(),
        }
(evidence / "pixel-summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
